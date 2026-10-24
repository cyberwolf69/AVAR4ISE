# AVAR4ISE Trading Office

A local, read-only dashboard inspired by virtual agent offices. Its six desks represent real engine modules, not six independent LLM agents. SVG room artwork is original and requires no image downloads or external frontend dependencies.

## Run

The usual command starts the dashboard alongside the engine and Telegram remote:

```bash
python3 main.py run
```

Open **http://127.0.0.1:8765**. The dashboard stops with the engine. Optional flags: `--dashboard-port 8766` or `--no-dashboard`. If the port is occupied, the engine continues and reports `DASHBOARD_UNAVAILABLE`; no other process is stopped.

To inspect stored data without starting trading:

```bash
python3 main.py dashboard
```

This is an optional diagnostic command, not a second required startup step. Stop a standalone dashboard with Ctrl+C before starting the integrated engine on the same port.

## What is shown

- **Office:** selectable module desks, inspector, account summary and recent scanner decisions. Scanner/review activity animates only while fresh engine telemetry indicates the work is in progress. AI review publishes a small local activity record; it creates no new AI requests.
- **Scanner:** up to 20 pairs, filter by symbol, structural screen result and review state. WAIT is normal; no setup does not mean a stalled agent.
- **Portfolio:** up to 100 recent stored positions, actual entry/stop references, realized and unrealized strategy PnL. Portfolio values are saved observations, not a new OKX balance request.
- **Signals:** latest 30 reference histories, milestones and data quality. Reference price touches are not fills or realized trading profits.
- **Events:** latest 12 initial alert delivery records. This is not a complete engine event log or proof that Telegram remote/card workers are alive.

The browser polls every five seconds. A process lock and a heartbeat no older than 30 seconds are both required for RUNNING. A held lock with an old heartbeat is STALE; no held lock is OFFLINE. Saved data remains visible with a warning. Scanner data older than 180 seconds is stale. Network failures in the browser stop activity animations and label the page stale. Reduced-motion preferences disable animations.

AI cooldown is computed against the current time. An expired cooldown is RETRY_READY with its historical failure shown separately; it does not assert a new provider error or recovery. Token usage is the recorded total over the preceding 24 hours, capped at the latest 1,000 usage records; it is not the router's remaining quota.

Daily equity change uses the saved strategy equity and day-start baseline. The UI does not invent account values when a database is missing. No trade settings or account mutations are available through the dashboard. Independent worker liveness, comprehensive performance charts and remote control are outside this first version.

## Access and data

The HTTP server binds only to `127.0.0.1`. Static routes are allowlisted; SQLite is opened in read-only mode. The API returns selected monitoring fields, never `.env`, credentials, webhook URLs or full order evidence. Foreign Host/Origin headers and cross-site requests are rejected. No CORS access is granted, and POST has no handler. The dashboard is for the same computer, not public hosting; do not expose it through a tunnel or reverse proxy without adding authentication.

The UI and local API make no exchange or AI calls. Loading the dashboard cannot place orders, edit alerts, or change controls. `run` still performs its normal authorized trading and notification work independently.
