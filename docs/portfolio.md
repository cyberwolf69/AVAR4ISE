# OKX Demo portfolio

`python3 main.py run` continues until Ctrl+C and supports at most **five active positions**, one per pair. The scanner dynamically selects up to 20 crypto USDT perpetuals; see [discovery and tracking](discovery-and-signals.md). Instruments must be available/live in demo, pass screening, and receive a valid AI proposal. News is optional for technical setups. Five slots are a capacity limit, not a target to force.

## Position rules

- Maximum margin 5 USDT, leverage 10–30x, and entry risk 1 USDT.
- TP1/TP2/TP3 at 1R/2R/3R, initially allocated 40%/35%/25%.
- Quantities are rounded to exchange lots; rounding remainder goes to TP3. Entry is rejected if three valid minimum-size orders cannot be formed.
- Native TP and SL attach to the FOK entry using mark-price triggers. TP prices use the entry limit price and initial SL before submission; better fills can change actual R. Alerts use snapshot reference prices.
- After a confirmed TP1 fill, SL moves to actual entry ±0.2% cost buffer. After TP2, it moves to at least 1R profit. The buffer does not guarantee net breakeven when slippage, fees, or funding exceed estimates.
- If price has crossed the new stop by the time supervision checks it, the agent submits a reduce-only market exit. Stops are never loosened.
- Partial TP1/TP2 exits still occupy a slot. A slot becomes free only when the entire position closes, fills/fees reconcile, and remaining position-owned algo orders are canceled.
- There is no 60-second holding-time exit. Signal expiry limits entry only; opened positions remain managed until TP, SL, or a protective exit.

## Daily loss and publishing

The initial 50 USDT strategy budget is inherited from the legacy ledger rather than reset. Strategy equity uses agent-owned fills/PnL/fees, attributable funding, and unrealized PnL; it does not use the full demo balance. Remaining stop risk across open positions, including estimated exit costs, is reserved before new entries.

Loss reaching 10% of the WIB daily baseline latches **new entries** off until the next day, even if equity recovers that day. Management, scanning, and publishing continue. Day rollover uses the higher of the previous snapshot and the first new-day snapshot so restart gaps do not erase losses. This is conservative, not an exact midnight reconstruction. Polling does not observe every tick.

Signal cards share one format without slot-full, signal-only, or order-status labels. Publication does not prove a fill. Entry rejection reasons remain in internal output. The footer links to Powered by [𝕏 web3rai](https://www.x.com/web3rai).

## Running and stopping

```sh
python3 main.py run
# Optional: stop after two completed scans
python3 main.py run --cycles 2
# Scan and supervision delays
python3 main.py run --interval 60 --poll 5
```

The same `run` command starts the Telegram remote when enabled and locally configured. The remote is supervised separately, retried after process failure, and stopped when `run` exits. No second terminal is required; see the [remote guide](telegram-remote.md).

Research runs in a subprocess so position supervision continues while AI requests wait. `--poll 5` is a delay between checks, not a guaranteed five-second response time; network requests add latency.

**Ctrl+C does not close positions.** Confirmed native TP/SL remains at OKX. Automatic stop adjustments after TP1/TP2 require a running supervisor. Restart `run` to resume. Do not delete the ledger or run another bot on the same pairs. Use a dedicated demo account. Manual positions count toward capacity; unknown exposure/orders block entries and are not automatically adopted or closed.

State: `runtime/portfolio.sqlite3`. Reports: `runtime/portfolio-output.json` and `runtime/research-output.json`. Order identities are stored before submission and reconciled on restart. Unproven timeouts/rejections are not blindly retried. If `ENTRY_BLOCKED` persists, inspect errors, journals, and OKX; do not delete state to bypass it. Management of other positions continues where possible.

Funding gaps exceeding six days block entries and require manual reconciliation. Missing execution/fee/funding evidence is not automatically reset. Legacy `agent --once`/`bounded-run` cannot be used after the portfolio ledger exists; `agent --recover` remains available for pending activity before migration.

## Validation

Offline tests cover full/free slots, daily halt, split TP, stop adjustments, restart, ambiguous orders, combined risk, fees/funding, and publication during a halt. The native split-TP integration has not yet been verified through actual OKX Demo orders. The agent does not force synthetic trades when market signals fail checks.

API parameters follow [official OKX documentation](https://www.okx.com/docs-v5/en/) for attached split TP/SL, algo amendments, order details, and funding bills. Both local state and exchange responses are required to confirm execution.
