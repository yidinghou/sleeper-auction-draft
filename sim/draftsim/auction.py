"""Sealed-bid auction resolution + the budget reserve rule.

`resolve_auction_round` follows the TS `domain/auction.ts`: first-price sealed
bid — highest amount wins, earliest submission breaks ties, no bids means no
winner. It adds one thing the TS lacks: a final `manager_id` tiebreak. In the
app `submitted_at` is a real timestamp so collisions never happen; in a
simulator many bids for one round are stamped with the same logical tick, so
without a total order the winner would depend on list iteration order. The
extra tiebreak makes resolution fully order-independent.

`max_bid` is sim-original — there is no upstream oracle for it. The TS
`domain/bidding.ts` only checks `amount <= budgetRemaining`; the $1-per-slot
reserve is a modelling choice introduced here for the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Bid:
    manager_id: str
    amount: int
    submitted_at: int  # epoch/logical tick; lower = earlier


def resolve_auction_round(bids: List[Bid]) -> Optional[Tuple[str, int]]:
    """Return (winner_id, amount) or None when there are no bids.

    Fully order-independent: ranked by (amount desc, submitted_at asc,
    manager_id asc). The manager_id tiebreak guarantees a single deterministic
    winner even when amount and tick both collide.
    """
    if not bids:
        return None
    best = min(bids, key=lambda b: (-b.amount, b.submitted_at, b.manager_id))
    return (best.manager_id, best.amount)


def max_bid(budget: int, open_slots: int) -> int:
    """Highest legal bid that still lets a manager fill its remaining roster.

    `open_slots` counts the slot being bid on *plus* every other empty slot.
    Reserve $1 for each of the other (open_slots - 1) slots, so the ceiling is
    `budget - (open_slots - 1)`. A full roster (open_slots <= 0) can bid 0.
    """
    if open_slots <= 0:
        return 0
    return max(0, budget - (open_slots - 1))
