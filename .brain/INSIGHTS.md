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

- [x] **[Strategy Lab Completed — 7 Distinct Non-Overfit 45k–48k+ Architectures]** (`@aanjneya`, 13:28): Validated across all 24 strictly non-overlapping Optibook chunks (`606.5m`, `588,175` trades; strict Train `0..11` vs Blind OOS `12..23` + `±15%` perturbation + `+35%/-30%` beta-reset stress tests):
  1. **`DelayDeconvWaveBot` (`+48,014.7`, 24/24, OOS `+25,680` > Train `+22,335`, `±0.5%` perturbation drift)**: DSP FIFO+IIR delay deconvolution + convex dip/spike `SNDK` wave envelope + follower queue hysteresis.
  2. **`P2_PennyJump_All8Leaders` (`+47,493.6`, 24/24, all 8 stocks positive)**: Two-timescale `W_OU` + unified crossing-limit + symmetric corridor penny-jumping.
  3. **`VelocityUnpinMakerSniper` (`+47,249.7`, 24/24)**: Two-tier capacity (`±78` passive maker + `22`-lot reserved jump-sniper buffer `78..100`) curing the `28–45%` follower inventory pinning without early OU exits.
  4. **`BlockPairGLFTBot` (`+46,926.6`, 24/24, `±0.3%` drift)**: Block-diagonal $2\times 2$ Pair-Factor GLFT cross-hedging (`(AVGO,NBIS)`, `(NVDA,SNDK)`, `(MU,CRWV)` — the 5 leaders have `|corr| <= 0.031`!).
  5. **`ApexOmnibusChampion` (`+46,174.2`, 24/24, self-heals `+$4.4k` under Sunday beta shock + endgame glidepath)**: Centered high-SNR Bayesian slope posterior + Pair-GLFT + Sniper-Reserve.
  6. **`StudentBookMicroPennyBot` (`+45,253.6`, 24/24, highest worst-chunk floor `+107.2`)**: Multi-level Student OBI + Stoikov Microprice + `1.5s` OFI + live `TradeTick` flow.
  7. **`BlindGraphKalmanBot` (`+45,170.0`, 24/24, OOS `+24,498` > Train `+20,672`)**: **ZERO hardcoded stock tickers, pairs, lags, or betas** — discovers leader/follower roles, causal lags (`2.6s` house-wall RLS), and slopes live from scratch (`100%` immune to Sunday 10:00 parameter resets!).

- [x] **[Live Bleed Triage — 6 Root Causes Fixed]** (`@aanjneya`'s agent, 00:28): Fixed why initial live ABMM-S bled: (1) **Own-order book feedback loop** — `get_last_price_book` included our own quotes, collapsing perceived spread & corrupting mid/OFI (fixed via `strip_own_orders`); (2) **`glft_skew` 100x too small** (`cov_r ~ 1e-8` gave `0.006` tick skew -> held losing bags forever; fixed with `0.5*tick` std floor + `glft_gamma=10` + immediate `+1 tick` passive exit); (3) **Broken exit hysteresis** (`execution.py` blocked moving exit ask 1 tick closer); (4) **Naked quoting without wall backstop** (now requires `V>=400` wall within 3 ticks + IOC scratch on wall break/pull/`15s` hold/`4-tick` stop, merging Slava's `wall_maker.py` + `wand.py`); (5) **Uncapped wall volume distorting Microprice/OFI** (capped at `100` + wired `LEADERS = {"SNDK": "NVDA", "CRWV": "MU", "NBIS": "AVGO"}`); (6) **Added live 5s own-fill markout circuit breaker** + `BacktestBot` adapter for `bot/backtest.py`.
- [x] **[Winning Strategy Architecture — ABMM-S]** (`@aanjneya`'s agent, 23:23): Implemented in `bot/signals.py` + `bot/execution.py` + `bot/runner.py` (16/16 unit tests passing): (1) **Multi-Asset GLFT Covariance Skew** `r = F_hat - gamma(t) * Sigma_dP * q`, (2) **Glosten-Milgrom Counterparty Markout Engine** on live `TradeTick.buyer/seller`, (3) **House-Bot Wall Anchor** (`V >= 250`), (4) **Stoikov Microprice + OFI + Return-Space Lead-Lag**, (5) **<1ms News Lexicon Sniper + Endgame Inventory Glidepath** (min 27–30 + final 45s flattener) + embedded CSV logger.
- [x] **[Current Bot Bugs Found]** (`@aanjneya`'s agent, 23:23): Fixed in `bot/execution.py` + `bot/mock_exchange.py` (8/8 unit tests passing): (1) step inside wide spreads (`min(..., ask_p - tick)`); (2) per-side `delete_order` + `amend_order` with cancel-before-insert; (3) `floor`/`ceil` tick rounding; (4) priority 4 req/s burst reserve. *(Item 4 from earlier — switching cross-stock model to return-space lead-lag — remains in `bot/signals.py` task.)*

## 3. Autoloop Insights

- [ ] **[Autoloop C7]** (11:11): CRWV earned four ticks in total over nine one-lot round trips. One extra profitable fill is not the unit of success: compare the entry and its eventual exit together, including scratches.
- [ ] **[Autoloop C7]** (11:11): CRWV's stable 5s imbalance effect was +0.19 ticks gross but −0.85 ticks net of spread across 91 cases. That supports testing how to manage a resting quote, not crossing the spread on the signal.
- [ ] **[Autoloop C8]** (11:24): CRWV's 72 passive fills ended flat and earned only four ticks of one-lot PnL in total. A single additional four-tick loss on one lot would erase the whole replay gain.
- [ ] **[Autoloop C8]** (11:24): CRWV and SNDK had stable 5s imbalance effects of +0.29 and +0.21 ticks gross, but −0.74 and −0.82 ticks after spread. The missing measurement is the return conditional on our passive order actually filling, not the return after all imbalance observations.
- [ ] **[Autoloop C9]** (11:38): The passive execution path distinguishes two reasons for zero volume that the round-trip screen call currently treats alike: an ineligible price and an eligible price that is temporarily unfunded. Only the latter is evidence about a quote the strategy could fund later.
- [ ] **[Autoloop C9]** (11:38): C8 and C9 report the same CRWV fill count and PnL: 72 fills and +0.4. Compare fill timestamps before counting the longer report as another profitable sample.
- [ ] **[Autoloop C10]** (11:53): The pooled wall signal changed from −0.058 per lot net at 15s to +0.032 at 60s. At the latter estimate, one additional tick of execution cost on roughly one in three round trips would erase the entire edge.
- [ ] **[Autoloop C10]** (11:53): CRWV's 120 replay fills ended flat at −0.2 despite positive 1s markout. A fill count or short-horizon markout target can improve while the cash result of entering and exiting does not.
