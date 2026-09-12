# sold-shadow

**Build your own eBay sold-comp corpus from the official API — then measure,
rather than assume, whether it can replace the one you pay for.**

eBay's completed-listing search is closed. Everything free gives you *asking*
prices, and an ask is an opinion. The paid resellers of sold data are metered:
mine is 250 requests a month.

So this does the slow thing instead. It watches live listings until they end,
and records the ones that sold. And because "can I stop paying now?" is an
empirical question, it ships with the harness that answers it.

---

## The finding it rests on

An ended eBay listing **does not disappear from the API**. `GET /item/{itemId}`
returns HTTP 200 with `itemEndDate` set, and the outcome is readable:

```
OUT_OF_STOCK, estimatedRemainingQuantity 0, estimatedSoldQuantity >= 1  -> SOLD
IN_STOCK,     estimatedRemainingQuantity 1, estimatedSoldQuantity 0     -> ended unsold
```

Verified on 30 real listings re-fetched 6–8 days after capture: 30/30 returned
200, two had ended, one of those had sold.

This uses the **Browse API** with an application token — client credentials, no
user consent, no scraping, nothing outside the API License Agreement.

---

## What it actually produces, measured

Numbers from the install this was extracted from, on **2026-09-12** (evening),
after about a week of daily sweeps:

| | |
|---|---|
| listings tracked | **3,190** |
| distinct queries | **54** |
| confirmed **sold** | **14** |
| of which best-offer (upper bounds, see below) | **9** |
| **usable** sold comps | **5** |
| queries with ≥1 usable comp | **5 / 54 = 9%** |

The switch-off bar in `shadow.py` is **80% coverage** and **within 15% of the
paid source's median**. So the honest verdict on that install is: *not close*.
One query has a real cluster (four Casio FX-CG50 sales in four days); the rest
are n=1.

That is not a disclaimer, it is the point. **A corpus matures or it is
nothing**, and the only way to know which you have is to measure it.

---

## Four things that will bite you

**⚠️ There is no backfill, ever.** Search returns *active* listings only. A sale
that completed before you started watching is gone. The single highest-value
thing you can do with this repo is start it today and ignore it for a month.

**⚠️ Best-offer sales are upper bounds, not prices.** eBay never exposes an
accepted offer — the ended listing still shows its ask. On the data above that
is **9 of 14 sales**, so excluding them costs you 64% of your comps and
including them prices against a number nobody paid. They are stored with a flag,
excluded from `sold_comps()` by default, and returned separately by
`best_offer_comps()` so a caller can use them as what they are: a **ceiling**.

⚠️ Read what the flag actually means before deciding. It is set from
`"BEST_OFFER" in buyingOptions`, which says the *listing accepted offers* — not
that this sale went through one. On eBay UK that is most used fixed-price
listings. On the install above it was 9 of the first 14 sales, and the one
product holding both kinds (a Casio FX-CG50: one clean sale at £89.99, three
best-offer asks at £80.00, £83.90, £88.99) had the excluded asks sitting *below*
the clean sale. Discarding them was not the conservative choice it looked like.
`compare` therefore counts them toward coverage in their own `bound` column.

**⚠️ Auctions carry `itemEndDate` from the moment they are created.** The
premise "live listings have a null end date" is true for fixed-price only. An
earlier version of this code filed every auction as *ended unsold* while it was
still taking bids, and never looked at the row again — so auctions could never
contribute a comp, and auctions are the cheap end of a used market. The corpus
was biased upward by construction. A *future* end date now leaves the row open.

---

**⚠️ One search string finds one slice.** eBay UK holds about twenty live
listings of any mid-volume used product at once, and a single query does not
see all of them: the same case is listed as "Argon ONE M.2", "Raspberry Pi Argon
One With" and "Argon One V2 aluminium", and each phrasing returns a different
subset. Measured on the install above, adding a second and third phrasing for
the same product in one sweep:

| product | one phrasing | up to three |
|---|---|---|
| Apple Magic Mouse | 210 | **388** |
| Casio fx-CG50 | 18 | **104** |
| Xiaomi TV Box S | 36 | **81** |
| Raspberry Pi Argon ONE | 23 | **23** |

Sweep-wide that was **+640 listings for +100 calls**. The Argon did not move
because there was nothing more to find — phrasings are a lever on volume, not a
fix for a niche product, and only the calendar fixes those. Two rules from the
same run: keep a phrasing to **eight words or fewer** (a long string ANDs every
word and returns *less*), and cap it at **three per product** so a sweep of
thirty products stays under a hundred searches. `track` takes each phrasing as
its own line; `comps` and `compare` match by *title*, so a listing enrolled
under any phrasing is found from any of them.

---

## Use

```bash
export EBAY_TOKEN="$(your client-credentials token)"

python -m soldshadow track "Casio FX-CG50 graphing calculator"
python -m soldshadow track "Casio fx-CG50"       # a second phrasing of the same product
python -m soldshadow track -f queries.txt        # one per line

python -m soldshadow sweep                       # daily, from cron
python -m soldshadow stats
python -m soldshadow comps "Casio FX-CG50 graphing calculator"
```

A daily sweep, before your own quota resets:

```cron
40 6 * * *  cd /opt/sold-shadow && python -m soldshadow sweep >> sweep.log 2>&1
```

**You choose what is tracked.** There is no category sweep and no "track
everything" mode: the quota is finite and a corpus of products you will never
sell is worth nothing. `track` spends one search; `sweep` spends one call per
open listing per day, self-capped at 600 (`DAILY_CALL_CAP`), against a 5,000/day
Browse allowance.

### Measuring it against what you pay for

```bash
export TRAWL_KEY="..."
python -m soldshadow compare -f queries.txt
```

```
 ours  trawl    ourMed  theirMed     gap  query
    4      9     85.00     87.50     -3%  Casio FX-CG50 graphing calculator
    0      6      0.00     44.99       -  Google Nest Mini 2nd gen
...
READY: False - coverage 20% of 5 answerable quer(ies) - need 80%
```

It spends one paid request per uncached query, on purpose: proving you can stop
paying for something is a fair use of the last of it. The 24-hour cache means
re-running the same day is free. `compare` is the only part that talks to a paid
service, and nothing else in this repo needs it.

## What this deliberately is not

It does not scrape, reverse-engineer, or replicate where any paid sold-comps
service gets its data. Every one of them is reselling eBay's blocked sold
search; the credentials that make *this* approach work are the same credentials
that run publishing and orders, and they are worth more than the comps. The
comparison in `shadow.py` is against a paid service's **output**, which you have
paid for and are entitled to read.

## Requires

Python 3.11+. **No dependencies** — stdlib only.

## Licence

MIT. Extracted from a private listing tool; issues and pull requests welcome.
