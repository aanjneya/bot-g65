"""ABMM-S (Adaptive Bayesian Multi-Asset Market Maker & Sniper) signal & GLFT engine.

Combines:
1. Stoikov Microprice + normalized Order Flow Imbalance (OFI)
2. Optiver house-bot wall anchor (single book level volume >= wall_min_volume, e.g. 250)
3. Glosten-Milgrom online counterparty markout profiler on live TradeTicks
4. Return-space cross-stock lead-lag & 8x8 EWMA covariance matrix (Sigma)
5. Multi-asset GLFT reservation price: r_t = F_hat_t - gamma(t) * Sigma_dP * q_t
6. <1ms news lexicon sentiment sniper + jump guard
7. Endgame inventory glidepath (min 27-30) & live regime-decay spread adaptation
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 1. Stoikov Microprice + Order Flow Imbalance (OFI)
# ---------------------------------------------------------------------------


class MicropriceOFI:
    """Computes volume-capped Stoikov microprice edge and 2s/EWMA Order Flow Imbalance (OFI)."""

    def __init__(
        self,
        w_micro: float = 0.65,
        w_ofi: float = 0.35,
        ewma_alpha: float = 0.25,
        max_shift_ticks: float = 2.5,
        vol_cap: int = 100,
        win_sec: float = 2.0,
    ) -> None:
        self.w_micro = w_micro
        self.w_ofi = w_ofi
        self.ewma_alpha = ewma_alpha
        self.max_shift_ticks = max_shift_ticks
        self.vol_cap = max(10, int(vol_cap))
        self.win_sec = max(0.2, float(win_sec))
        self._prev_bbo: Dict[str, Tuple[float, int, float, int]] = {}
        self._ewma_ofi: Dict[str, float] = {}
        self._ewma_ofi_sq: Dict[str, float] = {}
        self._ofi_win: Dict[str, Deque[Tuple[float, float]]] = {}
        self.last_micro_edge: Dict[str, float] = {}
        self.last_ofi_win: Dict[str, float] = {}

    def update(self, inst_id: str, book: Any, tick: float, now: float = 0.0) -> Tuple[Optional[float], float]:
        """Return (mid, impulse_in_price_units). Caps BBO volumes so 800-lot walls don't distort microprice/OFI."""
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []
        if not bids or not asks:
            return None, 0.0

        bp, raw_bv = float(bids[0].price), int(bids[0].volume)
        ap, raw_av = float(asks[0].price), int(asks[0].volume)
        if ap <= bp or bp <= 0 or ap <= 0:
            return None, 0.0

        bv = min(raw_bv, self.vol_cap)
        av = min(raw_av, self.vol_cap)
        mid = 0.5 * (bp + ap)
        total_v = bv + av
        micro = (bp * av + ap * bv) / total_v if total_v > 0 else mid
        micro_edge = micro - mid
        self.last_micro_edge[inst_id] = micro_edge

        prev = self._prev_bbo.get(inst_id)
        self._prev_bbo[inst_id] = (bp, bv, ap, av)

        ofi_z = 0.0
        ofi = 0.0
        if prev is not None:
            pbp, pbv, pap, pav = prev
            db = (bv if bp >= pbp else 0) - (pbv if bp <= pbp else 0)
            da = (av if ap <= pap else 0) - (pav if ap >= pap else 0)
            ofi = float(db - da)
            a = self.ewma_alpha
            ewma = (1.0 - a) * self._ewma_ofi.get(inst_id, 0.0) + a * ofi
            ewma_sq = (1.0 - a) * self._ewma_ofi_sq.get(inst_id, ofi * ofi) + a * (ofi * ofi)
            self._ewma_ofi[inst_id] = ewma
            self._ewma_ofi_sq[inst_id] = ewma_sq
            rms = math.sqrt(max(ewma_sq, 1e-9))
            ofi_z = max(-2.0, min(2.0, ewma / rms))

        dq = self._ofi_win.setdefault(inst_id, deque())
        dq.append((now, ofi))
        while len(dq) > 1 and now - dq[0][0] > self.win_sec:
            dq.popleft()
        self.last_ofi_win[inst_id] = sum(x[1] for x in dq)

        eff_tick = tick if tick > 0 else 0.01
        raw_impulse = self.w_micro * micro_edge + self.w_ofi * ofi_z * eff_tick
        cap = self.max_shift_ticks * eff_tick
        return mid, max(-cap, min(cap, raw_impulse))


# ---------------------------------------------------------------------------
# 2. House-Bot Wall Anchor & Protecting Wall Finder (V >= 400 lots)
# ---------------------------------------------------------------------------


def find_protecting_wall(
    book: Any,
    side: str,
    tick: float,
    wall_min_volume: int = 400,
    max_dist_ticks: int = 3,
) -> Optional[float]:
    """
    Find closest resting order-book wall (volume >= wall_min_volume) within max_dist_ticks of BBO.
    Assumes book has already been stripped of our own resting orders.
    """
    levels = (getattr(book, "bids", None) if side == "bid" else getattr(book, "asks", None)) or []
    if not levels:
        return None
    eff_tick = tick if tick > 0 else 0.10
    best = float(levels[0].price)
    max_dist = max_dist_ticks * eff_tick + 1e-9
    for lv in levels:
        p, v = float(lv.price), int(lv.volume)
        dist = (best - p) if side == "bid" else (p - best)
        if dist > max_dist:
            break
        if v >= wall_min_volume:
            return p
    return None


def volume_at_price(levels: Any, price: float, tick: float = 0.10) -> int:
    tol = max(1e-6, 0.25 * (tick if tick > 0 else 0.10))
    for lv in levels or []:
        if abs(float(lv.price) - price) <= tol:
            return int(lv.volume)
    return 0


