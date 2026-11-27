from __future__ import annotations
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from find_pairs import half_life, hurst, estimate_spread_ols, get_cointegrated_pairs
from get_data import FetchStocks
from ols import Strategy

ROOT = Path(__file__).resolve().parent
#CSV_PATH = ROOT / "stocks_sp500.csv"
CSV_PATH = ROOT / "stocks_russell3000.csv"
OOS_START = "2025-01-01"

GLOBAL_PAIRS_CACHE = {}

DEFAULT_PARAMS = {
    'rebalance_step': 60,
    'lookback': 504,
    'entry_z1': 2.0,
    'entry_z2': 2.5,
    'stop_z': 3.5,
    'exit_z': 0.0,
    't1_weight': 1.0,
    'min_hurdle_bps': 75.0,
    'k_floor': 8,
    'max_holding_mult': 2.0,
    'momentum_filter': True,
    'momentum_window': 10,
    'max_z_drift': 1.5,
    'start_date': OOS_START,
    'end_date': "2026-04-30"  # last date in stocks_sp500.csv
}

MIN_OVERLAP = 180
HURST_MAX = 0.45
MIN_HALF_LIFE = 3.0
MAX_HALF_LIFE = 45.0


def _build_rebalance_dates(oos_index, first, days_step):

    oos_sorted = oos_index.sort_values()
    subset = oos_sorted[oos_sorted >= first]

    if len(subset) == 0:
        return []

    dates = [pd.Timestamp(subset[0])]

    while True:
        cal_next = dates[-1] + pd.Timedelta(days=days_step)
        nxt = oos_sorted[oos_sorted >= cal_next]

        if len(nxt) == 0:
            break

        t_new = pd.Timestamp(nxt[0])
        if t_new == dates[-1]:
            break

        dates.append(t_new)

    return dates

def _resolve_col(sym, columns):

    s = str(sym)
    if s in columns:
        return s

    alt = s.replace(".", "-")
    if alt in columns:
        return alt

    return None

