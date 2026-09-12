"""Our own sold comps, recorded rather than rented.

⚠️ EXTRACTED FROM A LARGER APPLICATION. Two functions that read that app's own
database (`harvest`, `tracked_queries`) were removed rather than generalised:
what you want tracked is your business, not this module's. Feed `track()` the
queries you care about - see `__main__.py`.

WHY THIS EXISTS. `trawl.py` buys completed sales at 250 requests a month.
Everything else free gives ASKING prices, and an ask is an opinion. This module
gets a real sold price and a real sold date from eBay's Browse API — the one we
already hold credentials for — by watching listings until they end.

⭐ THE MEASURED FACT IT RESTS ON. An ended eBay listing does NOT disappear from
the API. `GET /item/{itemId}` returns HTTP 200 with `itemEndDate` set, and the
outcome is readable:

    OUT_OF_STOCK, estimatedRemainingQuantity 0, estimatedSoldQuantity >= 1  -> SOLD
    IN_STOCK,     estimatedRemainingQuantity 1, estimatedSoldQuantity 0     -> ended unsold

Verified on 30 real listings re-fetched 6-8 days after capture: 30/30 returned
200, two had ended, one of those had sold. Live listings carry
`itemEndDate: null`, so no search-diffing is needed to notice an ending.

⚠️⚠️ WHAT THIS IS NOT. It is not a trawl replacement on any deadline, and
pretending otherwise is how it gets switched on and then blamed. There is NO
BACKFILL — search returns active listings only, so a sale that completed before
we started watching is gone. Measured yield is ~6.7% of tracked listings ending
per week and ~3.3% selling, so a product needs ~200 concurrent tracked listings
to produce the 8 comps `pricing.py` calls "strong" inside about ten days, and a
market with fewer than ~10 live listings never reaches 5 comps inside the 60-day
window at all. It is a corpus that matures, or it is nothing. Keep trawl as the
fallback until this one can answer.

⚠️ AND THE COMPS ARE BIASED UPWARD. An accepted best-offer price is never
exposed - the ended listing still shows its ASK - so a sale from a BEST_OFFER
listing is an upper bound. They are stored with a flag and excluded by default.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .comp import Comp

BROWSE = "https://api.ebay.com/buy/browse/v1"
# ⚠️ python's default User-Agent gets Cloudflare 1010 browser_signature_banned
# on api.ebay.com, exactly as trawl.py records for trawl.dev.
UA = "curl/8.5.0"

# The sweep's own daily ceiling, well under the 5000/day Browse quota that
# `identify`, `live` and `search_by_image` all share. Those failing breaks the
# product; a sweep that stops early just resumes tomorrow.
# Sized against the measured workload: ~37 tracked products x (1 search + up
# to 10 rechecks) = ~407. Still 12% of the 5000/day Browse quota, and the
# research path's own worst measured day is ~65 calls - so `identify` and
# `live` cannot be starved by a sweep even at full tilt.
DAILY_CALL_CAP = 600
BUDGET_NAME = "browse"

LEGACY_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{9,})")


def legacy_id(url: str) -> str:
    m = LEGACY_RE.search(url or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------
def _today() -> str:
    # eBay's Browse quota resets 07:00 UTC, not midnight - measured from the
    # Developer Analytics rate_limit endpoint. Counting on a midnight day would
    # let a pre-dawn sweep and a post-dawn sweep share one bucket.
    now = datetime.now(timezone.utc) - timedelta(hours=7)
    return now.strftime("%Y-%m-%d")


def spent(store, name: str = BUDGET_NAME) -> int:
    r = store._c.execute("SELECT used FROM api_budget WHERE day=? AND name=?",
                         (_today(), name)).fetchone()
    return int(r["used"]) if r else 0


def _charge(store, n: int = 1, name: str = BUDGET_NAME) -> None:
    store._c.execute(
        "INSERT INTO api_budget(day,name,used) VALUES(?,?,?)"
        " ON CONFLICT(day,name) DO UPDATE SET used = used + excluded.used",
        (_today(), name, n))
    store._c.commit()


# ---------------------------------------------------------------------------
# harvest - no API calls
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------
def _get(url: str, token: str, timeout: int = 30) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:                                      # noqa: BLE001
            return e.code, {}
    except Exception:                                          # noqa: BLE001
        return 0, {}


def read_outcome(d: dict) -> dict:
    """Turn one getItem body into {ended_at, sold, sold_qty, price, best_offer}.

    ⚠️ `estimatedRemainingQuantity` is read first: `estimatedAvailableQuantity`
    is NULL on multi-quantity listings and only populated on single-quantity
    ones, so trusting it first reads a live multi-listing as exhausted.
    """
    ea = (d.get("estimatedAvailabilities") or [{}])[0] or {}
    remaining = ea.get("estimatedRemainingQuantity")
    if remaining is None:
        remaining = ea.get("estimatedAvailableQuantity")
    sold_qty = ea.get("estimatedSoldQuantity") or 0
    status = (ea.get("estimatedAvailabilityStatus") or "").upper()
    # ⚠️⚠️ A NON-NULL itemEndDate DOES NOT MEAN ENDED. The premise this module
    # was built on - "live listings carry itemEndDate: null" - is true for
    # FIXED-PRICE ONLY. Every auction carries its scheduled end date from the
    # moment it is created, so the first version recorded every live auction as
    # "ended unsold", and because `sweep` only re-checks WHERE sold IS NULL the
    # row was then never looked at again: the sale three hours later was never
    # recorded and auctions could never contribute a comp at all.
    # ⭐ That biased the whole corpus UPWARD, since auctions are systematically
    # the cheap end of a used market - and the measured sell-rate quoted in
    # these docstrings was taken from a population that silently discarded them.
    # Reproduced on 5 real live auctions, all IN_STOCK with a FUTURE end date.
    raw_end = d.get("itemEndDate")
    ended = None
    if raw_end:
        try:
            when = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
            if when <= datetime.now(timezone.utc):
                ended = raw_end
        except ValueError:
            ended = raw_end          # unparseable: fall back to old behaviour
    sold = None
    if ended:
        # An ending is only a SALE when the stock actually went. An expiry
        # leaves remaining stock and a zero sold count.
        sold = 1 if (status == "OUT_OF_STOCK" and not remaining
                     and int(sold_qty) >= 1) else 0
    try:
        price = float(((d.get("price") or {}).get("value")) or 0) or None
    except (TypeError, ValueError):
        price = None
    # ⚠️ POSTAGE, because everything downstream works in LANDED price:
    # crosscheck.gather(sold=[c.landed_gbp ...]) and pricing.quote both do.
    # trawl.py sets postage on its Comps; ours defaulted to 0.0, so every
    # median we contributed was silently ~5% light on an £85 item - in the
    # direction crosscheck.py itself calls "the more expensive mistake".
    postage = 0.0
    for opt in (d.get("shippingOptions") or []):
        cost = (opt or {}).get("shippingCost") or {}
        try:
            postage = float(cost.get("value") or 0)
            break
        except (TypeError, ValueError):
            continue
    return {"ended_at": ended, "ends_at": raw_end, "sold": sold, "sold_qty": int(sold_qty or 0),
            "price": price,
            "postage_gbp": postage,
            "best_offer": 1 if "BEST_OFFER" in (d.get("buyingOptions") or [])
                          else 0,
            "item_id": d.get("itemId") or "",
            "title": (d.get("title") or "")[:300]}


def sweep(store, token: str, *, limit: int = 120,
          cap: int = DAILY_CALL_CAP) -> dict:
    """Re-check open listings. Returns a summary; never raises.

    Oldest-checked first, so nothing starves. Stops on the daily cap rather
    than borrowing from the quota the research path needs.
    """
    used_today = spent(store)
    room = max(0, cap - used_today)
    if room <= 0:
        return {"checked": 0, "sold": 0, "ended": 0,
                "note": f"daily cap {cap} already spent"}

    rows = store._c.execute(
        "SELECT item_id, legacy_id FROM watchlist WHERE sold IS NULL"
        " ORDER BY COALESCE(last_checked, 0) ASC LIMIT ?",
        (min(limit, room),)).fetchall()

    checked = sold = ended = gone = 0
    for r in rows:
        iid, lid = r["item_id"], r["legacy_id"]
        if iid.startswith("legacy:"):
            # ⚠️ RESOLVE, never reconstruct. This endpoint returns the full
            # item AND its real itemId, so the first check costs nothing extra.
            url = (f"{BROWSE}/item/get_item_by_legacy_id?"
                   + urllib.parse.urlencode({"legacy_item_id": lid}))
        else:
            url = f"{BROWSE}/item/{urllib.parse.quote(iid, safe='')}"
        code, body = _get(url, token)
        _charge(store)
        checked += 1

        if code != 200:
            # 400 errorId 11006 = a multi-variation group; it needs
            # get_items_by_item_group and is not a single sellable listing, so
            # it is retired rather than retried forever.
            note = f"HTTP {code}"
            # ⚠️⚠️ `sold = -1`, NOT 0. Marking a retired row "ended unsold"
            # corrupted the one statistic the trawl cut-off rests on: 11 of 103
            # rows were multi-variation groups (errorId 11006) that are STILL
            # LIVE, yet stats() reported unsold=13 when only 2 listings had
            # genuinely expired - a ~6x error in the measured sell rate.
            # ⚠️ And only 11006 retires. Retiring on ANY 400/404 buried a live
            # listing permanently after one transient failure, with no way back
            # into the open set.
            eid = str(((body.get("errors") or [{}])[0] or {}).get("errorId", ""))
            if code == 400 and eid == "11006":
                gone += 1
                store._c.execute(
                    "UPDATE watchlist SET sold=-1, ended_at=NULL,"
                    " last_checked=?, checks=checks+1,"
                    " note=? WHERE legacy_id=?",
                    (time.time(), "retired: multi-variation group", lid))
            else:
                store._c.execute(
                    "UPDATE watchlist SET last_checked=?, checks=checks+1,"
                    " note=? WHERE legacy_id=?", (time.time(), note, lid))
            continue

        o = read_outcome(body)
        if o["ended_at"]:
            ended += 1
            sold += 1 if o["sold"] else 0
        store._c.execute(
            "UPDATE watchlist SET item_id=?, title=COALESCE(NULLIF(?,''),title),"
            " price_gbp=COALESCE(?,price_gbp), best_offer=?, postage_gbp=?,"
            " ended_at=?, sold=?, sold_qty=?, last_checked=?, checks=checks+1"
            " WHERE legacy_id=?",
            (o["item_id"] or iid, o["title"], o["price"], o["best_offer"],
             o.get("postage_gbp") or 0.0, o["ended_at"], o["sold"],
             o["sold_qty"], time.time(), lid))
    store._c.commit()
    return {"checked": checked, "ended": ended, "sold": sold,
            "retired": gone, "spent_today": spent(store), "cap": cap}


# ---------------------------------------------------------------------------
# track - wide enrolment and cheap ending-detection
# ---------------------------------------------------------------------------
# ⭐ THE ARITHMETIC THAT FORCED THIS. Seeding from item payloads gave 103
# listings, ~4 per researched item, and a product needs THREE completed sales
# before pricing.py will quote it. At the measured ~9%/week sell rate, 4
# listings produce one sale roughly every three weeks - so coverage measured
# 0% on 25 real queries. The lever is not patience, it is BREADTH: 200 listings
# of one product yield ~18 sales a week.
#
# ⭐ And it is nearly free, because a search returns `itemId` for every hit.
# One call enrols up to 200 listings. The old path resolved ids one at a time
# from a stored URL's legacy id - correct, but 200x dearer.
#
# ⚠️ RE-SEARCHING IS ALSO HOW WE SPOT AN ENDING CHEAPLY. A listing that has
# left the active result set is a candidate for having ended; only those need
# a getItem. Re-checking every tracked listing every day would cost one call
# each and would not scale past the daily quota. This costs 1 + (the few that
# vanished).
SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def track(store, token: str, query: str, *, limit: int = 200,
          cap: int = DAILY_CALL_CAP, recheck_cap: int = 10) -> dict:
    """Enrol everything currently listed for `query`, and check what left.

    Returns a summary. Never raises - a sweep that fails is a sweep that
    resumes tomorrow, and it must never take the research path down with it.
    """
    if spent(store) >= cap:
        return {"query": query, "note": f"daily cap {cap} reached"}

    url = SEARCH + "?" + urllib.parse.urlencode(
        {"q": query, "limit": min(int(limit), 200)})
    code, body = _get(url, token)
    _charge(store)
    if code != 200:
        return {"query": query, "note": f"search HTTP {code}"}

    live_ids = set()
    added = 0
    now = time.time()
    for it in (body.get("itemSummaries") or []):
        iid = it.get("itemId") or ""
        if not iid:
            continue
        live_ids.add(iid)
        try:
            price = float(((it.get("price") or {}).get("value")) or 0) or None
        except (TypeError, ValueError):
            price = None
        cur = store._c.execute(
            "SELECT 1 FROM watchlist WHERE item_id=?", (iid,)).fetchone()
        if cur:
            # Still listed: push the clock forward so it is not mistaken for
            # a dropout later.
            store._c.execute(
                "UPDATE watchlist SET last_checked=? WHERE item_id=?",
                (now, iid))
            continue
        store._c.execute(
            "INSERT OR IGNORE INTO watchlist"
            "(item_id, legacy_id, query, title, price_gbp, best_offer,"
            " first_seen, last_checked)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (iid, str(it.get("legacyItemId") or ""), query,
             (it.get("title") or "")[:300], price,
             1 if "BEST_OFFER" in (it.get("buyingOptions") or []) else 0,
             now, now))
        added += 1
    store._c.commit()

    # Anything we were tracking for this query that is no longer in the
    # results has either ended or fallen off the ranking. Only those cost a
    # getItem. ⚠️ 30 minutes of grace: eBay's search index is eventually
    # consistent and a live listing can miss one page without having ended.
    # ⚠️⚠️ CAPPED, AND THE CAP IS NOT COSMETIC. A search returns at most 200
    # hits, so for any query with MORE listings than that, every tracked row
    # outside today's top 200 looks like a dropout - a query with 500 live
    # listings would demand 300 getItems a day, every day, forever, and starve
    # every other query behind it. Oldest-checked first, bounded, so a genuine
    # ending is still found within a few days and nothing runs away.
    gone = [r["item_id"] for r in store._c.execute(
        "SELECT item_id FROM watchlist WHERE query=? AND sold IS NULL"
        "  AND item_id NOT LIKE 'legacy:%' AND last_checked < ?"
        "  ORDER BY last_checked ASC LIMIT ?",
        (query, now - 1800, int(recheck_cap)))]

    checked = ended = sold = 0
    for iid in gone:
        if spent(store) >= cap:
            break
        code, d = _get(
            f"{BROWSE}/item/{urllib.parse.quote(iid, safe='')}", token)
        _charge(store)
        checked += 1
        if code != 200:
            store._c.execute(
                "UPDATE watchlist SET last_checked=?, checks=checks+1,"
                " note=? WHERE item_id=?",
                (time.time(), f"HTTP {code}", iid))
            continue
        o = read_outcome(d)
        if o["ended_at"]:
            ended += 1
            sold += 1 if o["sold"] else 0
        store._c.execute(
            "UPDATE watchlist SET price_gbp=COALESCE(?,price_gbp),"
            " best_offer=?, postage_gbp=?, ended_at=?, sold=?, sold_qty=?,"
            " last_checked=?, checks=checks+1 WHERE item_id=?",
            (o["price"], o["best_offer"], o.get("postage_gbp") or 0.0,
             o["ended_at"], o["sold"], o["sold_qty"], time.time(), iid))
    store._c.commit()
    return {"query": query, "listed": len(live_ids), "added": added,
            "rechecked": checked, "ended": ended, "sold": sold,
            "spent_today": spent(store)}


# ---------------------------------------------------------------------------
# read back
# ---------------------------------------------------------------------------
def sold_comps(store, query: str, *, window_days: int = 60,
               include_best_offer: bool = False,
               min_score: float = 0.6) -> list[Comp]:
    """Completed sales WE observed, as pricing.Comp rows.

    ⚠️ Best-offer sales are excluded by default. The listing shows its ask, not
    what was accepted, so including them prices against a number nobody paid -
    the exact mistake `from_asking` exists to avoid.
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=window_days)).isoformat()
    sql = ("SELECT title, price_gbp, postage_gbp, ended_at FROM watchlist"
           " WHERE sold=1 AND ended_at IS NOT NULL AND ended_at >= ?"
           "   AND price_gbp IS NOT NULL")
    args: list = [cutoff]
    if not include_best_offer:
        sql += " AND best_offer=0"
    out: list[Comp] = []
    for r in store._c.execute(sql, args):
        # Scored in python, not ANDed in SQL - see match_score.
        if query and match_score(query, r["title"] or "") < min_score:
            continue
        try:
            when = datetime.fromisoformat(
                str(r["ended_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(Comp(price_gbp=float(r["price_gbp"]), sold_at=when,
                        title=r["title"] or "",
                        postage_gbp=float(r["postage_gbp"] or 0.0)))
    return out


# Words that say nothing about WHICH product this is. A seller's adjectives are
# not part of the identity, and requiring them is how a real match gets missed.
_NOISE = {
    "the", "and", "for", "with", "new", "used", "genuine", "original", "free",
    "fast", "same", "day", "dispatch", "uk", "gb", "official", "boxed", "set",
    "compatible", "scientific", "quality", "great", "excellent", "condition",
    "unit", "item", "only", "inc", "vat", "postage", "delivery", "sale",
}


def _tokens(text: str) -> set[str]:
    """⚠️⚠️ SHORT TOKENS ARE KEPT. Dropping anything under three characters
    threw away the ONLY word that distinguishes the products: "Ti", "SE",
    "XL", and bare generation digits. Measured: "Nvidia RTX 3060 Ti" reduced to
    {3060, card, graphics, nvidia, rtx} - byte-identical to a plain RTX 3060 -
    and scored 1.00 against a £324 card. Single letters are still dropped; two
    characters carry meaning in this domain and one rarely does."""
    return {w.strip(".,()[]/-").lower() for w in (text or "").split()
            if len(w.strip(".,()[]/-")) >= 2}


# A token that looks like a model designation: contains a digit, or is a short
# alphanumeric suffix like "ti" / "xl" / "se". These are the identity of the
# product; the rest is category and marketing.
_MODELISH = re.compile(r"^(?=.*\d)[a-z0-9-]{2,}$|^(ti|xl|se|xs|pro|max|mini|plus)$")


def _modelish(tokens: set[str]) -> set[str]:
    return {t for t in tokens if _MODELISH.match(t)}


def match_score(query: str, title: str) -> float:
    """Fraction of the query's MEANINGFUL words that the title carries.

    ⚠️⚠️ THE FIRST VERSION REQUIRED EVERY TOKEN AND FOUND NOTHING. Measured on
    real data: the desk held FOUR completed Casio FX-CG50 sales and the query
    "Casio FX-CG50 Scientific A Level Compatible Graphing Calculator" returned
    ZERO, because "scientific" and "compatible" are the seller's adjectives and
    appear in no sold title. An AND across every word is not a search, it is a
    filter that only passes an exact re-listing of the same wording.
    """
    q = _tokens(query) - _NOISE
    if not q:
        return 0.0
    t = _tokens(title)

    # ⭐ EVERY MODEL-ISH TOKEN MUST BE PRESENT, and that is a veto, not a
    # weighting. Measured false positives at 0.6 on plain overlap:
    #   "RTX 3060 Ti"  matched a plain RTX 3060 at 1.00  (£324, ~4x wrong)
    #   "Nest Mini"    matched "Nest Audio" at 0.67      (£20 item vs £90-130)
    #   "WH-1000XM5"   matched WH-1000XM3 at 0.75
    #   "FX-CG50"      matched an FX-991EX at 0.67       (£85 item vs £14.70)
    # Brand plus category words alone clear any sane threshold, so the model
    # number cannot be one vote among five - it has to be a gate. Pricing an
    # item off a DIFFERENT product's sales is the worst failure this file has.
    want = _modelish(q)
    if want and not want.issubset(t):
        return 0.0
    return len(q & t) / len(q)


def stats(store) -> dict:
    """⚠️ `retired` is reported SEPARATELY from `unsold`. Folding them together
    is what made the measured sell rate wrong by ~6x, and that rate is the
    number the decision to stop paying trawl rests on."""
    q = store._c.execute(
        "SELECT COUNT(*) n, SUM(sold IS NULL) open, SUM(sold=1) sold,"
        " SUM(sold=0) unsold, SUM(sold=-1) retired FROM watchlist").fetchone()
    ended = (q["sold"] or 0) + (q["unsold"] or 0)
    return {"tracked": q["n"] or 0, "open": q["open"] or 0,
            "sold": q["sold"] or 0, "unsold": q["unsold"] or 0,
            "retired": q["retired"] or 0,
            "sell_rate": round((q["sold"] or 0) / ended, 3) if ended else None,
            "spent_today": spent(store)}
