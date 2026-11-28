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
    """Computes Stoikov microprice edge and EWMA-normalized top-of-book OFI."""

    def __init__(
        self,
        w_micro: float = 0.65,
        w_ofi: float = 0.35,
        ewma_alpha: float = 0.25,
        max_shift_ticks: float = 2.5,
    ) -> None:
        self.w_micro = w_micro
        self.w_ofi = w_ofi
        self.ewma_alpha = ewma_alpha
        self.max_shift_ticks = max_shift_ticks
        self._prev_bbo: Dict[str, Tuple[float, int, float, int]] = {}
        self._ewma_ofi: Dict[str, float] = {}
        self._ewma_ofi_sq: Dict[str, float] = {}

    def update(self, inst_id: str, book: Any, tick: float) -> Tuple[Optional[float], float]:
        """Return (mid, impulse_in_price_units). Returns (None, 0.0) if book is one-sided."""
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []
        if not bids or not asks:
            return None, 0.0

        bp, bv = float(bids[0].price), int(bids[0].volume)
        ap, av = float(asks[0].price), int(asks[0].volume)
        if ap <= bp or bp <= 0 or ap <= 0:
            return None, 0.0

        mid = 0.5 * (bp + ap)
        total_v = bv + av
        micro = (bp * av + ap * bv) / total_v if total_v > 0 else mid
        micro_edge = micro - mid

        prev = self._prev_bbo.get(inst_id)
        self._prev_bbo[inst_id] = (bp, bv, ap, av)

        ofi_z = 0.0
        if prev is not None:
            pbp, pbv, pap, pav = prev
            if bp > pbp:
                db = bv
            elif bp == pbp:
                db = bv - pbv
            else:
                db = -pbv

            if ap < pap:
                da = av
            elif ap == pap:
                da = av - pav
            else:
                da = -pav

            ofi = float(db - da)
            a = self.ewma_alpha
            ewma = (1.0 - a) * self._ewma_ofi.get(inst_id, 0.0) + a * ofi
            ewma_sq = (1.0 - a) * self._ewma_ofi_sq.get(inst_id, ofi * ofi) + a * (ofi * ofi)
            self._ewma_ofi[inst_id] = ewma
            self._ewma_ofi_sq[inst_id] = ewma_sq
            rms = math.sqrt(max(ewma_sq, 1e-9))
            ofi_z = max(-2.0, min(2.0, ewma / rms))

        eff_tick = tick if tick > 0 else 0.01
        raw_impulse = self.w_micro * micro_edge + self.w_ofi * ofi_z * eff_tick
        cap = self.max_shift_ticks * eff_tick
        return mid, max(-cap, min(cap, raw_impulse))


# ---------------------------------------------------------------------------
# 2. House-Bot Wall Anchor (V > 250 lots cannot be a student team)
# ---------------------------------------------------------------------------


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


