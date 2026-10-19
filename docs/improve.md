# Improve mode: evaluate before changing

A separate local worker runs with `python3 main.py run`. Approximately every 60 seconds, it reads recent history and reviews initial stops, stops after TP, protective exits, and losing positions. It does not interrupt supervision or issue AI calls, orders, channel messages, or strategy changes.

```sh
# Review available evidence
python3 main.py improve
# Evaluate candidate entry filters on local history
python3 main.py improve --evaluate
# Include case-level evidence
python3 main.py improve --evaluate --details
```

## Recorded evidence

New entries save strategy version, indicator snapshots, reference prices, decision quotes, AI rationale/invalidation, news source IDs, risk budget, and active positions. Confirmed stop changes are recorded during management. Reconciliation retains entry/exit fill IDs, TP/SL roles, quantities, prices, fees, order PnL, timestamps, and attributable funding bills.

Primary sources are `portfolio.sqlite3` and `signal-history.sqlite3`, opened read-only. The report is `runtime/improve-output.json`; review/proposal history is `runtime/improve.sqlite3`. Evidence hashes identify revisions and prevent duplicate reviews of unchanged cases. Missing historical evidence is marked insufficient/unknown rather than fabricated.

## Interpreting reviews

- `SL`: a reconciled fill belongs to the position's stop algo.
- `PROTECTED_STOP`: a stop after partial TP; not necessarily a losing trade.
- `PROTECTIVE_EXIT`: closure caused by protection problems, such as a missing SL.
- `UNKNOWN`: insufficient evidence to classify a loss/closure as SL.

Wide spreads, low relative volume, and old snapshots are **observations**, not established causes of loss. Without causal evidence, the conclusion is `CAUSE_NOT_ESTABLISHED`. Losses exceeding budget are flagged. A single SL does not prove the strategy is wrong.

**Actual demo trades** and **reference signals** remain separate. Evaluation uses actual order PnL after fees, **excluding funding**; recorded funding is shown separately. Reference signals receive no invented PnL. Ambiguous, incomplete, expired, and timed-out signals are excluded from terminal-outcome tests. This can introduce selection bias, so results do not represent all signals.

## Candidate evaluation

The initial version tests three predefined entry filters:

1. RVOL at least 1.2.
2. Spread at most 0.05%.
3. Snapshot age at entry decision at most 60 seconds.

This replays **take/skip entry decisions** against observed historical outcomes. It is not a new fill simulation or a stop-change backtest. TP/SL, size, leverage, and risk are not optimized. Position interactions, capital reuse, and opportunities made possible by freed slots are not modeled.

Data is split by source and strategy version. The first two thirds form training and the final third forms holdout. Training outcomes still open at holdout start are purged to avoid future leakage. Minimum requirements are 20 training cases, ten holdout cases, and five initial SLs in training. Insufficient samples yield `INSUFFICIENT_DATA`.

A filter must retain at least 70% of samples and 80% of positive/TP3 outcomes, reject at least two cases, and improve the training metric. Only the training winner is tested on holdout. A failed holdout does not trigger selection of another candidate using the same holdout results.

For actual trades, the metric is summed order PnL after fees and before funding. For references, it is initial SL count with TP3 retention. These are not combined into a single score. Repeated evaluation on growing data remains exploratory; fresh forward testing is required before relying on a change.

## Review and versioning

Passing proposals receive `NEEDS_USER_REVIEW`, with proposal ID, dataset hash, training/holdout sample IDs, proposed rule, and comparison results. `HOLDOUT_FAILED` and `NO_SUPPORTED_CHANGE` do not support promotion.

**This version has no automatic application path or approve command.** Discuss the proposal ID first. Code/configuration changes require separate approval of a concrete change; version migration and rollback are prepared with that change, not silently applied by the worker.

Hard limits remain five positions, 5 USDT margin, 10–30x leverage, 1 USDT entry risk, and 10% daily loss. Stops are not widened to recover losses, and reviews do not reset daily halt.
