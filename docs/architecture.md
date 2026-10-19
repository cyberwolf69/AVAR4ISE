# Architecture

The active `run` command uses a persistent five-position portfolio; see the [portfolio guide](portfolio.md). The single-position executor described below is a legacy test path.

Flow: public market data → screening → optional headlines → validated AI proposal → risk checks → OKX Demo → position supervision → reconciliation → budget settlement.

| Module | Responsibility |
|---|---|
| `okx_public`, `scanner`, `scan` | Public data and screening |
| `news`, `analyst`, `ai_client`, `research` | Headline evidence, AI, proposal validation |
| `risk_engine`, `paper`, `paper_bridge` | Risk and local simulation ledger |
| `okx_demo` | Signed demo requests on restricted routes |
| `demo_execution`, `demo_monitor` | Legacy entry, SL, local 2R target, timed exit, cleanup |
| `reconcile_demo` | Fill/fee/PnL evidence and native-stop order linkage |
| `demo_agent` | Legacy budget, single cycle, daily halt, recovery, cooldown |
| `demo_runner` | Legacy bounded loop, process lock, cycle history |
| `portfolio`, `portfolio_runner` | Persistent portfolio, split TP/SL management, supervision |
| `controls`, `telegram_remote`, `remote_worker` | Runtime settings, owner-only remote, and its lifecycle under `run` |
| `paths` | Configuration and runtime locations |

## Controls

The AI cannot change risk limits. Demo leverage searches from the allowed maximum down to the allowed minimum, initially 30x to 10x, constrained by instrument limits and stop risk plus an estimated 0.2% notional cost. Technical tests without a signal remain at 10x. The legacy IOC entry attaches an SL; bot exits are reduce-only. Its monitor checks actual position state, live stops, mark price, and liquidation buffer. Active portfolio execution uses FOK and split native TP/SL.

Legacy settlement matches all exit fills, including native stops, with the entry and reconciles equity changes against PnL after fees. Evidence is saved atomically. Pending activity, loss limits, consumed proposals, and cooldowns persist across restarts. A process lock prevents concurrent legacy agent, runner, and recovery operations.

## Limits

- No established strategy profitability or managed 24/7 daemon.
- In the legacy path, 2R targets/timed exits are local; only SL resides at the exchange. Active portfolio behavior differs as documented above.
- Actual liquidation prices are available after entry; gaps/slippage can exceed planned risk.
- Legacy settlement does not automatically separate funding from equity changes. Active portfolio reconciliation records attributable funding.
- Legacy recovery blocks day rollover and incomplete evidence.
- Some demo limits remain constants; JSON configuration mainly serves paper/screening.
- Exchange verification of triggered native stops and multiple trades remains incomplete.
- The package targets local/editable checkouts: `.env` and `runtime/` are relative to the checkout, not the shell's working directory.

Tests require no keys or network. CI is configured for Python 3.11–3.14; GitHub results are available only after the workflow runs.
