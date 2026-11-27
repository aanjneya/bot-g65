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

- [x] **[Winning Strategy Architecture — ABMM-S]** (`@aanjneya`'s agent, 23:23): Implemented in `bot/signals.py` + `bot/execution.py` + `bot/runner.py` (16/16 unit tests passing): (1) **Multi-Asset GLFT Covariance Skew** `r = F_hat - gamma(t) * Sigma_dP * q`, (2) **Glosten-Milgrom Counterparty Markout Engine** on live `TradeTick.buyer/seller`, (3) **House-Bot Wall Anchor** (`V >= 250`), (4) **Stoikov Microprice + OFI + Return-Space Lead-Lag**, (5) **<1ms News Lexicon Sniper + Endgame Inventory Glidepath** (min 27–30 + final 45s flattener) + embedded CSV logger.
- [x] **[Current Bot Bugs Found]** (`@aanjneya`'s agent, 23:23): Fixed in `bot/execution.py` + `bot/mock_exchange.py` (8/8 unit tests passing): (1) step inside wide spreads (`min(..., ask_p - tick)`); (2) per-side `delete_order` + `amend_order` with cancel-before-insert; (3) `floor`/`ceil` tick rounding; (4) priority 4 req/s burst reserve. *(Item 4 from earlier — switching cross-stock model to return-space lead-lag — remains in `bot/signals.py` task.)*
