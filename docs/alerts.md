# Telegram and Discord alerts

Alerts are sent automatically by `research --ai`, including research launched by `agent`/`run`, when a LONG/SHORT proposal passes AI validation. WAIT, data errors, and expired proposals are not published. Publishing is independent of execution permission: cards do not display slot availability or execution labels. Account checks may still reject an entry; an alert does not confirm a fill.

Cards contain the pair, direction, reference entry/SL, TP1/TP2/TP3 at 1R/2R/3R, risk limits, AI rationale, invalidation, headline source names, and expiration in WIB (UTC+7). No invented confidence or win probability is shown. Signal targets use the snapshot; actual position targets use the entry limit price, and subsequent stop adjustments use the actual fill.

Inspect reference outcomes with `python3 main.py signals`. See [signal tracking](discovery-and-signals.md). The tracker does not edit published cards or equate reference outcomes with trade PnL.

## Configuration

Store credentials locally in `.env`; never send tokens/webhooks in chat or commit them:

```dotenv
ALERTS_ENABLED=true
ALERT_CHANNELS=discord,telegram
DISCORD_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Use `ALERT_CHANNELS=discord` or `telegram` for a single destination.

**Discord:** create an incoming webhook under server/channel settings → Integrations → Webhooks. Set `DISCORD_WEBHOOK_URL` to its HTTPS `discord.com/api/webhooks/...` URL. Forum/thread-specific webhooks are not supported.

**Telegram:** create a bot through BotFather and add it as a channel administrator with posting permission. Set `TELEGRAM_CHAT_ID` to the public channel username, such as `@example_channel`, or the private channel's numeric ID. The alert module only sends messages. Owner commands are handled by the separately configured [Telegram remote](telegram-remote.md).

```sh
# Local synthetic preview; sends nothing
python3 main.py alerts --preview
# Send a clearly labeled TEST to all enabled destinations
python3 main.py alerts --test
# Start normal operation
python3 main.py run
```

The preview is also saved to `runtime/alerts-preview.txt`. Alert credentials are not shared with the AI or OKX. `.env` remains Git-ignored.

## Delivery behavior

Delivery status is stored in `runtime/alerts.sqlite3`, with a summary in `runtime/research-output.json`. The ledger excludes tokens and webhook URLs. Persistent deduplication uses destination, pair, direction, and the research packet's 15-minute bucket. A new bucket can generate another alert in the same direction.

Requests time out after ten seconds per destination, reject redirects, and disable Discord mentions. Telegram HTML escapes AI text. AVAR4ISE appears as the project brand; **Powered by [𝕏 web3rai](https://www.x.com/web3rai)** remains a masked link at the bottom of the Discord embed and Telegram message. Notification failures do not cancel trading; inspect the research report's `alerts` field.

The ledger records an attempt before sending. Timeout/UNCONFIRMED deliveries are not retried automatically because the message may already have arrived. This policy can lose alerts during outages and does not guarantee exactly-once delivery. Each new `--test` invocation is a new delivery.

Previous connectivity tests for both channels were confirmed by the user. Branding and split-TP formatting are tested offline; use the preview to inspect them.

API references: [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage), [Discord Execute Webhook](https://discord.com/developers/docs/resources/webhook#execute-webhook).
