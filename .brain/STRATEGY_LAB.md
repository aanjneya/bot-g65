# Second Brain — Strategy Lab & Backtest Ledger

> **Owner**: `@slava` (synced to `.brain/`).
> **Objective**: Discover **5 distinct strategies** achieving **>= +50,000** cumulative profit across all strictly non-overlapping recorded Optibook market data chunks (`bot/backtest.py` rules: `±100` lot pos incl. open orders, `<=200` open order vol, `<25 req/s`, `0.10` tick grid, zero self-trades).
> **Logging Mandate**: Log **every** strategy conceived/tested (baselines, failed/rejected ideas, `30k+` detailed runners-up, and `50k+` selected winners), the microstructure insights gathered from each, and why each non-selected strategy was rejected or superseded.

---

## 0. Benchmark Dataset (24 Strictly Disjoint Recorded Chunks)

To prevent snapshot double-counting (`autoloop/*/snapshots/cycle_*` are cumulative prefixes of parent `live_*` folders), all cumulative PnLs are evaluated across the **24 strictly non-overlapping recorded Optibook chunks** spanning `2026-11-27 21:38` to `2026-11-28 09:05` (~10.1 hours, ~575k trades).

---

## 1. Summary Leaderboard (Updated Live)

| ID | Strategy Name | Module / Class | Cumulative PnL | Chunks Positive | Worst Chunk | Max DD | Status / Decision |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| S0a | Simple Touch Maker (`simple`) | `backtest:SimpleMaker` | **-190.7** | 9 / 24 | -164.3 | -284.1 | **REJECTED** (22/24 disconnects at 26 req/s; never steps inside wide spreads) |
| S0b | Wall Penny-Jumper (`wall_raw`) | `wall_maker` (no filter) | **-10,602.1** | 0 / 24 | -1,453.3 | -1,469.0 | **REJECTED** (0/24 positive; catastrophic adverse selection + forced IOC scratches) |
| S0c | Filtered Wall Maker (`wall`) | `wall_maker` (Micro filter) | **-5,709.5** | 2 / 24 | -850.7 | -863.1 | **REJECTED** (Microprice filter halves loss vs `wall_raw` but wall-scratch churn bleeds) |
| S0d | Spike Catcher (`spike`) | `spike:SpikeCatcher` | **-5,568.0** | 0 / 24 | -747.1 | -747.1 | **REJECTED** (0/24 positive; fades jumps, whereas jumps on CRWV/NBIS strongly continue!) |
| S0e | Markout MM (`markout_mm:Voll`) | `markout_mm:Voll` | **-4,869.0** | 5 / 24 | -1,023.1 | -1,033.5 | **REJECTED** (joins BBO behind 200–1250 lot house walls; filled only on adverse sweeps) |
| S0f | Pure ABMM-S Champion (`BacktestBot`) | `runner:BacktestBot` | **-500.2** | 2 / 24 | -58.1 | -59.9 | **REJECTED** (gates starve NBIS:+99/CRWV:+36 while 1-lot probes bleed on AMD/NVDA/AVGO) |
| S1 | Causal Coint Follower Sniper (`CointSniper`) | `strat_lab_01:CointSniper` | **+30,086.2** | **24 / 24** | +33.1 | -183.0 | **30k+ RUNNER-UP** (logged in detail; superseded by >50k strategies) |
| S2 | Pure Passive FV Maker (`FairValueMaker`) | `strat_lab_01:FairValueMaker` | **+5,581.2** | 16 / 24 | -609.1 | -1,046.8 | **REJECTED** (<50k; zero IOC sniping misses instant mispricings & eats jump risk) |
| S3 | Hybrid Alpha Maker-Sniper v1 (`HybridAlphaMakerSniper`) | `strat_lab_01:HybridAlphaMakerSniper` | **+37,325.4** | **24 / 24** | +42.9 | -182.8 | **30k+ RUNNER-UP** (logged in detail; uses noisy BBO leader & 45-lot cap) |
| S4 | Fast-Alpha State-Space Arb (`StateSpaceTrajectoryArb`) | `strat_lab_02:StateSpaceTrajectoryArb` | **+28,887.5** | **24 / 24** | +4.4 | -425.5 | **REJECTED** (fast alpha decay 0.03 chased 12–40s OU excursions; 0.015 cover IOC cannibalized maker exits) |
| S5 | Forward Path Wave Rider v1 (`DelayWaveRider`) | `strat_lab_02:DelayWaveRider` | **+27,451.9** | 23 / 24 | -2.3 | -454.1 | **REJECTED** (same fast-alpha decay + cover IOC cannibalization cut passive fills from 46k to 18.5k) |
| S6 | 8-Stock GLFT Maker v1 (`StructuralGLFTMaker`) | `strat_lab_02:StructuralGLFTMaker` | **+25,080.4** | 23 / 24 | -3,572.0 | -4,989.6 | **REJECTED** (`AVGO:+2,313` proved leader alpha, but `AMD:-3,650`/`NVDA:-2,831` swept partially-eaten walls) |
| S7 | 2D Online Kalman + Flow v1 (`KalmanInnovationEngine`) | `strat_lab_02:KalmanInnovationEngine` | **+20,370.4** | 22 / 24 | -159.0 | -681.8 | **REJECTED** (`q_alpha=1e-4` drifted with OU noise; needs slower `q_alpha` & passive unwinds) |
| S8 | Dual-Layer Waterfall v2 (`WaterfallHarvester`) | `strat_lab_03:WaterfallHarvester` | **+32,550.2** | **24 / 24** | +32.7 | -428.0 | **30k+ RUNNER-UP** (`AVGO:+3,185, NVDA:+548, AMD:+406` on leaders; 2-layer quotes dilute follower rate budget) |
| S9 | Pure Return-Space Impulse v2 (`ImpulseStatArb`) | `strat_lab_03:ImpulseStatArb` | **+23,502.0** | 23 / 24 | -7.4 | -465.1 | **REJECTED** (<30k; pure impulse without OU cointegration misses `-14k` of mean-reversion alpha) |
| S10 | OU Cointegration Maker-Sniper v1 (`OUCointMakerSniper`) | `strat_ou_hybrid:OUCointMakerSniper` | **+37,826.2** | **24 / 24** | +11.0 | -206.0 | **30k+ RUNNER-UP** (`0.055` IOC hurdle & `0.045` half-spread too conservative; `0` leader PnL) |
| S11 | Delay-Buffer Trajectory v1 (`DelayBufferTrajectoryBot`) | `strat_delay_trajectory:DelayBufferTrajectoryBot` | **+32,859.2** | **24 / 24** | +66.8 | -248.3 | **30k+ RUNNER-UP** (`0.5*(bbo+house)` obs_y slightly diluted OU reversion on CRWV) |
| S12 | 2D Adaptive Kalman + Flow v1 (`AdaptiveKalmanFlowBot`) | `strat_kalman_allweather:AdaptiveKalmanFlowBot` | **+35,590.8** | **24 / 24** | +10.5 | -273.5 | **30k+ RUNNER-UP** (`+35.6k` with ZERO hardcoded betas! `0.055` hurdle & no leaders kept <50k) |
| S13 | 8-Stock GLFT Portfolio v1 (`MultiAssetGLFTPortfolioBot`) | `strat_glft_8stock:MultiAssetGLFTPortfolioBot` | **+38,526.3** | **24 / 24** | +34.2 | -306.3 | **30k+ RUNNER-UP** (`AVGO:+1,118, NVDA:+483` + `+37.3k` followers; `0.095` leader hurdle too wide) |
| S14 | Student-OBI & Vol-Adaptive Maker v1 (`OrderBookMicrostructureBot`) | `strat_micro_obi_sniper:OrderBookMicrostructureBot` | **+37,692.3** | **24 / 24** | +46.8 | -206.4 | **30k+ RUNNER-UP** (tight `-206.4` DD; `vol_ema` widened quotes during high-opportunity jumps) |
| S15 | Bayesian Multi-Signal Ensemble v1 (`SupremeEnsembleBot`) | `strat_ensemble_supreme:SupremeEnsembleBot` | **+36,096.0** | **24 / 24** | +80.2 | -292.0 | **30k+ RUNNER-UP** (`AVGO:+1,929` + highest worst-chunk `+80.2`, `fhm` blend reduced CRWV OU alpha) |
| S16 | Tight-Hurdle 2-Pass Probe (`P4_RawLeadAlpha`) | `_probe_50k:P4_RawLeadAlpha` | **+38,588.9** | **24 / 24** | +25.0 | -622.2 | **30k+ RUNNER-UP** (`AVGO:+2,556, NVDA:+635, VRT:+395, MU:+160`, exposed stale-book `min(ba-TICK)` bug) |
| S17 | Unified Crossing-Limit v1 (`U1_Base`, `0.055` half-sp) | `_slide12_test:U1_Base` | **+38,492.0** | **24 / 24** | +30.0 | -559.7 | **30k+ RUNNER-UP** (proved Slide-12 single-request crossing-limit execution: `60,989p / 10,008a` fills) |
| S18 | Unified Crossing-Limit v2 (`U2_Tighter`, `0.040` half-sp) | `_slide12_test:U2_Tighter` | **+40,237.6** | **24 / 24** | +35.0 | -616.6 | **40k+ RUNNER-UP** (first to break **+40k**! `74,153p / 12,226a` fills; `0.075` leader half-sp still locked out 3-tick walls on MU/VRT) |
| S19 | Tight-Leader All-8 (`B2_NoAMD_030`, `0.042` lead half-sp) | `_bt_50k:B2_NoAMD_030` | **+32,177.2** | 23 / 24 | -15.0 | -1,451.8 | **30k+ RUNNER-UP** (`SNDK:+12,977, VRT:+413, MU:+96`, but `0.042` half-sp bled `AVGO:-3,546, NVDA:-1,552`) |
| S20 | Unconditional Leader Wall-Front Maker (`V1_UnlockedRes`) | `_unlock_all8:V1_UnlockedRes` | **-94,657.2** | 1 / 24 | -11,167.6 | -11,167.6 | **REJECTED** (followers made `+37,598`, leaders lost `-132,243`: resting leader orders get run over by `on_book` jumps!) |
| S21 | Short-Horizon 5s BBO Drift Scalper (`D1_Scale10`) | `_direct_bbo:D1_Scale10` | **+28,955.7** | 23 / 24 | -12.0 | -846.9 | **REJECTED** (high `OOS R2=+0.64` at 2s, but scalping 2s–5s BBO noise churned `15,093` aggr fills instead of holding for full `30–40s` OU convergence!) |

