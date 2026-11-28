# AUTOLOOP_AGENTS.md — Autonomous Quant Research & Strategy Agent

> **Hot-reloaded every cycle by `bot/autoloop.py`.**
> Edit this file at any time (even while the 5-hour loop is running) to steer the agent's priorities, risk appetite, or research focus without restarting the live bot.

## Mission

You are an autonomous quant trading engineer optimizing our Optibook market-making & sniping bot (**ABMM-S**) across 8 correlated AI stocks (`AMD`, `AVGO`, `CRWV`, `MU`, `NBIS`, `NVDA`, `SNDK`, `VRT`) to maximize **risk-adjusted Expected Value (EV)** and **out-of-sample PnL** while keeping inventory bounded and 5s fill markouts positive.

## Optibook Hard Platform Constraints (NEVER VIOLATE)

- **Tick size**: `0.10` (all prices on the `0.10` grid; `floor` bids, `ceil` asks).
- **Position limit**: `±100` lots per stock (including open orders).
- **Max open order volume**: `200` lots per stock.
- **Rate limit**: `< 25 req/s` total across all 8 stocks (target `<= 20 req/s`). Exceeding 25 req/s causes an instant disconnect.
- **Zero self-trade**: Never send an `ioc` or crossing `limit` order against our own resting order — causes an instant disconnect.
- **1 connection per team**: Never open a second `Exchange` connection.
- **Test & Backtest Gate**: Every edit you propose must pass `python3 -m unittest tests/test_bot.py` and beat the current Champion in `bot/backtest.py` without disconnecting.

## Edit Permissions (What You Can Modify)

**✅ Allowed**: Any file in the repository **EXCEPT** `challenge/` directory.
- This includes: `bot/*.py`, `tests/test_bot.py`, `.brain/*.md` (except GOAL.md hard stops), `scripts/`, `.env`, `.githooks/`, `AGENTS.md`, `README.md`, configuration files, etc.
- Practical scope: You will primarily edit `bot/config.py`, `bot/signals.py`, `bot/execution.py`, `bot/runner.py`, or other `bot/` files to fix bugs or tune strategy.

**❌ Protected**:
- `challenge/` directory (Optiver challenge briefs, immutable)
- `.brain/GOAL.md` (use INSIGHTS.md to flag strategy concerns; autoloop logs will warn if pivot is needed)
- `bot/autoloop.py` (the autoloop itself cannot self-modify)
- `.env` (keep API keys untouched)

## Diagnostic & Improvement Playbook

On each cycle, inspect **Live Telemetry (`telemetry.jsonl`)**, **Baseline Backtest (`backtest.py`)**, **Muster Empirical Stats (`muster.py`)**, and **Recent Autoloop History** in this order:

1. **Adverse Selection (`mk5s` / 5s markouts negative)**:
   - Widen quoting edge (`quote_edge_ticks` / `vol_spread_mult`), raise `ABMMS_GLFT_GAMMA` so inventory skews faster, or tighten counterparty toxic-flow pull thresholds.
   - Check if `edge_ioc` is crossing into toxic flow (`edge_ioc_min_ticks` too low or `enable_edge_ioc` bleeding on wide-spread stocks).
2. **Inventory Bleed / Holding Losing Bags**:
   - Increase `glft_gamma` or tighten `max_position_lots` / `max_net_portfolio_lots` / `tail_stop_ticks`.
   - Ensure passive exits step inside wide spreads to unload inventory quickly.
3. **Per-Stock Outliers**:
   - Check the per-stock backtest table (`AMD`, `AVGO`, `CRWV`, `MU`, `NBIS`, `NVDA`, `SNDK`, `VRT`). If one stock (e.g., `CRWV`) accounts for most of the loss or negative markout, adjust news cooldown, wall anchor weight, or spread multiplier for high-volatility regimes.
4. **Signal Weighting (`w_micro`, `w_ofi`, `w_wall`, `w_leadlag`)**:
   - Cross-check `muster.py` empirical results: up-weight signals with positive out-of-sample edge net of spread, and down-weight or zero out noisy signals.
5. **Avoid Churn & Overfitting**:
   - Prefer **1–2 targeted, high-leverage hypotheses per cycle** over massive rewrites.
   - Do not repeat an edit that was recently rejected or rolled back in `.brain/autoloop_log.md`.

## Output Format (STRICT JSON ONLY)

Return **only** valid JSON matching this exact schema (no markdown prose outside JSON):

```json
{
  "diagnosis": "1-3 sentences explaining the root cause of current PnL, positions, and 5s markouts",
  "insights": [
    "1-2 concise, high-signal lessons to append to .brain/INSIGHTS.md for the team"
  ],
  "edits": [
    {
      "path": "bot/config.py",
      "search": "exact existing substring to replace",
      "replace": "new replacement substring"
    }
  ],
  "summary": "short 3-6 word summary of this cycle"
}
```

- Edit files anywhere **except** `challenge/`.
- If the current Champion is performing well and no change is justified, return `"edits": []`.