def extract_wall_anchor(
    book: Any,
    mid: float,
    tick: float,
    wall_min_volume: int = 250,
    max_shift_ticks: float = 4.0,
) -> float:
    """
    Extract fair-value shift implied by house-bot book walls (volume >= wall_min_volume).
    Returns 0.0 if either side lacks a qualifying wall so asymmetric walls never distort FV.
    """
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return 0.0

    wall_bid = next((float(lv.price) for lv in bids if int(lv.volume) >= wall_min_volume), None)
    wall_ask = next((float(lv.price) for lv in asks if int(lv.volume) >= wall_min_volume), None)
    if wall_bid is None or wall_ask is None or wall_ask <= wall_bid:
        return 0.0

    wall_mid = 0.5 * (wall_bid + wall_ask)
    gap = wall_mid - mid
    eff_tick = tick if tick > 0 else 0.01
    cap = max_shift_ticks * eff_tick
    return max(-cap, min(cap, gap))


# ---------------------------------------------------------------------------
# 3. Glosten-Milgrom Online Counterparty Markout Engine
# ---------------------------------------------------------------------------


@dataclass
class _PendingMarkout:
    eval_time: float
    inst_id: str
    aggressor: str
    sign: int
    price: float
    volume: int
    tick: float


@dataclass
class CounterpartyStats:
    trades: int = 0
    total_volume: int = 0
    ewma_markout_ticks: float = 0.0


class CounterpartyTracker:
    """
    Profiles live TradeTick aggressors via post-trade markouts.
    - Informed / toxic counterparties (positive markout): follow direction & widen spread.
    - Uninformed / noise counterparties (negative markout): tighten spread to harvest flow.
    """

    def __init__(
        self,
        horizon_sec: float = 5.0,
        min_trades: int = 12,
        informed_threshold_ticks: float = 0.25,
        noise_threshold_ticks: float = 0.15,
        impulse_ticks: float = 1.0,
        max_impulse_ticks: float = 3.0,
        decay_half_life_sec: float = 2.0,
        own_username: Optional[str] = None,
        max_pending: int = 5000,
    ) -> None:
        self.horizon_sec = horizon_sec
        self.min_trades = min_trades
        self.informed_threshold_ticks = informed_threshold_ticks
        self.noise_threshold_ticks = noise_threshold_ticks
        self.impulse_ticks = impulse_ticks
        self.max_impulse_ticks = max_impulse_ticks
        self.decay_half_life_sec = max(0.1, decay_half_life_sec)
        self.own_username = (own_username or "").strip()

        self._pending: Deque[_PendingMarkout] = deque(maxlen=max_pending)
        self.stats: Dict[str, CounterpartyStats] = {}
        # Decaying per-instrument state: (value, last_update_time)
        self._impulse: Dict[str, Tuple[float, float]] = {}
        self._toxicity: Dict[str, Tuple[float, float]] = {}
        self._noise_flow: Dict[str, Tuple[float, float]] = {}

    def _decay(self, val: float, dt: float) -> float:
        if dt <= 0.0:
            return val
        return val * math.pow(0.5, dt / self.decay_half_life_sec)

    def classify(self, counterparty: str) -> str:
        """Return 'informed', 'noise', or 'unknown'."""
        st = self.stats.get(counterparty)
        if st is None or st.trades < self.min_trades:
            return "unknown"
        if st.ewma_markout_ticks >= self.informed_threshold_ticks:
            return "informed"
        if st.ewma_markout_ticks <= -self.noise_threshold_ticks:
            return "noise"
        return "unknown"

    def on_trade(
        self,
        now: float,
        inst_id: str,
        price: float,
        volume: int,
        aggressor_side: str,
        buyer: Optional[str],
        seller: Optional[str],
        tick: float,
    ) -> None:
        side = (aggressor_side or "").strip().lower()
        if side == "bid":
            sign = 1
            aggressor = (buyer or "").strip()
        elif side == "ask":
            sign = -1
            aggressor = (seller or "").strip()
        else:
            return

        if not aggressor or (self.own_username and aggressor == self.own_username):
            return

        eff_tick = tick if tick > 0 else 0.01
        self._pending.append(
            _PendingMarkout(
                eval_time=now + self.horizon_sec,
                inst_id=inst_id,
                aggressor=aggressor,
                sign=sign,
                price=float(price),
                volume=max(1, int(volume)),
                tick=eff_tick,
            )
        )

        role = self.classify(aggressor)
        if role == "informed":
            prev_imp, prev_t = self._impulse.get(inst_id, (0.0, now))
            decayed = self._decay(prev_imp, now - prev_t)
            vol_scale = min(2.0, math.sqrt(max(1, int(volume)) / 10.0))
            new_imp = decayed + sign * self.impulse_ticks * eff_tick * vol_scale
            cap = self.max_impulse_ticks * eff_tick
            self._impulse[inst_id] = (max(-cap, min(cap, new_imp)), now)

            prev_tox, tox_t = self._toxicity.get(inst_id, (0.0, now))
            new_tox = min(2.0, self._decay(prev_tox, now - tox_t) + 0.5)
            self._toxicity[inst_id] = (new_tox, now)
        elif role == "noise":
            prev_nf, nf_t = self._noise_flow.get(inst_id, (0.0, now))
            new_nf = min(1.0, self._decay(prev_nf, now - nf_t) + 0.35)
            self._noise_flow[inst_id] = (new_nf, now)

    def update_markouts(self, now: float, mids: Dict[str, float]) -> int:
        """Evaluate matured trades against current mid-prices. Returns number evaluated."""
        evaluated = 0
        while self._pending and self._pending[0].eval_time <= now:
            obs = self._pending.popleft()
            mid = mids.get(obs.inst_id)
            if mid is None:
                continue
            markout_ticks = obs.sign * (mid - obs.price) / obs.tick
            # Clip extreme single-trade markout outliers (e.g., news jumps) at +-20 ticks
            markout_ticks = max(-20.0, min(20.0, markout_ticks))

            st = self.stats.get(obs.aggressor)
            if st is None:
                st = CounterpartyStats()
                self.stats[obs.aggressor] = st
            st.trades += 1
            st.total_volume += obs.volume
            alpha = max(0.08, 1.0 / st.trades)
            st.ewma_markout_ticks = (1.0 - alpha) * st.ewma_markout_ticks + alpha * markout_ticks
            evaluated += 1
        return evaluated

    def get_impulse_and_spread_mult(self, now: float, inst_id: str) -> Tuple[float, float]:
        """Return (fair_value_impulse_price, spread_multiplier) for inst_id."""
        imp_val, imp_t = self._impulse.get(inst_id, (0.0, now))
        imp = self._decay(imp_val, now - imp_t)

        tox_val, tox_t = self._toxicity.get(inst_id, (0.0, now))
        tox = self._decay(tox_val, now - tox_t)

        nf_val, nf_t = self._noise_flow.get(inst_id, (0.0, now))
        nf = self._decay(nf_val, now - nf_t)

        # Widen spread up to 2.5x on toxic flow; tighten down to 0.85x on pure noise flow
        spread_mult = 1.0 + 0.75 * tox - 0.15 * nf
        spread_mult = max(0.85, min(2.5, spread_mult))
        return imp, spread_mult


