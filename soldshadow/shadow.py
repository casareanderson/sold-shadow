"""Measure whether our own comps can replace trawl yet. `python -m desk.shadow`.

⭐ THE POINT: "can we switch trawl off?" is an empirical question and was being
answered by feel. This asks BOTH sources the same queries and reports how often
we can answer at all, and how far off we are when we can. That turns the
cut-over into a criterion instead of a hunch.

⚠️ IT SPENDS TRAWL ALLOWANCE ON PURPOSE — one request per uncached query. That
is the right use of a metered resource you are trying to stop paying for:
proving you can stop. It honours the 24h cache, so re-running the same day is
free. Default is a hard cap of 10 queries per run.

⛔ WHAT THIS DELIBERATELY IS NOT. It does not inspect, reverse-engineer or
replicate where trawl.dev gets its data. Every third-party "eBay sold comps"
service is reselling eBay's blocked sold search, and this app holds eBay API
credentials under the API License Agreement — credentials that run publishing,
repricing, orders and a customer's inventory. Scraping from the same estate
risks the keyset, and the keyset is the product. The comparison below is
against trawl's OUTPUT, which we already pay for and are entitled to read.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Row:
    query: str
    ours: int = 0
    # ⭐ Sales from listings that accepted offers - upper bounds, priced as a
    # ceiling since 2.23, and 10 of the first 14 sales the desk recorded.
    # Counting clean comps alone measured readiness for a pricing path that
    # no longer exists.
    bound: int = 0
    theirs: int = 0
    our_median: float | None = None
    their_median: float | None = None

    @property
    def evidence(self) -> int:
        return self.ours + self.bound

    @property
    def delta_pct(self) -> float | None:
        """How far our median sits from theirs, as a percentage of theirs."""
        if not (self.our_median and self.their_median):
            return None
        return (self.our_median - self.their_median) / self.their_median * 100


@dataclass
class Verdict:
    rows: list = field(default_factory=list)
    ready: bool = False
    reason: str = ""


# The bar for switching trawl off. Both must hold, and they are deliberately
# separate: agreeing beautifully on the two queries we can answer is not the
# same as being able to answer.
MIN_COVERAGE = 0.80      # we can answer 80% of the queries trawl was asked
MAX_MEDIAN_DELTA = 15.0  # and our median sits within 15% of theirs


def compare(store, queries: list[str], trawl_key: str, *,
            window_days: int = 60, limit: int = 10) -> Verdict:
    from . import trawl as trawl_mod, watch as watch_mod

    rows: list[Row] = []
    for q in [x for x in queries if x][:limit]:
        r = Row(query=q)
        ours = watch_mod.sold_comps(store, q, window_days=window_days)
        bound = watch_mod.best_offer_comps(store, q, window_days=window_days)
        r.ours, r.bound = len(ours), len(bound)
        # The median is taken over everything the price would see. A bound is
        # an upper bound on one sale, so this median leans high by however
        # much offers were accepted under ask - the "gap" column shows it.
        if ours or bound:
            r.our_median = round(statistics.median(
                c.price_gbp for c in ours + bound), 2)
        try:
            theirs = trawl_mod.cached_sold(q, trawl_key, store)
        except Exception:                                      # noqa: BLE001
            theirs = []
        r.theirs = len(theirs)
        if theirs:
            r.their_median = round(statistics.median(
                c.price_gbp for c in theirs), 2)
        rows.append(r)

    answerable = [r for r in rows if r.theirs]
    covered = [r for r in answerable if r.evidence >= 3]
    coverage = len(covered) / len(answerable) if answerable else 0.0
    deltas = [abs(r.delta_pct) for r in covered if r.delta_pct is not None]
    worst = max(deltas) if deltas else None

    v = Verdict(rows=rows)
    if not answerable:
        v.reason = "trawl answered nothing - inconclusive, not a pass"
    elif coverage < MIN_COVERAGE:
        v.reason = (f"coverage {coverage:.0%} of {len(answerable)} answerable "
                    f"quer(ies) - need {MIN_COVERAGE:.0%}")
    elif worst is not None and worst > MAX_MEDIAN_DELTA:
        v.reason = (f"coverage {coverage:.0%} but worst median gap {worst:.0f}% "
                    f"- need under {MAX_MEDIAN_DELTA:.0f}%")
    else:
        v.ready = True
        v.reason = (f"coverage {coverage:.0%}, worst median gap "
                    f"{(worst or 0):.0f}% - trawl can be switched off")
    return v