def run_walk_forward(params= None, df_master= None, df_tickers= None, verbose= False, return_details= False):

    if params is None:
        params = DEFAULT_PARAMS

    rebalance_step = params["rebalance_step"]
    lookback = params["lookback"]
    start_date = params.get("start_date", OOS_START)
    end_date = params.get("end_date", "2026-12-31")

    strat_risk_kw = {
        "entry_z1": params["entry_z1"],
        "entry_z2": params["entry_z2"],
        "stop_z": params["stop_z"],
        "exit_z": params["exit_z"],
        "t1_weight": params["t1_weight"],
        "min_hurdle_bps": params.get("min_hurdle_bps", 75.0),
        "method": params.get("method", "rolling_ols"),
        "tikhonov_window": params.get("tikhonov_window", 60),
        "tikhonov_lambda": params.get("tikhonov_lambda", 1000.0),
        "kalman_delta": params.get("kalman_delta", 1e-5),
        "kalman_R": params.get("kalman_R", 1.0),
        "max_holding_mult": params.get("max_holding_mult", 2.0),
        "max_holding_days": params.get("max_holding_days", None),
        "momentum_filter": params.get("momentum_filter", True),
        "momentum_window": params.get("momentum_window", 10),
        "max_z_drift": params.get("max_z_drift", 1.5)
    }

    if df_master is None or df_tickers is None:
        df_master = pd.read_csv(CSV_PATH, index_col=0, parse_dates=True)
        df_tickers = FetchStocks(str(CSV_PATH)).get_all_tickers()

    idx = df_master.index
    oos_ix = idx[(idx >= pd.Timestamp(start_date)) & (idx <= pd.Timestamp(end_date))]

    rebalance_dates = _build_rebalance_dates(oos_ix, pd.Timestamp(start_date), rebalance_step)
    if not rebalance_dates:
        return 0.0, 0.0

    pair_registry = {}
    log_rows = []

    for seg_i, T_start in enumerate(rebalance_dates):
        if seg_i + 1 < len(rebalance_dates):
            T_next = rebalance_dates[seg_i + 1]
            seg_dates = oos_ix[(oos_ix >= T_start) & (oos_ix < T_next)]
        else:
            T_next = oos_ix[-1] + pd.Timedelta(days=1)
            seg_dates = oos_ix[oos_ix >= T_start]

        if len(seg_dates) == 0:
            continue

        hist_upto = df_master.loc[df_master.index < T_start]
        if len(hist_upto) < lookback:
            continue

        rank_df = hist_upto.tail(lookback)
        if len(rank_df) < MIN_OVERLAP:
            continue

        cache_key = f"{T_start.strftime('%Y-%m-%d')}_{lookback}"
        if cache_key not in GLOBAL_PAIRS_CACHE:
            GLOBAL_PAIRS_CACHE[cache_key] = get_cointegrated_pairs(rank_df, df_tickers)

        pairs = GLOBAL_PAIRS_CACHE[cache_key]

        train_start = pd.Timestamp(rank_df.index[0]).strftime("%Y-%m-%d")

        candidates = []
        for p in pairs:
            a, b = p["stockA"], p["stockB"]
            try:
                ca = _resolve_col(a, rank_df.columns)
                cb = _resolve_col(b, rank_df.columns)
                if not ca or not cb:
                    continue
                if rank_df[[ca, cb]].dropna().shape[0] < MIN_OVERLAP:
                    continue

                spread = estimate_spread_ols(rank_df, ca, cb)
                if spread.empty or len(spread) < MIN_OVERLAP:
                    continue

                h = hurst(spread)
                hl, beta_slope = half_life(spread)

                if np.isnan(h) or h >= HURST_MAX:
                    continue
                if np.isnan(hl) or beta_slope >= 0:
                    continue
                if hl < MIN_HALF_LIFE or hl > MAX_HALF_LIFE:
                    continue

                candidates.append({
                    "Stock_A": ca, "Stock_B": cb, "pValue": p["pValue"],
                    "hurst": h, "half_life": hl, "ou_lambda": -float(beta_slope),
                })
            except Exception:
                continue

        candidates.sort(key=lambda r: r["half_life"])
        top20 = []
        ticker_counts = {}

        for c in candidates:
            a, b = c["Stock_A"], c["Stock_B"]

            if ticker_counts.get(a, 0) >= 1 or ticker_counts.get(b, 0) >= 1:
                continue

            try:
                strat_is = Strategy(rank_df, a, b, half_life=c["half_life"], **strat_risk_kw)
                _, summ_is = strat_is.backtest(start_date=train_start)

                if summ_is["n_trades"] <= 0:
                    continue

                top20.append({**c, **summ_is})
                ticker_counts[a] = ticker_counts.get(a, 0) + 1
                ticker_counts[b] = ticker_counts.get(b, 0) + 1

                if len(top20) >= 20: break
            except Exception:
                continue

        if not top20:
            continue

        T_start_str = T_start.strftime("%Y-%m-%d")
        T_next_str = T_next.strftime("%Y-%m-%d")
        seg_end_str = seg_dates[-1].strftime("%Y-%m-%d")
        for row in top20:
            a, b = row["Stock_A"], row["Stock_B"]
            try:
                ma = _resolve_col(a, df_master.columns)
                mb = _resolve_col(b, df_master.columns)
                if not ma or not mb:
                    continue
                if df_master[[ma, mb]].dropna().shape[0] < MIN_OVERLAP:
                    continue

                log_rows.append({
                    "rebalance_date": T_start_str, "segment_end": seg_end_str,
                    "Stock_A": ma, "Stock_B": mb, "pValue": row["pValue"],
                    "hurst": row["hurst"], "half_life": row["half_life"],
                })

                # Contiguous re-selection in the same orientation extends the current activation.
                # Anything else (new pair, re-selection after a gap, orientation flip) starts a new
                # activation using the parameters chosen at THIS rebalance date (causal).
                pair_key = "|".join(sorted([ma, mb]))
                last = pair_registry.get(pair_key, [None])[-1]
                if last is not None and last["end"] == T_start_str and last["stockA"] == ma:
                    last["end"] = T_next_str
                else:
                    pair_registry.setdefault(pair_key, []).append({
                        "stockA": ma, "stockB": mb, "half_life": row["half_life"],
                        "start": T_start_str, "end": T_next_str, "key": pair_key
                    })
            except Exception:
                continue

    completed_activations = []
    all_trades = []
    pnl_series_dict = {}
    pos_series_dict = {}

    for pair_key, acts in pair_registry.items():
        prev_pos = None
        for act in acts:
            ma, mb = act["stockA"], act["stockB"]
            eff_start = pd.Timestamp(act["start"])
            end_ts = pd.Timestamp(act["end"])

            # Block entries until the previous activation on this pair is flat
            if prev_pos is not None:
                flat = prev_pos[(prev_pos.index >= eff_start) & (prev_pos == 0.0)]
                if len(flat) == 0 or flat.index[0] >= end_ts:
                    continue
                eff_start = flat.index[0]
                # The previous position closed at this close; open a new one from the next bar
                later = oos_ix[oos_ix > eff_start]
                if prev_pos.loc[eff_start] == 0.0 and eff_start != pd.Timestamp(act["start"]):
                    if len(later) == 0 or later[0] >= end_ts:
                        continue
                    eff_start = later[0]

            act["eff_start"] = eff_start.strftime("%Y-%m-%d")
            i = len(completed_activations)
            completed_activations.append(act)

            strat = Strategy(df_master, ma, mb, half_life=act["half_life"], **strat_risk_kw)
            strat.backtest(start_date=act["eff_start"], entry_end_date=act["end"], transaction_cost_bps=5.0)

            pnl = strat._pnl.reindex(oos_ix).fillna(0.0)
            pos = strat.position.reindex(oos_ix).fillna(0.0)
            col_id = f"act_{i}_{act['key']}"
            pnl_series_dict[col_id] = pnl
            pos_series_dict[col_id] = pos
            prev_pos = pos

            if hasattr(strat, "trade_log") and strat.trade_log:
                all_trades.extend(strat.trade_log)

    df_pnls = pd.DataFrame(pnl_series_dict, index=oos_ix) if pnl_series_dict else pd.DataFrame(index=oos_ix)
    df_poss = pd.DataFrame(pos_series_dict, index=oos_ix) if pos_series_dict else pd.DataFrame(index=oos_ix)

    daily_portfolio_return = pd.Series(0.0, index=oos_ix)
    if not df_pnls.empty:
        k_floor = params.get('k_floor', 8)
        open_mask = (df_poss != 0.0) | (df_pnls != 0.0)
        open_counts = open_mask.sum(axis=1)
        sum_pnls = df_pnls.sum(axis=1)
        divisors = np.maximum(open_counts, k_floor)
        daily_portfolio_return = pd.Series(
            np.where(divisors > 0, sum_pnls / divisors, 0.0),
            index=oos_ix
        )

    if verbose:
        pd.DataFrame(log_rows).to_csv(ROOT / "oos_survivors.csv", index=False)
        if all_trades:
            pd.DataFrame(all_trades).to_csv(ROOT / "trade_log.csv", index=False)

    portfolio_equity_curve = (1.0 + daily_portfolio_return).cumprod()

    if len(portfolio_equity_curve):
        vol = daily_portfolio_return.std()
        p_sharpe = float(daily_portfolio_return.mean() / vol * np.sqrt(252)) if vol and vol > 0 else 0.0
        p_dd = float((portfolio_equity_curve / portfolio_equity_curve.cummax() - 1.0).min())
        tot_ret = float(portfolio_equity_curve.iloc[-1] - 1.0) if len(portfolio_equity_curve) else 0.0
        n_days = len(daily_portfolio_return)
        ann_ret = float((1.0 + tot_ret) ** (252.0 / max(n_days, 1)) - 1.0) if tot_ret > -1.0 else -1.0
        ann_vol = float(vol * np.sqrt(252)) if vol and vol > 0 else 0.0

        exp_rets = [t["expected_return"] for t in all_trades]
        act_rets = [t["actual_return"] for t in all_trades]
        corr_exp_act = float(np.corrcoef(exp_rets, act_rets)[0, 1]) if len(exp_rets) > 2 and np.std(exp_rets) > 0 and np.std(act_rets) > 0 else 0.0
        win_rate = float(np.mean([r > 0 for r in act_rets])) if len(act_rets) > 0 else 0.0

        summary = {
            "total_return": tot_ret,
            "annualized_return": ann_ret,
            "annualized_vol": ann_vol,
            "sharpe": p_sharpe,
            "max_drawdown": p_dd,
            "n_trades": len(all_trades),
            "win_rate": win_rate,
            "exp_vs_actual_corr": corr_exp_act
        }

        if verbose:
            print("\n" + "="*40)
            print("WALK-FORWARD OOS RESULTS")
            print("="*40)
            print(f"Rebalance Events:       {len(rebalance_dates)}")
            print(f"Total Portfolio Return: {tot_ret*100:.2f}%")
            print(f"Annualized Return:      {ann_ret*100:.2f}%")
            print(f"Annualized Volatility:  {ann_vol*100:.2f}%")
            print(f"Annualized Sharpe:      {p_sharpe:.2f}")
            print(f"Max Drawdown:           {p_dd*100:.2f}%")
            print(f"Total Completed Trades: {len(all_trades)}")
            print(f"Win Rate:               {win_rate*100:.1f}%")
            print(f"E[R] vs Realized Corr:  r = {corr_exp_act:.3f}")
            print("="*40)

            fig, ax = plt.subplots(figsize=(10, 4))
            portfolio_equity_curve.plot(ax=ax, color="navy", linewidth=1.5)
            ax.set_title(f"Walk-Forward Portfolio Equity ({params['rebalance_step']} days rebalance)")
            ax.set_ylabel("Equity Multiplier")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            plt.show()

        if return_details:
            return p_sharpe, p_dd, summary, portfolio_equity_curve, daily_portfolio_return, all_trades

        return p_sharpe, p_dd

    if return_details:
        return 0.0, 0.0, {}, pd.Series(dtype=float), pd.Series(dtype=float), []
    return 0.0, 0.0