# ---------------------------------------------------------------------------
# 4. Return-Space Cross-Stock Lead-Lag & 8x8 EWMA Covariance (GLFT)
# ---------------------------------------------------------------------------


# Empirically validated Optibook leader -> follower map (from wand.py / folge.py live analysis)
KNOWN_LEADERS: Dict[str, str] = {
    "SNDK": "NVDA",
    "CRWV": "MU",
    "NBIS": "AVGO",
}


class ReturnLeadLagCovariance:
    """
    Tracks log-return EWMA covariance Sigma_r across the universe, rolling 2s mid-price
    changes (for empirical leader-follower filters), and multi-asset GLFT skew:
        skew = -gamma * Sigma_dP_eff @ q
    with a 0.5-tick volatility floor so GLFT skew never collapses to ~0 on quiet ticks.
    """

    def __init__(
        self,
        universe: Sequence[str],
        ret_alpha: float = 0.35,
        cov_alpha: float = 0.05,
        shrinkage: float = 0.15,
        w_leadlag: float = 0.45,
        max_leadlag_ticks: float = 3.5,
        min_warmup: int = 5,
        win_sec: float = 2.0,
    ) -> None:
        self.universe: List[str] = list(universe)
        self._idx: Dict[str, int] = {u: i for i, u in enumerate(self.universe)}
        self.n = len(self.universe)
        self.ret_alpha = ret_alpha
        self.cov_alpha = cov_alpha
        self.shrinkage = shrinkage
        self.w_leadlag = w_leadlag
        self.max_leadlag_ticks = max_leadlag_ticks
        self.min_warmup = min_warmup
        self.win_sec = max(0.2, float(win_sec))

        self._prev_mids: Dict[str, float] = {}
        self._mid_win: Dict[str, Deque[Tuple[float, float]]] = {}
        self._ewma_ret = np.zeros(self.n, dtype=float)
        # Positive-definite prior covariance (5 bps per tick std, 0.30 AI sector corr)
        prior_var = (5e-4) ** 2
        self.cov_r = np.full((self.n, self.n), 0.30 * prior_var, dtype=float)
        np.fill_diagonal(self.cov_r, prior_var)
        self.updates = 0

    def update(self, mids: Dict[str, float], now: float = 0.0) -> None:
        """Update return state, 2s rolling mid windows, and 8x8 EWMA return covariance."""
        if self.n == 0:
            return

        raw_r = np.zeros(self.n, dtype=float)
        cov_r_vec = np.zeros(self.n, dtype=float)
        has_any = False

        for inst_id, mid in mids.items():
            idx = self._idx.get(inst_id)
            if idx is None or mid is None or mid <= 0.0:
                continue
            dq = self._mid_win.setdefault(inst_id, deque())
            dq.append((now, float(mid)))
            while len(dq) > 1 and now - dq[0][0] > self.win_sec:
                dq.popleft()

            prev = self._prev_mids.get(inst_id)
            self._prev_mids[inst_id] = mid
            if prev is not None and prev > 0.0:
                r = math.log(mid / prev)
                # Winsorize extreme jumps so Sigma stays well-conditioned
                raw_r[idx] = max(-0.03, min(0.03, r))
                cov_r_vec[idx] = max(-0.02, min(0.02, r))
                has_any = True

        if not has_any:
            return

        self.updates += 1
        a_r = self.ret_alpha
        self._ewma_ret = (1.0 - a_r) * self._ewma_ret + a_r * raw_r

        a_c = self.cov_alpha
        self.cov_r = (1.0 - a_c) * self.cov_r + a_c * np.outer(cov_r_vec, cov_r_vec)
        # Enforce symmetry and strictly positive diagonal
        self.cov_r = 0.5 * (self.cov_r + self.cov_r.T)
        diag = np.maximum(np.diag(self.cov_r), 1e-10)
        np.fill_diagonal(self.cov_r, diag)

    def move_over_window(self, inst_id: str) -> float:
        """Return mid-price change over the rolling 2s window."""
        dq = self._mid_win.get(inst_id)
        if not dq or len(dq) < 2:
            return 0.0
        return float(dq[-1][1] - dq[0][1])

    def leader_move_over_window(self, inst_id: str) -> float:
        """Return 2s price move of empirical leader stock (if inst_id is a known follower)."""
        leader = KNOWN_LEADERS.get(inst_id)
        if not leader:
            return 0.0
        return self.move_over_window(leader)

    def _shrunk_cov_r(self) -> np.ndarray:
        diag_mat = np.diag(np.diag(self.cov_r))
        return (1.0 - self.shrinkage) * self.cov_r + self.shrinkage * diag_mat

    def lead_lag_impulses(self, mids: Dict[str, float], ticks: Dict[str, float]) -> Dict[str, float]:
        """Compute return-space peer/leader catch-up impulse in price units for each stock."""
        if self.n <= 1 or self.updates < self.min_warmup:
            return {u: 0.0 for u in self.universe}

        sigma = self._shrunk_cov_r()
        std = np.sqrt(np.maximum(np.diag(sigma), 1e-10))
        denom = np.outer(std, std)
        corr = np.divide(sigma, denom, out=np.zeros_like(sigma), where=denom > 0)
        weights = np.clip(corr, 0.0, 0.95)
        np.fill_diagonal(weights, 0.0)

        out: Dict[str, float] = {}
        for i, u in enumerate(self.universe):
            mid = mids.get(u)
            if mid is None or mid <= 0.0:
                out[u] = 0.0
                continue
            eff_tick = ticks.get(u, 0.01) or 0.01

            # Direct leader boost if u has an empirically confirmed leader on Optibook
            leader = KNOWN_LEADERS.get(u)
            leader_imp = 0.0
            if leader and leader in mids and mids[leader] > 0:
                l_idx = self._idx.get(leader)
                if l_idx is not None:
                    l_ret = self._ewma_ret[l_idx] - self._ewma_ret[i]
                    leader_imp = 0.5 * self.w_leadlag * mid * l_ret

            w_row = weights[i]
            w_sum = float(np.sum(w_row))
            peer_imp = 0.0
            if w_sum > 1e-9:
                peer_ret = float(np.dot(w_row, self._ewma_ret) / w_sum)
                peer_std = float(np.dot(w_row, std) / w_sum)
                beta_i = min(2.0, std[i] / max(peer_std, 1e-9))
                lag_ret = peer_ret - self._ewma_ret[i]
                peer_imp = self.w_leadlag * mid * beta_i * lag_ret

            raw_imp = leader_imp + (0.5 * peer_imp if leader else peer_imp)
            cap = self.max_leadlag_ticks * eff_tick
            out[u] = max(-cap, min(cap, float(raw_imp)))
        return out

    def glft_skew(
        self,
        mids: Dict[str, float],
        positions: Dict[str, int],
        gamma: float,
        ticks: Dict[str, float],
        max_skew_ticks: float = 8.0,
    ) -> Dict[str, float]:
        """
        Compute multi-asset GLFT reservation-price skew: -gamma * (Sigma_dP_eff @ q).
        Floors per-stock price std at 0.5 * tick so skew never vanishes during quiet book ticks.
        """
        if self.n == 0:
            return {}

        p_vec = np.array([float(mids.get(u, 0.0) or 0.0) for u in self.universe], dtype=float)
        q_vec = np.array([float(positions.get(u, 0)) for u in self.universe], dtype=float)
        t_vec = np.array([float(ticks.get(u, 0.10) or 0.10) for u in self.universe], dtype=float)

        sigma_r = self._shrunk_cov_r()
        std_r = np.sqrt(np.maximum(np.diag(sigma_r), 1e-10))
        denom = np.outer(std_r, std_r)
        corr = np.divide(sigma_r, denom, out=np.eye(self.n), where=denom > 0)

        # Floor effective dollar std at 0.5 * tick so inventory skew is always meaningful in ticks
        std_dp = np.maximum(p_vec * std_r, 0.5 * t_vec)
        sigma_dp = np.outer(std_dp, std_dp) * corr
        raw_skew = -float(gamma) * (sigma_dp @ q_vec)

        out: Dict[str, float] = {}
        for i, u in enumerate(self.universe):
            eff_tick = float(t_vec[i])
            cap = max_skew_ticks * eff_tick
            val = float(raw_skew[i])
            if not math.isfinite(val):
                val = 0.0
            out[u] = max(-cap, min(cap, val))
        return out

    def tick_volatility_price(self, inst_id: str, mid: float) -> float:
        """Return 1-tick price standard deviation in dollars."""
        idx = self._idx.get(inst_id)
        if idx is None or mid <= 0.0:
            return 0.0
        return float(mid * math.sqrt(max(self.cov_r[idx, idx], 1e-10)))


