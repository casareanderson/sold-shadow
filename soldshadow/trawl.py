"""Real eBay sold comps, from trawl.dev.

The only affordable source that returns a **sold date**. Everything free gives
asking prices, and an ask is an opinion — see docs/SOURCES.md.

Metered: 250 requests/month on the free tier, resetting on the 1st (UTC). So
this is called only when the retail anchor cannot answer, and every result is
cached by model. A repeat model must never cost a second request.

⚠️ THREE THINGS THE INTERFACE DOES NOT TELL YOU, each of which cost a request
to discover:
  * paths are `/<product>/v1/<endpoint>`, so `/ebay/v1/sold` — not `/sold`
  * auth is the header `x-api-key`, NOT `Authorization: Bearer`
  * `site` must be `EBAY_GB` or `EBAY_US` — not `UK`
And a fourth from this estate: Cloudflare rejects python's default user-agent
with **error 1010 browser_signature_banned**, so a UA must be set.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .comp import Comp

BASE = "https://api.trawl.dev/ebay/v1/sold"
UA = "curl/8.5.0"          # see the module docstring - CF 1010 otherwise


class TrawlError(RuntimeError):
    pass


def _parse_date(raw: str | None) -> datetime | None:
    """Trawl sends `2026-09-01T22:08:00.000Z` - ISO 8601 WITH MILLISECONDS.

    fromisoformat first because it handles the fraction and the `Z` natively;
    the strptime list is a fallback for shapes it will not take. An earlier
    version had only whole-second formats, so every comp failed to parse and
    the pricing engine reported "no completed sales" - a silent zero that looks
    exactly like a product nobody has ever sold.
    """
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# How long a completed-sale answer stays good. Sales already happened - they do
# not change - so the only thing ageing costs us is NEW sales appearing. Against
# a 60-day pricing window (DESK_WINDOW_DAYS) a day-old answer moves a median by
# nothing, and the allowance is 250 A MONTH.
CACHE_TTL_S = 24 * 3600


def _norm(q: str) -> str:
    """The cache key. Whitespace and case only - never word reordering.

    Deliberately timid: "RTX 3060" and "3060 RTX" are NOT treated as the same
    query, because they are not the same search and conflating them would serve
    the wrong comps to save a request. Saving an allowance by answering the
    wrong question is not a saving.
    """
    return " ".join((q or "").lower().split())


def _freeze(c) -> dict:
    """A Comp as plain JSON. `landed_gbp` is a PROPERTY and is not stored."""
    return {"price_gbp": c.price_gbp, "title": c.title, "url": c.url,
            "postage_gbp": c.postage_gbp,
            "sold_at": c.sold_at.isoformat() if c.sold_at else None}


def _revive(d: dict) -> Comp:
    """JSON back to a Comp.

    ⚠️⚠️ `sold_at` MUST come back a datetime, not a string. `pricing.quote`
    filters comps by age against a 60-day window, and a str/datetime comparison
    either raises or - worse, if anything ever coerces - silently keeps the
    wrong sales. Round-tripping a dataclass through json is exactly where that
    kind of corruption hides, because everything still LOOKS like a Comp.
    """
    raw = d.get("sold_at")
    when = None
    if raw:
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            when = _parse_date(str(raw))
    return Comp(price_gbp=float(d.get("price_gbp") or 0.0), sold_at=when,
                title=d.get("title") or "", url=d.get("url") or "",
                postage_gbp=float(d.get("postage_gbp") or 0.0))


def cached_sold(query: str, api_key: str, store=None, *, site: str = "EBAY_GB",
                ttl_s: int = CACHE_TTL_S, **kw) -> list[Comp]:
    """`sold()`, but honouring the cache the docstring always promised.

    ⚠️ Falls through to a live call when `store` is None so that every existing
    caller keeps working unchanged - but a caller with no store gets no saving,
    which is the whole point of passing one.
    """
    import json as _json
    import time as _time

    if store is None:
        return sold(query, api_key, site=site, **kw)

    key = _norm(query)
    row = store._c.execute(
        "SELECT comps_json, fetched_at FROM trawl_cache WHERE q=? AND site=?",
        (key, site)).fetchone()
    if row and (_time.time() - float(row["fetched_at"])) < ttl_s:
        store._c.execute(
            "UPDATE trawl_cache SET used = used + 1 WHERE q=? AND site=?",
            (key, site))
        store._c.commit()
        return [_revive(c) for c in _json.loads(row["comps_json"])]

    comps = sold(query, api_key, site=site, **kw)
    store._c.execute(
        "INSERT INTO trawl_cache(q,site,comps_json,hit,fetched_at,used)"
        " VALUES(?,?,?,?,?,0)"
        " ON CONFLICT(q,site) DO UPDATE SET comps_json=excluded.comps_json,"
        "   hit=excluded.hit, fetched_at=excluded.fetched_at",
        (key, site, _json.dumps([_freeze(c) for c in comps]),
         len(comps), _time.time()))
    store._c.commit()
    return comps


def sold(query: str, api_key: str, *, site: str = "EBAY_GB",
         limit: int = 12, timeout: int = 60) -> list[Comp]:
    """Completed sales for `query`. ONE request against the monthly allowance.

    `limit` defaults to 12, not the API's own default: a median stabilises
    around ten comps and `pricing.py` already calls 8+ "strong", so asking for
    more spends allowance on precision we do not use.
    """
    q = urllib.parse.urlencode({"query": query, "site": site, "limit": limit})
    req = urllib.request.Request(
        f"{BASE}?{q}", headers={"x-api-key": api_key,
                                "Accept": "application/json",
                                "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        if e.code == 429 and "retry-after" not in {k.lower() for k in e.headers}:
            # No Retry-After means the MONTHLY allowance is gone. Retrying that
            # is pointless and the API says so explicitly - do not loop.
            raise TrawlError(f"monthly allowance exhausted: {body}") from e
        raise TrawlError(f"HTTP {e.code}: {body}") from e
    except Exception as e:                                    # noqa: BLE001
        raise TrawlError(f"{type(e).__name__}: {e}") from e

    out: list[Comp] = []
    for row in payload.get("results") or []:
        when = _parse_date(row.get("date_sold"))
        price = row.get("sale_price")
        if when is None or not price:
            # A comp with no date cannot be windowed, so it is not evidence.
            continue
        out.append(Comp(
            price_gbp=float(price),
            sold_at=when,
            title=str(row.get("title") or "")[:160],
            url=str(row.get("item_link") or ""),
            postage_gbp=float(row.get("shipping_price") or 0),
        ))
    return out
