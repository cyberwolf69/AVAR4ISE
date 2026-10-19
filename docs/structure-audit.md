# Structure candidate audit — October 19, 2026

This offline audit read the latest 2,000 rows from `scans.sqlite3` without modifying it. Of these, 1,377 observations contained structure-model results and were comparable; 623 lacked the model marker, including errors before structure detection and other model results. Comparable observations span **13:45–19:05 WIB**. Repeated candle snapshots are not independent samples.

| Model | Scanner-eligible observations | Unique BOS setups |
|---|---:|---:|
| Active HH/HL or LH/LL definition | 0 | 0 |
| Confirmed 1H swing close-break direction hypothesis | 23 | 6 |

The alternative sets direction when a close crosses a 1H pivot available before that candle. Direction persists until the protective swing breaks; an opposite break can reverse it. Each pivot is consumed once. This is a research hypothesis, not proof of institutional activity. Both models share 15m BOS/retest detection, the 0.75 ATR entry-distance limit, structure stops, spread/freshness checks, and scanner sizing checks.

First alternative candidate per event:

| Pair | Direction | First observation (WIB) |
|---|---|---|
| HYPE | LONG | 14:00 |
| FIL | LONG | 14:00 |
| XRP | LONG | 14:45 |
| UNI | LONG | 14:47 |
| BTC | LONG | 16:48 |
| LIT | SHORT | 16:48 |

The active model rejected 1,126 observations at its 1H structure definition. The alternative rejected 842 because the 15m BOS had expired. Changing direction recognition does not automatically produce entries for every pair.

## Interpretation limits

This is a candidate-availability audit, **not a PnL backtest**. It makes no AI calls, historical account/slot checks, fill simulations, notifications, or orders. The six candidates are not established executable or winning trades. Scanner sizing is not the complete portfolio risk gate. Parameters were not selected using profit from this dataset.

The active structure definition remains unchanged. The proposed next step was forward comparison without execution, followed by outcome evaluation with costs and correct TP/SL sequencing before proposing activation. Candidate count alone does not justify adoption.

## Reproducing the audit

```sh
python3 -B -m hedging_tools.structure_audit --limit 2000
```

The command reads the local database and writes `runtime/structure-audit.json`. Results can change as new rows arrive. The report records each event's first scan ID, time, levels, size, rejection reasons, and model transitions. The source database is unchanged. This standalone research module is not imported by the trading runner.