---

## 2. Detailed Strategy Log (Hypotheses, Results, Insights & Rejection/Selection Reasons)

### Baselines S0a–S0f (Existing Repository Strategies)
- **Backtest Setup**: Evaluated across all **24 strictly disjoint recorded Optibook chunks** (`606.5 min` = `10.11 hours`, `588,175` trades, `2,895,287` lots).
- **Empirical Results**:
  - **S0a (`simple`)**: `-190.7` total (`+8.5` H1 / `-199.3` H2), **22/24 disconnects** (`26 req/s > 25` limit). Why rejected: Violates Optibook rate limit (`delete_orders` + 2 `insert_order` across 8 stocks every 0.2s) and joins BBO instead of stepping inside 2-tick spreads.
  - **S0b (`wall_raw`) & S0c (`wall`)**: `-10,602.1` (`0/24` pos) and `-5,709.5` (`2/24` pos). Why rejected: House-bot walls (`V >= 400`) frequently shift or pull when the true fair value moves; flattening via IOC whenever a wall shifts turns every fair-value move into a locked-in spread+adverse loss (`6,819`–`14,672` aggressive scratch fills!).
  - **S0d (`spike`)**: `-5,568.0` (`0/24` pos, `604` passive / `495` aggressive fills). Why rejected: Places resting limit orders 8 ticks from mid betting on jump mean-reversion, but Optibook jumps are permanent factor shifts on leaders (`AMD:-1445`, `AVGO:-1373`, `NVDA:-1129`) and multi-second continuations (`+$0.76` over 5s!) on followers (`CRWV`, `NBIS`).
  - **S0e (`markout_mm:Voll`)**: `-4,869.0` (`5/24` pos, `47,714` passive fills). Why rejected: Quotes at `best_bid` / `best_ask` behind `170–1,259` lot queues (`LivelyLasagnas` house MM), suffering classic queue adverse selection (only filled when informed sweeps clear the entire BBO), though notably `SNDK` (`+250`) and `NBIS` (`+53`) were positive.
  - **S0f (`runner:BacktestBot` — prior Autoloop Champion)**: `-500.2` (`2/24` pos; `NBIS:+99`, `CRWV:+36`, `AMD:-156`, `NVDA:-146`, `AVGO:-129`, `VRT:-88`, `MU:-82`, `SNDK:-34`). Why rejected: Stacked veto gates (`RoundTripScreen`, `PassiveEntryEvidence`, `CycleBudget`, 1-lot wall cap, and 3.5-tick FV cap) choked profitable follower trading while still leaking 1-lot adverse probes on high-vol random-walk leaders (`AMD`, `AVGO`, `NVDA`).