def run_all_baselines(df_master=None, df_tickers=None):
    if df_master is None or df_tickers is None:
        df_master = pd.read_csv(CSV_PATH, index_col=0, parse_dates=True)
        df_tickers = FetchStocks(str(CSV_PATH)).get_all_tickers()

    print("\n" + "="*80)
    print("RUNNING MULTI-BASELINE QUANTITATIVE EVALUATION SUITE")
    print("="*80)

    # 1. Dynamic Walk-Forward (60-day rebalance, Rolling OLS)
    print("\n[1/6] Running Dynamic Walk-Forward Strategy (Rolling OLS, 60d)...")
    wf_params = dict(DEFAULT_PARAMS)
    wf_params['rebalance_step'] = 60
    wf_params['method'] = 'rolling_ols'
    _, _, wf_summ, wf_eq, wf_daily, wf_trades = run_walk_forward(wf_params, df_master, df_tickers, verbose=False, return_details=True)

    # 2. Dynamic Walk-Forward (60-day rebalance, Kalman Filter)
    print("[2/6] Running Dynamic Walk-Forward Strategy (Kalman Filter, 60d)...")
    kalman_params = dict(DEFAULT_PARAMS)
    kalman_params['rebalance_step'] = 60
    kalman_params['method'] = 'kalman'
    kalman_params['kalman_delta'] = 1e-5
    kalman_params['kalman_R'] = 1.0
    _, _, kalman_summ, kalman_eq, kalman_daily, kalman_trades = run_walk_forward(kalman_params, df_master, df_tickers, verbose=False, return_details=True)

    # 3. Dynamic Walk-Forward (60-day rebalance, Tikhonov-Miller Regularization)
    print("[3/6] Running Dynamic Walk-Forward Strategy (Tikhonov-Miller Regularization, 60d)...")
    tik_params = dict(DEFAULT_PARAMS)
    tik_params['rebalance_step'] = 60
    tik_params['method'] = 'tikhonov'
    tik_params['tikhonov_window'] = 60
    tik_params['tikhonov_lambda'] = 1000.0
    _, _, tik_summ, tik_eq, tik_daily, tik_trades = run_walk_forward(tik_params, df_master, df_tickers, verbose=False, return_details=True)

    # 4. Static Parameters (No rebalancing during OOS)
    print("[4/6] Running Static Parameter Baseline (Single In-Sample Screen)...")
    static_params = dict(DEFAULT_PARAMS)
    static_params['rebalance_step'] = 9999
    static_params['method'] = 'rolling_ols'
    _, _, static_summ, static_eq, static_daily, _ = run_walk_forward(static_params, df_master, df_tickers, verbose=False, return_details=True)

    # 5. Naive Rule-of-Thumb Strategy (Lookback=252, Entry z=2.0, Exit z=0.0, Stop z=4.0)
    print("[5/6] Running Naive Benchmark (Lookback=252d, z=2.0, Exit=0.0)...")
    naive_params = {
        'rebalance_step': 9999,
        'lookback': 252,
        'entry_z1': 2.0,
        'entry_z2': 3.0,
        'stop_z': 4.0,
        'exit_z': 0.0,
        't1_weight': 1.0,
        'min_hurdle_bps': 0.0,
        'k_floor': 8,
        'method': 'rolling_ols',
        'start_date': OOS_START,
        'end_date': DEFAULT_PARAMS.get("end_date", "2026-09-30")
    }
    _, _, naive_summ, naive_eq, naive_daily, _ = run_walk_forward(naive_params, df_master, df_tickers, verbose=False, return_details=True)

    # 6. S&P 500 Market Benchmark (Universe Equal-Weighted Proxy)
    print("[6/6] Computing Market Benchmark (S&P 500 Proxy)...")
    idx = df_master.index
    oos_ix = idx[(idx >= pd.Timestamp(OOS_START)) & (idx <= pd.Timestamp(DEFAULT_PARAMS.get("end_date", "2026-09-30")))]
    mkt_daily = df_master.pct_change().mean(axis=1).reindex(oos_ix).fillna(0.0)
    mkt_eq = (1.0 + mkt_daily).cumprod()
    mkt_tot = float(mkt_eq.iloc[-1] - 1.0) if len(mkt_eq) else 0.0
    mkt_ann = float((1.0 + mkt_tot) ** (252.0 / max(len(mkt_daily), 1)) - 1.0)
    mkt_vol = float(mkt_daily.std() * np.sqrt(252))
    mkt_sharpe = float(mkt_daily.mean() / mkt_daily.std() * np.sqrt(252)) if mkt_daily.std() > 0 else 0.0
    mkt_dd = float((mkt_eq / mkt_eq.cummax() - 1.0).min()) if len(mkt_eq) else 0.0

    def compute_beta_corr(strat_daily, bmk_daily):
        common = strat_daily.align(bmk_daily, join='inner')
        s, b = common[0], common[1]
        var_b = float(b.var())
        cov_sb = float(s.cov(b))
        beta = cov_sb / var_b if var_b > 0 else 0.0
        corr = float(s.corr(b)) if s.std() > 0 and b.std() > 0 else 0.0
        return beta, corr

    wf_beta, wf_corr = compute_beta_corr(wf_daily, mkt_daily)
    kalman_beta, kalman_corr = compute_beta_corr(kalman_daily, mkt_daily)
    tik_beta, tik_corr = compute_beta_corr(tik_daily, mkt_daily)
    static_beta, static_corr = compute_beta_corr(static_daily, mkt_daily)
    naive_beta, naive_corr = compute_beta_corr(naive_daily, mkt_daily)

    # Save all trades from dynamic strategy to trade_log.csv
    if wf_trades:
        df_trades = pd.DataFrame(wf_trades)
        df_trades.to_csv(ROOT / "trade_log.csv", index=False)
        print(f"\n[Export] Saved {len(df_trades)} round-trip trades to {ROOT / 'trade_log.csv'}")

    records = [
        {
            "Strategy": "Dynamic Walk-Forward (Rolling OLS)",
            "Total Ret (%)": f"{wf_summ.get('total_return', 0.0)*100:.2f}%",
            "Ann. Ret (%)": f"{wf_summ.get('annualized_return', 0.0)*100:.2f}%",
            "Ann. Vol (%)": f"{wf_summ.get('annualized_vol', 0.0)*100:.2f}%",
            "Sharpe": f"{wf_summ.get('sharpe', 0.0):.2f}",
            "Max DD (%)": f"{wf_summ.get('max_drawdown', 0.0)*100:.2f}%",
            "Beta (SPY)": f"{wf_beta:.3f}",
            "Corr (SPY)": f"{wf_corr:.3f}",
            "Win Rate (%)": f"{wf_summ.get('win_rate', 0.0)*100:.1f}%",
            "Trades": wf_summ.get('n_trades', 0)
        },
        {
            "Strategy": "Dynamic Walk-Forward (Kalman Filter)",
            "Total Ret (%)": f"{kalman_summ.get('total_return', 0.0)*100:.2f}%",
            "Ann. Ret (%)": f"{kalman_summ.get('annualized_return', 0.0)*100:.2f}%",
            "Ann. Vol (%)": f"{kalman_summ.get('annualized_vol', 0.0)*100:.2f}%",
            "Sharpe": f"{kalman_summ.get('sharpe', 0.0):.2f}",
            "Max DD (%)": f"{kalman_summ.get('max_drawdown', 0.0)*100:.2f}%",
            "Beta (SPY)": f"{kalman_beta:.3f}",
            "Corr (SPY)": f"{kalman_corr:.3f}",
            "Win Rate (%)": f"{kalman_summ.get('win_rate', 0.0)*100:.1f}%",
            "Trades": kalman_summ.get('n_trades', 0)
        },
        {
            "Strategy": "Dynamic Walk-Forward (Tikhonov-Miller)",
            "Total Ret (%)": f"{tik_summ.get('total_return', 0.0)*100:.2f}%",
            "Ann. Ret (%)": f"{tik_summ.get('annualized_return', 0.0)*100:.2f}%",
            "Ann. Vol (%)": f"{tik_summ.get('annualized_vol', 0.0)*100:.2f}%",
            "Sharpe": f"{tik_summ.get('sharpe', 0.0):.2f}",
            "Max DD (%)": f"{tik_summ.get('max_drawdown', 0.0)*100:.2f}%",
            "Beta (SPY)": f"{tik_beta:.3f}",
            "Corr (SPY)": f"{tik_corr:.3f}",
            "Win Rate (%)": f"{tik_summ.get('win_rate', 0.0)*100:.1f}%",
            "Trades": tik_summ.get('n_trades', 0)
        },
        {
            "Strategy": "Static In-Sample Params",
            "Total Ret (%)": f"{static_summ.get('total_return', 0.0)*100:.2f}%",
            "Ann. Ret (%)": f"{static_summ.get('annualized_return', 0.0)*100:.2f}%",
            "Ann. Vol (%)": f"{static_summ.get('annualized_vol', 0.0)*100:.2f}%",
            "Sharpe": f"{static_summ.get('sharpe', 0.0):.2f}",
            "Max DD (%)": f"{static_summ.get('max_drawdown', 0.0)*100:.2f}%",
            "Beta (SPY)": f"{static_beta:.3f}",
            "Corr (SPY)": f"{static_corr:.3f}",
            "Win Rate (%)": f"{static_summ.get('win_rate', 0.0)*100:.1f}%",
            "Trades": static_summ.get('n_trades', 0)
        },
        {
            "Strategy": "Naive Canonical (252d, z=2)",
            "Total Ret (%)": f"{naive_summ.get('total_return', 0.0)*100:.2f}%",
            "Ann. Ret (%)": f"{naive_summ.get('annualized_return', 0.0)*100:.2f}%",
            "Ann. Vol (%)": f"{naive_summ.get('annualized_vol', 0.0)*100:.2f}%",
            "Sharpe": f"{naive_summ.get('sharpe', 0.0):.2f}",
            "Max DD (%)": f"{naive_summ.get('max_drawdown', 0.0)*100:.2f}%",
            "Beta (SPY)": f"{naive_beta:.3f}",
            "Corr (SPY)": f"{naive_corr:.3f}",
            "Win Rate (%)": f"{naive_summ.get('win_rate', 0.0)*100:.1f}%",
            "Trades": naive_summ.get('n_trades', 0)
        },
        {
            "Strategy": "S&P 500 Market Benchmark",
            "Total Ret (%)": f"{mkt_tot*100:.2f}%",
            "Ann. Ret (%)": f"{mkt_ann*100:.2f}%",
            "Ann. Vol (%)": f"{mkt_vol*100:.2f}%",
            "Sharpe": f"{mkt_sharpe:.2f}",
            "Max DD (%)": f"{mkt_dd*100:.2f}%",
            "Beta (SPY)": "1.000",
            "Corr (SPY)": "1.000",
            "Win Rate (%)": "-",
            "Trades": "-"
        }
    ]

    df_comp = pd.DataFrame(records)
    print("\n" + "="*105)
    print("STRATEGY PERFORMANCE COMPARISON (OUT-OF-SAMPLE)")
    print("="*105)
    print(df_comp.to_string(index=False))
    print("="*105)

    # Print Calibration Analysis
    if wf_trades:
        exp_rets = [t["expected_return"] * 100 for t in wf_trades]
        act_rets = [t["actual_return"] * 100 for t in wf_trades]
        corr_val = wf_summ.get("exp_vs_actual_corr", 0.0)
        reasons = pd.Series([t["exit_reason"] for t in wf_trades]).value_counts().to_dict()

        print("\n" + "="*50)
        print("TRADE-LEVEL CALIBRATION & ATTRIBUTION")
        print("="*50)
        print(f"Total Completed Trades:     {len(wf_trades)}")
        print(f"Mean Expected Return:       {np.mean(exp_rets):.2f}%")
        print(f"Mean Realized Return:       {np.mean(act_rets):.2f}%")
        print(f"Expected vs Realized Corr:  r = {corr_val:.3f}")
        print(f"Exit Reasons Breakdown:     {reasons}")
        print("="*50 + "\n")

    # Generate Comparative Equity Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

    # Panel 1: Stat-Arb Strategies
    if len(wf_eq):
        wf_eq.plot(ax=ax1, label="Dynamic Walk-Forward (Rolling OLS)", color="navy", linewidth=1.8)
    if len(kalman_eq):
        kalman_eq.plot(ax=ax1, label="Dynamic Walk-Forward (Kalman Filter)", color="forestgreen", linewidth=1.8)
    if len(tik_eq):
        tik_eq.plot(ax=ax1, label="Dynamic Walk-Forward (Tikhonov-Miller)", color="darkorchid", linewidth=1.8)
    if len(static_eq):
        static_eq.plot(ax=ax1, label="Static In-Sample Params", color="darkorange", linestyle="--", linewidth=1.5)
    if len(naive_eq):
        naive_eq.plot(ax=ax1, label="Naive Canonical (252d, z=2)", color="firebrick", linestyle=":", linewidth=1.5)
    ax1.set_title("Stat-Arb Strategies Out-of-Sample Performance (2025–2026)")
    ax1.set_ylabel("Equity Multiplier")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # Panel 2: Strategy vs Market Benchmark
    if len(wf_eq):
        wf_eq.plot(ax=ax2, label="Dynamic Walk-Forward (Rolling OLS)", color="navy", linewidth=1.8)
    if len(kalman_eq):
        kalman_eq.plot(ax=ax2, label="Dynamic Walk-Forward (Kalman Filter)", color="forestgreen", linewidth=1.8)
    if len(tik_eq):
        tik_eq.plot(ax=ax2, label="Dynamic Walk-Forward (Tikhonov-Miller)", color="darkorchid", linewidth=1.8)
    if len(mkt_eq):
        mkt_eq.plot(ax=ax2, label="S&P 500 Market Benchmark", color="gray", alpha=0.8, linewidth=1.5)
    ax2.set_title("Market Neutrality: Walk-Forward Strategies vs. S&P 500 Benchmark")
    ax2.set_ylabel("Equity Multiplier")
    ax2.set_xlabel("Date")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    plt.show()

    return df_comp


if __name__ == "__main__":
    run_all_baselines()
