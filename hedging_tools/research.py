"""Fetch headlines, scan market, and journal provider-neutral analyst packets."""
from hedging_tools.paths import CONFIG_PATH
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.analyst import make_packet, validate_proposal
from hedging_tools.news import fetch_news, news_for_pair
from hedging_tools.okx_public import OKXPublic
from hedging_tools.scan import ROOT, run
from hedging_tools.ai_client import AIClient, AIError
from hedging_tools.alerts import notify_signals
from hedging_tools.universe import select as select_universe
from hedging_tools.signal_history import SignalHistory
from hedging_tools.controls import Controls


def review_priority(packets, database):
    """Review least recently attempted pairs first; discovery ranking breaks ties."""
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS ai_review_cursor (pair TEXT PRIMARY KEY, reviewed_at TEXT)')
        previous = dict(db.execute('SELECT pair,reviewed_at FROM ai_review_cursor'))
    return sorted(range(len(packets)), key=lambda i: (previous.get(packets[i]['input']['pair'], ''), i))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "scans.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "research-output.json")
    parser.add_argument("--proposal", type=Path, help="Validate one JSON proposal against a stored packet")
    parser.add_argument("--ai", action="store_true", help="Analyze eligible candidates with configured provider")
    parser.add_argument("--review-all", action="store_true", help="With --ai, also review WAIT snapshots; cannot override screening")
    parser.add_argument("--portfolio", action="store_true", help="Discover up to 20 liquid crypto perpetual pairs")
    args = parser.parse_args()
    if args.review_all and not args.ai:
        parser.error("--review-all requires --ai")
    ai = AIClient() if args.ai else None
    if args.proposal:
        proposal = json.loads(args.proposal.read_text())
        with closing(sqlite3.connect(args.db)) as db, db:
            row = db.execute("SELECT envelope_json FROM analyst_packets WHERE packet_id=?", (proposal["packet_id"],)).fetchone()
        if row is None:
            raise ValueError("Packet not found in local journal")
        print(json.dumps(validate_proposal(proposal, json.loads(row[0])), indent=2))
        return 0
    config = json.loads(CONFIG_PATH.read_text())
    controls = Controls().read()
    config.update(min_leverage=controls['min_leverage'], max_leverage=controls['max_leverage'],
                  margin_per_trade=controls['margin'])
    public = OKXPublic()
    universe = {'status': 'FIXED', 'pairs': config['pairs']}
    if args.portfolio:
        universe = select_universe(public)
        config['pairs'] = universe['pairs']
        config['max_positions'] = 5
        (ROOT / 'universe-output.json').write_text(json.dumps(universe, indent=2) + '\n')
    health = fetch_news(args.db)
    scanned = run(public, config, args.db, workers=4 if args.portfolio else 1)
    packets = []
    with closing(sqlite3.connect(args.db)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS analyst_packets (packet_id TEXT PRIMARY KEY, run_id TEXT, envelope_json TEXT)")
    for result in scanned["results"]:
        now = datetime.now(timezone.utc)
        articles = news_for_pair(args.db, result["pair"], now)
        envelope = make_packet(result, articles, health, now)
        with closing(sqlite3.connect(args.db)) as db, db:
            db.execute("INSERT INTO analyst_packets VALUES (?,?,?)", (envelope["input"]["packet_id"], scanned["run_id"], json.dumps(envelope)))
        packets.append(envelope)
    analyses = []
    deliveries, tracking_errors = [], []
    alert_states = []
    ai_calls = 0
    history = SignalHistory()
    chosen_for_ai = set()
    if args.portfolio:
        for index in review_priority(packets, args.db):
            candidate = packets[index]['input']
            market = candidate['market']
            observed = datetime.fromisoformat(market['observed_at']) if market.get('observed_at') else None
            eligible = ((market['status'] == 'WATCHLIST')
                        or (args.review_all and market['status'] in ('WAIT', 'WATCHLIST')))
            if eligible and observed and 0 <= (datetime.now(timezone.utc)-observed).total_seconds() <= 180:
                chosen_for_ai.add(index)
                if len(chosen_for_ai) == 3:
                    break
    for index, envelope in enumerate(packets):
        data = envelope["input"]
        analysis = {"packet_id": data["packet_id"], "status": "SKIPPED", "execution": "DISABLED"}
        eligible = (data["market"]["status"] == "WATCHLIST") or (args.review_all and data["market"]["status"] in ("WAIT", "WATCHLIST"))
        observed = datetime.fromisoformat(data['market']['observed_at']) if data['market'].get('observed_at') else None
        stale = observed is None or not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 180
        exhausted = args.portfolio and index not in chosen_for_ai
        if ai and eligible and not stale and not exhausted:
            ai_calls += 1
            if args.portfolio:
                with closing(sqlite3.connect(args.db)) as db, db:
                    db.execute('INSERT OR REPLACE INTO ai_review_cursor VALUES (?,?)',
                               (data['pair'], datetime.now(timezone.utc).isoformat()))
            try:
                analysis.update(status="VALIDATED", **ai.analyze(envelope))
            except AIError as exc:
                analysis.update(status="ERROR", reason=str(exc))
        else:
            analysis['reason'] = ('AI not requested' if not ai else
                                  'SNAPSHOT_NOT_ELIGIBLE' if not eligible else
                                  'STALE_SNAPSHOT' if stale else 'AI_CALL_BUDGET_REACHED')
        analysis['screen_reasons'] = data['market'].get('reasons', [])
        if analysis.get('status') == 'VALIDATED':
            try:
                history.register(envelope, analysis['proposal'])
            except Exception as exc:
                tracking_errors.append({'pair': data['pair'], 'reason': type(exc).__name__})
            notice = notify_signals([envelope], [analysis])
            alert_states.append(notice['status'])
            deliveries.extend(notice.get('deliveries', []))
        analyses.append(analysis)
        with closing(sqlite3.connect(args.db)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS ai_analyses (id INTEGER PRIMARY KEY, run_id TEXT, packet_id TEXT, result_json TEXT)")
            db.execute("INSERT INTO ai_analyses (run_id,packet_id,result_json) VALUES (?,?,?)", (scanned["run_id"], data["packet_id"], json.dumps(analysis)))
    ai_status = "REQUESTED" if args.ai else "NOT_REQUESTED"
    alerts = {'status': 'ERROR' if 'ERROR' in alert_states else 'CHECKED' if alert_states else 'NO_SIGNALS', 'deliveries': deliveries}
    output = {"mode": "RESEARCH_ONLY", "ai_status": ai_status, "run_id": scanned["run_id"], "packets": packets, "analyses": analyses, 'alerts': alerts, 'universe': universe, 'signal_tracking': {'errors': tracking_errors}, 'ai_calls': ai_calls}
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"output": str(args.output.resolve()), "feed_health": health,
                      "pairs": [{"pair": p["input"]["pair"], "market_status": p["input"]["market"]["status"],
                                 "news_count": len(p["input"]["news"])} for p in packets],
                      "ai_status": ai_status, "analyses": analyses, 'alerts': alerts, "execution": "DISABLED"}, indent=2))
    if args.portfolio:
        # A failed pair/provider analysis must not discard other validated pairs.
        return int(universe['status'] in ('EMPTY', 'UNAVAILABLE')
                   or all(r['status'] == 'DATA_ERROR' for r in scanned['results']))
    return int(any(r['status'] == 'DATA_ERROR' for r in scanned['results'])
               or any(a['status'] == 'ERROR' for a in analyses))


if __name__ == "__main__":
    raise SystemExit(main())