# ---------------------------------------------------------------------------
# 5. <1ms News Lexicon Sentiment Sniper & Jump Guard
# ---------------------------------------------------------------------------


_COMPANY_ALIASES: Dict[str, str] = {
    "NVIDIA": "NVDA",
    "BROADCOM": "AVGO",
    "MICRON": "MU",
    "COREWEAVE": "CRWV",
    "NEBIUS": "NBIS",
    "SANDISK": "SNDK",
    "VERTIV": "VRT",
}

_POSITIVE_RE = re.compile(
    r"\b("
    r"beat|beats|beating|surge|surges|surging|soar|soars|soaring|rally|rallies|"
    r"upgrade|upgraded|upgrades|outperform|bullish|breakthrough|record revenue|"
    r"raises guidance|raised guidance|strong demand|exceeds|exceeded|partnership|"
    r"contract win|wins contract|massive order|ai boom|accelerates|profit jumps"
    r")\b",
    re.IGNORECASE,
)

_NEGATIVE_RE = re.compile(
    r"\b("
    r"miss|misses|missed|plunge|plunges|plunging|slump|slumps|crash|crashes|"
    r"downgrade|downgraded|downgrades|underperform|bearish|cuts guidance|cut guidance|"
    r"lowers guidance|weak demand|recall|delay|delayed|delays|lawsuit|investigation|"
    r"probe|subpoena|warning|warns|outage|hack|breach|shortfall|drops|falls|tumbles|"
    r"ban|export restriction|tariff|sanction"
    r")\b",
    re.IGNORECASE,
)

