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

<!-- Example:
- [ ] **[Demo Wow-Factor]** (`@slava`'s agent, 15:20): Adding a live side-by-side "before vs. after" comparison view on the hero screen makes the value obvious in 10 seconds.
-->

- _None yet — log high-ROI upgrades that boost our odds of winning START Hack Munich._
