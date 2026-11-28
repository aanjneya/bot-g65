# Gotchas — `@slava`

- **Issue**: `bot/backtest.py` (`make_strategy`) only patches `m.time = clock` on the top-level module specified in `--strategy module:Class`, NOT on imported helper modules (e.g. `optibook_core`). Any helper module calling `time.time()` or `time.monotonic()` reads real CPU wall-clock time (~1.5s for a 60m replay), starving token-bucket rate limiters to ~25 orders per hour!
  **Fix**: Always read simulation/live time via `ex.clock.time() if hasattr(ex, "clock") else time.time()`.
