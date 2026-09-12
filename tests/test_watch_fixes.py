"""Regressions for the defects an adversarial review found in watch.py.

Every one of these was CONFIRMED against the live eBay API before it was fixed.
"""
import sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soldshadow import watch
from soldshadow.store import Store


def body(end=None, status="IN_STOCK", sold=0, remaining=1, price="10.00",
         options=("FIXED_PRICE",), postage=None):
    d = {"itemId": "v1|1|0", "title": "a thing", "itemEndDate": end,
         "price": {"value": price, "currency": "GBP"},
         "buyingOptions": list(options),
         "estimatedAvailabilities": [{"estimatedAvailabilityStatus": status,
                                      "estimatedSoldQuantity": sold,
                                      "estimatedRemainingQuantity": remaining}]}
    if postage is not None:
        d["shippingOptions"] = [{"shippingCost": {"value": postage}}]
    return d


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Auctions(unittest.TestCase):
    def test_a_live_auction_is_not_an_ending(self):
        """⭐⭐ THE BUG. Every auction carries its scheduled end date from the
        moment it is created, so a non-null itemEndDate meant every live
        auction was filed 'ended unsold' - and since sweep only re-checks
        `sold IS NULL`, the sale hours later was never recorded. Auctions could
        never contribute a comp, biasing the whole corpus upward."""
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        o = watch.read_outcome(body(end=iso(future)))
        self.assertIsNone(o["sold"])
        self.assertIsNone(o["ended_at"])
        self.assertTrue(o["ends_at"], "the scheduled end is still reported")

    def test_a_past_end_date_is_a_real_ending(self):
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        o = watch.read_outcome(body(end=iso(past), status="OUT_OF_STOCK",
                                    sold=1, remaining=0))
        self.assertEqual(o["sold"], 1)
        self.assertTrue(o["ended_at"])

    def test_a_past_end_with_stock_left_is_an_expiry(self):
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertEqual(watch.read_outcome(body(end=iso(past)))["sold"], 0)


class Postage(unittest.TestCase):
    def test_postage_is_captured(self):
        """⚠️ Everything downstream prices in LANDED terms; ours defaulted to
        0.0 and were ~5% light against trawl's."""
        self.assertEqual(watch.read_outcome(body(postage="3.95"))["postage_gbp"],
                         3.95)

    def test_absent_postage_is_zero_not_none(self):
        self.assertEqual(watch.read_outcome(body())["postage_gbp"], 0.0)


class Matching(unittest.TestCase):
    """⭐ Every case below is a real title pair that scored a FALSE POSITIVE."""

    def test_a_variant_suffix_is_not_optional(self):
        self.assertEqual(watch.match_score(
            "Nvidia RTX 3060 Ti graphics card",
            "GIGABYTE NVIDIA GeForce RTX 3060 Gaming OC 12GB"), 0.0)

    def test_a_generation_digit_is_not_optional(self):
        self.assertEqual(watch.match_score(
            "Sony WH-1000XM5 Wireless Headphones",
            "Sony WH-1000XM3 Wireless Over-Ear"), 0.0)

    def test_a_different_model_number_never_matches(self):
        self.assertEqual(watch.match_score(
            "Casio FX-CG50 Scientific A Level",
            "Casio FX-991EX Scientific Calculator A/AS Level"), 0.0)

    def test_the_right_product_still_matches(self):
        self.assertGreaterEqual(watch.match_score(
            "Casio FX-CG50 Scientific A Level",
            "Casio FX-CG50 3D Graphic Calculator A-level GCSE"), 0.6)


class Retired(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.dir.name) / "t.db"))

    def tearDown(self):
        self.dir.cleanup()

    def test_retired_is_not_counted_as_unsold(self):
        """⚠️ 11 of 103 rows were multi-variation groups that are STILL LIVE,
        filed as 'ended unsold'. stats() reported unsold=13 when 2 listings had
        actually expired - a ~6x error in the rate the trawl cut-off rests on."""
        for i, sold in enumerate((1, 0, -1, -1)):
            self.store._c.execute(
                "INSERT INTO watchlist(item_id,legacy_id,query,first_seen,sold)"
                " VALUES(?,?,?,?,?)", (f"v1|{i}|0", str(i), "q", 0, sold))
        self.store._c.commit()
        st = watch.stats(self.store)
        self.assertEqual(st["sold"], 1)
        self.assertEqual(st["unsold"], 1)
        self.assertEqual(st["retired"], 2)
        self.assertEqual(st["sell_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
