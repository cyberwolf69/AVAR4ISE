# Market structure setup v1

The default scanner is `structure_bos_retest_v1`, an independent BOS/retest implementation, not a port of LuxAlgo code or a replica of TradingView results. Order blocks and FVGs are not active detectors. Concept references: [LuxAlgo SMC](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo/) and [Order Block Detector](https://www.tradingview.com/script/KvGhxGxY-Order-Block-Detector-LuxAlgo/). The LuxAlgo backtester is an evaluation reference, not an integration.

## Testable rules

- All candles are closed. Swings require two candles on either side and strictly more extreme highs/lows. A pivot becomes available only after its second right-hand candle closes.
- 1H structure: the latest two swing highs and lows must both rise for a long or both fall for a short. Price must not have broken the latest invalidation swing.
- 15m BOS: a close crosses a swing in the trend direction, using a pivot confirmed before the breakout candle. Each pivot generates at most one BOS.
- Retest occurs 1–8 candles after BOS. The latest candle must touch the level ±0.25 ATR, close on the setup side, and move in the setup direction relative to the previous close.
- A close more than 0.25 ATR beyond the wrong side of the level after breakout invalidates that retest.
- The entry quote must remain on the correct side and within 0.75 ATR of the level.
- SL lies beyond the post-breakout sequence's low/high, with a 0.2 ATR buffer. Position sizing must pass existing margin/risk checks. Stops are not tightened to force eligibility.

These initial parameters are not proven profitable. EMA and RVOL provide AI context rather than mandatory gates for the structure model. Spread, freshness, lot, leverage, and risk requirements still apply. The portfolio permits at most five positions; legacy paper configuration has separate limits.

## AI, news, and logs

Technical candidates may be reviewed without news. Packets retain `news` and `feed_health` so the AI can distinguish failed feeds from no relevant headlines. It must not interpret missing news as evidence of safety, invent sources, reverse the candidate direction, or change risk. The three-review AI budget per cycle and pair rotation remain active. AI rationale and invalidation text are requested in English.

`SCAN_COMPLETE.decisions[].screen_reasons` explains rejected setups. Structure evidence appears in `metrics.structure` within `runtime/research-output.json`, including pivot origin, confirmation candle, BOS, and retest confirmation. These timestamps identify candle opening times; evidence becomes available only after the corresponding candle closes.

Improve mode stores evidence and strategy versions separately. It evaluates entry filters rather than a complete structure-strategy backtest. Demo testing is still required to assess actual outcomes.

Stop the older process before running:

```sh
python3 main.py run
```

Restarting does not close positions. Existing positions retain their saved plans; scanner changes apply to new candidates.
