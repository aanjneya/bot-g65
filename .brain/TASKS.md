# Live Task Board

> Lightweight shared board synced via `.brain/` on `main` (replaces separate lock files).
> Format: `[ ]` todo, `[~] @<id> Task (active/folder/, shared-hub-file)` in progress, `[x] @<id>` done.
> **Collision check**: Always list active folders and shared hub files (`API_CONTRACT.ts`, `package.json`, root router/layout, DB schema) in parentheses on your `[~]` task. Warn the user before editing a path listed on another teammate's active `[~]` task.

## Now (Active Sprint)

- [x] `@aanjneya` (handoff from `@slava`) Discover, anti-overfit validate & compare 7 distinct strategies with +45.2k to +48.0k cumulative Optibook PnL across 24/24 chunks (`bot/strat_top*.py`, `bot/strategy_lab_eval.py`, `.brain/STRATEGY_LAB.md`, `.brain/INSIGHTS.md`)
- [x] Drop Optiver HackaTUM 2026 challenge briefs & API docs into `challenge/`
- [x] `@aanjneya` Approve `.brain/GOAL.md` update for Optiver HackaTUM 2026 ABMM-S strategy
- [ ] Run `bot/collector.py` + `bot/muster.py` on live practice market (30–60m) to validate house-bot markouts, `V>250` walls, news feed, and lead-lag (`data_*/`)
- [x] `@aanjneya` Fix 4 execution bugs in `bot/execution.py` (`bot/execution.py`, `bot/mock_exchange.py`, `tests/test_bot.py`)
- [x] `@aanjneya` Build ABMM-S strategy: `bot/signals.py` (Microprice+OFI, `V>250` wall anchor, online counterparty markouts, return-space lead-lag, news lexicon, 8×8 GLFT), endgame glidepath, embedded CSV logger (`bot/signals.py`, `bot/execution.py`, `bot/runner.py`, `bot/config.py`, `bot/mock_exchange.py`, `tests/test_bot.py`)

- [x] `@aanjneya` Build 5-hour Gemini autonomous optimization & hot-deploy loop (`bot/autoloop.py`, `bot/runner.py`, `tests/test_bot.py`)

## Next

- [ ] Run live-practice stress test (`python3 -m bot.runner --log-csv`) & tune weights from `muster.py` before Sunday 10:00 reset (`--session-sec 1800`)

## Done

- [x] `@aanjneya` Build live Optibook Kalman StatArb bot (`bot/`, `tests/`)

- [x] `@aanjneya` Initialize 2-person hackathon repo, `second-brain` skill, `AGENTS.md`, and symlinks
- [x] `@aanjneya` Harden hackathon infra: 3-way `brain-push.sh`, `.githooks/pre-push`, `verify.sh`, `setup.sh`, `@AGENTS.md`, zero-intervention brain sync
