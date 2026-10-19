# AVAR4ISE

An **OKX Demo** perpetual trading agent combining market structure screening, AI analysis, optional headline context, and persistent risk controls. The project is experimental; simulation results do not establish profitability.

## Features

- [Dynamic scanning of up to 20 pairs](docs/discovery-and-signals.md), using closed candles, structure, volume context, and spread checks.
- Structured AI proposals with the option to WAIT; news is optional for technical setups.
- Isolated demo execution, native stops, position supervision, and reduce-only exits.
- Persistent daily budget, aggregate position risk, and fill/fee/funding/PnL reconciliation.
- [Up to five positions](docs/portfolio.md), split TP1/TP2/TP3, and protective stop adjustments.
- [Improve mode](docs/improve.md): loss reviews and holdout evaluation of proposed filters, without automatic strategy changes.
- Reference signal tracking, separate from actual trade PnL.
- [Telegram and Discord alerts](docs/alerts.md), plus an [owner-only Telegram remote](docs/telegram-remote.md).

## Getting started

Python **3.11+**, macOS or Linux. The runtime uses the Python standard library. Run from the repository root; package installation is optional.

```sh
# Offline tests and synthetic example; no API keys required
python3 -B -m unittest discover -s tests -t . -v
python3 -B main.py paper-demo
python3 main.py --help
```

Copy `.env.example` to `.env` **only if it does not already exist**, then fill in local credentials. Never commit `.env`. See the [usage guide](docs/usage.md).

```sh
# Read the demo account without placing orders
python3 main.py preflight
# Market, headlines, and AI analysis without orders
python3 main.py research --ai
# May place DEMO orders after signal validation and risk checks
python3 main.py run
# Start the private remote in a second terminal
python3 main.py telegram
```

By default, `run` continues until Ctrl+C, scans again 60 seconds after research completes, and checks positions with a five-second polling delay. Network requests add latency. Ctrl+C leaves confirmed native TP/SL orders in place; stop adjustments require the program to remain running. See `python3 main.py run --help`.

## Initial limits

| Parameter | Value |
|---|---|
| Strategy budget | 50 USDT, separate from the full demo account balance |
| Daily loss limit | 10% of start-of-day strategy equity, Asia/Jakarta timezone |
| Margin per trade | Maximum 5 USDT |
| Leverage | 10–30x, constrained by stop-based risk |
| Planned entry risk | Maximum 1 USDT or remaining daily allowance |
| Concurrent positions | Maximum five, one per pair |
| Take profit | 1R/2R/3R, 40%/35%/25% allocation subject to lot sizes |

Planned risk is not a guarantee of realized losses during gaps or slippage. Paper configuration is in [default.json](hedging_tools/config/default.json); it does not control every demo executor limit. See the [portfolio guide](docs/portfolio.md). `bounded-run` retains the legacy test runner for use before portfolio migration.

## Layout

```text
main.py             CLI entry point
hedging_tools/       Application code and default configuration
tests/              Offline tests
docs/               Guides, architecture, and experiments
.github/workflows/  Offline test CI
runtime/            Local databases, reports, journals, archives (Git-ignored)
.env.example        Credential-free configuration template
pyproject.toml      Project metadata and optional console entry points
```

The Python package remains `hedging_tools` for compatibility. Optional editable installation provides `avar4ise`; the existing `hedging-tools` command remains available.

The active setup is [1H/15m BOS and retest market structure](docs/market-structure.md). [Shadow comparison](docs/shadow.md) evaluates an alternative without orders; inspect it with `python3 main.py shadow`. OU/Zeta remains a [separate experiment](docs/research.md) with no effect on orders. Legacy `python3 demo_runner.py` usage is replaced by `python3 main.py run`.

Project documentation, menus, and generated AI explanations use English.
