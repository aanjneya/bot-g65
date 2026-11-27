"""Online Kalman Filter stat-arb model and pair selection ported from statarb."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np


def estimate_half_life(series: np.ndarray) -> Tuple[float, float]:
    """Estimate Ornstein-Uhlenbeck half-life and AR(1) slope from a 1D spread."""
    s = np.asarray(series, dtype=np.float64)
    s = s[np.isfinite(s)]
    if s.size < 20:
        return np.nan, np.nan

    ds = np.diff(s)
    lag = s[:-1]
    if ds.size < 15:
        return np.nan, np.nan

    x_mat = np.column_stack([np.ones(len(lag)), lag])
    coef, _, _, _ = np.linalg.lstsq(x_mat, ds, rcond=None)
    beta = float(coef[1])
    if beta >= 0:
        return np.nan, beta
    hl = float(np.log(2.0) / (-beta))
    return hl, beta


def _ols_spread(a: np.ndarray, b: np.ndarray) -> Tuple[float, float, np.ndarray]:
    """Compute OLS intercept, slope, and residual spread for a = alpha + beta * b."""
    x_mat = np.column_stack([np.ones(len(b)), b])
    coef, _, _, _ = np.linalg.lstsq(x_mat, a, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    spread = a - (alpha + beta * b)
    return alpha, beta, spread


def _coint_pvalue(a: np.ndarray, b: np.ndarray) -> float:
    """Return Engle-Granger cointegration p-value (uses statsmodels if installed, else ADF proxy)."""
    try:
        from statsmodels.tsa.stattools import coint  # type: ignore

        return float(coint(a, b)[1])
    except Exception:
        # Pure-numpy fallback: OLS residual AR(1) t-stat mapped to rough significance
        _, _, spread = _ols_spread(a, b)
        ds = np.diff(spread)
        lag = spread[:-1]
        if ds.size < 15 or np.std(lag) < 1e-12:
            return 1.0
        x_mat = np.column_stack([np.ones(len(lag)), lag])
        coef, _, _, _ = np.linalg.lstsq(x_mat, ds, rcond=None)
        resid = ds - x_mat @ coef
        sigma2 = float(np.sum(resid**2) / max(len(ds) - 2, 1))
        cov = sigma2 * np.linalg.pinv(x_mat.T @ x_mat)
        se = float(np.sqrt(max(cov[1, 1], 1e-16)))
        t_stat = float(coef[1] / se)
        # Engle-Granger 5% critical value ~ -3.34; 1% ~ -3.90
        if t_stat <= -3.90:
            return 0.005
        if t_stat <= -3.34:
            return 0.03
        if t_stat <= -2.86:
            return 0.08
        return 0.50


@dataclass
class PairCandidate:
    stock_a: str
    stock_b: str
    p_value: float
    corr: float
    beta: float
    half_life: float


def select_top_pairs(
    price_history: Dict[str, List[float]],
    max_pairs: int = 4,
    min_points: int = 30,
    min_corr: float = 0.50,
    max_pvalue: float = 0.10,
) -> List[PairCandidate]:
    """Discover disjoint mean-reverting pairs from recent mid-price histories."""
    tickers = [t for t, vals in price_history.items() if len(vals) >= min_points]
    candidates: List[PairCandidate] = []

    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            t1, t2 = tickers[i], tickers[j]
            n = min(len(price_history[t1]), len(price_history[t2]))
            if n < min_points:
                continue
            s1 = np.asarray(price_history[t1][-n:], dtype=np.float64)
            s2 = np.asarray(price_history[t2][-n:], dtype=np.float64)
            mask = np.isfinite(s1) & np.isfinite(s2)
            s1, s2 = s1[mask], s2[mask]
            if s1.size < min_points or np.std(s1) < 1e-9 or np.std(s2) < 1e-9:
                continue

            corr = float(np.corrcoef(s1, s2)[0, 1])
            if not np.isfinite(corr) or corr < min_corr:
                continue

            p1 = _coint_pvalue(s1, s2)
            p2 = _coint_pvalue(s2, s1)
            if p1 > max_pvalue or p2 > max_pvalue:
                continue

            if p1 <= p2:
                stock_a, stock_b, p_val = t1, t2, p1
                _, beta, spread = _ols_spread(s1, s2)
            else:
                stock_a, stock_b, p_val = t2, t1, p2
                _, beta, spread = _ols_spread(s2, s1)

            if not (0.20 <= beta <= 5.0):
                continue

            hl, slope = estimate_half_life(spread)
            if not np.isfinite(hl) or slope >= 0:
                continue

            candidates.append(
                PairCandidate(
                    stock_a=stock_a,
                    stock_b=stock_b,
                    p_value=p_val,
                    corr=corr,
                    beta=beta,
                    half_life=hl,
                )
            )

    # Sort by fastest mean reversion (lowest half-life), then lowest p-value
    candidates.sort(key=lambda c: (c.half_life, c.p_value))

    selected: List[PairCandidate] = []
    used: set[str] = set()
    for c in candidates:
        if c.stock_a in used or c.stock_b in used:
            continue
        selected.append(c)
        used.add(c.stock_a)
        used.add(c.stock_b)
        if len(selected) >= max_pairs:
            break

    return selected


class OnlineKalmanPair:
    """
    2-state recursive Kalman Filter for live pair trading:
        A_t = alpha_t + beta_t * B_t + spread_t
    Ported from statarb/ols.py Strategy._kalman_filter for O(1) per-tick updates.
    """

    def __init__(
        self,
        stock_a: str,
        stock_b: str,
        *,
        delta: float = 1e-5,
        r_var: float = 1.0,
        z_window: int = 60,
        min_warmup: int = 30,
        entry_z1: float = 1.8,
        entry_z2: float = 2.5,
        exit_z: float = 0.2,
        stop_z: float = 3.8,
        t1_weight: float = 0.5,
        min_hurdle_bps: float = 10.0,
    ) -> None:
        self.stock_a = stock_a
        self.stock_b = stock_b
        self.delta = delta
        self.r_var = r_var
        self.z_window = max(z_window, 15)
        self.min_warmup = max(min_warmup, 10)
        self.entry_z1 = entry_z1
        self.entry_z2 = entry_z2
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.t1_weight = t1_weight
        self.min_hurdle_bps = min_hurdle_bps

        self.theta = np.array([0.0, 1.0], dtype=np.float64)
        self.p_cov = np.eye(2, dtype=np.float64)
        self.q_cov = np.eye(2, dtype=np.float64) * delta
        self.initialized = False

        self.warmup_a: List[float] = []
        self.warmup_b: List[float] = []
        self.spread_hist: Deque[float] = deque(maxlen=self.z_window)
        self.ticks_seen: int = 0
        self.current_weight: float = 0.0
        self.last_z: float = 0.0
        self.last_spread: float = 0.0

    @property
    def alpha(self) -> float:
        return float(self.theta[0])

    @property
    def beta(self) -> float:
        return float(self.theta[1])

    def update(self, price_a: float, price_b: float) -> Optional[float]:
        """Ingest one mid-price observation and return the updated z-score (or None during warmup)."""
        if not (np.isfinite(price_a) and np.isfinite(price_b) and price_a > 0 and price_b > 0):
            return None

        self.ticks_seen += 1
        if not self.initialized:
            self.warmup_a.append(price_a)
            self.warmup_b.append(price_b)
            if len(self.warmup_a) >= min(self.min_warmup, 20):
                a_arr = np.asarray(self.warmup_a, dtype=np.float64)
                b_arr = np.asarray(self.warmup_b, dtype=np.float64)
                alpha0, beta0, _ = _ols_spread(a_arr, b_arr)
                if np.isfinite(alpha0) and np.isfinite(beta0) and 0.1 <= beta0 <= 10.0:
                    self.theta = np.array([alpha0, beta0], dtype=np.float64)
                self.initialized = True
                for pa, pb in zip(self.warmup_a, self.warmup_b):
                    self._kalman_step(pa, pb)
            return None

        return self._kalman_step(price_a, price_b)

    def _kalman_step(self, price_a: float, price_b: float) -> Optional[float]:
        p_pred = self.p_cov + self.q_cov
        h_vec = np.array([1.0, price_b], dtype=np.float64)
        y_pred = float(np.dot(h_vec, self.theta))
        error = float(price_a - y_pred)

        f_var = float(np.dot(h_vec, np.dot(p_pred, h_vec)) + self.r_var)
        gain = np.dot(p_pred, h_vec) / max(f_var, 1e-8)

        self.theta = self.theta + gain * error
        self.p_cov = p_pred - np.outer(gain, np.dot(h_vec, p_pred))

        spread = float(price_a - (self.theta[0] + self.theta[1] * price_b))
        self.last_spread = spread

        if len(self.spread_hist) < 10:
            self.spread_hist.append(spread)
            return None

        arr = np.asarray(self.spread_hist, dtype=np.float64)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1))
        self.spread_hist.append(spread)

        if std < 1e-8 or self.ticks_seen < self.min_warmup:
            return None

        self.last_z = float((spread - mean) / std)
        return self.last_z

    def target_weight(self, price_a: float, price_b: float) -> float:
        """
        Compute normalized pair weight in [-1.0, 1.0]:
        +w => long A, short beta * B
        -w => short A, long beta * B
        """
        z = self.update(price_a, price_b)
        if z is None:
            return self.current_weight

        # Stop-loss
        if abs(z) >= self.stop_z:
            self.current_weight = 0.0
            return self.current_weight

        # Mean-reversion exit
        if self.current_weight > 0 and z >= -self.exit_z:
            self.current_weight = 0.0
            return self.current_weight
        if self.current_weight < 0 and z <= self.exit_z:
            self.current_weight = 0.0
            return self.current_weight

        # Hurdle check: expected reversion edge vs pair notional
        notional = max(price_a + abs(self.beta) * price_b, 1e-6)
        edge_bps = abs(self.last_spread) / notional * 10_000.0
        if edge_bps < self.min_hurdle_bps:
            return self.current_weight

        # Tranche entries (scale in, never scale down unless exit/stop)
        if z > self.entry_z2:
            self.current_weight = -1.0
        elif z > self.entry_z1:
            self.current_weight = min(self.current_weight, -self.t1_weight) if self.current_weight <= 0 else -self.t1_weight
        elif z < -self.entry_z2:
            self.current_weight = 1.0
        elif z < -self.entry_z1:
            self.current_weight = max(self.current_weight, self.t1_weight) if self.current_weight >= 0 else self.t1_weight

        return self.current_weight


class BasketFairValueModel:
    """
    Cross-sectional 7-vs-1 Kalman fair-value model for the 8-stock universe.
    Each stock is modeled against the equal-weighted normalized index of the other stocks:
        P_i(t) = alpha_i,t + beta_i,t * Basket_{-i}(t) + spread_i,t
    """

    def __init__(
        self,
        instruments: List[str],
        *,
        delta: float = 1e-5,
        r_var: float = 1.0,
        z_window: int = 60,
        min_warmup: int = 25,
        z_entry: float = 1.6,
        z_exit: float = 0.2,
        z_stop: float = 3.8,
    ) -> None:
        self.instruments = list(instruments)
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.z_stop = z_stop
        self._base_prices: Dict[str, float] = {}
        self._models: Dict[str, OnlineKalmanPair] = {
            inst: OnlineKalmanPair(
                inst,
                f"BASKET_{inst}",
                delta=delta,
                r_var=r_var,
                z_window=z_window,
                min_warmup=min_warmup,
                entry_z1=z_entry,
                entry_z2=z_entry + 0.7,
                exit_z=z_exit,
                stop_z=z_stop,
                t1_weight=0.5,
                min_hurdle_bps=5.0,
            )
            for inst in self.instruments
        }

    def update(self, mids: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        Ingest latest mid-prices for all available stocks.
        Returns (fair_values, target_weights) keyed by instrument_id.
        """
        for inst, px in mids.items():
            if inst not in self._base_prices and px > 0:
                self._base_prices[inst] = px

        norm_prices: Dict[str, float] = {}
        for inst, px in mids.items():
            base = self._base_prices.get(inst)
            if base and base > 0:
                norm_prices[inst] = px / base

        fair_values: Dict[str, float] = {}
        weights: Dict[str, float] = {}

        if len(norm_prices) < 3:
            return fair_values, weights

        total_norm = sum(norm_prices.values())
        count = len(norm_prices)

        for inst, px in mids.items():
            if inst not in norm_prices or inst not in self._models:
                continue
            # Leave-one-out normalized basket scaled to 100.0 for numerical conditioning
            loo_index = ((total_norm - norm_prices[inst]) / (count - 1)) * 100.0
            model = self._models[inst]
            w = model.target_weight(px, loo_index)
            if model.initialized:
                fair_values[inst] = model.alpha + model.beta * loo_index
            weights[inst] = w

        return fair_values, weights
