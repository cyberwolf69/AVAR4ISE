# AVAR4ISE private Telegram remote

The owner-only remote controls new OKX Demo entries. Leverage remains automatic: the risk engine selects the highest leverage satisfying stop risk within your chosen range. Hard limits remain 10–30x leverage, 1–5 USDT margin, one to five positions, maximum 1 USDT entry risk, and 10% daily loss. Initial settings are 10–30x, 5 USDT margin, and five positions.

## Activation

Set these values in your local `.env`; never share the token:

```dotenv
TELEGRAM_REMOTE_ENABLED=true
TELEGRAM_OWNER_ID=123456789
```

Replace the example with your numeric Telegram user ID. The remote uses the existing `TELEGRAM_BOT_TOKEN`. `TELEGRAM_CHAT_ID` identifies the alert destination, not the remote owner. Usernames are not used for authorization.

```sh
# Local configuration check; no network requests
python3 main.py telegram --check
# Start the engine and configured Telegram remote together
python3 main.py run
```

Open a **private chat** with the bot and send `/start` or `/help`. The remote starts automatically as a managed child of `run` when enabled and configured. No second terminal is required. Ctrl+C or a cycle-limit exit stops the managed remote together with the engine. It cannot start a stopped OS process. `/resume` only enables entries in an already running engine. The remote does not restart processes or force-close positions.

## Automatic command menu

Each managed remote startup registers the slash commands and Telegram menu button from code, scoped to the owner's private chat. Manual BotFather `/setcommands` setup is unnecessary. `COMMAND_MENU_READY` confirms Telegram accepted registration. On failure, polling continues and registration is retried after at least 60 seconds. Send `/start` first if you have never opened the bot chat.

Deployment only needs `python3 main.py run` with local credentials and `TELEGRAM_REMOTE_ENABLED=true`. A GitHub push alone does not register the menu. Restart `run` after menu or credential updates. `--check` remains offline.

Disabled or invalid local configuration produces `REMOTE_NOT_STARTED`; position supervision continues. Fix `.env` and restart `run`. A remote process exit or launch failure is retried after at least 60 seconds, without stopping position management. The remote retains its own lock to reject duplicate pollers.

`python3 main.py telegram` remains an optional standalone troubleshooting command. Do not run it alongside an enabled managed remote or another poller using the same token. A standalone remote is not stopped by `run`; stop that standalone process before switching to the single-command workflow.

| Command | Purpose |
|---|---|
| `/start`, `/menu`, `/help` | Help and remote buttons |
| `/status` | Engine heartbeat, latest report, strategy equity, daily halt, errors, and AI summary |
| `/settings` | Current settings |
| `/leverage 10 20` | Automatic range; equal values such as `10 10` select fixed 10x |
| `/margin 3` | Maximum margin for new entries, 1–5 USDT |
| `/slots 3` | Maximum active positions, 1–5 |
| `/pause` | Block new entries; scanning, alerts, and position management continue |
| `/resume` | Allow entries, subject to daily halt and risk checks |
| `/confirm CODE` | Apply the latest requested change within two minutes |
| `/positions` | Active positions and saved prices, levels, and PnL |
| `/history` | Last ten closed positions; closed_ms is Unix milliseconds |
| `/performance` | Realized PnL, fees, funding, equity, and closed-trade win rate before funding |

Each change shows before → after and requires owner confirmation. Confirmations are single-use, owner- and revision-bound, and expire after 120 seconds. A new proposal replaces the previous one. Open positions retain their original leverage and exit plans. Lowering the slot limit does not close positions. Research reads the new settings; execution checks them again. Changes and entries are serialized, so confirmation can wait for an entry already being processed.

## Access and reporting limits

- Only fresh messages from `TELEGRAM_OWNER_ID` in the matching private chat are accepted. Groups, channels, other users, bots, forwarded messages, and messages older than 120 seconds are ignored.
- A local lock prevents duplicate pollers on the same host. Do not run another poller or webhook with the token. Alert sending can share it. The remote does not delete webhooks.
- Updates are acknowledged before handling to prevent replay. A crash can lose a command or reply; check `/settings` before resubmitting. This is not exactly-once delivery.
- Tokens are excluded from reports and error logs. Settings, confirmation requests, cursor, and audit data live in Git-ignored `runtime/controls.sqlite3`.
- Stale heartbeats show UNKNOWN. Reports use saved snapshots rather than fresh connection probes. Remote failures do not stop the separate position supervisor.
- Performance uses actual portfolio records, including partial exits. Funding is reported separately and then added. Win rate includes filled, closed positions after fees but before funding. Reference signals and shadow results are excluded.
- Historical drawdown is unavailable until a complete equity curve exists. Missing values are N/A, not fabricated zeroes or profits.
- Margin, leverage, and slot changes start separate shadow/improve experiment versions.

Implementation uses the [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) for polling, replies, and automatic menu registration.
