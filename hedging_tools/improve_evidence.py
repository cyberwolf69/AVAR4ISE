"""Small allowlisted evidence snapshots; never captures configuration secrets."""
import hashlib
from datetime import datetime, timezone
from hedging_tools.paths import PACKAGE_DIR, CONFIG_PATH


def strategy_version():
    paths = [CONFIG_PATH] + [PACKAGE_DIR/name for name in ('market_time.py', 'regime.py', 'structure.py', 'scanner.py', 'position_policy.py', 'demo_execution.py', 'portfolio.py', 'analyst.py', 'universe.py', 'research.py')]
    return hashlib.sha256(b'\n'.join(path.read_bytes() for path in paths)).hexdigest()[:16]


def snapshot(envelope, proposal, now=None, ticker=None):
    now = now or datetime.now(timezone.utc)
    market = envelope['input']['market']
    return {'schema': 1, 'strategy_version': strategy_version(), 'captured_at': now.isoformat(),
            'market_observed_at': market.get('observed_at'), 'packet_id': proposal['packet_id'],
            'metrics': market.get('metrics', {}), 'candidate': market.get('candidate', {}),
            'ai_reason': proposal['reason'], 'ai_invalidation': proposal['invalidation'],
            'news_source_ids': proposal['source_ids'],
            'entry_quote': {k: ticker.get(k) for k in ('bidPx', 'askPx', 'ts')} if ticker else None}


def order_evidence(order, role):
    fields = ('ordId', 'clOrdId', 'algoId', 'state', 'side', 'accFillSz', 'avgPx', 'fee', 'feeCcy', 'pnl', 'cTime', 'uTime')
    return {'role': role, **{k: order.get(k) for k in fields}}
