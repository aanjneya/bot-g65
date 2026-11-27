# Live Task Board

> Lightweight shared board synced via `.brain/` on `main` (replaces separate lock files).
> Format: `[ ]` todo, `[~] @<id> Task (active/folder/, shared-hub-file)` in progress, `[x] @<id>` done.
> **Collision check**: Always list active folders and shared hub files (`API_CONTRACT.ts`, `package.json`, root router/layout, DB schema) in parentheses on your `[~]` task. Warn the user before editing a path listed on another teammate's active `[~]` task.

## Now (Active Sprint)

- [x] Drop Optiver HackaTUM 2026 challenge briefs & API docs into `challenge/`
- [x] `@aanjneya` Approve `.brain/GOAL.md` update for Optiver HackaTUM 2026 ABMM-S strategy
- [ ] Run `bot/collector.py` + `bot/muster.py` on live practice market (30–60m) to validate house-bot markouts, `V>250` walls, news feed, and lead-lag (`data_*/`)
- [x] `@aanjneya` Fix 4 execution bugs in `bot/execution.py` (`bot/execution.py`, `bot/mock_exchange.py`, `tests/test_bot.py`)
- [ ] Build `bot/signals.py` (Microprice+OFI, `V>250` wall anchor, online counterparty markouts, return-space lead-lag, news lexicon) & matrix GLFT `r = F - gamma(t)*Sigma*q` (`bot/runner.py`)

## Next

- [ ] Embed lightweight CSV logger inside `bot/runner.py` (1-connection-per-team rule)
- [ ] Add endgame inventory glidepath (min 27–30) & live regime-decay spread adaptation
- [ ] Run `pytest` / `./scripts/verify.sh` and live-practice stress test before Sunday 10:00 reset

## Done

- [x] `@aanjneya` Build live Optibook Kalman StatArb bot (`bot/`, `tests/`)

- [x] `@aanjneya` Initialize 2-person hackathon repo, `second-brain` skill, `AGENTS.md`, and symlinks
- [x] `@aanjneya` Harden hackathon infra: 3-way `brain-push.sh`, `.githooks/pre-push`, `verify.sh`, `setup.sh`, `@AGENTS.md`, zero-intervention brain sync
