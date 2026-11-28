# Autonomous Autoloop: Quick Start Guide

## What It Does

**bot/autoloop.py** runs a 5-hour continuous loop that:

1. **Keeps ONE live bot running** (`bot.runner` on MockExchange by default)
2. **Every 20 minutes**, snapshots the latest market data (books, trades, news, telemetry)
3. **Runs baseline backtest** on the latest chunk
4. **Calls GPT-6.2 Astra** (via OpenAI-compatible gateway, model id `azure/gpt-6.2-astra`) to diagnose root causes and propose code fixes
5. **Tests candidate changes** (compile + unit tests + head-to-head backtest)
6. **Promotes winning configs** (commits to personal branch, restarts live bot)
7. **Auto-rollback if live loss** (if promoted version bleeds, reverts to previous champion)
8. **Syncs insights to `.brain/`** and pushes `.brain/INSIGHTS.md` to `main`

---

## Setup (One-Time)

### 1. Install Dependencies
```bash
pip install openai  # Already done above
```

### 2. Verify `.env` is configured
```bash
cat .env | grep LLM_GATEWAY
# Should show:
# LLM_GATEWAY_BASE_URL=https://llm.orange.sixt.com/v1
# LLM_GATEWAY_API_KEY=sk-MkbM7_irwo6_LzPkNoUTtg
# LLM_GATEWAY_MODEL=azure/gpt-6.2-astra
```

### 3. Verify git is set up
```bash
git config --get hackathon.teammate
# Should show: aanjneya or slava
```

If not set, run:
```bash
./scripts/setup.sh aanjneya  # (or slava)
```

---

## Running the Autoloop

### Quick dry-run (5 min on mock mode)
```bash
python3 -m bot.autoloop --hours 0.08 --cycle-min 1 --mock
```

This runs 1–2 cycles against MockExchange (no real API calls, synthetic market) in ~5 minutes to test the pipeline.

### Production 5-hour run
```bash
python3 -m bot.autoloop --hours 5.0 --cycle-min 20
```

This:
- Runs the live bot on `bot.runner` (against MockExchange by default; set `MOCK_MODE=false` for live Optibook)
- Waits 20 minutes, then every 20 min runs the full optimization cycle
- Logs all cycles to `.brain/autoloop_log.md`
- Commits promoted changes to your personal branch (`aanjneya` or `slava`)
- Pushes `.brain/` updates to `main` automatically

### Options
```bash
--hours FLOAT           Total runtime (default 5.0)
--cycle-min FLOAT       Minutes between optimization cycles (default 20)
--mock                  Use MockExchange (safe for testing)
--no-push               Skip brain-push.sh syncs
--rollback-loss FLOAT   Auto-rollback if live delta PnL <= this (default -150.0)
```

---

## Monitoring

### Live outputs
- **stderr**: Real-time logs of each cycle (basis backtest, LLM call, test results)
- **stdout**: Progress and diagnostics

### Post-run artifacts
```
data/autoloop_<DATE>_<TIME>/
  live_001_<TIME>/          # First live bot run
    runner.log              # Bot activity
    books.csv               # Order book snapshots
    trades.csv              # Trade ticks
    news.csv                # Social media feeds
    telemetry.jsonl         # PnL, inventory, markouts
  snapshots/cycle_001/      # Baseline backtest input
  cycle_002/                # Next cycle, etc.
  backups/cycle_001/        # Backups of files before edits
```

### Brain updates
```
.brain/autoloop_log.md      # Every cycle logged here
.brain/INSIGHTS.md          # Key learnings (Autoloop section)
```

---

## Expected Behavior

**Cycle 1** (baseline):
- Runs backtest on current champion config
- LLM diagnoses current state
- May propose weight tweaks or logic fixes

**Cycle 2–N** (optimization):
- Runs backtest on **candidate** (with LLM-proposed edits)
- Compares candidate PnL vs baseline
- If candidate wins: **PROMOTED** → live bot restarted, code committed to personal branch
- If candidate loses: **REJECTED** → reverted, baseline stays live

**Live monitoring**:
- After promotion, next cycle checks if the new config bled in live trading
- If delta PnL <= -$150: **AUTO-ROLLBACK** to previous champion, alert logged to `.brain/`

---

## Autoloop Respects Team Rules

✅ **What it does right**:
- Commits only to personal branch (never force-pushes `main`)
- Syncs `.brain/` via `brain-push.sh` (3-way merge, no silent overwrites)
- Never modifies `.brain/GOAL.md` (read-only strategy anchor)
- Never edits `challenge/` (immutable Optiver briefs)
- Only promotes configs that pass tests + backtest
- Auto-detects teammate ID from `git config hackathon.teammate`

❌ **What it won't do**:
- Edit `challenge/` (protected)
- Modify `.brain/GOAL.md` (use INSIGHTS.md to flag concerns)
- Push directly to `main` (personal branch only)
- Run live on second connection (would kick first bot offline)

---

## Troubleshooting

### "LLM_GATEWAY_API_KEY missing"
→ Check `.env` file exists and has `LLM_GATEWAY_API_KEY=sk-...` set

### "Could not auto-detect teammate ID"
→ Run `./scripts/setup.sh aanjneya` (or `slava`) once

### "openai SDK not installed"
→ `pip install openai`

### "git commit failed"
→ Check branch is set up correctly: `git branch --show-current` should be `aanjneya` or `slava`

### "Brain-push.sh failed"
→ Likely a `.brain/` merge conflict. Resolve manually: `git fetch origin && git merge origin/main --no-edit`

---

## What Astra (the LLM) Can Edit

✅ **Allowed**:
- `bot/config.py` (weights, thresholds)
- `bot/signals.py` (fair-value logic, signal weights)
- `bot/runner.py` (live bot loop)
- `bot/execution.py` (order placement, risk layer)
- `tests/test_bot.py` (fix failing tests)
- Any other file in the repo **except** `challenge/`

❌ **Protected**:
- `challenge/` (Optiver briefs, immutable)
- `bot/autoloop.py` (itself, can't self-modify)
- `.brain/GOAL.md` (strategy anchor, read-only)

---

## Example: Running a Quick Test

```bash
# 1. Dry-run: 5 cycles on mock mode (no live API, no LLM calls)
python3 -m bot.autoloop --hours 0.08 --cycle-min 1 --mock --no-push

# 2. Watch the output
# "Cycle 1: ... backtest pnl=+120 ..."
# "LLM API call failed" (expected in mock mode)
# "PROMOTED candidate" or "REJECTED candidate" (even without LLM)

# 3. Check results
ls -la data/autoloop_*/
cat .brain/autoloop_log.md
```

Done! The autoloop is ready to tune your bot autonomously for 5 hours.
