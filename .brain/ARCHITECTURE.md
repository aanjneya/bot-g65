# Architecture

> Shared system architecture for **Optiver HackaTUM 2026** (`aanjneya`, `slava`).

## System Overview

**ABMM-S (Adaptive Bayesian Multi-Asset Market Maker & Sniper)** trading the 8-stock Optibook universe (`AMD`, `AVGO`, `CRWV`, `MU`, `NBIS`, `NVDA`, `SNDK`, `VRT`) in a 30-minute live final (Sunday 10:00).

## Tech Stack

- **Language / Runtime**: Python 3.10+ (`numpy`, `optibook`)
- **Live Bot (`bot/`)**: Single-connection event loop (200–250ms tick) with `MOCK_MODE=true` (`bot/mock_exchange.py`)
- **Research & Discovery**: `bot/collector.py`, `bot/insights.py`, `bot/muster.py` (live recording; `HistoricalDataLoader` is disabled by organizers)
- **Tests (`tests/`)**: `tests/test_bot.py`

## Directory Layout

```text
.
├── bot/
│   ├── config.py          # Env-driven BotConfig (USE_HISTORICAL_SEED=false)
│   ├── signals.py         # (Planned) Microprice+OFI, V>250 wall anchor, counterparty markouts, return lead-lag, news lexicon
│   ├── kalman.py          # Online Kalman & Basket models (migrating cross-stock to return-space)
│   ├── execution.py       # Risk limits, floor/ceil tick rounding, queue-preserving quoting, priority token bucket
│   ├── runner.py          # Live/Mock loop + endgame inventory glidepath + embedded CSV logger
│   ├── mock_exchange.py   # Synthetic cointegrated market for MOCK_MODE=true
│   ├── collector.py       # Standalone live recorder (books, trades, news)
│   ├── insights.py        # 1h rolling diagnostics (spread, bot markout, wallgap, lead-lag, jumps)
│   └── muster.py          # Out-of-sample (split-half) pattern validator net of spread
├── challenge/             # Optiver HackaTUM 2026 briefs, slides PDF, API reference, notes
└── tests/                 # Unit & integration tests
```

## Service Boundaries & Data Flow

1. **Market Data Ingest (1 connection max)**: Poll `get_last_price_book`, `poll_new_trade_ticks` (with `buyer`/`seller`/`aggressor_side`), and `poll_new_social_media_feeds`.
2. **Short-Horizon Fair Value ($\hat{F}_{i,t}$)**: Blend Stoikov Microprice + OFI, house-bot wall anchor (`V > 250`), Glosten-Milgrom counterparty markout impulse, and return-space cross-stock lead-lag.
3. **Portfolio Reservation Price**: Multi-asset GLFT covariance skew $\mathbf{r}_t = \hat{\mathbf{F}}_t - \gamma(t)\Sigma\mathbf{q}_t$ (ramps $\gamma(t)$ in min 27–30).
4. **3-Tier Execution (<25 req/s)**: News/edge IOC snipers (cancel opposite quotes first) → queue-preserving passive limit quoting (`floor` bid / `ceil` ask, step inside wide spreads) → hard `±100` pos & `≤180` open vol rails.
