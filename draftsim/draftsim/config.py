"""League / draft configuration and roster-slot rules.

Slot tokens and flex eligibility mirror the TS domain layer
(`domain/roster.ts` / `lib/positions.ts`). Defaults match the real league this
project drafts in: 12 teams, $200 budget, a 2QB/superflex 16-slot roster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

BENCH = "BN"

# Default lineup template (10 starters + 6 bench). Order matters for the greedy
# roster fill in roster.py.
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
FLEX_ELIGIBILITY: Dict[str, List[str]] = {
    "FLEX": ["RB", "WR", "TE"],
    "SUPER_FLEX": ["QB", "RB", "WR", "TE"],
    "REC_FLEX": ["WR", "TE"],
    "WRRB_FLEX": ["WR", "RB"],
}

# How a flex slot's startable spot divides across the positions that compete for
# it, for replacement-level valuation. Not eligibility and not an even split:
# positions do not win flex slots equally, so these are *measured* from filled
# lineups (8 seeds x 12 seats = 96 lineups, via roster.starters()).
#
# SUPER_FLEX went to a QB 97.9% of the time and REC_FLEX to a WR 100%, so those
# stay whole; only FLEX is genuinely contested. Splitting every slot evenly
# instead would put QB replacement at #15 rather than #24 (+50.8 points) and TE
# at #25 rather than #12 -- a 2x error, because it assumes a TE is as likely to
# take the superflex as a QB.
#
# Re-measure if the lineup template changes: run drafts, tally p.pos by slot over
# zip(config.starter_slots, starters(roster, config)), and normalise per slot.
FLEX_SHARES: Dict[str, Dict[str, float]] = {
    "FLEX": {"RB": 0.77, "WR": 0.23},
    "SUPER_FLEX": {"QB": 1.0},
    "REC_FLEX": {"WR": 1.0},
    "WRRB_FLEX": {"RB": 0.5, "WR": 0.5},  # unused by the default lineup
}

CONCRETE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True)
class DraftConfig:
    teams: int = 12
    budget: int = 200
    roster_slots: Tuple[str, ...] = DEFAULT_ROSTER_SLOTS

    def __post_init__(self) -> None:
        if self.teams < 1:
            raise ValueError(f"teams must be >= 1, got {self.teams}")
        if not self.roster_slots:
            raise ValueError("roster_slots must not be empty")
        # Every team must afford at least $1 per slot, else managers are frozen
        # out of auctions under the reserve rule (see auction.max_bid).
        if self.budget < len(self.roster_slots):
            raise ValueError(
                f"budget ({self.budget}) must be >= roster size "
                f"({len(self.roster_slots)}) so every slot is affordable"
            )

    @property
    def roster_size(self) -> int:
        return len(self.roster_slots)

    @property
    def starter_slots(self) -> Tuple[str, ...]:
        """Slots that count toward the starting lineup (everything but bench)."""
        return tuple(s for s in self.roster_slots if s != BENCH)

    def starter_shares(self) -> Dict[str, float]:
        """Per-team startable spots by position, flex slots split by `FLEX_SHARES`.

        Fractional on purpose: the default lineup gives QB 2.00, RB 2.77,
        WR 3.23, TE 1.00, DEF 1.00 — the FLEX is 77% RB in practice, not 100%.
        Always sums to the number of starter slots. This is the honest input to
        replacement level, where a fifth of a slot really does move the bar.
        """
        shares: Dict[str, float] = {p: 0.0 for p in CONCRETE_POSITIONS}
        for slot in self.starter_slots:
            if slot in CONCRETE_POSITIONS:
                shares[slot] += 1.0
            else:
                for pos, share in FLEX_SHARES.get(slot, {}).items():
                    shares[pos] = shares.get(pos, 0.0) + share
        return shares

    def starter_counts(self) -> Dict[str, int]:
        """Per-team startable spots by position, as whole players.

        `starter_shares()` rounded, for callers that need a countable target
        rather than a bar — you can't need 2.77 running backs. Rounds to the
        same 2/3/3/1/1 the all-or-nothing attribution produced, so positional
        need is unchanged by the share split.
        """
        return {pos: round(share) for pos, share in self.starter_shares().items()}

    def owned_starters(self) -> Dict[str, int]:
        """Per-team startable spots a position owns *outright* — the same shares,
        floored rather than rounded.

        The default lineup gives QB 2.00, RB 2.77, WR 3.23, TE 1.00, so this
        reads 2 / 2 / 3 / 1: the FLEX is genuinely contested and neither the
        backs nor the receivers own it. Where `starter_counts()` rounds — it is a
        target you buy against, and 2.77 backs means buy three — this floors,
        because it answers the other question: how many bodies does the lineup
        seat this position no matter what else is on the roster. A body past this
        count may well start; what it is not is a slot the seat was owed.

        Used by the folded card strip, which draws what a seat has rather than
        what it should still buy.
        """
        return {pos: int(share) for pos, share in self.starter_shares().items()}
