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

### 1. Download and configure

For a new checkout:

```sh
git clone https://github.com/cyberwolf69/AVAR4ISE.git
cd AVAR4ISE
# Preserve .env if it already exists
[ -f .env ] || cp .env.example .env
chmod 600 .env
nano .env
```

In `.env`, fill in `APINEX_API_KEY`, `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, and `OKX_DEMO_PASSPHRASE`. Use OKX **Demo Trading** credentials and set `AI_ENABLED=true`.

For the Telegram remote, fill in `TELEGRAM_BOT_TOKEN`, set `TELEGRAM_OWNER_ID` to your numeric Telegram user ID, and set `TELEGRAM_REMOTE_ENABLED=true`. The owner ID is different from the alert channel's `TELEGRAM_CHAT_ID`. For signal delivery, also configure `ALERTS_ENABLED`, `ALERT_CHANNELS`, and the destination credentials described in the [alert guide](docs/alerts.md). Never commit `.env`.

### 2. Check the configuration

Run these commands from the repository directory:

```sh
# Read the demo account without placing orders
python3 main.py preflight
# Check remote configuration locally; no Telegram request
python3 main.py telegram --check
```

Resolve any `BLOCKED` result before continuing. The remote check should return `READY`; it does not verify network connectivity.

### 3. Start everything

```sh
python3 main.py run
```

Keep this terminal running. This single command starts the trading engine, research, signal tracking, improve/shadow workers, and the Telegram remote when `TELEGRAM_REMOTE_ENABLED=true` and its local configuration is valid. It may place **DEMO orders** after signal validation and risk checks. No second terminal or separate remote command is required.

For Telegram, wait for `COMMAND_MENU_READY`, then open a private chat with your bot. Send these commands one at a time:

```text
/start
/status
/settings
/positions
/performance
```

The menu registers automatically; no manual BotFather `/setcommands` step is needed. Settings changes require `/confirm CODE`. See the [remote guide](docs/telegram-remote.md) for leverage, margin, slots, pause, and resume.

If remote configuration is disabled or incomplete, `REMOTE_NOT_STARTED` explains why and the engine continues. Correct `.env` and restart `run` to enable it. If the remote process exits, the supervisor retries after at least 60 seconds while continuing position management. Do not also start a standalone Telegram poller with the same token.

### Stopping and restarting

Press `Ctrl+C` once in the running terminal. The supervisor shuts down its engine workers and managed Telegram remote. Stop an older instance before starting a replacement; restart everything with `python3 main.py run`.

By default, `run` scans again 60 seconds after research completes and checks positions with a five-second polling delay. Network requests add latency. **Ctrl+C does not close positions:** confirmed native TP/SL orders remain at OKX, but stop adjustments require the engine to remain running. The managed Telegram remote also stops, so it cannot restart the application. `/resume` permits entries in an active engine; it cannot start a stopped process. See `python3 main.py run --help`.

### Optional checks

```sh
# Offline tests and synthetic paper example; no API keys required
python3 -B -m unittest discover -s tests -t . -v
python3 -B main.py paper-demo
# Local alert preview; sends nothing
python3 main.py alerts --preview
# Market, headlines, and AI research without orders
python3 main.py research --ai
# Full command list
python3 main.py --help
```

`research --ai` can publish alerts when configured. See the [usage guide](docs/usage.md) for additional commands.

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
