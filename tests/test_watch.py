"""Our own sold comps, from watching listings end.

The decisive measured fact: an ended eBay listing returns HTTP 200 with
`itemEndDate` set, and sold-vs-unsold is readable from the availability block.
These fixtures are the two real bodies observed on 2026-09-11.
"""
import sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soldshadow import watch
from soldshadow.store import Store

SOLD = {
    "itemId": "v1|267770423863|0", "title": "Gigabyte Radeon HD 6850",
    "itemEndDate": "2026-09-07T15:35:27.000Z",
    "price": {"value": "10.00", "currency": "GBP"},
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK",
                                 "estimatedSoldQuantity": 1,
                                 "estimatedRemainingQuantity": 0}]}
UNSOLD = {
    "itemId": "v1|267655836462|0", "title": "Breast pump",
    "itemEndDate": "2026-09-07T13:00:02.000Z",
    "price": {"value": "34.50", "currency": "GBP"},
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK",
                                 "estimatedSoldQuantity": 0,
                                 "estimatedRemainingQuantity": 1}]}
LIVE = {
    "itemId": "v1|111|0", "title": "Still for sale", "itemEndDate": None,
    "price": {"value": "20.00", "currency": "GBP"},
    "buyingOptions": ["FIXED_PRICE"],
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK",
                                 "estimatedSoldQuantity": 0,
                                 "estimatedRemainingQuantity": 1}]}
# Multi-quantity: estimatedAvailableQuantity is NULL, only remaining is set.
MULTI_LIVE = {
    "itemId": "v1|222|0", "title": "Cable, many", "itemEndDate": None,
    "price": {"value": "4.25", "currency": "GBP"},
    "buyingOptions": ["FIXED_PRICE"],
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK",
                                 "estimatedAvailableQuantity": None,
                                 "estimatedSoldQuantity": 644,
                                 "estimatedRemainingQuantity": 893}]}


class Outcome(unittest.TestCase):
    def test_sold_is_recognised(self):
        o = watch.read_outcome(SOLD)
        self.assertEqual(o["sold"], 1)
        self.assertEqual(o["price"], 10.0)
        self.assertTrue(o["ended_at"])

    def test_ended_but_unsold_is_not_a_sale(self):
        """⭐ An expiry leaves stock behind and a zero sold count. Counting it
        as a sale would invent a comp at a price nobody paid."""
        self.assertEqual(watch.read_outcome(UNSOLD)["sold"], 0)

    def test_a_live_listing_is_neither(self):
        """None, not 0 - 'still running' must not read as 'ended unsold'."""
        self.assertIsNone(watch.read_outcome(LIVE)["sold"])

    def test_multi_quantity_live_is_not_read_as_exhausted(self):
        """⚠️ estimatedAvailableQuantity is NULL on multi-quantity listings.
        Reading it first makes a busy live listing look sold out."""
        o = watch.read_outcome(MULTI_LIVE)
        self.assertIsNone(o["sold"])

    def test_best_offer_is_flagged(self):
        self.assertEqual(watch.read_outcome(SOLD)["best_offer"], 1)
        self.assertEqual(watch.read_outcome(LIVE)["best_offer"], 0)


class Ids(unittest.TestCase):
    def test_legacy_id_from_a_real_url(self):
        self.assertEqual(watch.legacy_id(
            "https://www.ebay.co.uk/itm/205418877120?_skw=X&hash=item2f:g:l-U"),
            "205418877120")

    def test_no_id_is_empty_not_a_guess(self):
        self.assertEqual(watch.legacy_id("https://example.com/thing"), "")


class Comps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.dir.name) / "t.db"))
        now = datetime.now(timezone.utc)
        rows = [("v1|1|0", "1", "Casio FX-CG50", 89.99, 0, now - timedelta(days=2)),
                ("v1|2|0", "2", "Casio FX-CG50 offer", 120.0, 1, now - timedelta(days=2)),
                ("v1|3|0", "3", "Casio FX-CG50 old", 70.0, 0, now - timedelta(days=400))]
        for iid, lid, title, price, bo, when in rows:
            self.store._c.execute(
                "INSERT INTO watchlist(item_id,legacy_id,query,title,price_gbp,"
                "best_offer,first_seen,ended_at,sold) VALUES(?,?,?,?,?,?,?,?,1)",
                (iid, lid, "casio", title, price, bo, 0, when.isoformat()))
        self.store._c.commit()

    def tearDown(self):
        self.dir.cleanup()

    def test_best_offer_sales_are_excluded_by_default(self):
        """⚠️ The ended listing shows its ASK, never the accepted offer. Averaging
        those in prices against a number nobody paid."""
        got = self.store and watch.sold_comps(self.store, "casio")
        self.assertEqual([c.price_gbp for c in got], [89.99])

    def test_they_can_be_asked_for_as_upper_bounds(self):
        got = watch.sold_comps(self.store, "casio", include_best_offer=True)
        self.assertEqual(sorted(c.price_gbp for c in got), [89.99, 120.0])

    def test_the_window_is_honoured(self):
        got = watch.sold_comps(self.store, "casio", window_days=3650,
                               include_best_offer=True)
        self.assertEqual(len(got), 3)

    def test_sold_at_is_a_datetime(self):
        got = watch.sold_comps(self.store, "casio")
        self.assertIsInstance(got[0].sold_at, datetime)


if __name__ == "__main__":
    unittest.main()