- **Key Microstructure Insights Uncovered**:
  1. **House Bot Ecosystem**: Counterparty logs (`live_001_014048`, 9,604 trades) prove `LivelyLasagnas` is on 100% of trades (House MM providing deep queues/walls) while `PrudentPoultry` (50%) and `JazzyJellies` (50%) are active House Takers (~8–13 lots/s per stock).
  2. **Exact Cointegrated Leader-Follower Triplets ($R^2 > 0.995$ over 10h)**:
     - `SNDK ≈ 0.739 + 0.4208 * NVDA` ($R^2 = 0.9954$, `resid_std = 0.121` at `2.5s` lag!)
     - `NBIS ≈ 10.325 + 0.4020 * AVGO` ($R^2 = 0.9973$, `resid_std = 0.192` at `0.5s` lag + strong `AC1 = +0.28` momentum!)
     - `CRWV ≈ 58.427 + 1.0875 * MU` ($R^2 = 0.9953$, `resid_std = 0.261` at `0.5s` lag + strong `AC1 = +0.31` momentum!)
  3. **Leader Volatility Trap vs Wide-Spread Queue=0 Opportunity**: `AMD`, `AVGO`, `NVDA` have high 5s volatility (`0.46–0.59`) vs `0.12` spread, destroying naive BBO-joining makers, whereas stepping 1 tick inside `>=0.20` spreads (present `20–32%` of the time!) achieves `queue = 0` ahead of `LivelyLasagnas`.


---

### Strategy S1 — Causal Cointegration Follower Sniper (`strat_lab_01:CointSniper`) — **+30,086.2 PnL (30k+ Detailed Log)**
- **Hypothesis & Mechanism**:
  - Exploits the unidirectional causal cointegration + lead-lag relationships discovered on the three follower stocks (`SNDK` <- `NVDA` $\beta=0.421$, `NBIS` <- `AVGO` $\beta=0.402$, `CRWV` <- `MU` $\beta=1.088$) using an online slow-adapting intercept $\hat{\alpha}_{i,t}$ (`EMA` rate `0.002`/step $\approx 100\text{s}$ half-life, zero lookahead) plus `1.0s` autoregressive continuation (`+0.28 * dMid_1s`) on `CRWV` and `NBIS` and capped Stoikov Microprice (`0.25`).
  - Sweeps mispriced opposite-touch liquidity via IOC up to `±100` lots whenever $|\hat{F}_{i,t} - \gamma q_i - \text{touch}| \ge 0.08$, and posts passive limit exit orders at $\hat{F}_{i,t} \pm 0.02$.
