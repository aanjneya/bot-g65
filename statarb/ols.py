import numpy as np
import pandas as pd
import statsmodels.api as sm
import matplotlib.pyplot as plt
from scipy.linalg import solve


class Strategy:

    def __init__(self, df, stockA, stockB, *, half_life, t1_weight = 1.0, entry_z1=2.0, entry_z2=2.5, exit_z=0.0, stop_z=3.5, rolling_hedge=True, min_hurdle_bps=50.0, method="rolling_ols", kalman_delta=1e-5, kalman_R=1.0, tikhonov_window=60, tikhonov_lambda=1000.0, max_holding_mult=2.0, max_holding_days=None, momentum_filter=True, momentum_window=10, max_z_drift=1.5):
        self.df = df[[stockA, stockB]].dropna()
        self.stockA = stockA
        self.stockB = stockB
        self.half_life = half_life
        self.t1_weight = t1_weight
        self.z_window = int(max(half_life * 4, 30))
        self.hedge_window = int(max(half_life * 4, 30))
        self.entry_z1 = entry_z1
        self.entry_z2 = entry_z2
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.rolling_hedge = rolling_hedge
        self.min_hurdle_bps = min_hurdle_bps
        self.method = method
        self.kalman_delta = kalman_delta
        self.kalman_R = kalman_R
        self.tikhonov_window = tikhonov_window
        self.tikhonov_lambda = tikhonov_lambda

        self.momentum_filter = momentum_filter
        self.momentum_window = int(momentum_window)
        self.max_z_drift = float(max_z_drift)

        self.max_holding_mult = max_holding_mult
        if max_holding_days is not None:
            self.max_holding_days = int(max_holding_days)
        elif max_holding_mult is not None:
            self.max_holding_days = int(max(round(half_life * max_holding_mult), 15))
        else:
            self.max_holding_days = None

        self.alpha, self.beta = None, None
        self.spread, self.z_score, self.position = None, None, None
        self._oos_start, self._equity, self._pnl = None, None, None
        self.trade_log = []

    def _kalman_filter(self, A, B, delta=1e-4, R_var=1e-3):
        """
        2-state recursive Kalman Filter estimating dynamic intercept (alpha_t)
        and dynamic hedge ratio (beta_t).
        State vector theta_t = [alpha_t, beta_t]^T evolves as a random walk.
        Measurement equation: A_t = alpha_t + beta_t * B_t + e_t.
        """
        n = len(A)
        alpha_arr = np.zeros(n)
        beta_arr = np.zeros(n)
        spread_arr = np.zeros(n)
        spread_std_arr = np.zeros(n)

        # Initial state estimate [alpha_0, beta_0] from first 30 days
        init_n = min(30, n)
        if init_n > 2:
            c = np.column_stack([np.ones(init_n), B.iloc[:init_n]])
            init_coef, _, _, _ = np.linalg.lstsq(c, A.iloc[:init_n], rcond=None)
            theta = np.array([float(init_coef[0]), float(init_coef[1])])
        else:
            theta = np.array([0.0, 1.0])

        P = np.eye(2) * 1.0
        Q = np.eye(2) * delta
        R = float(R_var)

        a_vals = A.to_numpy(dtype=np.float64)
        b_vals = B.to_numpy(dtype=np.float64)

        for t in range(n):
            # Prediction step (random walk for latent beta and alpha)
            P_pred = P + Q

            # Measurement prediction and residual error (the spread)
            H = np.array([1.0, b_vals[t]])
            y_pred = float(np.dot(H, theta))
            error = float(a_vals[t] - y_pred)

            # Conditional variance of the residual spread: F_t = H @ P_pred @ H.T + R
            F = float(np.dot(H, np.dot(P_pred, H)) + R)

            # Kalman Gain
            K = np.dot(P_pred, H) / max(F, 1e-8)

            # State update
            theta = theta + K * error
            P = P_pred - np.outer(K, np.dot(H, P_pred))

            alpha_arr[t] = theta[0]
            beta_arr[t] = theta[1]
            spread_arr[t] = error
            spread_std_arr[t] = np.sqrt(max(F, 1e-8))

        alpha_series = pd.Series(alpha_arr, index=A.index)
        beta_series = pd.Series(beta_arr, index=A.index)
        spread_series = pd.Series(spread_arr, index=A.index)
        spread_std = pd.Series(spread_std_arr, index=A.index)

        return alpha_series, beta_series, spread_series, spread_std

    def _rolling_ols(self, A, B, window):

        mean_b = B.rolling(window).mean()
        mean_a = A.rolling(window).mean()

        cov_ab = (A * B).rolling(window).mean() - mean_a * mean_b
        var_b = (B ** 2).rolling(window).mean() - mean_b ** 2

        beta = cov_ab / var_b.replace(0, np.nan)
        alpha = mean_a - beta * mean_b

        return alpha, beta

    def _tikhonov_miller_filter(self, A, B, window=60, lam=1000.0):
        """
        Causal Nonparametric Tikhonov-Miller Regularized Filter.
        Estimates time-varying alpha_t and beta_t by solving a regularized inverse
        problem over a trailing window of length W to penalize parameter roughness
        (first differences) without look-ahead bias:
        min_{beta} ||(A - mean_A) - beta * (B - mean_B)||^2 + lambda * Var(B) * ||D beta||^2.
        Extracts the causal terminal boundary estimate beta_t = beta^*_W, alpha_t = mean_A - beta_t * mean_B
        at each time step t.
        """
        a_vals = A.to_numpy(dtype=np.float64)
        b_vals = B.to_numpy(dtype=np.float64)
        n = len(a_vals)
        W = min(window, n)

        alpha_arr = np.full(n, np.nan)
        beta_arr = np.full(n, np.nan)

        if W < 5:
            return pd.Series(0.0, index=A.index), pd.Series(1.0, index=A.index)

        # Precompute difference operator D^T D for window W
        D_T_D = np.zeros((W, W), dtype=np.float64)
        for i in range(W - 1):
            D_T_D[i, i] += 1.0
            D_T_D[i+1, i+1] += 1.0
            D_T_D[i, i+1] -= 1.0
            D_T_D[i+1, i] -= 1.0

        eye_W = np.eye(W, dtype=np.float64)

        for t in range(W - 1, n):
            sub_a = a_vals[t - W + 1 : t + 1]
            sub_b = b_vals[t - W + 1 : t + 1]

            ma = np.mean(sub_a)
            mb = np.mean(sub_b)
            ta = sub_a - ma
            tb = sub_b - mb

            var_b = float(np.var(tb))
            pen = lam * max(var_b, 1e-4) * D_T_D
            M = np.diag(tb**2) + pen + 1e-6 * eye_W
            rhs = tb * ta

            try:
                b_seq = solve(M, rhs, assume_a='pos')
                bt = float(b_seq[-1])
                at = float(ma - bt * mb)
            except Exception:
                cov = float(np.mean(tb * ta))
                bt = float(cov / var_b) if var_b > 1e-6 else 1.0
                at = float(ma - bt * mb)

            alpha_arr[t] = at
            beta_arr[t] = bt

        alpha_series = pd.Series(alpha_arr, index=A.index)
        beta_series = pd.Series(beta_arr, index=A.index)

        return alpha_series, beta_series

    def OLS(self):
        A = self.df[self.stockA]
        B = self.df[self.stockB]

        if self.method == "kalman":
            alpha, beta, spread, kalman_std = self._kalman_filter(A, B, delta=self.kalman_delta, R_var=self.kalman_R)
            self.alpha = alpha
            self.beta = beta
            self.spread = A - (alpha + beta * B)

            past = self.spread.shift(1)
            roll_mean = past.rolling(self.z_window).mean()
            roll_std = past.rolling(self.z_window).std()

            self.z_score = (self.spread - roll_mean) / roll_std.replace(0, np.nan)
            self._roll_std_z = roll_std
            self._roll_vol_10 = self.spread.rolling(10).std()
        elif self.method == "tikhonov":
            alpha, beta = self._tikhonov_miller_filter(A, B, window=self.tikhonov_window, lam=self.tikhonov_lambda)
            self.alpha = alpha
            self.beta = beta
            self.spread = A - (alpha + beta * B)

            past = self.spread.shift(1)
            roll_mean = past.rolling(self.z_window).mean()
            roll_std = past.rolling(self.z_window).std()

            self.z_score = (self.spread - roll_mean) / roll_std.replace(0, np.nan)
            self._roll_std_z = roll_std
            self._roll_vol_10 = self.spread.rolling(10).std()
        elif self.rolling_hedge:
            alpha, beta = self._rolling_ols(A, B, self.hedge_window)
            self.alpha = alpha
            self.beta = beta
            self.spread = A - (alpha + beta * B)

            past = self.spread.shift(1)
            roll_mean = past.rolling(self.z_window).mean()
            roll_std = past.rolling(self.z_window).std()

            self.z_score = (self.spread - roll_mean) / roll_std.replace(0, np.nan)
            self._roll_std_z = roll_std
            self._roll_vol_10 = self.spread.rolling(10).std()
        else:
            C = sm.add_constant(B)
            model = sm.OLS(A, C).fit()
            alpha = pd.Series(model.params.iloc[0], index=A.index)
            beta = pd.Series(model.params.iloc[1], index=A.index)

            self.alpha = alpha
            self.beta = beta
            self.spread = A - (alpha + beta * B)

            past = self.spread.shift(1)
            roll_mean = past.rolling(self.z_window).mean()
            roll_std = past.rolling(self.z_window).std()

            self.z_score = (self.spread - roll_mean) / roll_std.replace(0, np.nan)
            self._roll_std_z = roll_std
            self._roll_vol_10 = self.spread.rolling(10).std()

    def _tranche_targets(self, current_z):

        if current_z > self.stop_z or current_z < -self.stop_z:
            return 0.0, 0.0
        elif current_z > self.entry_z2:
            return 0.0, -1.0
        elif current_z > self.entry_z1:
            return 0.0, -self.t1_weight
        elif current_z < -self.entry_z2:
            return 1.0, 0.0
        elif current_z < -self.entry_z1:
            return self.t1_weight, 0.0
        elif abs(current_z) < self.exit_z:
            return 0.0, 0.0
        else:
            return None, None

    def generate_signals(self, start_date="2021-01-01", transaction_cost_bps=5.0, entry_end_date=None, entry_windows=None):
        if self.z_score is None:
            self.OLS()
        self._oos_start = pd.Timestamp(start_date)
        self._entry_end = pd.Timestamp(entry_end_date) if entry_end_date is not None else None
        self._entry_windows = entry_windows
        win_ts = [(pd.Timestamp(s), pd.Timestamp(e)) for s, e in entry_windows] if entry_windows else None

        z = self.z_score
        idx = z.index
        zvals = z.to_numpy()
        n = len(zvals)

        pos = np.zeros(n, dtype=np.float64)
        current = 0.0
        days_held = 0
        xz, sz = self.exit_z, self.stop_z

        for i in range(n):
            if idx[i] < self._oos_start:
                pos[i] = 0.0
                current = 0.0
                days_held = 0
                continue

            zi = zvals[i]
            if np.isnan(zi):
                pos[i] = current
                if current != 0.0:
                    days_held += 1
                continue

            if abs(zi) >= sz:
                current = 0.0
                pos[i] = current
                days_held = 0
                continue

            if current > 0 and zi >= -xz:
                current = 0.0
                pos[i] = current
                days_held = 0
                continue
            if current < 0 and zi <= xz:
                current = 0.0
                pos[i] = current
                days_held = 0
                continue

            # Time-decay exit: close position if held beyond max_holding_days
            if current != 0.0 and self.max_holding_days is not None and days_held >= self.max_holding_days:
                current = 0.0
                pos[i] = current
                days_held = 0
                continue

            # Runoff mode: outside the allowed entry window(s), suppress new entries and scaling up
            if win_ts is not None:
                if not any(s <= idx[i] < e for s, e in win_ts):
                    pos[i] = current
                    if current != 0.0:
                        days_held += 1
                    continue
            elif self._entry_end is not None and idx[i] >= self._entry_end:
                pos[i] = current
                if current != 0.0:
                    days_held += 1
                continue

            long_t, short_t = self._tranche_targets(zi)

            v10 = self._roll_vol_10.iloc[i]
            vz = self._roll_std_z.iloc[i]

            if not np.isnan(v10) and not np.isnan(vz) and vz > 0 and v10 > 2.0 * vz:
                long_t, short_t = 0.0, 0.0

            # Expected Return Hurdle Filter
            if (long_t is not None and long_t > 0) or (short_t is not None and short_t < 0):
                sigma_i = vz if (not np.isnan(vz) and vz > 0) else 1.0
                dist_z = abs(zi) - self.exit_z
                price_a = abs(self.df[self.stockA].iloc[i])
                beta_val = abs(self.beta.iloc[i]) if self.beta is not None and not np.isnan(self.beta.iloc[i]) else 1.0
                price_b = abs(self.df[self.stockB].iloc[i])
                gross_exp = price_a + beta_val * price_b
                if gross_exp > 0 and dist_z > 0:
                    exp_gross = (dist_z * sigma_i) / gross_exp
                    roundtrip_cost = 2.0 * (transaction_cost_bps / 10000.0)
                    exp_net_bps = (exp_gross - roundtrip_cost) * 10000.0
                    if exp_net_bps < self.min_hurdle_bps:
                        long_t, short_t = 0.0, 0.0

            # Momentum / Trend Veto Filter: block entry if spread is in strong adverse trend
            if self.momentum_filter and i >= self.momentum_window:
                z_past = self.z_score.iloc[i - self.momentum_window]
                if not np.isnan(z_past):
                    delta_z = zi - z_past
                    # Adverse downward plunge veto for long spread entry (catching a falling knife)
                    if long_t is not None and long_t > 0 and delta_z < -self.max_z_drift:
                        long_t = 0.0
                    # Adverse upward surge veto for short spread entry (fighting a breakout rally)
                    if short_t is not None and short_t < 0 and delta_z > self.max_z_drift:
                        short_t = 0.0

            if long_t is None or short_t is None:
                pos[i] = current
                if current != 0.0:
                    days_held += 1
                continue

            if current > 0:
                if long_t > current: current = long_t
            elif current < 0:
                if short_t < current: current = short_t
            else:
                if long_t > 0 and short_t < 0:
                    current = long_t if zi < 0 else short_t
                elif long_t > 0:
                    current = long_t
                elif short_t < 0:
                    current = short_t

            if current != 0.0:
                days_held += 1
            else:
                days_held = 0

            pos[i] = current

        self.position = pd.Series(pos, index=idx, name="position")

    def backtest(self, transaction_cost_bps=5.0, start_date="2021-01-01", entry_end_date=None, entry_windows=None):

        # Always regenerate: positions depend on start_date, entry windows and the cost-aware hurdle,
        # so reusing a cached position series with different arguments would be silently wrong.
        self.generate_signals(start_date=start_date, transaction_cost_bps=transaction_cost_bps,
                              entry_end_date=entry_end_date, entry_windows=entry_windows)

        oos = self.position.index >= pd.Timestamp(start_date)

        A = self.df[self.stockA]
        B = self.df[self.stockB]

        hedge = self.beta.shift(1)
        pos_lag = self.position.shift(1).fillna(0.0)

        diff_a = A.diff()
        diff_b = B.diff()
        dollar_pnl = diff_a - hedge * diff_b
        gross_exposure = A.shift(1) + hedge.abs() * B.shift(1)

        spread_ret = dollar_pnl / gross_exposure.replace(0, np.nan)
        pnl_gross = pos_lag * spread_ret

        turnover = self.position.diff().abs().fillna(0.0)
        cost = turnover * (transaction_cost_bps / 10000.0) if transaction_cost_bps else 0.0

        pnl = (pnl_gross - cost).fillna(0.0)
        pnl = pnl.where(oos, 0.0)

        self._pnl = pnl
        equity = (1.0 + pnl).cumprod()
        self._equity = equity

        pnl_oos = pnl.loc[oos]
        vol = pnl_oos.std()
        sharpe = (pnl_oos.mean() / vol * np.sqrt(252)) if vol and vol > 0 else 0.0

        eq_oos = equity.loc[oos]
        dd = (eq_oos / eq_oos.cummax() - 1.0).min() if len(eq_oos) else 0.0

        # Build trade log for attribution and calibration
        trade_log = []
        in_trade = False
        trade_entry_date = None
        trade_entry_z = 0.0
        trade_direction = ""
        trade_daily_pnl = []
        trade_exp_ret = 0.0

        pos_series = self.position.loc[oos]
        pnl_series = pnl.loc[oos]
        z_series = self.z_score.loc[oos]
        std_series = self._roll_std_z.loc[oos]
        beta_series = self.beta.loc[oos]
        a_series = A.loc[oos]
        b_series = B.loc[oos]

        dates = pos_series.index
        n_days = len(dates)

        for i in range(n_days):
            d = dates[i]
            p_curr = pos_series.iloc[i]
            p_prev = pos_series.iloc[i-1] if i > 0 else 0.0
            z_curr = z_series.iloc[i]

            if p_prev == 0.0 and p_curr != 0.0:
                in_trade = True
                trade_entry_date = d
                trade_entry_z = z_curr
                trade_direction = "LONG" if p_curr > 0 else "SHORT"
                trade_daily_pnl = [pnl_series.iloc[i]]

                sigma_i = std_series.iloc[i] if not np.isnan(std_series.iloc[i]) else 1.0
                dist_z = abs(z_curr) - self.exit_z
                g_exp = abs(a_series.iloc[i]) + abs(beta_series.iloc[i] * b_series.iloc[i])
                exp_gross = (dist_z * sigma_i) / g_exp if g_exp > 0 and dist_z > 0 else 0.0
                trade_exp_ret = exp_gross - 2.0 * (transaction_cost_bps / 10000.0)

            elif in_trade:
                trade_daily_pnl.append(pnl_series.iloc[i])

                if p_curr == 0.0 or i == n_days - 1:
                    realized_ret = float(np.prod([1.0 + r for r in trade_daily_pnl]) - 1.0)
                    reason = "target_exit"
                    if abs(z_curr) >= self.stop_z:
                        reason = "stop_loss"
                    elif self.max_holding_days is not None and len(trade_daily_pnl) >= self.max_holding_days:
                        reason = "time_decay"
                    elif i == n_days - 1 and p_curr != 0.0:
                        reason = "end_of_period"

                    trade_log.append({
                        "stockA": self.stockA,
                        "stockB": self.stockB,
                        "entry_date": trade_entry_date.strftime("%Y-%m-%d"),
                        "exit_date": d.strftime("%Y-%m-%d"),
                        "direction": trade_direction,
                        "entry_z": float(trade_entry_z),
                        "exit_z": float(z_curr),
                        "expected_return": float(trade_exp_ret),
                        "actual_return": float(realized_ret),
                        "holding_days": int((d - trade_entry_date).days),
                        "exit_reason": reason
                    })
                    in_trade = False
                    trade_daily_pnl = []

        self.trade_log = trade_log

        exp_rets = [t["expected_return"] for t in trade_log]
        act_rets = [t["actual_return"] for t in trade_log]
        corr_exp_act = float(np.corrcoef(exp_rets, act_rets)[0, 1]) if len(exp_rets) > 2 and np.std(exp_rets) > 0 and np.std(act_rets) > 0 else 0.0
        win_rate = float(np.mean([r > 0 for r in act_rets])) if len(act_rets) > 0 else 0.0
        avg_holding = float(np.mean([t["holding_days"] for t in trade_log])) if len(trade_log) > 0 else 0.0
        tot_ret = float(eq_oos.iloc[-1] - 1.0) if len(eq_oos) else 0.0
        ann_ret = float((1.0 + tot_ret) ** (252.0 / max(len(pnl_oos), 1)) - 1.0) if tot_ret > -1.0 else -1.0
        ann_vol = float(vol * np.sqrt(252)) if vol and vol > 0 else 0.0

        return equity, {
            "total_return": tot_ret,
            "annualized_return": ann_ret,
            "annualized_vol": ann_vol,
            "sharpe": float(sharpe) if not np.isnan(sharpe) else 0.0,
            "max_drawdown": float(dd),
            "n_trades": len(trade_log),
            "win_rate": win_rate,
            "avg_holding_days": avg_holding,
            "exp_vs_actual_corr": corr_exp_act,
            "oos_start": str(pd.Timestamp(start_date).date()),
        }

    def plotOLS(self, start_date=None):

        if self.z_score is None:
            self.OLS()
        self.z_score.plot(figsize=(10, 4))

        for lvl, sty in [(self.entry_z1, "--"), (self.entry_z2, "--")]:

            plt.axhline(lvl, color="red", linestyle=sty, alpha=0.75)
            plt.axhline(-lvl, color="green", linestyle=sty, alpha=0.75)

        plt.axhline(self.stop_z, color="darkred", linestyle="-", linewidth=1.0, alpha=0.6)
        plt.axhline(-self.stop_z, color="darkgreen", linestyle="-", linewidth=1.0, alpha=0.6)
        plt.axhline(self.exit_z, color="red", linestyle=":", alpha=0.5)
        plt.axhline(-self.exit_z, color="green", linestyle=":", alpha=0.5)
        plt.axhline(0, color="black", linewidth=0.8)

        if start_date is not None:
            plt.axvline(pd.Timestamp(start_date), color="blue", linestyle="--", alpha=0.7, label="OOS start")
            plt.legend(loc="upper right")

        plt.title(f"z-score: {self.stockA} vs {self.stockB}")
        plt.tight_layout()
        plt.show()
