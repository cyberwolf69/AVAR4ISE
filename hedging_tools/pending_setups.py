"""Retain failed-review pairs for fresh scans; never reuse a saved price for entry."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from hedging_tools.analyst import validate_proposal
from hedging_tools.risk_engine import number


class PendingSetups:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS pending_setups (pair TEXT PRIMARY KEY, expires REAL)')

    def pairs(self, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM pending_setups WHERE expires<=?', (now,))
            return [r[0] for r in db.execute('SELECT pair FROM pending_setups ORDER BY expires LIMIT 20')]

    def retain(self, pair, expires):
        with closing(sqlite3.connect(self.path)) as db, db:
            # Repeated failures do not extend the original retention window.
            db.execute('INSERT OR IGNORE INTO pending_setups VALUES (?,?)', (pair, expires))

    def remove(self, pair):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM pending_setups WHERE pair=?', (pair,))


def technical_proposal(envelope, now=None):
    """Publication only. Caller must retain the failed/skipped AI status."""
    now = now or datetime.now(timezone.utc)
    data = envelope['input']
    market = data['market']
    observed = datetime.fromisoformat(market['observed_at'])
    if market['status'] != 'WATCHLIST' or not 0 <= (now-observed).total_seconds() <= 180:
        raise ValueError('Technical publication requires a fresh passing screen')
    candidate = market['candidate']
    if candidate.get('setup_model') != 'structure_bos_retest_v1':
        raise ValueError('Unsupported technical setup')
    action = candidate['action']
    entry, stop = number(candidate['entry']), number(candidate['stop_reference'])
    if action not in ('LONG','SHORT') or min(entry,stop)<=0 or (entry-stop)*(1 if action=='LONG' else -1)<=0:
        raise ValueError('Invalid technical price geometry')
    proposal = dict(packet_id=data['packet_id'],pair=data['pair'],action=action,
                    reason='Confirmed structure break and retest passed the technical screen. AI review is temporarily unavailable. News context is not assessed.',
                    invalidation=f'The setup is invalid if the protective stop at {stop} is reached or the retest structure breaks.',
                    source_ids=[],expires_at=data['expires_at'])
    if data.get('version') == 2:
        proposal.update(supporting_evidence=[{'observation':'Fresh structural screen passed.', 'ref':'/market/status'}],
                        opposing_evidence=[], wait_for='')
    validate_proposal(proposal,envelope,now)
    return proposal