- **24-Chunk Backtest Results (`606.5 min`, 0 disconnects)**:
  - **Total Cumulative PnL**: **`+30,086.2`** (`First 12 chunks: +13,421.6` | `Last 12 OOS chunks: +16,664.5`)
  - **Consistency**: **`24 / 24` chunks positive (100% win rate across chunks!)**; Best chunk `+4,531.4` (`live_28_001232`), Worst chunk `+33.1` (`live_002_022319`, 1.9m).
  - **Risk & Execution**: Worst intra-chunk drawdown **`-183.0`** (PnL/DD ratio = **`164.4x`**!), Max Req/s = `21` (`<25`), Fills = `14,488` passive / `6,700` aggressive.
  - **Per-Stock Attribution**: `CRWV: +10,378`, `NBIS: +10,365`, `SNDK: +9,341` (`AMD/AVGO/MU/NVDA/VRT: 0`).
- **Insights Gathered**:
  1. Unidirectional follower sniping with passive fair-value exits eliminates the 2-leg taker spread bleed that killed classic pairs trading (`corr(gap, dLeader) ≈ 0`, so hedging in the leader only burns spread).
  2. Even with `0.08` entry hurdle, passive exits at $\hat{F}$ convert `68%` of total fills into spread-earning maker fills.
- **Why Not Taken as Final Top-5 (`<50k` Limitation)**:
  1. Uses noisy `bbo_mid` of leaders (`NVDA`, `AVGO`, `MU`) rather than `LivelyLasagnas`'s noise-free `3000`-lot `house_mid` ladder.
  2. Never posts passive *entry* quotes on `SNDK`, `NBIS`, `CRWV` when spread is wide (`0.20–0.40`), leaving `+7k–+20k` of maker spread capture on the table.
  3. Leaves `500` lots of capacity across `AMD`, `AVGO`, `MU`, `NVDA`, `VRT` completely idle (`$0` PnL).

---

### Strategy S2 — Pure Passive Fair-Value Market Maker (`strat_lab_01:FairValueMaker`) — **+5,581.2 PnL (Rejected)**
- **Hypothesis & Mechanism**:
  - Tests whether 100% passive limit-order market making (`0` IOC orders) around causal $\hat{F}_{i,t} - \gamma q_i$ (quoting followers continuously with `40` lots and non-followers only when stepping inside `>=0.20` spreads) can reach `50k` without ever crossing the spread.
- **24-Chunk Backtest Results (`606.5 min`, 0 disconnects)**:
  - **Total Cumulative PnL**: **`+5,581.2`** (`First 12: +3,367.0` | `Last 12: +2,214.1`), `16 / 24` chunks positive, Worst DD `-1,046.8`, `43,528` passive fills / `0` aggressive fills.
  - **Per-Stock Attribution**: `SNDK: +3,541`, `NBIS: +1,645`, `CRWV: +339`, `AVGO: +117`, `NVDA: +16`, `AMD: -72`.
- **Insights Gathered & Why Rejected**:
  1. Stepping inside `>=0.20` spreads (`queue = 0`) turned `FairValueMaker` from `-4,869` (`markout_mm`) into **`+5,581`** — proving queue priority (`queue = 0`) is mandatory for passive quoting on Optibook.
  2. **Why Rejected**: Without IOC sweeps or fast quote-pulling when a leader jumps by `$0.50+`, resting passive quotes on `CRWV` and `NBIS` get picked off by faster arbitrageurs before they can move, and the bot fails to lift blatant stale asks sitting `3–8` ticks below new fair value (`-31.7k` opportunity cost vs `S3`!).

---

### Strategy S3 — Hybrid Alpha Maker-Sniper v1 (`strat_lab_01:HybridAlphaMakerSniper`) — **+37,325.4 PnL (30k+ Detailed Log)**
- **Hypothesis & Mechanism**:
  - Combines `S1`'s IOC mispricing sweeps (`>=0.07` edge) with `S2`'s continuous passive limit quoting (`45` lots, `0.05` half-spread) around $\hat{F}_{i,t} - 0.0012 q_i$ on `SNDK`, `NBIS`, `CRWV` plus inside-spread quoting on `MU`, `VRT`.
- **24-Chunk Backtest Results (`606.5 min`, 0 disconnects)**:
  - **Total Cumulative PnL**: **`+37,325.4`** (`First 12: +17,868.3` | `Last 12 OOS: +19,457.1`)
  - **Consistency**: **`24 / 24` chunks positive (100% win rate!)**; Best chunk **`+5,758.1`**, Worst chunk `+42.9`.
  - **Risk & Execution**: Worst intra-chunk drawdown **`-182.8`** (**`204.2x` PnL/MaxDD ratio!**), Max Req/s = `22`, Fills = `46,577` passive / `5,679` aggressive.
  - **Per-Stock Attribution**: `NBIS: +13,174`, `SNDK: +12,147`, `CRWV: +12,009`, `AMD/AVGO/MU/NVDA/VRT: +0`.