class ReturnLeadLagCovariance:
    """
    Tracks log-return EWMA covariance Sigma_r across the universe, computes
    return-space peer lead-lag impulses, and produces multi-asset GLFT skew:
        skew = -gamma * Sigma_dP @ q
    where Sigma_dP = diag(P) @ Sigma_r @ diag(P).
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

        self._prev_mids: Dict[str, float] = {}
        self._ewma_ret = np.zeros(self.n, dtype=float)
        # Positive-definite prior covariance (5 bps per tick std, 0.30 AI sector corr)
        prior_var = (5e-4) ** 2
        self.cov_r = np.full((self.n, self.n), 0.30 * prior_var, dtype=float)
        np.fill_diagonal(self.cov_r, prior_var)
        self.updates = 0

    def update(self, mids: Dict[str, float]) -> None:
        """Update return state and 8x8 EWMA return covariance from latest mids."""
        if self.n == 0:
            return

        raw_r = np.zeros(self.n, dtype=float)
        cov_r_vec = np.zeros(self.n, dtype=float)
        has_any = False

        for inst_id, mid in mids.items():
            idx = self._idx.get(inst_id)
            if idx is None or mid is None or mid <= 0.0:
                continue
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

    def _shrunk_cov_r(self) -> np.ndarray:
        diag_mat = np.diag(np.diag(self.cov_r))
        return (1.0 - self.shrinkage) * self.cov_r + self.shrinkage * diag_mat

    def lead_lag_impulses(self, mids: Dict[str, float], ticks: Dict[str, float]) -> Dict[str, float]:
        """Compute return-space peer catch-up impulse in price units for each stock."""
        if self.n <= 1 or self.updates < self.min_warmup:
            return {u: 0.0 for u in self.universe}

        sigma = self._shrunk_cov_r()
        std = np.sqrt(np.maximum(np.diag(sigma), 1e-10))
        denom = np.outer(std, std)
        corr = np.divide(sigma, denom, out=np.zeros_like(sigma), where=denom > 0)
        # Only use positive AI-sector correlations for peer catch-up
        weights = np.clip(corr, 0.0, 0.95)
        np.fill_diagonal(weights, 0.0)

        out: Dict[str, float] = {}
        for i, u in enumerate(self.universe):
            mid = mids.get(u)
            if mid is None or mid <= 0.0:
                out[u] = 0.0
                continue
            w_row = weights[i]
            w_sum = float(np.sum(w_row))
            if w_sum <= 1e-9:
                out[u] = 0.0
                continue
            peer_ret = float(np.dot(w_row, self._ewma_ret) / w_sum)
            peer_std = float(np.dot(w_row, std) / w_sum)
            beta_i = min(2.0, std[i] / max(peer_std, 1e-9))
            lag_ret = peer_ret - self._ewma_ret[i]

            eff_tick = ticks.get(u, 0.01) or 0.01
            raw_imp = self.w_leadlag * mid * beta_i * lag_ret
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
        """Compute multi-asset GLFT reservation-price skew: -gamma * (Sigma_dP @ q)."""
        if self.n == 0:
            return {}

        p_vec = np.array([float(mids.get(u, 0.0) or 0.0) for u in self.universe], dtype=float)
        q_vec = np.array([float(positions.get(u, 0)) for u in self.universe], dtype=float)
        sigma_r = self._shrunk_cov_r()
        sigma_dp = (p_vec[:, None] * sigma_r) * p_vec[None, :]
        raw_skew = -float(gamma) * (sigma_dp @ q_vec)

        out: Dict[str, float] = {}
        for i, u in enumerate(self.universe):
            eff_tick = ticks.get(u, 0.01) or 0.01
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


@dataclass
class ABMMSSnapshot:
    fair_values: Dict[str, float] = field(default_factory=dict)
    quote_plans: Dict[str, QuotePlan] = field(default_factory=dict)
    paused: Set[str] = field(default_factory=set)
    news_snipes: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    endgame: EndgameState = field(default_factory=EndgameState)


class ABMMSAlphaEngine:
    """
    Unified ABMM-S engine producing fair values, GLFT reservation prices,
    regime-adapted half-spreads, news snipes, and endgame glidepath state.
    """

    def __init__(
        self,
        universe: Sequence[str],
        *,
        w_micro: float = 0.65,
        w_ofi: float = 0.35,
        w_wall: float = 0.40,
        wall_min_volume: int = 250,
        w_leadlag: float = 0.45,
        glft_gamma: float = 2.5,
        base_edge_ticks: float = 1.0,
        vol_spread_mult: float = 0.6,
        quote_lots: int = 5,
        max_position_lots: int = 100,
        news_cooldown_ticks: int = 20,
        enable_news_sniper: bool = True,
        news_snipe_lots: int = 10,
        session_duration_sec: float = 0.0,
        endgame_start_sec: float = 180.0,
        flatten_start_sec: float = 45.0,
        endgame_gamma_mult: float = 5.0,
        own_username: Optional[str] = None,
    ) -> None:
        self.universe: List[str] = list(universe)
        self.w_wall = w_wall
        self.wall_min_volume = wall_min_volume
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

        # 2. Extract mids, Microprice+OFI, wall anchors, and live spread EWMA
        mids: Dict[str, float] = {}
        ticks: Dict[str, float] = {}
        micro_imp: Dict[str, float] = {}
        wall_imp: Dict[str, float] = {}

        for inst_id in self.universe:
            inst = instruments.get(inst_id)
            book = books.get(inst_id)
            tick = float(getattr(inst, "tick_size", 0.01) or 0.01)
            ticks[inst_id] = tick
            if book is None:
                continue

            mid, m_imp = self.micro_ofi.update(inst_id, book, tick)
            if mid is None:
                continue
            mids[inst_id] = mid
            micro_imp[inst_id] = m_imp
            wall_imp[inst_id] = self.w_wall * extract_wall_anchor(
                book, mid, tick, wall_min_volume=self.wall_min_volume
            )

            # Live regime-decay spread tracker
            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []
            if bids and asks:
                sp = max(tick, float(asks[0].price) - float(bids[0].price))
                prev_sp = self._ewma_spread.get(inst_id, sp)
                self._ewma_spread[inst_id] = 0.90 * prev_sp + 0.10 * sp

        # 3. Feed live TradeTicks into counterparty markout profiler
        for inst_id, t_list in trade_ticks.items():
            tick = ticks.get(inst_id, 0.01)
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

        # 4. Update return-space covariance & lead-lag
        self.lead_lag.update(mids)
        ll_imp = self.lead_lag.lead_lag_impulses(mids, ticks)

        # 5. Compute multi-asset GLFT skew r = F_hat - gamma(t) * Sigma_dP * q
        eff_gamma = self.glft_gamma * endgame.gamma_mult
        max_skew_ticks = 8.0 if endgame.gamma_mult <= 1.5 else 15.0
        glft_skews = self.lead_lag.glft_skew(
            mids, positions, gamma=eff_gamma, ticks=ticks, max_skew_ticks=max_skew_ticks
        )

        # 6. Assemble Fair Values & Quote Plans
        fair_values: Dict[str, float] = {}
        quote_plans: Dict[str, QuotePlan] = {}

        for inst_id, mid in mids.items():
            tick = ticks[inst_id]
            cp_imp, cp_spread_mult = self.counterparties.get_impulse_and_spread_mult(now, inst_id)

            # Cap total fair-value displacement around live mid to +-5 ticks so quotes never drift off-market
            total_alpha = micro_imp.get(inst_id, 0.0) + wall_imp.get(inst_id, 0.0) + cp_imp + ll_imp.get(inst_id, 0.0)
            max_fv_shift = 5.0 * tick
            total_alpha = max(-max_fv_shift, min(max_fv_shift, total_alpha))
            fv = mid + total_alpha
            fair_values[inst_id] = fv

            res_price = fv + glft_skews.get(inst_id, 0.0)

            # Adaptive half-spread: base edge + volatility component, scaled by counterparty toxicity
            vol_px = self.lead_lag.tick_volatility_price(inst_id, mid)
            regime_sp = self._ewma_spread.get(inst_id, 2.0 * tick)
            # In wide markets we can earn more spread; in tight Sunday 10:00 markets we tighten to >= base_edge_ticks
            base_half = max(self.base_edge_ticks * tick, 0.30 * regime_sp)
            half_spread = (base_half + self.vol_spread_mult * vol_px) * cp_spread_mult
            # Never quote wider than 6 ticks + 0.5*regime_sp so we stay competitive
            half_spread = max(tick, min(half_spread, 0.5 * regime_sp + 4.0 * tick))

            # Inventory-aware + endgame-aware quote sizing
            cur_pos = int(positions.get(inst_id, 0))
            base_bid = max(1, self.quote_lots // 2) if cur_pos > 15 else self.quote_lots
            base_ask = max(1, self.quote_lots // 2) if cur_pos < -15 else self.quote_lots

            # During endgame glidepath, scale down inventory-increasing side toward 0
            if cur_pos > 0:
                bid_lots = int(round(base_bid * endgame.inv_inc_scale))
                ask_lots = base_ask
            elif cur_pos < 0:
                bid_lots = base_bid
                ask_lots = int(round(base_ask * endgame.inv_inc_scale))
            else:
                bid_lots = int(round(base_bid * endgame.inv_inc_scale))
                ask_lots = int(round(base_ask * endgame.inv_inc_scale))

            quote_plans[inst_id] = QuotePlan(
                fair_value=fv,
                reservation_price=res_price,
                half_spread=half_spread,
                bid_lots=max(0, bid_lots),
                ask_lots=max(0, ask_lots),
                spread_mult=cp_spread_mult,
            )

        return ABMMSSnapshot(
            fair_values=fair_values,
            quote_plans=quote_plans,
            paused=paused,
            news_snipes=news_snipes,
            endgame=endgame,
        )
