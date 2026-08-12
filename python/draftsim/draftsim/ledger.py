"""The ledger: the record that a sale happened, and the live auction that feeds it.

A `Pick` records one fact -- this team bought this player for this price, at this
moment -- and everything else in the system derives from it. The list of picks is
frozen and append-only; undo is popping the last row.

`Lot` and `Bid` are the other half: an auction in progress. They are never
persisted and nothing derives from them. A losing bid is a fact about a moment,
not about the draft; storing bids alongside picks would force every calculation
to filter for "the winning one", which is the kind of conditional that rots. If
bid history is wanted later it belongs in a second log that nothing reads from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Pick:
    """A completed sale. The only record that one happened."""

    team_id: str
    player_id: str
    price: int
    at: float
    """Wall-clock or simulation time of the hammer, in seconds."""


@dataclass(frozen=True)
class Bid:
    team_id: str
    amount: int
    at: float


@dataclass
class Lot:
    """A live auction in progress. Exists between nomination and hammer, then is
    discarded.

    Only one lot is open at a time. That is what keeps `TeamState.max_bid` a
    one-liner: money is either uncommitted or spent, never in between. Running
    lots in parallel would mean a team leading three auctions has committed money
    the ledger hasn't seen, and max_bid would have to subtract it.
    """

    player_id: str
    nominator_id: str
    deadline: float
    bids: List[Bid] = field(default_factory=list)

    @property
    def high(self) -> Optional[Bid]:
        """The standing bid, or None before the nomination bid lands."""
        return self.bids[-1] if self.bids else None


class InvalidPick(ValueError):
    """Raised by `Draft.record_pick` when a sale would break the rules."""