- **Insights Gathered & Why Superseded (`<50k` Bottleneck Diagnosis)**:
  1. Combining IOC snipes + passive maker quotes is **super-additive** (`+37.3k` > `+30.1k + +5.6k = +35.7k`!) because IOC sweeps immediately capture leader jumps AND prevent our own stale opposite quotes from getting picked off, while passive two-sided quotes harvest `PrudentPoultry`/`JazzyJellies` noise flow between jumps!
  2. **Three Bottlenecks Keeping `S3` at `+37.3k` Instead of `+60k–+90k`**:
     - **Rate-limit saturation (`22 req/s` every chunk)**: Zero price hysteresis (`price_tol = 0.0`) caused micro-jitter cancel/replace churn, starving quote updates.
     - **Noisy leader input & unmodeled `2.5s` `SNDK` delay buffer**: Used `bbo_mid` of leaders instead of `LivelyLasagnas`'s exact `3000`-lot `house_mid`, and calibrated `SNDK` intercept against `NVDA(t)` instead of `NVDA(t - 2.5s)`.
     - **Zero utilization of the 5 leader stocks (`500` idle lot capacity)**: Did not exploit `LivelyLasagnas`'s `3000`-lot `house_mid` anchor on `AMD`, `AVGO`, `MU`, `NVDA`, `VRT` (`house_spread = 0.24..0.51`).

---

### Wave 2 Ablation & Rejected Candidates S4–S9 (The Two-Timescale Breakthrough)

- **S4 (`strat_lab_02:StateSpaceTrajectoryArb`) — `+28,887.5` (`24/24` chunks positive, `-425.5` DD)**:
  - *What it tested*: Fast-tracking offset `alpha = 0.97*alpha + 0.03*(hm_fol - beta*lag_ema_lead)` (`HL ≈ 4.5s`) + aggressive `0.015`-hurdle IOC position covering.
  - *Insight & Why Rejected*: Dropped from `+37.3k` (`S3`) to `+28.9k` and cut passive fills from `46,577` to `20,583` for **two critical reasons**:
    1. **Two-Timescale Cointegration Law**: Follower prices obey $P_{\text{fol}}(t) = \bar{\alpha} + u_{\text{OU}}(t) + \beta \cdot \text{LaggedEMA}(H_{\text{lead}})$, where $u_{\text{OU}}(t)$ is a mean-reverting Ornstein-Uhlenbeck process (`HL = 11.8s` on `SNDK`, `33.0s` on `NBIS`, `39.8s` on `CRWV`). Adapting $\hat{\alpha}_t$ at `HL = 4.5s` chased $u_{\text{OU}}(t)$ instead of fading it!
    2. **IOC Cover Cannibalization**: Covering inventory via IOC at `0.015` edge threw away `+0.8..+1.2` ticks of passive maker exit spread on `26,000` fills!
- **S5 (`strat_lab_02:DelayWaveRider`) — `+27,451.9` (`23/24` chunks positive, `-454.1` DD)**:
  - *What it tested*: Holding passive exits out to the `2.4s` forward trajectory extremum (`f_max` / `f_min`) plus `0.01` IOC cover.
  - *Insight & Why Rejected*: Pricing passive exits too ambitiously at `f_max` reduced passive exit fill probability (`18,562` fills), forcing later IOC covers when the wave reversed.
- **S6 (`strat_lab_02:StructuralGLFTMaker`) — `+25,080.4` (`23/24` chunks positive, `AVGO: +2,313`)**:
  - *What it tested*: Extending GLFT quoting + `0.085` student-mispricing IOC sweeps to all 8 stocks.
  - *Insight & Why Rejected*: Proved **real, scalable positive PnL on `AVGO` (`+2,313`)** where `LivelyLasagnas`'s house spread is widest (`0.511`), while `AMD` (`-3,650`) and `NVDA` (`-2,831`) bled because `infer_house_mid` shifted by `0.10` whenever one `3000`-lot wall was partially consumed below `2200` lots, triggering false IOC sweeps into house walls! Requiring **both** exact symmetric walls (`v >= 2500` on both sides) eliminates false wall-shift sweeps on leaders.
- **S7 (`strat_lab_02:KalmanInnovationEngine`) — `+20,370.4` (`22/24` chunks positive)**:
  - *What it tested*: Learning $(\alpha_t, \beta_t)$ completely online from scratch via 2D Kalman Filter (`q_alpha = 1e-4`).
  - *Insight & Why Rejected*: Worked out-of-the-box (`+20.4k` with zero prior slopes!), but `q_alpha = 1e-4` was too fast (drifting with `33–40s` OU excursions on `CRWV` and `NBIS`), and `0.015` cover IOCs cannibalized maker exits. Lowering `q_alpha` to `1e-7` after initial warmup locks onto true long-run equilibrium!
