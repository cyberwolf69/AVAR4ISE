# Structure comparison and market context

This feature uses market-regime context and forward hypothesis evaluation. It does not install QuantGPT, REA, AgenKit, or a new model provider.

## Running

```sh
python3 main.py run
python3 main.py shadow
```

The first command starts the normal supervisor with a separate comparison worker. The second reads the local report. `python3 main.py shadow --update` performs one update, consuming fresh local snapshots and fetching public mark candles for tracked outcomes. The comparison requires no trading credentials.

Restart an older `run` process to enable the worker. The active structure remains HH/HL or LH/LL with BOS/retest. AI packets include regime context, which can influence AI judgment, while structural gates and risk limits remain unchanged.

## Models

- **baseline:** the active structure definition.
- **break_bias:** direction from a confirmed 1H pivot close-break, retained until invalidation; the hypothesis from the earlier audit.

Both use the same snapshots, re-evaluated at worker observation time, and the same retest, spread, and risk-sizing requirements. Stale data is rejected. Snapshots older than 180 seconds are not backfilled. Quotes have stricter scanner freshness limits, so worker delays can reduce samples; rejection reasons are recorded.

On first activation, the cursor starts at the latest scan ID. Older snapshots are not treated as forward samples. The worker checks again five seconds after the preceding process completes, plus supervisor polling delay. Outcome polling is throttled to once per 60 seconds with a persistent cursor. Each update handles at most 200 scans and rotates outcome updates across at most five pairs per model. The worker has a 120-second timeout and runs separately from position management.

The comparison makes no AI calls, orders, or Telegram/Discord deliveries. Deterministic candidates are not labeled AI recommendations. Records live in `runtime/shadow/<experiment>/`, separate from trades, public signals, and improve mode. A BOS is registered once per pair/direction/model, including across restarts. New code/configuration versions start separate experiments.

## Market context

`metrics.regime` for 15m and 1H uses closed candles:

- 20-candle efficiency = absolute net displacement / total absolute close changes. Values ≥0.4 are DIRECTIONAL, ≤0.2 CHOPPY, otherwise MIXED.
- 20-candle close direction: UP, DOWN, or FLAT.
- Mean true range over 14 candles / mean over the preceding 28 candles: ≥1.5 EXPANDING, ≤0.75 CONTRACTING, otherwise NORMAL. A zero baseline yields UNKNOWN.

These initial descriptive thresholds are hypotheses, not probabilities, certain forecasts, or mandatory filters. AI considers conflicts with the setup without having to reject solely on a label. Improve snapshots retain this context for later evaluation.

## Reading results

`runtime/shadow-output.json` reports observations, rejection reasons, candidate counts, TP1/2/3, SL, protected stops, expiry, timeout, and incomplete data. Outcomes represent **reference price touches**, not executed fills or trade PnL.

Tracking starts with the next full minute after registration. Unknown entry/TP/SL sequences within a candle are AMBIGUOUS; data gaps are not successes or failures. The engine uses one-minute mark candles, not execution tick reconstruction.

**Fees, slippage, funding, and net PnL are not simulated.** Candidate/TP counts do not produce a win rate or automatically activate a model. Alternative AI judgment and account slot availability are not evaluated. Execution backtesting after costs remains a separate step before adoption.
