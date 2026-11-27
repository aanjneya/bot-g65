"""Configuration for the Optibook Kalman StatArb bot."""

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
    poll_interval_sec: float = field(default_factory=lambda: float(os.getenv("POLL_INTERVAL_SEC", "0.5")))
    max_position_lots: int = field(default_factory=lambda: int(os.getenv("MAX_POSITION_LOTS", "100")))

    # Kalman & signal parameters (adapted from statarb/ols.py for intraday ticks)
    kalman_delta: float = field(default_factory=lambda: float(os.getenv("KALMAN_DELTA", "1e-5")))
    kalman_r: float = field(default_factory=lambda: float(os.getenv("KALMAN_R", "1.0")))
    z_window: int = field(default_factory=lambda: int(os.getenv("Z_WINDOW", "60")))
    min_warmup: int = field(default_factory=lambda: int(os.getenv("MIN_WARMUP", "30")))

    entry_z1: float = field(default_factory=lambda: float(os.getenv("ENTRY_Z1", "1.8")))
    entry_z2: float = field(default_factory=lambda: float(os.getenv("ENTRY_Z2", "2.5")))
    exit_z: float = field(default_factory=lambda: float(os.getenv("EXIT_Z", "0.2")))
    stop_z: float = field(default_factory=lambda: float(os.getenv("STOP_Z", "3.8")))
    t1_weight: float = field(default_factory=lambda: float(os.getenv("T1_WEIGHT", "0.5")))
    min_hurdle_bps: float = field(default_factory=lambda: float(os.getenv("MIN_HURDLE_BPS", "10.0")))

    # Sizing & pair selection
    max_pairs: int = field(default_factory=lambda: int(os.getenv("MAX_PAIRS", "4")))
    max_lots_per_pair: int = field(default_factory=lambda: int(os.getenv("MAX_LOTS_PER_PAIR", "25")))
    rebalance_pairs_every_ticks: int = field(default_factory=lambda: int(os.getenv("REBALANCE_PAIRS_TICKS", "120")))
    use_historical_seed: bool = field(default_factory=lambda: _env_bool("USE_HISTORICAL_SEED", True))
    log_every_ticks: int = field(default_factory=lambda: int(os.getenv("LOG_EVERY_TICKS", "20")))