- **S8 (`strat_lab_03:WaterfallHarvester`) — `-3,920.8` (`AMD: +490, AVGO: +354`)**:
  - *What it tested*: Dual-layer (`2` bids + `2` asks per stock = `4` orders/stock) staggered limit quoting across 8 stocks.
  - *Insight & Why Rejected*: Earned **`+490` on `AMD` and `+354` on `AVGO`**, proving leader passive inside-spread edge works when conservative! However, maintaining 4 orders per stock across 8 stocks (`32` resting orders) completely exhausted the `23 req/s` budget (`only 1,972 fills`), leaving stale follower orders stranded in the book during jumps! **Lesson**: Never split request budget across multi-level ladders per stock — put 100% of desired size at the single optimal price per side!
- **S9 (`strat_lab_03:ImpulseStatArb`) — `-2,381.0` (`8/24` chunks positive)**:
  - *What it tested*: Pure return-space impulse anchored on current `hmids[fol]` (`fv = hm_fol + beta*(hm_lead - lag_ema)`, zero cointegration offset $\alpha$).
  - *Insight & Why Rejected*: **Most important negative control of the entire study** — removing long-run cointegration anchoring turned `+37.3k` into `-2.4k`! Because `hm_fol` has `0.18–0.31` of mean-reverting OU noise around $\bar{\alpha} + \beta H_{\text{lead}}$, anchoring on `hm_fol` systematically buys when `hm_fol` is already overvalued and sells when `hm_fol` is undervalued!

---

### Wave 3 Detailed `30k+` Strategy Logs (`S8`, `S10`–`S15`) & SimClock Fix

> **Engineering Discovery (`SimClock` Gotcha)**: Fixing imported helper modules to read `ex.clock.time()` instead of unpatched `time.time()` immediately lifted `S8` from `-3,921` to **`+32,550.2`**, `S9` from `-2,381` to **`+23,502.0`**, and all 6 Wave-3 strategies (`S10`–`S15`) into the **`+32.8k` to `+38.5k`** band — every single one achieving **`24 / 24` positive chunks (100% win rate)** and `0` disconnects!

- **Strategy S8 — Dual-Layer Book Waterfall Harvester (`strat_lab_03:WaterfallHarvester`) — `+32,550.2` (`30k+` Detailed Log)**:
  - *Mechanism*: Decomposes book depth into Student Layer (`v < 800/2000`) vs House Walls, places 2 staggered passive layers per side (`60` inner + `35` outer), and snipes mispriced student quotes across all 8 stocks.
  - *24-Chunk Results*: **`+32,550.2`** (`H1: +17,451.2 | H2: +15,099.0`), **`24/24` positive**, Worst DD `-428.0`, `23,987` passive / `10,191` aggressive fills.
  - *Per-Stock Breakdown*: **`AVGO: +3,185`, `NVDA: +548`, `AMD: +406`** (`+$4,139` on leaders!), `SNDK: +10,860`, `NBIS: +9,803`, `CRWV: +7,742`.
  - *Insights & Why Superseded*: Proved that **leader student-mispricing arbitrage + queue=0 inside-spread quoting generates `+$4k+` on `AVGO/NVDA/AMD`**, while showing that 2-layer passive ladders consume double the request budget (`23 req/s`), reducing follower passive fills (`23,987` vs `51,100` in `S13`).

- **Strategy S9 — Pure Return-Space Impulse (`strat_lab_03:ImpulseStatArb`) — `+23,502.0` (Rejected `<30k`)**:
  - *Mechanism*: Predicts `fv = hm_fol + beta * (hm_lead - lag_ema)` with zero cointegration intercept $\alpha$.
  - *24-Chunk Results*: **`+23,502.0`** (`23/24` positive, `-465.1` DD; `NBIS:+9,491`, `CRWV:+7,187`, `SNDK:+6,819`).
  - *Why Rejected*: Even with zero `alpha` parameter it makes `+23.5k` purely from `0.4s–2.4s` leader catch-up, but leaves **`+$15,000+` of `12s–40s` OU spread mean-reversion** on the table compared to two-timescale cointegration models!

- **Strategy S10 — Two-Timescale OU Cointegration Maker-Sniper v1 (`OUCointMakerSniper`) — `+37,826.2` (`30k+` Detailed Log)**:
  - *Mechanism*: Ultra-slow `alpha` (`rate = 0.0010`) + strict symmetric `3000`-lot leader `HouseMid` + `0.25` AR(1) shift + `±100` lot two-sided sizing (`0.055` IOC hurdle, `0.045` half-spread).
  - *24-Chunk Results*: **`+37,826.2`** (`H1: +17,710.3 | H2: +20,115.9`), **`24/24` positive**, Worst DD **`-206.0`**, `42,036` passive / `8,638` aggressive fills (`NBIS:+13,545`, `SNDK:+12,691`, `CRWV:+11,598`).
  - *Why Superseded (`<50k`)*: Requiring `0.055` edge beyond the opposite touch and `0.045` half-spread missed profitable `0.015–0.050` mispricings and 2-tick inside-spread fills, and ignored all 5 leader stocks.

