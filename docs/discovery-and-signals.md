# Dynamic scanner and signal history

`python3 main.py run` enables both features automatically. Trading capacity remains **five positions**, one per pair, under the existing margin and risk budget. Positions remain managed even when their pairs leave the scanner universe.

## Selecting up to 20 pairs

Each scan fetches fresh public OKX instruments and tickers. Requirements:

- Category `1` crypto SWAP, linear, USDT settlement, live status.
- Valid contract sizing metadata and instrument leverage supporting at least 10x.
- Estimated 24-hour turnover of at least **10 million USDT**.
- Bid/ask spread at most **0.1%**, with a ticker timestamp no older than 30 seconds.

Estimated turnover is `volCcy24h × last`. For OKX derivatives, `volCcy24h` is the base-asset quantity, not contract count or USDT value. This is a last-price estimate, not exact historical notional turnover.

Ranking weights are 60% relative liquidity, 20% spread quality, and 20% liquidity-weighted price-range activity. Activity is capped at a 10% range so extreme spikes do not increase scores indefinitely. Scores are not win probabilities. The top 20 enter candle screening. Failed/empty discovery does not reuse a stale universe to open positions.

Snapshot fetching uses up to four workers. Portfolio research permits at most three AI reviews per scan, rotating toward pairs least recently reviewed with rank as the tie-breaker. A persistent cursor prevents the highest-ranked pairs from consuming every review. Other candidates receive `AI_CALL_BUDGET_REACHED`. Snapshots older than three minutes before review are skipped. Public requests contain no account keys. Failure for one pair does not cancel other valid candidates; execution risk checks and revalidation remain mandatory.

Known token names help headline searches; new/ambiguous tickers require a cashtag `$TOKEN` or symbol `TOKENUSDT`/`TOKEN/USDT`. BTC headlines are not automatically evidence for every altcoin. News is optional for the active technical structure model; missing headlines and failed feeds remain explicit context. More pairs do not guarantee more trades.

```sh
# Read-only public discovery; no account credentials
python3 main.py universe
# Research and alerts only; no orders
python3 main.py research --ai --portfolio
```

Latest ranking: `runtime/universe-output.json`.

## Tracking model

Every validated LONG/SHORT proposal is recorded, including when alerts are disabled, slots are full, or risk blocks execution. WAIT is not a signal. Identity uses pair + direction + 15-minute bucket, matching alert deduplication. The first snapshot is retained rather than overwritten with later prices. Tracking begins when the proposal is accepted, not when its market snapshot was created. Recording does not confirm channel delivery.

1. Use **closed one-minute mark-price candles**, starting with the first complete minute after signal registration. Skip candles spanning registration so earlier price movement cannot affect outcomes. Movement in the remaining fraction of the first minute is not modeled.
2. Wait for the reference entry price to be touched before proposal expiry, usually 15 minutes after packet creation. This is a reference-touch assumption, not a simulation of the executor's FOK fill.
3. Once touched, observe for at most 24 hours. TP1/TP2/TP3 and SL use reference levels. After TP1, the modeled stop includes a 0.2% cost buffer; after TP2 it protects at least 1R. This tracks price levels, not net PnL.
4. Mark unprovable intrabar sequences `AMBIGUOUS`: entry and exit, TP and SL, or TP and a newly adjusted stop in the same candle. These are not definite wins or losses.

| Status | Meaning |
|---|---|
| PENDING | Waiting for reference entry touch |
| ACTIVE | Entry touched; TP1/TP2 recorded as milestones |
| TP3 | All reference targets reached without detected ambiguity |
| SL | Initial reference stop touched |
| PROTECTED_STOP | Stop following TP1/TP2 touched |
| EXPIRED | No entry before expiry on the observed timeline |
| TIMEOUT | 24-hour observation ended without another terminal result |
| AMBIGUOUS | Intrabar sequence unknown |

Missing/unavailable data is marked `DATA_GAP`/`DATA_ERROR`, not expired or successful. SQLite cursors resume unprocessed candles after restart. Each update reads at most one 100-minute window per pair, for up to 20 pairs; extended downtime backlogs are processed gradually. Tracking continues when pairs leave the latest universe.

A separate worker updates approximately every 60 seconds with a 120-second timeout; API latency can extend intervals. It continues during full slots or daily halt. Ctrl+C stops it with the supervisor, and restart resumes history. The tracker does not publish TP/SL updates or edit cards.

```sh
# Local summary without network requests
python3 main.py signals
# Per-signal milestones
python3 main.py signals --details
# Manual public-data update without orders or alerts
python3 main.py signals --update
```

Ledger: `runtime/signal-history.sqlite3`. Report: `runtime/signal-history-output.json`. This ledger is **separate from `portfolio.sqlite3`**. Target touches are not profitable trade counts. The tracker does not calculate actual fills, fees, funding, win rate, or PnL. Signals predating activation are not reconstructed with invented publication times.

Market fields follow [OKX documentation](https://www.okx.com/docs-v5/en/), particularly instruments, tickers, and historical mark-price candles.
