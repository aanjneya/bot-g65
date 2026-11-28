# Project Goal & Alignment

> **Living North Star for `aanjneya`, `slava` (synced regularly to `main` with `.brain/`).**
> Every agent MUST read this file before starting work and keep it aligned with where the team is heading.

## Agent Protection Rule (READ CAREFULLY)
- **Never overwrite or delete** existing items in this file unless the team is intentionally and completely changing that specific thing.
- **Never edit `.brain/GOAL.md` silently or bundled with code changes** (so a teammate never clicks "Allow" without noticing).
- Whenever `.brain/GOAL.md` should be changed, refined, or pivoted — or if a prompt contradicts it:
  1. **STOP** before editing `.brain/GOAL.md`.
  2. Send a **separate, dedicated chat message** starting with `⚠️ GOAL CHANGE PROPOSAL — EXPLICIT APPROVAL REQUIRED` showing the exact **BEFORE → AFTER** diff and a 1-line **Why**.
  3. Wait for the user's **explicit chat reply** approving the change before modifying `.brain/GOAL.md` and running `./scripts/brain-push.sh`.

---

## 1. Core Goal (What We Are Building)

- **Core Goal**: **Make maximum profit (PnL) — nothing else.**
- **Method**: Autonomous Senior Quant agent (`bot/autoloop.py`) has full freedom to tune, revamp, or replace the trading strategy from scratch, **using every single feed, API field, and feature provided by the challenge organizers to the fullest extent** (full book depth & walls, `TradeTick` buyer/seller/aggressor_side, `poll_new_social_media_feeds`, `poll_new_trades`, cross-stock correlations, `limit`/`ioc`/`amend_order`/`delete_order`) to maximize live PnL on Optibook (`AMD`, `AVGO`, `CRWV`, `MU`, `NBIS`, `NVDA`, `SNDK`, `VRT`) while obeying hard exchange limits (`±100` pos, `≤200` open vol, `<25 req/s`, zero self-trade, 1 connection).

## 2. Must-Have Scope (For the Demo)

1. Live empirical validation via `bot/collector.py` + `bot/muster.py` (signal gating at $t > 2.0$ net of spread; no `HistoricalDataLoader`).
2. Queue-preserving execution in `bot/execution.py` (`floor`/`ceil` ticks, step inside wide spreads, per-order `delete_order`/`amend_order`, priority `<20 req/s` token bucket, zero self-trade).
3. Multi-signal `bot/signals.py` + 8×8 GLFT skew + endgame inventory glidepath (min 27–30) + embedded logger in `bot/runner.py`.

## 3. Explicit Non-Goals (Do NOT Build)

- No `HistoricalDataLoader` calls (disabled by organizers), no heavy ML/LLM inference in the hot loop, no raw-price cointegration taker pairs, no second live exchange connection.

## 4. Direction & Alignment Log

| Time | By | Direction Update / Alignment Note |
| :--- | :--- | :--- |
| Init | `aanjneya` | Initialized living goal & alignment file for START Hack Munich. |
| 23:28 | `aanjneya` | Approved Optiver HackaTUM 2026 ABMM-S strategy (multi-asset GLFT + counterparty/wall/news alpha) & non-goals. |
| 03:16 | `aanjneya` | Added +$10,000+ overnight live PnL target & autonomous strategy evolution (12–15m cycles) while keeping EV/risk rails. |
| 03:35 | `aanjneya` | Simplified Core Goal to pure profit (PnL) maximization, using every Optiver/Optibook feature & feed to the fullest extent. |