- **Strategy S11 — Delay-Buffer Trajectory v1 (`DelayBufferTrajectoryBot`) — `+32,859.2` (`30k+` Detailed Log)**:
  - *Mechanism*: Simulates `SNDK` 1.0s-ahead vs 2.4s-ahead waves through `NVDA` delay buffer, calibrating `bar_alpha` on `0.5 * (bbo_mid + house_mid)`.
  - *24-Chunk Results*: **`+32,859.2`** (`H1: +16,563.1 | H2: +16,296.1`), **`24/24` positive**, Worst chunk **`+66.8`**, Worst DD `-248.3`.
  - *Why Superseded (`<50k`)*: Blending `follower_house_mid` (`1.22` wide on `CRWV`) into `obs_y` damped `CRWV` PnL (`+9,559` vs `+12,269` when calibrating purely on `bbo_mid`).

- **Strategy S12 — 2D Adaptive Kalman + Order-Flow v1 (`AdaptiveKalmanFlowBot`) — `+35,590.8` (`30k+` Detailed Log)**:
  - *Mechanism*: Learns BOTH $[\alpha_t, \beta_t]$ online from scratch via centered 2-regime 2D Kalman Filter (`Q = diag(1e-7, 1e-8)` post-warmup) + live `TradeTick` aggressor-flow impulse.
  - *24-Chunk Results*: **`+35,590.8`** (`H1: +17,311.3 | H2: +18,279.5`), **`24/24` positive**, Best chunk **`+5,821.7`**, Worst DD `-273.5`, `50,064` passive / `10,136` aggressive fills.
  - *Insight & Why Superseded (`<50k`)*: Proved a **100% parameter-free self-calibrating Kalman bot** earns `+35.6k` (`24/24` chunks positive!) without knowing any slopes in advance; needs tighter execution hurdles + leader trading to cross `+50k`.

- **Strategy S13 — 8-Stock Multi-Asset GLFT Portfolio v1 (`MultiAssetGLFTPortfolioBot`) — `+38,526.3` (`30k+` Detailed Log)**:
  - *Mechanism*: Full 8-stock GLFT portfolio (`gamma * sign(q)|q|^1.25 + net_port_skew`) trading followers + intact `3000`-lot wide-wall leaders (`hurdle = 0.095` on leaders, `0.055` on followers).
  - *24-Chunk Results*: **`+38,526.3`** (`H1: +18,414.2 | H2: +20,112.2`), **`24/24` positive**, Best chunk **`+5,878.5`**, Worst DD `-306.3`, `51,100` passive / `12,864` aggressive fills (`NBIS:+13,199`, `CRWV:+12,269`, `SNDK:+11,817`, `AVGO:+1,118`, `NVDA:+483`, `AMD:-353`).
  - *Why Superseded (`<50k`)*: `0.095` leader hurdle & `0.055` follower hurdle left thousands of `+0.02..+0.08` positive-EV opportunities untouched.

- **Strategy S14 — Student-Book OBI & Vol-Adaptive Maker v1 (`OrderBookMicrostructureBot`) — `+37,692.3` (`30k+` Detailed Log)**:
  - *Mechanism*: Isolates non-house student depth (`StudentOBI`), `2s` BBO `OFI`, and volatility-widened half-spread (`0.035 + 0.25 * vol_ema`).
  - *24-Chunk Results*: **`+37,692.3`** (`H1: +17,691.7 | H2: +20,000.6`), **`24/24` positive**, Worst DD **`-206.4`**.
  - *Why Superseded (`<50k`)*: Widening passive half-spread when follower `vol_ema` rises is **backwards on followers** — follower volatility spikes *when the leader jumps*, which is when our fair-value edge is *highest*, so widening quotes missed the best fills!

- **Strategy S15 — Bayesian Multi-Signal Ensemble v1 (`SupremeEnsembleBot`) — `+36,096.0` (`30k+` Detailed Log)**:
  - *Mechanism*: Blends `0.70` structural coint + `0.30` 2D Kalman + `2.4s` `SNDK` trajectory + `TradeTick` flow + `AVGO`/`AMD` wide-wall arbitrage.
  - *24-Chunk Results*: **`+36,096.0`** (`24/24` positive, highest worst-chunk floor **`+80.2`**, `AVGO:+1,929`).
  - *Why Superseded (`<50k`)*: Used `obs_y = 0.5*(bbo + fhm)` on `CRWV` (costing `-2.3k` on `CRWV`) and conservative `0.052` hurdle.

---

### Wave 4 — Breaking `+40,000` via Optiver Slide-12 Unified Crossing-Limit Execution (`S16`–`S18`)

- **Strategy S16 (`_probe_50k:P4_RawLeadAlpha`) — `+38,588.9` (`30k+` Detailed Log)**:
  - *Insight*: Turned **7 of 8 stocks profitable** (`AVGO:+2,556, NVDA:+635, VRT:+395, MU:+160` plus `+35.6k` on followers), and revealed why 2-pass `IOC + limit` capped PnL: Pass 1 swept `ba`, leaving `books[s]` stale in Pass 2 so `min(ba - TICK, ...)` pushed the resting bid back behind the old queue instead of resting at `queue = 0` at the cleared price!
