"""Configuration for the Optibook ABMM-S trading bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


DEFAULT_UNIVERSE = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class BotConfig:
    """Runtime configuration loaded from environment variables."""

    mock_mode: bool = field(default_factory=lambda: _env_bool("MOCK_MODE", False))
    exchange_host: Optional[str] = field(default_factory=lambda: os.getenv("OPTIBOOK_HOST") or None)
    info_port: Optional[int] = field(
        default_factory=lambda: int(os.environ["OPTIBOOK_INFO_PORT"]) if os.getenv("OPTIBOOK_INFO_PORT") else None
    )
    exec_port: Optional[int] = field(
        default_factory=lambda: int(os.environ["OPTIBOOK_EXEC_PORT"]) if os.getenv("OPTIBOOK_EXEC_PORT") else None
    )
    username: Optional[str] = field(default_factory=lambda: os.getenv("OPTIBOOK_USERNAME") or None)
    password: Optional[str] = field(default_factory=lambda: os.getenv("OPTIBOOK_PASSWORD") or None)

    universe: List[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    poll_interval_sec: float = field(default_factory=lambda: float(os.getenv("POLL_INTERVAL_SEC", "0.25")))
    max_position_lots: int = field(default_factory=lambda: int(os.getenv("MAX_POSITION_LOTS", "100")))

    # ABMM-S signal weights & GLFT parameters
    w_micro: float = field(default_factory=lambda: float(os.getenv("ABMMS_W_MICRO", "0.65")))
    w_ofi: float = field(default_factory=lambda: float(os.getenv("ABMMS_W_OFI", "0.35")))
    w_wall: float = field(default_factory=lambda: float(os.getenv("ABMMS_W_WALL", "0.40")))
    wall_min_volume: int = field(default_factory=lambda: int(os.getenv("ABMMS_WALL_MIN_VOL", "250")))
    w_leadlag: float = field(default_factory=lambda: float(os.getenv("ABMMS_W_LEADLAG", "0.45")))
    glft_gamma: float = field(default_factory=lambda: float(os.getenv("ABMMS_GLFT_GAMMA", "2.5")))
    vol_spread_mult: float = field(default_factory=lambda: float(os.getenv("ABMMS_VOL_SPREAD_MULT", "0.6")))

    # Endgame inventory glidepath (set SESSION_DURATION_SEC=1800 for the 30-min Sunday final)
    session_duration_sec: float = field(default_factory=lambda: float(os.getenv("SESSION_DURATION_SEC", "0")))
    endgame_start_sec: float = field(default_factory=lambda: float(os.getenv("ENDGAME_START_SEC", "180")))
    flatten_start_sec: float = field(default_factory=lambda: float(os.getenv("FLATTEN_START_SEC", "45")))
    endgame_gamma_mult: float = field(default_factory=lambda: float(os.getenv("ENDGAME_GAMMA_MULT", "5.0")))

    # Legacy Kalman & StatArb fields (kept for backward compatibility with tests/tools)
    kalman_delta: float = field(default_factory=lambda: float(os.getenv("KALMAN_DELTA", "1e-5")))
    kalman_r: float = field(default_factory=lambda: float(os.getenv("KALMAN_R", "1.0")))
    z_window: int = field(default_factory=lambda: int(os.getenv("Z_WINDOW", "60")))
    min_warmup: int = field(default_factory=lambda: int(os.getenv("MIN_WARMUP", "30")))
    entry_z1: float = field(default_factory=lambda: float(os.getenv("ENTRY_Z1", "2.0")))
    entry_z2: float = field(default_factory=lambda: float(os.getenv("ENTRY_Z2", "2.8")))
    exit_z: float = field(default_factory=lambda: float(os.getenv("EXIT_Z", "0.2")))
    stop_z: float = field(default_factory=lambda: float(os.getenv("STOP_Z", "3.8")))
    t1_weight: float = field(default_factory=lambda: float(os.getenv("T1_WEIGHT", "0.5")))
    min_hurdle_bps: float = field(default_factory=lambda: float(os.getenv("MIN_HURDLE_BPS", "25.0")))
    max_pairs: int = field(default_factory=lambda: int(os.getenv("MAX_PAIRS", "4")))
    max_lots_per_pair: int = field(default_factory=lambda: int(os.getenv("MAX_LOTS_PER_PAIR", "20")))
    rebalance_pairs_every_ticks: int = field(default_factory=lambda: int(os.getenv("REBALANCE_PAIRS_TICKS", "120")))
    use_historical_seed: bool = field(default_factory=lambda: _env_bool("USE_HISTORICAL_SEED", False))
    log_every_ticks: int = field(default_factory=lambda: int(os.getenv("LOG_EVERY_TICKS", "40")))
    enable_basket_model: bool = field(default_factory=lambda: _env_bool("ENABLE_BASKET_MODEL", True))
    basket_z_entry: float = field(default_factory=lambda: float(os.getenv("BASKET_Z_ENTRY", "1.6")))
    basket_max_lots: int = field(default_factory=lambda: int(os.getenv("BASKET_MAX_LOTS", "15")))

    # Passive market-making & edge sniping
    enable_passive_quoting: bool = field(default_factory=lambda: _env_bool("ENABLE_PASSIVE_QUOTING", True))
    quote_lots: int = field(default_factory=lambda: int(os.getenv("QUOTE_LOTS", "5")))
    quote_edge_ticks: int = field(default_factory=lambda: int(os.getenv("QUOTE_EDGE_TICKS", "1")))
    max_open_order_volume: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_ORDER_VOLUME", "180")))
    edge_ioc_lots: int = field(default_factory=lambda: int(os.getenv("EDGE_IOC_LOTS", "10")))
    edge_ioc_min_ticks: int = field(default_factory=lambda: int(os.getenv("EDGE_IOC_MIN_TICKS", "3")))

    # News-feed jump guard, sniper & Optibook rate limit (<25 req/s)
    news_cooldown_ticks: int = field(default_factory=lambda: int(os.getenv("NEWS_COOLDOWN_TICKS", "20")))
    enable_news_sniper: bool = field(default_factory=lambda: _env_bool("ENABLE_NEWS_SNIPER", True))
    news_snipe_lots: int = field(default_factory=lambda: int(os.getenv("NEWS_SNIPE_LOTS", "10")))
    max_requests_per_sec: int = field(default_factory=lambda: int(os.getenv("MAX_REQUESTS_PER_SEC", "20")))

    # Embedded CSV logger (safe under Optibook's 1-connection-per-team rule)
    enable_csv_logging: bool = field(default_factory=lambda: _env_bool("ENABLE_CSV_LOGGING", False))
    csv_log_dir: Optional[str] = field(default_factory=lambda: os.getenv("CSV_LOG_DIR") or None)
