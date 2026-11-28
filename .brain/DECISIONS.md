# Decisions

> Lightweight Architecture Decision Record (ADR) log for **start-hack-titans**.
> Append a short entry only when it saves teammates >15 minutes or settles a real trade-off.

## Template

```markdown
### [HH:MM] <Decision Title> (by <aanjneya|slava>)
- **Context**: Why this decision was needed.
- **Decision**: What we chose.
- **Trade-offs**: What we gave up or need to watch out for.
```

---

## Log

### Initial Setup
- **Context**: 2-person hackathon team (`aanjneya`, `slava`) collaborating with multiple coding agents.
- **Decision**: Use `.brain/` pushed automatically to `main` via `scripts/brain-push.sh` (temporary worktree) with 2-tier context loading, `.brain/TASKS.md` lightweight ownership, `API_CONTRACT.ts`, and `MOCK_MODE=true`.
- **Trade-offs**: Dropping lock files relies on `.brain/TASKS.md` + feature-folder isolation + table communication instead of hard locks.

### Pre-Kickoff Infrastructure Hardening (by aanjneya)
- **Context**: SWOT review identified silent-clobber risk in `brain-push.sh`, prompt-only guardrails, Windows symlink risk, doc tax, and Hour-18 branch divergence risk.
- **Decision**:
  1. Upgraded `scripts/brain-push.sh` to 3-way `git merge-file` + safe post-push local snapshot restore + auto-ID detection (`git config hackathon.teammate`).
  2. Added `.githooks/pre-push` (fast-passes `.brain/` & `challenge/`, verifies code on `main`, blocks `.env` & >25MB, has `ALLOW_MAIN_PUSH=1` escape hatch), stack-agnostic `scripts/verify.sh`, and `scripts/setup.sh`.
  3. Replaced `CLAUDE.md` symlink with `@AGENTS.md`, added `.claude/settings.json` allowlist for zero-intervention brain sync, collapsed live docs to 3-file core + 3h staleness guard, and added 2–3h rolling merge cadence.
- **Trade-offs**: `.githooks/pre-push` requires each teammate to run `./scripts/setup.sh <id>` once; Antigravity/Codex users click "Always allow `./scripts/brain-push.sh`" once for zero-prompt auto-sync.

### [23:26] Pivot to ABMM-S (Multi-Asset GLFT + Counterparty/Wall Alpha) (by aanjneya)
- **Context**: `HistoricalDataLoader` is disabled, final is a 30-min sprint with tighter spreads, and Optiver judges EV/risk/novelty alongside PnL. Price-level Kalman pairs pay 2-leg taker spreads and short into permanent news jumps.
- **Decision**: Adopt **ABMM-S**: (1) unified $\hat{F}_{i,t}$ from Microprice+OFI, `V>250` house walls, online `TradeTick` counterparty markouts, return-space lead-lag, and `<1ms` news sniper; (2) 8×8 matrix GLFT reservation price $\mathbf{r} = \hat{\mathbf{F}} - \gamma(t)\Sigma\mathbf{q}$ for passive cross-hedging + endgame flatten; (3) queue-preserving per-side quoting.
- **Trade-offs**: Signal weights must be validated empirically via `collector.py` + `muster.py` on live practice data before locking Sunday 10:00 params.

### [01:08] Strip Wall-Maker Override & Run Pure ABMM-S in Normal 2-Day Mode (by aanjneya)
- **Context**: User explicitly directed running **Pure ABMM-S** (Fair-Value-centered multi-asset GLFT market making + edge IOC sniping) in normal continuous mode for 2 days without wall-only entry lockout, 15s wall-scratch churn, or endgame timer.
- **Decision**: Removed `require_wall_for_entry`, wall-pinned pricing (`bw + tick`), and 15s forced churn from `ABMMSAlphaEngine`, while keeping critical engineering hygiene (`strip_own_orders`, floored `glft_skew` scale, volume-capped Microprice/OFI, exact per-side queue preservation, portfolio net-delta cap `±120`, 10-tick tail stop, 5s own-fill markout breaker, and `$500` session drawdown halt). `SESSION_DURATION_SEC=0` by default.
- **Trade-offs**: Pure Fair-Value quoting trades continuously across all 8 stocks; live 5s markouts (`mk5s=`) and `bot/backtest.py --strategy runner:BacktestBot` must be monitored to tune signal weights (`w_micro`, `w_ofi`, `w_wall`, `w_leadlag`) and `glft_gamma`.
