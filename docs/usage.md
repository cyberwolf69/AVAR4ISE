# Usage

Run from the repository root with Python 3.11+ on macOS/Linux. Credentials belong in the root `.env`, not source files or runtime reports.

## Local configuration

```sh
# New checkouts only; do not overwrite existing credentials
cp .env.example .env
chmod 600 .env
```

Fill `APINEX_API_KEY`, `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, and `OKX_DEMO_PASSPHRASE`. Use **Demo Trading** credentials. Set `AI_ENABLED=true` for AI research. The template uses APINEX; model availability and pricing are provider-dependent. All signed OKX requests use the demo header; there is no live trading switch.

Use a dedicated demo account. Unknown exposure/orders block new entries; the agent does not take over manual positions.

## Commands

Prefix each command with `python3 main.py` or `python3 -m hedging_tools`.

| Command | Purpose |
|---|---|
| `paper-demo` | Offline synthetic paper cycle |
| `risk-demo` | Offline risk example |
| `scan` | Public market screening for paper-configured pairs |
| `universe` | Read-only ranking of up to 20 dynamic pairs |
| `improve` | Review SL/exit evidence without changing the strategy |
| `improve --evaluate` | Evaluate entry filters on training/holdout data |
| `signals` | Local reference signal summary |
| `signals --details` | Per-signal milestones, separate from trades |
| `signals --update` | Update outcomes using public candles |
| `shadow` | Local structure comparison report |
| `shadow --update` | Collect new snapshots and update reference outcomes |
| `research --ai` | Market, headline, and AI research without orders |
| `preflight` | Read the demo account |
| `agent --once --hold-seconds 60` | Legacy single cycle; may place demo orders |
| `run` | Continuous portfolio supervisor, up to five positions |
| `run --cycles 2` | Stop after two scans; native TP/SL remains active |
| `bounded-run --cycles 2` | Legacy test runner before portfolio migration |
| `agent --recover` | Reconcile pending legacy state without orders |
| `reconcile runtime/demo-execution-….json` | Inspect a specific journal |
| `execution-test --execute-demo-test` | Open/close a technical demo trade without an AI signal |
| `ai-check --smoke-test` | Explicit AI connectivity test |
| `evaluate-ai` | Synthetic AI schema evaluation |
| `paper-bridge` | Send a proposal to the local paper ledger |
| `reversion` | OU/Zeta research without orders |
| `alerts --preview` | Local synthetic alert preview |
| `alerts --test` | Send a test to enabled Telegram/Discord destinations |
| `telegram --check` | Local remote configuration check |
| `telegram` | Start the owner-only Telegram remote and register its menu |

Direct modules remain available, for example `python3 -m hedging_tools.demo_runner --help`. Optional editable installation: `python3 -m pip install -e .`, then `avar4ise run`. The legacy `hedging-tools` console alias remains available. See the [alert guide](alerts.md) and [remote guide](telegram-remote.md).

## Reports and shutdown

`run` stores state in `runtime/portfolio.sqlite3`, supervision reports in `runtime/portfolio-output.json`, and analysis in `runtime/research-output.json`. Never delete the ledger to bypass a risk block. The initial migration inherits the old `demo-strategy.sqlite3` budget; pending legacy activity must be reconciled first.

Ctrl+C stops scanning and supervision. Confirmed native TP/SL remains at OKX, but stop adjustments after TP require the program. Restart `run` to resume management. See [portfolio rules](portfolio.md) for partial exits, daily loss, aggregate risk, and API recovery.

## Layout migration

Legacy data moved from the root to `runtime/`; `.env` remains at the root. Development notes and pre-migration database backups are in Git-ignored `runtime/archive/`. Budget and history are not reset.

For publishing, use Git-tracked files rather than a ZIP of the entire working directory, which may contain credentials and account data. The AVAR4ISE name does not rename the internal `hedging_tools` package or existing runtime files.
