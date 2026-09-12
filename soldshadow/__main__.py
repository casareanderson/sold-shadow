"""CLI. `python -m soldshadow <command>`.

The seam this tool is built around: YOU say what to track. There is no category
sweep and no "track everything" mode, because the quota is small and a corpus
of products you will never sell is worth nothing.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import shadow as shadow_mod, watch as watch_mod
from .store import Store


def _queries(args) -> list[str]:
    if args.queries:
        return args.queries
    if args.file:
        with open(args.file) as fh:
            return [ln.strip() for ln in fh if ln.strip()
                    and not ln.startswith("#")]
    return []


def _token() -> str:
    tok = os.environ.get("EBAY_TOKEN", "").strip()
    if not tok:
        sys.exit("EBAY_TOKEN is not set. It is an application (client "
                 "credentials) token for the Browse API - no user consent "
                 "needed. See the README.")
    return tok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="soldshadow")
    p.add_argument("--db", default=os.environ.get("SOLDSHADOW_DB", "watch.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("track", help="find live listings for a query and watch them")
    t.add_argument("queries", nargs="*")
    t.add_argument("-f", "--file", help="one query per line")
    t.add_argument("--limit", type=int, default=200)

    s = sub.add_parser("sweep", help="re-check watched listings; record the ones that ended")
    s.add_argument("--limit", type=int, default=120)

    sub.add_parser("stats", help="what the corpus holds")

    c = sub.add_parser("compare", help="measure the corpus against trawl.dev")
    c.add_argument("queries", nargs="*")
    c.add_argument("-f", "--file")
    c.add_argument("--window-days", type=int, default=60)

    sh = sub.add_parser("comps", help="the sold comps for one query")
    sh.add_argument("query")
    sh.add_argument("--window-days", type=int, default=60)
    sh.add_argument("--include-best-offer", action="store_true")

    args = p.parse_args(argv)
    store = Store(args.db)

    if args.cmd == "track":
        qs = _queries(args)
        if not qs:
            sys.exit("nothing to track: pass queries or -f queries.txt")
        tok = _token()
        total = {}
        for q in qs:
            total[q] = watch_mod.track(store, tok, q, limit=args.limit)
        for q, r in total.items():
            print(f"{q}: {r}")
        return 0

    if args.cmd == "sweep":
        print(watch_mod.sweep(store, _token(), limit=args.limit))
        return 0

    if args.cmd == "stats":
        for k, v in watch_mod.stats(store).items():
            print(f"{k:>12}  {v}")
        return 0

    if args.cmd == "comps":
        rows = watch_mod.sold_comps(store, args.query,
                                    window_days=args.window_days,
                                    include_best_offer=args.include_best_offer)
        if not rows:
            print("no sold comps yet for that query")
            return 0
        for c in rows:
            print(f"£{c.landed_gbp:>8.2f}  {c.sold_at:%Y-%m-%d}  {c.title[:60]}")
        return 0

    if args.cmd == "compare":
        key = os.environ.get("TRAWL_KEY", "").strip()
        if not key:
            sys.exit("TRAWL_KEY is not set - compare needs the paid source it "
                     "is measuring against.")
        qs = _queries(args)
        if not qs:
            sys.exit("nothing to compare: pass queries or -f queries.txt")
        v = shadow_mod.compare(store, qs, key, window_days=args.window_days)
        print(f"{'ours':>5} {'trawl':>6} {'ourMed':>9} {'theirMed':>9} {'gap':>7}  query")
        for r in v.rows:
            gap = f"{r.delta_pct:+.0f}%" if r.delta_pct is not None else "-"
            print(f"{r.ours:>5} {r.theirs:>6} {(r.our_median or 0):>9.2f} "
                  f"{(r.their_median or 0):>9.2f} {gap:>7}  {r.query[:44]}")
        print(f"\nREADY: {v.ready} - {v.reason}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
