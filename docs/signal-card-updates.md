# Live TP/SL markers

New Telegram and Discord signal cards show unchecked SL and TP1/TP2/TP3 markers. After a confirmed update, the same message is edited rather than reposted. The footer link is preserved. No AI request is needed.

- Reference cards use the existing closed one-minute mark-candle tracker. Confirmed milestones become `✅ TP1 HIT`, etc. A stop after TP1 is labeled `BEP HIT`; a protected stop after TP2 is labeled `PROFIT STOP HIT`. These are price references, not net profits or filled orders.
- Cards associated with an execution use reconciled OKX order evidence. Full terminal TP fill quantities are required for TP checkmarks. Partial TP fills do not imply completion. Stop fills use their actual protective stop; price touches alone cannot check an execution milestone. The card replaces reference entry/targets with execution levels and labels the source `OKX confirmed fills`.
- Ambiguous candle ordering, missing data and pending reconciliation remain visible. No guessed TP/SL outcome is added.

`python3 main.py run` starts the card-update worker automatically. It checks approximately every 15 seconds after its preceding run finishes; reference tracking normally refreshes every minute. Updates are eventual, not tick-by-tick. Position management runs separately. Only changed cards generate edit calls, with at most ten edits per worker run.

Confirmed remote message IDs, destination hashes and the original card are retained in `runtime/alerts.sqlite3`. Webhook URLs and bot tokens are read from `.env`, not stored in this table. Restarting preserves edit targets and last successful content hashes. Changed destinations do not inherit old message IDs. Unconfirmed initial sends are not automatically resent. Old cards created before this feature cannot be edited automatically because their message IDs were not retained.

Transient edit failures back off from 60 seconds to one hour. Deleted/inaccessible messages (403/404) are disabled; they are not recreated. Telegram's `message is not modified` response is treated as a successful idempotent retry. Worker diagnostics are written to `runtime/card-updates-output.json` without credentials. This feature does not modify trading rules, positions or orders.

API references: [Telegram editMessageText](https://core.telegram.org/bots/api#editmessagetext) and [Discord Edit Webhook Message](https://docs.discord.com/developers/resources/webhook#edit-webhook-message).
