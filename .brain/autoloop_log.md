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

### Cycle 2 (03:02) — PROMOTED (candidate pnl -21.2 beat baseline -44.8 (dd=-21.6))
- **Diagnosis**: NVDA and AVGO lost 31.7 of the net 44.8 replay loss on only 50 of 392 fills. Both ended flat. The replay lost in both halves (-32.1/-12.7), and live PnL fell 46.1 with only three lots left open. The problem is not primarily the closing inventory. Of 392 replay fills, 379 were passive; that identifies the main entry path to investigate, not the share of loss caused by passive orders. The Champion already limits an unsupported flat quote to one lot. Its remaining weakness is that a quote exactly one tick ahead of a qualifying wall can receive full size immediately, without evidence that fills on that side pay. The own-fill breaker needs six matured observations, pools buys with sells and entries with exits, and cannot learn from a quote it withholds. The scratch logs show costly exposures were possible, but they record accepted IOC submissions, not matched fills or proof that the entry wall disappeared. Likewise, the supplied aggregates do not identify a toxic named counterparty, a missed news jump, or losses specifically attributable to edge IOCs. Live markouts are latest EWMAs in ticks, not the replay's whole-window means in price units. These distinctions matter particularly for AVGO and CRWV, where 5s markout and realized PnL tell different stories.
- **Insight**: NVDA and AVGO accounted for 71% of the net replay loss on 13% of fills, both ending flat. The existing wall check qualifies geometry, not the expected return on a five-lot fill.
- **Insight**: CRWV made +1.5 with negative 5s replay markout, while AVGO lost 13.8 with positive 5s replay markout. Use markout to screen entry risk, but use size-weighted executions and completed inventory cycles to judge profit.

### Cycle 3 (03:17) — REJECTED (candidate pnl +0.0 did not beat baseline +1.0 (+0.5))
- **Diagnosis**: NBIS and CRWV earned +6.6 on 488 of 530 replay fills; AVGO, MU and NVDA lost 5.8 on only 20. All eight ended flat. The issue is what happens between opening and closing inventory, not an unfavorable final mark on a residual position. Live improved by 6.4 with a 2.9 drawdown, but the replay made only +1.0 (+1.1/-0.1 by half). Replay markouts above are in price units; live figures are the last 5s EWMAs in ticks, not whole-chunk averages. One execution asymmetry is directly visible in the source: a flat bid is tested against bid-side entry evidence, yet accepting it commits us to finding a buyer for the subsequent sell. The reverse holds for a flat ask. A same-side wall can limit entry size but cannot supply that opposite-side flow. Three short tail-stop submissions are consistent with costly covers; without joined fill and order histories they do not prove which entry qualified, that the wall vanished, or that a particular aggressor caused the loss. There are no name-conditioned results or timestamped news events here that justify labeling any house bot toxic or blaming news latency. Muster found no stable signal that paid the spread, so there is no case for increasing directional IOCs.
- **Insight**: NBIS and CRWV produced +6.6 on 92.1% of replay fills; AVGO, MU and NVDA gave back 5.8 on 3.8%. Retained profitable round trips matter more than a higher average markout achieved by barely trading.
- **Insight**: Every logged tail-stop submission was a buy to cover a one-lot short. The actionable question is whether seller-initiated flow was available at a viable passive cover price before entry; total trade count and ask-wall size do not answer it.

### Cycle 4 (03:32) — REJECTED_TESTS
- **Diagnosis**: MU and NVDA lost 19.6 on 34 of 1,014 replay fills; the other six stocks together made about 10.2. NBIS alone earned as much as the entire portfolio lost. All eight ended flat, so the replay loss is not an artifact of marking a large terminal position. The nine logged stop submissions were one lot each, six in MU or NVDA. They identify expensive inventory episodes, not the exact PnL attributable to IOC executions: a submitted limit price is not a fill price. Live PnL fell 1.9, with a 14.8 drawdown. Live markouts are clipped, recent EWMAs in ticks, whereas the replay table reports horizon averages in price units; neither measures the money retained after unwinding a larger order. The execution code gives an exact reason the bot cannot exploit a good market at scale: a flat quote without the specified wall geometry is capped at one lot before the existing evidence check, regardless of how profitably the stock has traded. Conversely, wall geometry alone does not establish that an exit is available. Muster found no stable signal that paid for crossing. With anonymous trades and no news, there is no evidence for attributing the losses to a particular house bot or a missed headline.
- **Insight**: MU and NVDA erased 19.6 on 3.4% of replay fills. The loss was already present at 1s, and this cycle's stop submissions cover both long and short inventory. Track realized loss per matched lot, not just stop count or final position.
- **Insight**: NBIS earned +9.4 on 472 fills in the narrowest observed trade range (2.20); NVDA lost 12.7 in the widest (8.90). For passive sizing, repeated profitable exits at reachable prices are better evidence than the size of the swing.
