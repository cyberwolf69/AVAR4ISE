<div align="center">

# AVAR4ISE

**Market structure. AI review. Disciplined execution.**

An experimental perpetual trading agent for **OKX Demo**, with a local monitoring dashboard and Telegram remote.

[![Tests](https://github.com/cyberwolf69/AVAR4ISE/actions/workflows/tests.yml/badge.svg)](https://github.com/cyberwolf69/AVAR4ISE/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![OKX Demo only](https://img.shields.io/badge/OKX-Demo%20only-92E4BA)
![Status Alpha](https://img.shields.io/badge/Status-Alpha-E6BD79)

[Quick start](#quick-start) · [Dashboard](#trading-office) · [Risk controls](#default-risk-controls) · [Documentation](#documentation)

</div>

---

## What it does

- **Finds setups:** scans up to 20 liquid perpetual pairs using 1H/15m market structure and BOS/retest confirmation.
- **Reviews evidence:** AI weighs supporting and opposing signals, remembers previous reviews, and uses news when available.
- **Manages positions:** up to five concurrent demo positions, partial take profits, and protective stop adjustments.
- **Keeps you informed:** Telegram/Discord signal cards with live TP/SL markers, plus an owner-only Telegram remote.
- **Tracks outcomes:** reference signals stay separate from execution PnL; improve mode evaluates proposed filters without automatically changing the strategy.

When slots are full or the daily loss limit is reached, qualified signals can still be published. During AI outages, fresh technical setups can publish with a technical rationale; **auto-trading still requires validated AI approval**.

## Quick start

Requires **Python 3.11+** on macOS or Linux. No third-party runtime dependencies are required when running from this checkout.

### 1. Clone and configure

```sh
git clone https://github.com/cyberwolf69/AVAR4ISE.git
cd AVAR4ISE
[ -f .env ] || cp .env.example .env
chmod 600 .env
nano .env
```

Fill these values in your local `.env`:

| Integration | Required configuration |
| :--- | :--- |
| AI | `AI_ENABLED=true`, `APINEX_API_KEY`; check `AI_BASE_URL` and `AI_MODEL` for your provider |
| OKX Demo | `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, `OKX_DEMO_PASSPHRASE` |
| Signal alerts *(optional)* | `ALERTS_ENABLED=true`, `ALERT_CHANNELS`, and Telegram or Discord destination credentials — [guide](docs/alerts.md) |
| Telegram remote *(optional)* | `TELEGRAM_REMOTE_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` — [guide](docs/telegram-remote.md) |

Use **Demo Trading credentials**. `TELEGRAM_OWNER_ID` is your numeric user ID, not the signal channel ID. Never commit `.env` or share API keys.

### 2. Check and run

```sh
# Read the demo account without placing orders
python3 main.py preflight

# Start the engine, dashboard, and configured Telegram remote
python3 main.py run
```

Resolve any `BLOCKED` preflight result first. Keep the terminal running; the engine can place **demo orders** after setup validation and risk checks.

For Telegram, wait for `COMMAND_MENU_READY`, then send `/start` in a private chat with your bot. The command menu is registered automatically. Use `/status`, `/settings`, `/positions`, and `/performance`; settings changes require confirmation.

> **Stopping:** press `Ctrl+C`. This stops supervision but **does not close positions**. Confirmed native TP/SL orders remain at OKX; further stop adjustments require the engine. Telegram `/resume` cannot restart a stopped process.

## Trading Office

Open **[127.0.0.1:8765](http://127.0.0.1:8765)** after starting `run`.

A dark glass dashboard shows module activity, scanner decisions, positions, signal milestones, and provider health. It is **local and read-only**, with explicit offline/stale labels and activity driven by engine telemetry.

To view saved observations without starting trading:

```sh
python3 main.py dashboard
```

## Default risk controls

| Control | Default |
| :--- | :--- |
| Strategy budget | **50 USDT**, separate from the full demo balance |
| Daily loss limit | **10%** of day-start strategy equity · Asia/Jakarta |
| Margin per trade | Up to **5 USDT** |
| Leverage | **10–30×**, constrained by stop-based risk |
| Planned entry risk | Up to **1 USDT**, bounded by remaining daily allowance |
| Open positions | Up to **5**, one per pair |
| Take profits | **1R / 2R / 3R** · **40% / 35% / 25%**, subject to lot sizes |

Experimental software: simulation results do not establish profitability. Planned risk cannot guarantee realized losses during gaps or slippage. See [position management and limits](docs/portfolio.md).

## Documentation

| Guide | Covers |
| :--- | :--- |
| [Usage](docs/usage.md) | Commands and diagnostics |
| [Market structure](docs/market-structure.md) · [AI review](docs/agent-review.md) | Setup rules, evidence, memory, and provider outages |
| [Alerts](docs/alerts.md) · [Card updates](docs/signal-card-updates.md) | Telegram/Discord delivery and TP/SL tracking |
| [Telegram remote](docs/telegram-remote.md) · [Dashboard](docs/dashboard.md) | Controls, monitoring, and data boundaries |
| [Improve mode](docs/improve.md) · [Architecture](docs/architecture.md) | Evaluation workflow and internals |

<details>
<summary><strong>Development checks</strong></summary>

```sh
python3 -B -m unittest discover -s tests -t . -v
python3 -B main.py paper-demo
python3 main.py --help
```

Code lives in `hedging_tools/`; tests in `tests/`. Local state and reports live in Git-ignored `runtime/`. Project documentation and UI use English.

</details>

---

<div align="center">
Powered by <a href="https://www.x.com/web3rai">𝕏 web3rai</a>
</div>
