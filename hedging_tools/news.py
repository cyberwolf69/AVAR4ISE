"""RSS headlines with durable first-seen timestamps; no article scraping."""
import hashlib
import re
import sqlite3
from contextlib import closing
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from hedging_tools.okx_public import OKXPublic

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}
ALIASES = {"BTC-USDT-SWAP": r"\b(bitcoin|btc)\b", "ETH-USDT-SWAP": r"\b(ethereum|ether|eth)\b",
           "SOL-USDT-SWAP": r"\b(solana|sol)\b", "XRP-USDT-SWAP": r"\b(ripple|xrp)\b",
           "DOGE-USDT-SWAP": r"\b(dogecoin|doge)\b"}



def headline_matches(pair, title):
    if pair in ALIASES:
        return bool(re.search(ALIASES[pair], title, re.I))
    base = pair.removesuffix('-USDT-SWAP')
    names = {'ADA': 'cardano', 'AVAX': 'avalanche', 'LINK': 'chainlink', 'DOT': 'polkadot',
             'LTC': 'litecoin', 'BCH': 'bitcoin cash', 'SUI': 'sui', 'AAVE': 'aave',
             'PEPE': 'pepe', 'TON': 'toncoin', 'UNI': 'uniswap', 'APT': 'aptos', 'ARB': 'arbitrum',
             'ZEC': 'zcash', 'SAND': 'sandbox', 'NEAR': 'near protocol', 'STRK': 'starknet',
             'HYPE': 'hyperliquid', 'FIL': 'filecoin', 'WLD': 'worldcoin', 'PUMP': 'pump.fun',
             'TIA': 'celestia', 'ENA': 'ethena', 'HBAR': 'hedera', 'INJ': 'injective'}
    if base in names and re.search(r'\b' + re.escape(names[base]) + r'\b', title, re.I):
        return True
    # Unknown/ambiguous tickers need an explicit cashtag or trading symbol.
    # Never treat a common word such as ON/OP as token-specific news.
    return bool(re.search(r'(?<!\w)\$' + re.escape(base) + r'\b|\b' + re.escape(base) + r'/?USDT\b', title, re.I))

def utc(value):
    if value.tzinfo is None:
        raise ValueError("Timezone required")
    return value.astimezone(timezone.utc)


def canonical_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid news URL")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def parse_feed(payload, source, now):
    now = utc(now)
    if len(payload) > 2_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unsafe or oversized feed")
    root = ElementTree.fromstring(payload)
    if root.tag != "rss":
        raise ValueError("Expected RSS feed")
    articles, rejected = [], 0
    for item in root.findall("./channel/item")[:200]:
        try:
            title = " ".join((item.findtext("title") or "").split())
            published = utc(parsedate_to_datetime(item.findtext("pubDate") or ""))
            link = canonical_url(item.findtext("link") or "")
            if not title or len(title) > 1000 or published > now:
                raise ValueError("Invalid headline or future publication")
            # Store only the headline and link, not full copyrighted articles.
            title_key = re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", title).casefold()).strip()
            articles.append({"id": hashlib.sha256(link.encode()).hexdigest(),
                             "source": source, "url": link, "title": title,
                             "title_key": title_key, "published_at": published.isoformat(),
                             "first_seen_at": now.isoformat()})
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    return articles, rejected


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, source TEXT, url TEXT, title TEXT, title_key TEXT, published_at TEXT, first_seen_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS news_fetches (id INTEGER PRIMARY KEY, fetched_at TEXT, source TEXT, status TEXT, accepted INTEGER, rejected INTEGER, error TEXT)")


def store(db, articles):
    for a in articles:
        # Preserve the originally observed headline and timestamps across refreshes.
        db.execute("INSERT OR IGNORE INTO news VALUES (?, ?, ?, ?, ?, ?, ?)",
                   tuple(a[k] for k in ("id", "source", "url", "title", "title_key", "published_at", "first_seen_at")))


def fetch_news(database, opener=urlopen, now=None):
    context = OKXPublic().context
    health = []
    with closing(sqlite3.connect(database)) as db, db:
        initialize(db)
        for source, url in FEEDS.items():
            status, error, articles, rejected = "OK", None, [], 0
            try:
                request = Request(url, headers={"User-Agent": "HedgingTools-NewsReader/0.1"})
                with opener(request, timeout=15, context=context) as response:
                    payload = response.read(2_000_001)
                observed = utc(now or datetime.now(timezone.utc))
                articles, rejected = parse_feed(payload, source, observed)
                if not articles:
                    raise ValueError("No valid RSS items")
                store(db, articles)
            except (OSError, ValueError, ElementTree.ParseError) as exc:
                observed = utc(now or datetime.now(timezone.utc))
                status, error = "ERROR", str(exc)
            db.execute("INSERT INTO news_fetches (fetched_at,source,status,accepted,rejected,error) VALUES (?,?,?,?,?,?)",
                       (observed.isoformat(), source, status, len(articles), rejected, error))
            health.append({"source": source, "status": status, "accepted": len(articles), "rejected": rejected, "error": error})
    return health


def news_for_pair(database, pair, as_of, max_age_hours=24, limit=10):
    as_of = utc(as_of)
    cutoff = as_of - timedelta(hours=max_age_hours)
    selected, titles = [], set()
    with closing(sqlite3.connect(database)) as db, db:
        initialize(db)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM news WHERE published_at >= ? AND published_at <= ? AND first_seen_at <= ? ORDER BY published_at DESC, id ASC",
                          (cutoff.isoformat(), as_of.isoformat(), as_of.isoformat()))
        for row in rows:
            article = dict(row)
            if not headline_matches(pair, article["title"]) or article["title_key"] in titles:
                continue
            titles.add(article.pop("title_key"))
            selected.append(article)
            if len(selected) == limit:
                break
    return selected
