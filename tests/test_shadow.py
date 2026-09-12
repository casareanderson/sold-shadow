"""The trawl switch-off is a criterion, not a hunch."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soldshadow import shadow, trawl, watch
from soldshadow.comp import Comp
from soldshadow.store import Store
from datetime import datetime, timezone


class Bar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.dir.name) / "t.db"))
        self._ours, self._theirs = watch.sold_comps, trawl.cached_sold

    def tearDown(self):
        watch.sold_comps, trawl.cached_sold = self._ours, self._theirs
        self.dir.cleanup()

    def _stub(self, ours, theirs):
        def c(n, p):
            return [Comp(price_gbp=p, sold_at=datetime.now(timezone.utc))
                    for _ in range(n)]
        watch.sold_comps = lambda s, q, **kw: c(*ours)
        trawl.cached_sold = lambda q, k, s=None, **kw: c(*theirs)

    def test_not_ready_when_we_cannot_answer(self):
        """⭐ Coverage and accuracy are separate bars on purpose: agreeing
        perfectly on the one query we can answer is not readiness."""
        self._stub(ours=(0, 0), theirs=(6, 100.0))
        v = shadow.compare(self.store, ["a thing"], "k")
        self.assertFalse(v.ready)
        self.assertIn("coverage", v.reason)

    def test_not_ready_when_our_median_is_off(self):
        self._stub(ours=(5, 60.0), theirs=(6, 100.0))
        v = shadow.compare(self.store, ["a thing"], "k")
        self.assertFalse(v.ready)
        self.assertIn("gap", v.reason)

    def test_ready_when_both_bars_are_met(self):
        self._stub(ours=(5, 96.0), theirs=(6, 100.0))
        v = shadow.compare(self.store, ["a thing"], "k")
        self.assertTrue(v.ready, v.reason)

    def test_trawl_silence_is_inconclusive_not_a_pass(self):
        """⚠️ If trawl answered nothing there is nothing to be measured
        against, and calling that a pass would switch it off on no evidence."""
        self._stub(ours=(5, 96.0), theirs=(0, 0))
        v = shadow.compare(self.store, ["a thing"], "k")
        self.assertFalse(v.ready)
        self.assertIn("inconclusive", v.reason)

    def test_the_run_is_capped(self):
        """It spends allowance; it must not spend it all."""
        self._stub(ours=(5, 96.0), theirs=(6, 100.0))
        v = shadow.compare(self.store, [f"q{i}" for i in range(50)], "k",
                           limit=4)
        self.assertEqual(len(v.rows), 4)


if __name__ == "__main__":
    unittest.main()
