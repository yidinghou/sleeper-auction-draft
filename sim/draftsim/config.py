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

# When counting "startable" spots per position for replacement-level valuation,
# each flex slot is attributed to one representative position.
FLEX_REPRESENTATIVE: Dict[str, str] = {
    "FLEX": "RB",
    "SUPER_FLEX": "QB",
    "REC_FLEX": "WR",
    "WRRB_FLEX": "RB",
}

CONCRETE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True)
class DraftConfig:
    teams: int = 12
    budget: int = 200
    roster_slots: Tuple[str, ...] = DEFAULT_ROSTER_SLOTS

    @property
    def roster_size(self) -> int:
        return len(self.roster_slots)

    @property
    def starter_slots(self) -> Tuple[str, ...]:
        """Slots that count toward the starting lineup (everything but bench)."""
        return tuple(s for s in self.roster_slots if s != BENCH)

    def starter_counts(self) -> Dict[str, int]:
        """Per-team startable spots by position, with flex slots attributed to a
        representative position. Used to set replacement level for VORP."""
        counts: Dict[str, int] = {p: 0 for p in CONCRETE_POSITIONS}
        for slot in self.starter_slots:
            if slot in CONCRETE_POSITIONS:
                counts[slot] += 1
            elif slot in FLEX_REPRESENTATIVE:
                counts[FLEX_REPRESENTATIVE[slot]] += 1
        return counts
