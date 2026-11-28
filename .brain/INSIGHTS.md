# Strategic Insights, Warnings & Winning Ideas

> **Active Brain Log for `aanjneya`, `slava` (synced regularly to `main` with `.brain/`).**
> Every agent must read this at session start and append high-signal strategic warnings, judging-rubric gaps, architectural shortcuts, or demo-winning ideas that affect the whole team.
>
> **Keep entries sharp and actionable.** Mark resolved items `[x]` once addressed.

## 1. Active Warnings & Risks (Fix / Resolve First)

<!-- Example:
- [ ] **[Rubric Gap]** (`@aanjneya`'s agent, 14:10): Challenge rubric weights sponsor API usage at 30%, but current backend plan only mocks it. **Recommendation**: Wire real sponsor endpoint behind `MOCK_MODE=false` before Hour 12.
-->

- [ ] **[Judging Rubric]** (`@aanjneya`'s agent, 23:10): Final 30-min competition is Sunday after 10:00, and Optiver explicitly judges **Expected Value (EV), risk management, approach, and novelty** — not just raw lucky PnL. Keep inventory bounded, avoid directional gambling, and flatten in the final 60–90s (PnL marks at last trade).
- [ ] **[Data Constraint — CONFIRMED]** (`@aanjneya`'s agent, 23:18): Organizers confirmed **`HistoricalDataLoader` is DISABLED** for this challenge — do NOT waste time calling it (default `USE_HISTORICAL_SEED=false` in `bot/config.py`). Check `your_optiver_workspace/` in the Dev Environment for static historical files, and record live market data (order books, `TradeTick`s with counterparties, and social media feeds) ourselves via `bot/collector.py` or inside the live bot.
- [ ] **[Hidden Alpha / Bots]** (`@aanjneya`'s agent, 23:10): Live `TradeTick` exposes `buyer`, `seller`, and `aggressor_side`. Four likely Optiver house bots (`JazzyJellies`, `PrudentPoultry`, `GroovyGrapefruits`, `LivelyLasagnas`) + 500–1000 lot book walls (exceeding team limits of 100 pos / 200 open vol) can reveal uninformed flow (MM against) vs informed flow (follow/pull quotes) and anchor fair value.
- [ ] **[Hidden Alpha / News]** (`@aanjneya`'s agent, 23:10): `poll_new_social_media_feeds()` exists in the API but wasn't mentioned in slides — check if live news drives the observed CRWV jumps.
- [ ] **[Operational Trap]** (`@aanjneya`'s agent, 23:10): Hard limits: 25 req/s across all 8 stocks (amend only changes volume, so price move = 2 msgs), 100 lot pos limit (including open orders), 200 max open order vol, **1 connection per team** (second login silently kicks the first). Sunday 10:00 will have way tighter spreads as all teams connect.

## 2. Better Approaches & Winning Ideas

- [x] **[Live Bleed Triage — 6 Root Causes Fixed]** (`@aanjneya`'s agent, 00:28): Fixed why initial live ABMM-S bled: (1) **Own-order book feedback loop** — `get_last_price_book` included our own quotes, collapsing perceived spread & corrupting mid/OFI (fixed via `strip_own_orders`); (2) **`glft_skew` 100x too small** (`cov_r ~ 1e-8` gave `0.006` tick skew -> held losing bags forever; fixed with `0.5*tick` std floor + `glft_gamma=10` + immediate `+1 tick` passive exit); (3) **Broken exit hysteresis** (`execution.py` blocked moving exit ask 1 tick closer); (4) **Naked quoting without wall backstop** (now requires `V>=400` wall within 3 ticks + IOC scratch on wall break/pull/`15s` hold/`4-tick` stop, merging Slava's `wall_maker.py` + `wand.py`); (5) **Uncapped wall volume distorting Microprice/OFI** (capped at `100` + wired `LEADERS = {"SNDK": "NVDA", "CRWV": "MU", "NBIS": "AVGO"}`); (6) **Added live 5s own-fill markout circuit breaker** + `BacktestBot` adapter for `bot/backtest.py`.
- [x] **[Winning Strategy Architecture — ABMM-S]** (`@aanjneya`'s agent, 23:23): Implemented in `bot/signals.py` + `bot/execution.py` + `bot/runner.py` (16/16 unit tests passing): (1) **Multi-Asset GLFT Covariance Skew** `r = F_hat - gamma(t) * Sigma_dP * q`, (2) **Glosten-Milgrom Counterparty Markout Engine** on live `TradeTick.buyer/seller`, (3) **House-Bot Wall Anchor** (`V >= 250`), (4) **Stoikov Microprice + OFI + Return-Space Lead-Lag**, (5) **<1ms News Lexicon Sniper + Endgame Inventory Glidepath** (min 27–30 + final 45s flattener) + embedded CSV logger.
- [x] **[Current Bot Bugs Found]** (`@aanjneya`'s agent, 23:23): Fixed in `bot/execution.py` + `bot/mock_exchange.py` (8/8 unit tests passing): (1) step inside wide spreads (`min(..., ask_p - tick)`); (2) per-side `delete_order` + `amend_order` with cancel-before-insert; (3) `floor`/`ceil` tick rounding; (4) priority 4 req/s burst reserve. *(Item 4 from earlier — switching cross-stock model to return-space lead-lag — remains in `bot/signals.py` task.)*

## 3. Autoloop Insights

- [ ] **[Autoloop C1]** (02:23): Test a passive exit one tick inside a wide spread, capped at the inventory remaining. This gives the bot a chance to exit before a stop without turning a one-lot exit into a four-lot position in the opposite direction.
- [ ] **[Autoloop C1]** (02:23): Compare tail-stop count and volume, drawdown, and 5s markouts against the Champion on an untouched chunk. Muster found no signal that covered the spread, so leave aggressive entries unchanged for this test.
- [ ] **[Autoloop C1]** (02:47): AMD and AVGO generated 64% of the replay loss on 13% of fills, with adverse markouts already present at 1s. Entry selection deserves a separate test from the previous passive-exit change.
- [ ] **[Autoloop C1]** (02:47): The active strategy uses two-sided walls to shift fair value, not to qualify entry size. The logs describe wall-backed quoting, but the execution path permits full-size passive entries with no same-side wall.
- [ ] **[Autoloop C2]** (03:02): NVDA and AVGO accounted for 71% of the net replay loss on 13% of fills, both ending flat. The existing wall check qualifies geometry, not the expected return on a five-lot fill.
- [ ] **[Autoloop C2]** (03:02): CRWV made +1.5 with negative 5s replay markout, while AVGO lost 13.8 with positive 5s replay markout. Use markout to screen entry risk, but use size-weighted executions and completed inventory cycles to judge profit.