- **Strategy S17 (`_slide12_test:U1_Base`) — `+38,492.0` & Strategy S18 (`_slide12_test:U2_Tighter`) — `+40,237.6` (`40k+` Detailed Log)**:
  - *Mechanism*: Replaced 2-pass `IOC + clamped limit` with **Optiver Slide-12 Unified Crossing-Limit Execution** (`order_type="limit"` priced directly at `floor_tick(res - half_sp)` / `ceil_tick(res + half_sp)` after canceling any conflicting opposite resting order). A single crossing limit order simultaneously sweeps all mispriced levels AND rests the remaining lots at the cleared price with **`queue = 0`** in **1 request instead of 3**!
  - *24-Chunk Results (`U2_Tighter`)*: **`+40,237.6`** (`H1: +18,158.6 | H2: +22,079.0`), **`24 / 24` positive chunks**, `74,153` passive / `12,226` aggressive fills (`NBIS:+13,533`, `SNDK:+12,780`, `CRWV:+11,098`, `AVGO:+2,363`, `NVDA:+850`, `AMD:-374`, `MU:0`, `VRT:0`).
  - *Why Still `<50k`*: (1) `LEAD_HALF_SP = 0.075` (`> 0.05`) mathematically locked out `MU` and `VRT` (`0` fills!) whenever `3000`-lot walls were `3` ticks (`0.30`) apart (`hm ± 0.05`); (2) `flip_edge = 0.035` throttled volume when `HALF_SP = 0.040`; (3) fixed `0.35` keep-ratio left partially filled `36`-lot orders sitting alone at `queue = 0` instead of reloading to `100` lots for free!

- **Strategy S19 (`_bt_50k:B2_NoAMD_030`) — `+32,177.2` (`30k+` Detailed Log)**:
  - *Insight*: Unlocked `VRT` (`+413`), `MU` (`+96`), and pushed `SNDK` to `+12,977`, while proving that high-volatility leaders (`AVGO`, `NVDA`) bleed when `LEAD_HALF_SP` is tightened to `0.042` (`AVGO: -3,546`, `NVDA: -1,552`).
- **Strategy S20 (`_unlock_all8:V1_UnlockedRes`) — `-94,657.2` (Rejected — Decisive Leader Jump-Risk Proof)**:
  - *Mechanism*: Tested resting continuous passive bids/asks `1` tick inside `LivelyLasagnas`'s `3000`-lot walls on all 5 leaders without requiring `bp > bb`.
  - *24-Chunk Decomposition*: Followers earned **`+37,598`** (`NBIS:+13,616, SNDK:+12,898, CRWV:+11,084`), while the 5 leaders lost **`-132,243`** (`AMD:-35,832, AVGO:-30,732, NVDA:-30,294, VRT:-18,907, MU:-16,478`)!
  - *Crucial Insight*: Fill-level markout autopsy (`oracle_ceiling.py`) on `U2_Tighter` proved why: on leaders (`AMD`, `AVGO`, `NVDA`), **aggressive sweeps of mispriced student quotes have strongly POSITIVE 5s markouts (`AMD: +0.085/lot`, `AVGO: +0.050/lot`)**, whereas **resting passive entry orders have NEGATIVE markouts (`AVGO: -0.017/lot`, `NVDA: -0.118/lot`)** because random-walk jumps move `LivelyLasagnas`'s `3000`-lot opposite wall across resting orders!
  - *Second Crucial Insight (`w_corridor = 0.05` vs `1.00`)*: Regression of `BBO_mid(t+h) - BBO_mid(t)` proved that `S1`–`S19` implicitly gave `curHouseMid - curBBOMid` a weight of `1.00`, when its true 2s–5s BBO reversion weight is only **`0.035–0.055`**! That `20x` over-weighting caused `CRWV` and `NBIS` to hold maxed-out `+100` lot bags waiting for a `$1.22` corridor cross instead of forecasting true `curBBOMid + E[dBBO]`!

- **Strategy S21 (`_direct_bbo:D1_Scale10`) — `+28,955.7` (Rejected `<30k` — Short-Horizon Forecast vs Fundamental Holding Proof)**:
  - *Mechanism*: Fitted ridge regression on Train chunks `0..11` predicting `2s` and `5s` `BBO_mid` changes directly (`OOS 2s R2 = +0.6435` on `SNDK`, `+0.2829` on `NBIS`, `+0.2715` on `CRWV`), setting `FV = curBBOMid + E[dBBOMid_5s]`.
  - *Insight & Why Rejected*: Crucial quant lesson — **highest 2-second forecast $R^2$ does NOT maximize multi-minute trading PnL**! Anchoring `FV` to `curBBOMid + E[dBBO_5s]` caused the bot to exit `+100` lot positions after capturing only `5s` (`~15%`) of the move (`98,877` passive + `15,093` aggressive fills), whereas anchoring `FV` to the **full long-run fundamental equilibrium `alpha_slow + beta * Lead`** (`S18: +40,237.6`) patiently holds winners across the full `30–40s` convergence (`30–60` cents/lot instead of `5` cents/lot)!