_NEGATION_RE = re.compile(r"\b(not|no|never|denies|denied|refutes|false|rumor|rumour)\b", re.IGNORECASE)


class NewsSentimentEngine:
    """Sub-millisecond rule-based ticker & sentiment parser for Optibook social feeds."""

    def __init__(self, universe: Iterable[str]) -> None:
        self.universe: List[str] = list(universe)
        self._universe_set: Set[str] = set(self.universe)
        self._ticker_patterns: Dict[str, re.Pattern[str]] = {
            t: re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in self.universe
        }
        self._alias_patterns: List[Tuple[re.Pattern[str], str]] = [
            (re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), ticker)
            for name, ticker in _COMPANY_ALIASES.items()
            if ticker in self._universe_set
        ]

    def parse(self, post: str) -> Tuple[List[str], int]:
        """
        Return (mentioned_tickers_or_all, sentiment) where sentiment is +1, -1, or 0.
        Always returns a non-empty ticker list so unknown/sector-wide news still pauses all quotes.
        """
        text = str(post or "").strip()
        if not text:
            return [], 0

        targets: List[str] = []
        seen: Set[str] = set()
        for ticker, pat in self._ticker_patterns.items():
            if pat.search(text) and ticker not in seen:
                targets.append(ticker)
                seen.add(ticker)
        for pat, ticker in self._alias_patterns:
            if pat.search(text) and ticker not in seen:
                targets.append(ticker)
                seen.add(ticker)

        if not targets:
            targets = list(self.universe)

        pos_hits = len(_POSITIVE_RE.findall(text))
        neg_hits = len(_NEGATIVE_RE.findall(text))

        # Only emit a directional snipe if sentiment is completely unambiguous and un-negated
        if _NEGATION_RE.search(text):
            return targets, 0
        if pos_hits > 0 and neg_hits == 0:
            return targets, 1
        if neg_hits > 0 and pos_hits == 0:
            return targets, -1
        return targets, 0


# ---------------------------------------------------------------------------
# 6. Endgame Inventory Glidepath (min 27-30)
# ---------------------------------------------------------------------------


@dataclass
class EndgameState:
    gamma_mult: float = 1.0
    inv_inc_scale: float = 1.0
    should_flatten: bool = False
    remaining_sec: Optional[float] = None


def compute_endgame_state(
    elapsed_sec: float,
    session_duration_sec: float,
    endgame_start_sec: float = 180.0,
    flatten_start_sec: float = 45.0,
    endgame_gamma_mult: float = 5.0,
) -> EndgameState:
    """
    Compute smooth inventory risk ramp over the final 3 minutes of a 30-min session.
    Set session_duration_sec <= 0 to disable timer (e.g. endless practice mode).
    """
    if session_duration_sec <= 0.0:
        return EndgameState(gamma_mult=1.0, inv_inc_scale=1.0, should_flatten=False, remaining_sec=None)

    remaining = max(0.0, session_duration_sec - max(0.0, elapsed_sec))
    if remaining > endgame_start_sec:
        return EndgameState(gamma_mult=1.0, inv_inc_scale=1.0, should_flatten=False, remaining_sec=remaining)

    if remaining <= flatten_start_sec:
        return EndgameState(
            gamma_mult=max(1.0, endgame_gamma_mult),
            inv_inc_scale=0.0,
            should_flatten=True,
            remaining_sec=remaining,
        )

    span = max(1.0, endgame_start_sec - flatten_start_sec)
    u = (endgame_start_sec - remaining) / span  # 0 at T-180s -> 1 at T-45s
    gamma_mult = 1.0 + max(0.0, endgame_gamma_mult - 1.0) * (u * u)
    inv_inc_scale = max(0.0, 1.0 - u)
    return EndgameState(
        gamma_mult=gamma_mult,
        inv_inc_scale=inv_inc_scale,
        should_flatten=False,
        remaining_sec=remaining,
    )


# ---------------------------------------------------------------------------
# 7. Unified ABMM-S Alpha & Quote-Plan Facade
# ---------------------------------------------------------------------------


@dataclass
class QuotePlan:
    fair_value: float
    reservation_price: float
    half_spread: float
    bid_lots: int
    ask_lots: int
    spread_mult: float = 1.0
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None


@dataclass
class _PositionTrack:
    last_pos: int = 0
    since: float = 0.0
    entry_price: float = 0.0
    wall_price: Optional[float] = None


@dataclass
class _OwnPendingFill:
    eval_time: float
    inst_id: str
    sign: int
    price: float
    tick: float


@dataclass
class ABMMSSnapshot:
    fair_values: Dict[str, float] = field(default_factory=dict)
    quote_plans: Dict[str, QuotePlan] = field(default_factory=dict)
    paused: Set[str] = field(default_factory=set)
    news_snipes: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    scratches: Dict[str, str] = field(default_factory=dict)
    endgame: EndgameState = field(default_factory=EndgameState)


