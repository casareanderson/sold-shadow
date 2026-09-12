"""The two tables this needs, and nothing else.

⚠️ `_c` is the raw sqlite connection and every module here reaches it directly.
That is deliberate in a tool this small - but if you graft this into an app with
per-customer data, note that going straight to `_c` is exactly how a
tenant-scoping bug hides: the object looks like a scoped handle and isn't.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    item_id      TEXT PRIMARY KEY,
    legacy_id    TEXT,
    query        TEXT,              -- the search that surfaced it
    title        TEXT,
    price_gbp    REAL,
    -- ⚠️ Kept because a comp is only comparable in LANDED terms: a
    -- free-postage sale and a £5-postage sale are not the same price.
    postage_gbp  REAL NOT NULL DEFAULT 0.0,
    -- ⚠️ An accepted BEST_OFFER price is never exposed; the ended listing keeps
    -- showing its ASK. So a sold comp from a best-offer listing is an upper
    -- bound, not a price, and must be excluded rather than averaged in.
    best_offer   INTEGER NOT NULL DEFAULT 0,
    first_seen   REAL NOT NULL,
    last_checked REAL,
    checks       INTEGER NOT NULL DEFAULT 0,
    ended_at     TEXT,              -- itemEndDate, ISO8601, NULL while live
    -- 1 sold | 0 ended unsold | -1 still-live variation group | NULL still live
    sold         INTEGER,
    sold_qty     INTEGER,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_watch_open  ON watchlist(sold, last_checked);
CREATE INDEX IF NOT EXISTS idx_watch_query ON watchlist(query);

-- Self-counted daily Browse spend.
-- ⚠️ eBay's own analytics counter LAGS BY MINUTES AND MOVES IN STEPS OF TEN, so
-- it is fine for a daily sanity check and useless as a governor. The sweep must
-- police itself.
CREATE TABLE IF NOT EXISTS api_budget (
    day  TEXT NOT NULL,
    name TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);

CREATE TABLE IF NOT EXISTS trawl_cache (
    q          TEXT NOT NULL,
    site       TEXT NOT NULL DEFAULT 'EBAY_GB',
    comps_json TEXT NOT NULL,
    hit        INTEGER NOT NULL DEFAULT 0,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (q, site)
);
"""


class Store:
    def __init__(self, path: str | Path = "watch.db"):
        self.path = str(path)
        self._c = sqlite3.connect(self.path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA busy_timeout=5000")
        self._c.executescript(SCHEMA)
        self._c.commit()

    def close(self) -> None:
        self._c.close()
