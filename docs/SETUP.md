# Setting up sold-shadow

Ten minutes to install and start tracking. Then the honest part: it needs **weeks** before it has
anything to show, because listings have to end before there's a sale to record, and there's no
backfill. Start it today.

The steps marked **CI checks this** run on a fresh Ubuntu machine every week by
[`setup-check`](../.github/workflows/setup-check.yml). CI has no eBay keyset, so it never calls eBay.

## What you need

| | |
|---|---|
| OS | anything with Python. A Linux box with cron for the daily sweep. |
| Python | **3.11 or newer** (CI runs 3.11 and 3.13). Nothing to install: standard library only. |
| eBay | a free [eBay developer account](https://developer.ebay.com/) and a **production** keyset (App ID and Cert ID). The Browse API needs no user consent. |
| For the token helper | `curl` |
| Optional | a [trawl.dev](https://trawl.dev/) key, only for `compare` (measuring this against the paid source) |

## 1. Install (CI checks this)

```bash
git clone https://github.com/casareanderson/sold-shadow && cd sold-shadow
python3 -m unittest            # 26 tests, no network, no token; ends with OK
python3 -m soldshadow --help
```

## 2. Get an eBay token

`soldshadow` reads a ready-made **application token** from `EBAY_TOKEN`. App tokens expire after a
couple of hours, so don't paste one into a file: mint a fresh one each run. The repo ships a helper
(**CI checks** it fails cleanly without keys, and parses a token response):

```bash
export EBAY_CLIENT_ID="your App ID"
export EBAY_CLIENT_SECRET="your Cert ID"
export EBAY_TOKEN=$(scripts/ebay-token.sh)
```

It asks eBay's token endpoint for a client-credentials token with the public
`https://api.ebay.com/oauth/api_scope` scope, and prints only the token.

## 3. Start tracking

```bash
python3 -m soldshadow track "Casio fx-CG50"
python3 -m soldshadow track "Casio FX-CG50 graphing calculator"   # a second phrasing finds more
python3 -m soldshadow stats
```

Success is `track` printing a line per query, and `stats` showing `tracked` above 0. With an empty
corpus (**CI checks this**) `stats` shows all zeros and `comps` says `no sold comps yet for that query`.
That's normal for days or weeks.

Tips that come from real runs (see the README for the numbers):
- Use **two or three phrasings** per product. Each finds a different slice of what's listed.
- Keep a phrasing to **eight words or fewer**. A long one ANDs every word and finds less.
- For many products, put one query per line in a file and `track -f queries.txt` (`#` lines are skipped).

## 4. Sweep every day

The sweep re-checks the listings you're watching and records the ones that ended. Run it daily from cron,
minting a token each time:

```cron
40 6 * * *  cd /opt/sold-shadow && EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... sh -c 'EBAY_TOKEN=$(scripts/ebay-token.sh) python3 -m soldshadow sweep' >> sweep.log 2>&1
```

Better: put the two keys in a file only you can read (`chmod 600`) and source it in the cron line,
rather than leaving them in the crontab.

The sweep stops itself at `DAILY_CALL_CAP` (600 Browse calls a day, set in `watch.py`) so it can't eat
your whole eBay quota.

## 5. Read the comps

```bash
python3 -m soldshadow comps "Casio fx-CG50"
python3 -m soldshadow comps "Casio fx-CG50" --include-best-offer --window-days 90
```

One line per sale: landed price, sold date, title. Best-offer sales are left out by default because eBay
never shows the accepted offer, only the ask. `--include-best-offer` adds them, but treat them as a
ceiling, not a price.

## 6. Optional: measure it against what you pay for

```bash
export TRAWL_KEY="your trawl.dev key"
python3 -m soldshadow compare -f queries.txt
```

This is the only command that calls a paid service: one request per uncached query, at most 10 per run,
cached for 24 hours. The last line says `READY: True` or `READY: False` with the reason.

## Settings

| name | default | what it does |
|---|---|---|
| `EBAY_TOKEN` | none, needed for `track` and `sweep` | eBay application token (step 2) |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | none | read by `scripts/ebay-token.sh` only |
| `TRAWL_KEY` | none, needed for `compare` | trawl.dev API key |
| `SOLDSHADOW_DB` or `--db` | `watch.db` in the current folder | the SQLite file: watchlist, daily spend counter, paid-source cache. Run from the same folder (or set this) every time. |
| `track --limit` | 200 | listings to enrol per query |
| `sweep --limit` | 120 | open listings to re-check per run |
| `--window-days` | 60 | how far back a sale counts (`comps`, `compare`) |

## Common errors

| you see | what's wrong |
|---|---|
| `EBAY_TOKEN is not set` | step 2. In cron, the token has to be minted inside the same command. |
| `nothing to track: pass queries or -f queries.txt` | give `track` a query or a file |
| `TRAWL_KEY is not set` | `compare` only. Nothing else needs it. |
| `set EBAY_CLIENT_ID` / `set EBAY_CLIENT_SECRET` | the token helper needs both keys |
| `no access_token from eBay: {"error":"invalid_client"...}` | wrong App ID or Cert ID, or you used the **sandbox** keyset against production |
| `search HTTP 401` in the `track` output | the token expired. Mint a new one. |
| `stats` shows 0 tracked after tracking | you ran from a different folder, so it opened a different `watch.db`. Use `--db` or `SOLDSHADOW_DB`. |
| `comps` says no sold comps for weeks | normal for slow-selling products. See "There is no backfill, ever" in the README. |

## Updating

```bash
cd sold-shadow && git pull && python3 -m unittest
```

Your corpus lives in `watch.db`, which isn't in the repo, so a pull keeps it. Back it up: it can't be
rebuilt.

## Uninstalling

Remove the cron line, then delete the folder (and `watch.db` if you kept it elsewhere). In the eBay
developer portal you can delete the keyset too.

## What CI can't prove

The weekly check runs the 26 offline tests, loads every command, checks the empty-corpus output, the
missing-key and empty-input messages, and that the token helper fails cleanly and reads a token
response. It never calls eBay or trawl.dev (no keys in CI, and eBay's quota is real), so it can't prove
your keyset works, that eBay's API still answers the same way, or how long it takes your products to
build up comps.