class ABMMSAlphaEngine:
    """
    Unified ABMM-S engine producing fair values, wall-protected entry quotes,
    immediate profit-taking exit quotes, wall-break/hold-timeout IOC scratches,
    own-fill 5s markout circuit breakers, GLFT cross-hedging, and endgame glidepath.
    """

    def __init__(
        self,
        universe: Sequence[str],
        *,
        w_micro: float = 0.35,
        w_ofi: float = 0.15,
        w_wall: float = 0.30,
        wall_min_volume: int = 400,
        wall_max_dist_ticks: int = 3,
        require_wall_for_entry: bool = False,
        max_hold_sec: float = 15.0,
        stop_loss_ticks: float = 4.0,
        w_leadlag: float = 0.30,
        glft_gamma: float = 10.0,
        base_edge_ticks: float = 1.0,
        vol_spread_mult: float = 1.0,
        quote_lots: int = 5,
        max_position_lots: int = 40,
        news_cooldown_ticks: int = 20,
        enable_news_sniper: bool = False,
        news_snipe_lots: int = 5,
        session_duration_sec: float = 0.0,
        endgame_start_sec: float = 180.0,
        flatten_start_sec: float = 45.0,
        endgame_gamma_mult: float = 5.0,
        own_username: Optional[str] = None,
    ) -> None:
        self.universe: List[str] = list(universe)
        self.w_wall = w_wall
        self.wall_min_volume = wall_min_volume
        self.wall_max_dist_ticks = wall_max_dist_ticks
        self.require_wall_for_entry = require_wall_for_entry
        self.max_hold_sec = max_hold_sec
        self.stop_loss_ticks = stop_loss_ticks
        self.glft_gamma = glft_gamma
        self.base_edge_ticks = base_edge_ticks
        self.vol_spread_mult = vol_spread_mult
        self.quote_lots = quote_lots
        self.max_position_lots = max_position_lots
        self.news_cooldown_ticks = news_cooldown_ticks
        self.enable_news_sniper = enable_news_sniper
        self.news_snipe_lots = news_snipe_lots
        self.session_duration_sec = session_duration_sec
        self.endgame_start_sec = endgame_start_sec
        self.flatten_start_sec = flatten_start_sec
        self.endgame_gamma_mult = endgame_gamma_mult

        self.micro_ofi = MicropriceOFI(w_micro=w_micro, w_ofi=w_ofi)
        self.counterparties = CounterpartyTracker(own_username=own_username)
        self.lead_lag = ReturnLeadLagCovariance(self.universe, w_leadlag=w_leadlag)
        self.news_engine = NewsSentimentEngine(self.universe)

        self.news_cooldown_until: Dict[str, int] = {}
        self._ewma_spread: Dict[str, float] = {}
        self._start_time: Optional[float] = None
        self._pos_track: Dict[str, _PositionTrack] = {u: _PositionTrack() for u in self.universe}
        self._own_pending: Deque[_OwnPendingFill] = deque(maxlen=2000)
        self.own_markout_stats: Dict[str, Tuple[int, float]] = {}  # inst -> (count, ewma_ticks)
        self.entry_breaker_until: Dict[str, float] = {}

    def _update_own_fills_and_positions(
        self,
        now: float,
        positions: Dict[str, int],
        books: Dict[str, Any],
        mids: Dict[str, float],
        ticks: Dict[str, float],
        own_trades: Optional[Dict[str, List[Any]]],
    ) -> Dict[str, str]:
        """Track position entries/walls, evaluate own 5s fill markouts, and flag mandatory IOC scratches."""
        for inst_id, trades in (own_trades or {}).items():
            tick = ticks.get(inst_id, 0.10) or 0.10
            last_px = None
            for tr in trades:
                px = float(getattr(tr, "price", 0.0) or 0.0)
                side = str(getattr(tr, "side", "")).lower()
                if px > 0 and side in ("bid", "ask"):
                    last_px = px
                    self._own_pending.append(
                        _OwnPendingFill(
                            eval_time=now + 5.0,
                            inst_id=inst_id,
                            sign=1 if side == "bid" else -1,
                            price=px,
                            tick=tick,
                        )
                    )
            if last_px is not None and inst_id in self._pos_track:
                st = self._pos_track[inst_id]
                cur = int(positions.get(inst_id, 0))
                if cur != 0 and (st.last_pos == 0 or (st.last_pos > 0) != (cur > 0)):
                    st.entry_price = last_px

        # Evaluate matured 5s own-fill markouts -> trip 45s entry breaker if losing > 0.6 ticks/fill
        while self._own_pending and self._own_pending[0].eval_time <= now:
            f = self._own_pending.popleft()
            mid = mids.get(f.inst_id)
            if mid is None:
                continue
            mk_ticks = max(-20.0, min(20.0, f.sign * (mid - f.price) / f.tick))
            n_prev, ewma_prev = self.own_markout_stats.get(f.inst_id, (0, 0.0))
            n_new = n_prev + 1
            ewma_new = 0.75 * ewma_prev + 0.25 * mk_ticks if n_prev > 0 else mk_ticks
            self.own_markout_stats[f.inst_id] = (n_new, ewma_new)
            if n_new >= 5 and ewma_new < -0.60 and now >= self.entry_breaker_until.get(f.inst_id, 0.0):
                self.entry_breaker_until[f.inst_id] = now + 45.0

        scratches: Dict[str, str] = {}
        for inst_id in self.universe:
            st = self._pos_track.setdefault(inst_id, _PositionTrack())
            pos = int(positions.get(inst_id, 0))
            mid = mids.get(inst_id)
            tick = ticks.get(inst_id, 0.10) or 0.10
            book = books.get(inst_id)

            opened = pos != 0 and (st.last_pos == 0 or (st.last_pos > 0) != (pos > 0))
            if opened:
                st.since = now
                if st.entry_price <= 0.0 or (st.last_pos != 0 and (st.last_pos > 0) != (pos > 0)):
                    st.entry_price = mid if mid is not None else 0.0
                st.wall_price = (
                    find_protecting_wall(
                        book,
                        "bid" if pos > 0 else "ask",
                        tick,
                        wall_min_volume=self.wall_min_volume,
                        max_dist_ticks=self.wall_max_dist_ticks,
                    )
                    if book is not None
                    else None
                )
            elif pos == 0:
                st.entry_price = 0.0
                st.wall_price = None
            elif st.wall_price is None and book is not None:
                st.wall_price = find_protecting_wall(
                    book,
                    "bid" if pos > 0 else "ask",
                    tick,
                    wall_min_volume=self.wall_min_volume,
                    max_dist_ticks=self.wall_max_dist_ticks,
                )
            st.last_pos = pos

            if pos == 0 or book is None:
                continue
            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []
            if not bids or not asks:
                continue
            best_bid, best_ask = float(bids[0].price), float(asks[0].price)

            if self.max_hold_sec > 0 and now - st.since > self.max_hold_sec:
                scratches[inst_id] = "hold_timeout"
                continue

            if st.entry_price > 0 and self.stop_loss_ticks > 0:
                adverse = (st.entry_price - best_bid) if pos > 0 else (best_ask - st.entry_price)
                if adverse >= self.stop_loss_ticks * tick - 1e-9:
                    scratches[inst_id] = "stop_loss"
                    continue

            if st.wall_price is not None:
                if pos > 0:
                    if best_bid < st.wall_price - 1e-9:
                        scratches[inst_id] = "wall_broken"
                    elif (
                        volume_at_price(bids, st.wall_price, tick) < self.wall_min_volume * 0.5
                        and best_bid <= (st.entry_price if st.entry_price > 0 else best_bid) + 1e-9
                    ):
                        scratches[inst_id] = "wall_pulled"
                else:
                    if best_ask > st.wall_price + 1e-9:
                        scratches[inst_id] = "wall_broken"
                    elif (
                        volume_at_price(asks, st.wall_price, tick) < self.wall_min_volume * 0.5
                        and best_ask >= (st.entry_price if st.entry_price > 0 else best_ask) - 1e-9
                    ):
                        scratches[inst_id] = "wall_pulled"
            elif self.require_wall_for_entry:
                # In strict wall-backed mode, if we got filled and no wall exists behind us, scratch immediately
                scratches[inst_id] = "no_wall_shield"

        return scratches

    def update(
        self,
        *,
        now: float,
        tick_count: int,
        books: Dict[str, Any],
        instruments: Dict[str, Any],
        positions: Dict[str, int],
        trade_ticks: Dict[str, List[Any]],
        social_feeds: List[Any],
        own_trades: Optional[Dict[str, List[Any]]] = None,
    ) -> ABMMSSnapshot:
        if self._start_time is None:
            self._start_time = now

        elapsed = max(0.0, now - self._start_time)
        endgame = compute_endgame_state(
            elapsed_sec=elapsed,
            session_duration_sec=self.session_duration_sec,
            endgame_start_sec=self.endgame_start_sec,
            flatten_start_sec=self.flatten_start_sec,
            endgame_gamma_mult=self.endgame_gamma_mult,
        )

        # 1. Process social feeds (<1ms) -> pause quotes & optionally emit news snipes
        news_snipes: Dict[str, Tuple[str, int]] = {}
        for feed in social_feeds:
            post = str(getattr(feed, "post", "") or "")
            targets, sentiment = self.news_engine.parse(post)
            for inst in targets:
                self.news_cooldown_until[inst] = tick_count + self.news_cooldown_ticks
                if self.enable_news_sniper and sentiment != 0 and not endgame.should_flatten:
                    side = "bid" if sentiment > 0 else "ask"
                    news_snipes[inst] = (side, self.news_snipe_lots)

        paused = {inst for inst, until in self.news_cooldown_until.items() if tick_count < until}

        # 2. Extract mids, volume-capped Microprice+OFI, wall anchors, and live spread EWMA
        mids: Dict[str, float] = {}
        ticks: Dict[str, float] = {}
        micro_imp: Dict[str, float] = {}
        wall_imp: Dict[str, float] = {}
        bid_walls: Dict[str, Optional[float]] = {}
        ask_walls: Dict[str, Optional[float]] = {}

        for inst_id in self.universe:
            inst = instruments.get(inst_id)
            book = books.get(inst_id)
            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
            ticks[inst_id] = tick
            if book is None:
                continue

            mid, m_imp = self.micro_ofi.update(inst_id, book, tick, now=now)
            if mid is None:
                continue
            mids[inst_id] = mid
            micro_imp[inst_id] = m_imp
            wall_imp[inst_id] = self.w_wall * extract_wall_anchor(
                book, mid, tick, wall_min_volume=self.wall_min_volume
            )
            bid_walls[inst_id] = find_protecting_wall(
                book, "bid", tick, wall_min_volume=self.wall_min_volume, max_dist_ticks=self.wall_max_dist_ticks
            )
            ask_walls[inst_id] = find_protecting_wall(
                book, "ask", tick, wall_min_volume=self.wall_min_volume, max_dist_ticks=self.wall_max_dist_ticks
            )

            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []
            if bids and asks:
                sp = max(tick, float(asks[0].price) - float(bids[0].price))
                prev_sp = self._ewma_spread.get(inst_id, sp)
                self._ewma_spread[inst_id] = 0.90 * prev_sp + 0.10 * sp

        # 3. Feed live TradeTicks into counterparty markout profiler
        for inst_id, t_list in trade_ticks.items():
            tick = ticks.get(inst_id, 0.10)
            for t in t_list:
                self.counterparties.on_trade(
                    now=now,
                    inst_id=inst_id,
                    price=float(getattr(t, "price", 0.0)),
                    volume=int(getattr(t, "volume", 0)),
                    aggressor_side=str(getattr(t, "aggressor_side", "")),
                    buyer=getattr(t, "buyer", None),
                    seller=getattr(t, "seller", None),
                    tick=tick,
                )
        self.counterparties.update_markouts(now, mids)

        # 4. Update return-space covariance & 2s leader/trend windows
        self.lead_lag.update(mids, now=now)
        ll_imp = self.lead_lag.lead_lag_impulses(mids, ticks)

        # 5. Track positions, own-fill markouts, and mandatory wall-break / timeout scratches
        scratches = self._update_own_fills_and_positions(now, positions, books, mids, ticks, own_trades)

        # 6. Compute multi-asset GLFT skew r = F_hat - gamma(t) * Sigma_dP_eff * q
        eff_gamma = self.glft_gamma * endgame.gamma_mult
        max_skew_ticks = 3.0 if endgame.gamma_mult <= 1.5 else 10.0
        glft_skews = self.lead_lag.glft_skew(
            mids, positions, gamma=eff_gamma, ticks=ticks, max_skew_ticks=max_skew_ticks
        )

        # 7. Assemble Fair Values & Wall-Protected / Profit-Exiting Quote Plans
        fair_values: Dict[str, float] = {}
        quote_plans: Dict[str, QuotePlan] = {}

        for inst_id, mid in mids.items():
            if inst_id in scratches:
                continue
            tick = ticks[inst_id]
            book = books[inst_id]
            best_bid = float(book.bids[0].price)
            best_ask = float(book.asks[0].price)
            cp_imp, cp_spread_mult = self.counterparties.get_impulse_and_spread_mult(now, inst_id)

            total_alpha = micro_imp.get(inst_id, 0.0) + wall_imp.get(inst_id, 0.0) + cp_imp + ll_imp.get(inst_id, 0.0)
            max_fv_shift = 1.5 * tick
            total_alpha = max(-max_fv_shift, min(max_fv_shift, total_alpha))
            fv = mid + total_alpha
            fair_values[inst_id] = fv

            skew_px = glft_skews.get(inst_id, 0.0)
            res_price = fv + skew_px

            vol_px = self.lead_lag.tick_volatility_price(inst_id, mid)
            regime_sp = self._ewma_spread.get(inst_id, 2.0 * tick)
            base_half = max(self.base_edge_ticks * tick, 0.45 * regime_sp)
            half_spread = (base_half + self.vol_spread_mult * vol_px) * cp_spread_mult
            half_spread = max(tick, min(half_spread, 0.65 * regime_sp + 4.0 * tick))

            # Anti-adverse-selection entry filters (wand.py 4-signal check + counterparty toxicity + own markout breaker)
            breaker_active = now < self.entry_breaker_until.get(inst_id, 0.0)
            m_edge = self.micro_ofi.last_micro_edge.get(inst_id, 0.0)
            ofi_2s = self.micro_ofi.last_ofi_win.get(inst_id, 0.0)
            lead_2s = self.lead_lag.leader_move_over_window(inst_id)
            trend_2s = self.lead_lag.move_over_window(inst_id)

            bw = bid_walls.get(inst_id)
            aw = ask_walls.get(inst_id)

            filter_buy_ok = (
                not breaker_active
                and m_edge >= -0.02 * tick
                and ofi_2s >= 0.0
                and lead_2s > -0.5 * tick
                and trend_2s >= -0.5 * tick
                and cp_imp >= -0.5 * tick
            )
            filter_sell_ok = (
                not breaker_active
                and m_edge <= 0.02 * tick
                and ofi_2s <= 0.0
                and lead_2s < 0.5 * tick
                and trend_2s <= 0.5 * tick
                and cp_imp <= 0.5 * tick
            )

            allow_bid_entry = filter_buy_ok and (bw is not None if self.require_wall_for_entry else True)
            allow_ask_entry = filter_sell_ok and (aw is not None if self.require_wall_for_entry else True)

            # Base entry prices: 1 tick in front of protecting wall (shifted conservatively by cross-asset GLFT skew)
            if bw is not None:
                bid_px = bw + tick + min(0.0, skew_px)
            else:
                bid_px = res_price - half_spread

            if aw is not None:
                ask_px = aw - tick + max(0.0, skew_px)
            else:
                ask_px = res_price + half_spread

            cur_pos = int(positions.get(inst_id, 0))
            st = self._pos_track[inst_id]
            entry_px = st.entry_price if st.entry_price > 0 else mid
            held_sec = max(0.0, now - st.since) if cur_pos != 0 else 0.0
            # Lock in at least +1 tick profit for first 8s (never skew exit down to break-even immediately!),
            # then step down to entry_px scratch between 8s-15s before IOC timeout
            min_profit_ticks = 0.0 if held_sec >= 8.0 else 1.0

            if cur_pos > 0:
                min_exit_ask = max(entry_px + min_profit_ticks * tick, best_bid + tick)
                exit_ask = max(min_exit_ask, entry_px + tick + min(0.0, skew_px))
                ask_px = min(ask_px, exit_ask) if allow_ask_entry else exit_ask
                ask_lots = min(self.max_position_lots + cur_pos, cur_pos)
                bid_lots = (
                    int(round(self.quote_lots * endgame.inv_inc_scale))
                    if (allow_bid_entry and cur_pos < self.quote_lots)
                    else 0
                )
            elif cur_pos < 0:
                max_exit_bid = min(entry_px - min_profit_ticks * tick, best_ask - tick)
                exit_bid = min(max_exit_bid, entry_px - tick + max(0.0, skew_px))
                bid_px = max(bid_px, exit_bid) if allow_bid_entry else exit_bid
                bid_lots = min(self.max_position_lots - cur_pos, -cur_pos)
                ask_lots = (
                    int(round(self.quote_lots * endgame.inv_inc_scale))
                    if (allow_ask_entry and -cur_pos < self.quote_lots)
                    else 0
                )
            else:
                bid_lots = int(round(self.quote_lots * endgame.inv_inc_scale)) if allow_bid_entry else 0
                ask_lots = int(round(self.quote_lots * endgame.inv_inc_scale)) if allow_ask_entry else 0

            quote_plans[inst_id] = QuotePlan(
                fair_value=fv,
                reservation_price=res_price,
                half_spread=half_spread,
                bid_lots=max(0, bid_lots),
                ask_lots=max(0, ask_lots),
                spread_mult=cp_spread_mult,
                bid_price=bid_px,
                ask_price=ask_px,
            )

        return ABMMSSnapshot(
            fair_values=fair_values,
            quote_plans=quote_plans,
            paused=paused,
            news_snipes=news_snipes,
            scratches=scratches,
            endgame=endgame,
        )
