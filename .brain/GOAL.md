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

- **Problem**: Target **+$15,000+ live PnL** (with positive 5s markouts and bounded drawdown) overnight, and maximize risk-adjusted Expected Value (EV) in Optiver HackaTUM's 30-minute live Optibook final (Sunday 10:00) under strict `±100` pos, `≤200` open vol, `25 req/s`, and 1-connection limits.
- **Solution (1–2 sentences)**: **Autonomous Strategy Evolution (`bot/autoloop.py`, 12–15 min cycles)** starting from **ABMM-S** (Microprice+OFI, `V>250` walls, counterparty markouts, lead-lag, news sniper, 8×8 GLFT skew), with full freedom for the agent to refactor, replace, or combine signals whenever out-of-sample `backtest.py` + live telemetry prove higher EV.
- **Target User**: Optiver HackaTUM 2026 Live Exchange & Trader Judges.
- **Why It Wins (Judge Hook)**: Genuine prop-desk quant engineering (8×8 GLFT passive cross-hedging + Glosten-Milgrom counterparty profiling + rule-implied wall extraction + empirical out-of-sample `muster.py` validation), high EV with tight variance and endgame flattening.

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
