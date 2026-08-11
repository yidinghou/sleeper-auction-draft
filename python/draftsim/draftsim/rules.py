"""League configuration: the budget, the roster template, and slot eligibility.

Slot tokens and flex eligibility mirror the TS domain layer
(`domain/roster.ts` / `lib/positions.ts`). Defaults match the real league this
project drafts in: 12 teams, $200 budget, a 2QB/superflex 16-slot roster.

Nothing here knows about a draft in progress. `DraftRules` is frozen and shared
by every team; it is the one object that is safe to pass everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# A player's position, as it appears in the projections CSV. A plain str rather
# than an enum: it arrives from CSV as a string, every comparison in the codebase
# is against a literal, and the flex tables below are keyed by it.
Position = str

BENCH = "BN"

# Default lineup template: 10 starters + 6 bench.
DEFAULT_ROSTER_SLOTS: Tuple[str, ...] = (
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "REC_FLEX",
    "SUPER_FLEX",
    "DEF",
    BENCH,
    BENCH,
    BENCH,
    BENCH,
    BENCH,
    BENCH,
)

# Which player positions each flex-type slot accepts (mirrors domain/roster.ts).
FLEX_ELIGIBILITY: Dict[str, List[Position]] = {
    "FLEX": ["RB", "WR", "TE"],
    "SUPER_FLEX": ["QB", "RB", "WR", "TE"],
    "REC_FLEX": ["WR", "TE"],
    "WRRB_FLEX": ["WR", "RB"],
}

# How a flex slot's startable spot divides across the positions that compete for
# it, for replacement-level valuation. Not eligibility and not an even split:
# positions do not win flex slots equally, so these are *measured* from filled
# lineups (8 seeds x 12 seats = 96 lineups).
#
# SUPER_FLEX went to a QB 97.9% of the time and REC_FLEX to a WR 100%, so those
# stay whole; only FLEX is genuinely contested. Splitting every slot evenly
# instead would put QB replacement at #15 rather than #24 (+50.8 points) and TE
# at #25 rather than #12 -- a 2x error, because it assumes a TE is as likely to
# take the superflex as a QB.
#
# Re-measure if the lineup template changes: run drafts, tally positions by slot
# over zip(rules.slots, lineup.slots), and normalise per slot.
FLEX_SHARES: Dict[str, Dict[Position, float]] = {
    "FLEX": {"RB": 0.77, "WR": 0.23},
    "SUPER_FLEX": {"QB": 1.0},
    "REC_FLEX": {"WR": 1.0},
    "WRRB_FLEX": {"RB": 0.5, "WR": 0.5},  # unused by the default lineup
}

CONCRETE_POSITIONS: Tuple[Position, ...] = ("QB", "RB", "WR", "TE", "K", "DEF")


def slot_shares(slot: str) -> Dict[Position, float]:
    """How one slot's startable spot divides across the positions competing for it.

    A concrete slot is worth a whole player at its own position; a flex slot splits
    by `FLEX_SHARES`. The unit of replacement-level accounting, so that counting the
    whole template and counting only the slots still open use one implementation.
    """
    if slot in CONCRETE_POSITIONS:
        return {slot: 1.0}
    return dict(FLEX_SHARES.get(slot, {}))


@dataclass(frozen=True)
class DraftRules:
    """Budget ($200), roster size (16 slots), and the minimum bid ($1)."""

    teams: int = 12
    budget: int = 200
    min_bid: int = 1
    roster_slots: Tuple[str, ...] = DEFAULT_ROSTER_SLOTS

    def __post_init__(self) -> None:
        if self.teams < 1:
            raise ValueError(f"teams must be >= 1, got {self.teams}")
        if not self.roster_slots:
            raise ValueError("roster_slots must not be empty")
        if self.min_bid < 1:
            raise ValueError(f"min_bid must be >= 1, got {self.min_bid}")
        # Every team must afford min_bid per slot, else managers are frozen out
        # of auctions under the reserve rule -- see TeamState.max_bid.
        if self.budget < self.roster_size * self.min_bid:
            raise ValueError(
                f"budget ({self.budget}) must be >= roster size "
                f"({self.roster_size}) x min bid ({self.min_bid}) so every slot "
                f"is affordable"
            )

    @property
    def roster_size(self) -> int:
        return len(self.roster_slots)

    @property
    def slots(self) -> Tuple[str, ...]:
        """Starting-lineup slots, in template order (everything but bench)."""
        return tuple(s for s in self.roster_slots if s != BENCH)

    def accepts(self, slot: str, position: Position) -> bool:
        """Can a player of `position` start in `slot`?"""
        eligible = FLEX_ELIGIBILITY.get(slot)
        if eligible is not None:
            return bool(position) and position in eligible
        return position == slot

    def startable_slots(self, position: Position) -> int:
        """How many starting slots this position could *ever* occupy.

        The concrete slot plus every flex that accepts it, which is the
        structural measure of how much a league plays a position. In the default
        2QB lineup: WR 5, RB 4, TE 4, QB 2, DEF 1, K 0. A ceiling on useful
        bodies, not a starter count -- you start one DEF, and since no flex
        accepts DEF, a second one can never enter the lineup.
        """
        return sum(1 for slot in self.slots if self.accepts(slot, position))

    def starter_shares(self) -> Dict[Position, float]:
        """Per-team startable spots by position, flex slots split by FLEX_SHARES.

        Fractional on purpose: the default lineup gives QB 2.00, RB 2.77,
        WR 3.23, TE 1.00, DEF 1.00 -- the FLEX is 77% RB in practice, not 100%.
        Always sums to the number of starter slots. This is the honest input to
        replacement level, where a fifth of a slot really does move the bar.
        """
        shares: Dict[Position, float] = {p: 0.0 for p in CONCRETE_POSITIONS}
        for slot in self.slots:
            for pos, share in slot_shares(slot).items():
                shares[pos] = shares.get(pos, 0.0) + share
        return shares

    def starter_counts(self) -> Dict[Position, int]:
        """`starter_shares()` rounded to whole players -- you can't need 2.77
        running backs. Reads 2 QB / 3 RB / 3 WR / 1 TE / 1 DEF on the default
        lineup, which fills exactly the ten starter slots."""
        return {pos: round(share) for pos, share in self.starter_shares().items()}

    def owned_starters(self) -> Dict[Position, int]:
        """`starter_shares()` floored -- the spots a position owns *outright*.

        The default lineup reads 2 QB / 2 RB / 3 WR / 1 TE, because the FLEX is
        genuinely contested and neither the backs nor the receivers own it. The
        sibling of `starter_counts()`, which rounds because it answers "how many
        should I buy"; this floors because it answers the other question -- how
        many bodies does the lineup seat this position no matter what else is on
        the roster. A body past this count may well start; what it is not is a
        slot the roster was owed.
        """
        return {pos: int(share) for pos, share in self.starter_shares().items()}
