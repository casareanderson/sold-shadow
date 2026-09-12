"""One completed sale."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Comp:
    price_gbp: float
    sold_at: datetime
    title: str = ""
    url: str = ""
    postage_gbp: float = 0.0

    @property
    def landed_gbp(self) -> float:
        """What the buyer actually paid, all in.

        Postage is part of the price to a buyer even when the listing splits
        them out, so a free-postage comp and a GBP 5-postage comp are only
        comparable once both are landed.
        """
        return self.price_gbp + self.postage_gbp
