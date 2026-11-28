# Autoloop Optimization Log

> Live record of every autonomous tuning cycle: LLM diagnoses, edits applied, test results, and promotions to live.
> This is the core audit trail for the 5-hour autoloop. Human reviews outcomes in `.brain/INSIGHTS.md` and final config on personal branch.

## Format

Each cycle appends:
```
### Cycle N (HH:MM) — OUTCOME
- **Diagnosis**: Root cause identified by LLM
- **Insight**: Key learnings for team (high-signal only)
- [git commit hash if promoted]
```

## Outcomes

- **NO_CHANGE**: LLM found no justified edits; Champion config still best.
- **EDIT_ERROR**: LLM-proposed edits failed syntax/validation checks; reverted.
- **REJECTED_TESTS**: Candidate passed compile but failed unit tests; reverted.
- **REJECTED**: Candidate compiled + tested but lost to baseline backtest; reverted.
- **PROMOTED**: Candidate beat baseline, now live. Code committed to personal branch + telemetry monitored.
- **ROLLBACK**: Promoted version bled too much in live trading; auto-reverted to previous champion.

---

(Cycles appended below)

### Cycle 1 (02:23) — PROMOTED (candidate pnl -61.2 beat baseline -63.2 (dd=-75.4))
- **Diagnosis**: Live PnL fell 135.3 despite ending with only five gross lots. NVDA and VRT finished flat in replay but lost 51.0 combined; both also had negative live 5s markout EWMAs (−2.29 and −4.17 ticks). Repeated tail-stop IOCs suggest inventory became costly before it was flattened, and the current passive exit calculation does not ensure an exit quote competes at the touch.
- **Insight**: Test a passive exit one tick inside a wide spread, capped at the inventory remaining. This gives the bot a chance to exit before a stop without turning a one-lot exit into a four-lot position in the opposite direction.
- **Insight**: Compare tail-stop count and volume, drawdown, and 5s markouts against the Champion on an untouched chunk. Muster found no signal that covered the spread, so leave aggressive entries unchanged for this test.

### Cycle 1 (02:47) — PROMOTED (candidate pnl -39.6 beat baseline -145.7 (dd=-41.6))
- **Diagnosis**: The main problem is taking full entry size before there is evidence the quote can be unwound. AMD and AVGO accounted for 93.5 of the 145.7 replay loss on only 17 of 131 fills, and both finished flat. The replay lost in both halves (−34.5, then −111.2); live PnL fell 160.1 and ended with only one short NBIS lot. Replay markouts are reported in price units; live readings are the latest 5s EWMAs in ticks, not whole-window averages. Their signs agree on the two largest losers. Of 131 replay fills, 125 were passive; the six aggressive fills and six logged stop submissions do not establish which orders filled or how much PnL each route contributed. The active code reveals a specific mismatch: wall depth shifts fair value when walls exist on both sides, but the passive entry path never calls find_protecting_wall. A flat instrument can therefore quote five lots with no nearby depth on the side it would need to sell into or buy back from. The own-fill breaker cannot protect the first fills: it waits for six matured 5s observations. The prior cycle improved passive exits; it did not address this initial exposure. No counterparty-conditioned markouts, quote ages, or timestamp-matched news outcomes were supplied, so attributing these losses to a named bot, stale quotes, or news latency would be speculation.
- **Insight**: AMD and AVGO generated 64% of the replay loss on 13% of fills, with adverse markouts already present at 1s. Entry selection deserves a separate test from the previous passive-exit change.
- **Insight**: The active strategy uses two-sided walls to shift fair value, not to qualify entry size. The logs describe wall-backed quoting, but the execution path permits full-size passive entries with no same-side wall.
