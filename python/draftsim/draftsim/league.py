"""The rules everybody at the table plays under.

A league is a number of seats, a budget each, a minimum bid, and a template of
roster slots. It is fixed for the whole of a draft and shared by everyone, and
it knows nothing about whether a draft is in progress — no picks, no spend, no
turn. Ask it structural questions only.

Positions arrive from a spreadsheet as strings — "QB", "RB", "WR", "TE", "K",
"DEF" — and every comparison here is against one of those literals. Modelling
them as a closed enumeration instead would buy a little safety at the cost of
converting on the way in and out of every spreadsheet, every live pick and
every test; the strings are a deliberate choice, not an oversight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

BENCH = "BN"

#: The flex slots and the positions each will take. WRRB_FLEX is here even
#: though this league does not use one, because leagues that do exist and the
#: model should not have to be edited to read them.
FLEX_ELIGIBILITY = {
    "FLEX": ("RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "WRRB_FLEX": ("WR", "RB"),
}

#: How a filled flex slot actually divided among the positions competing for
#: it, measured over 96 filled lineups — eight simulated drafts, twelve seats
#: each — and normalised per slot. The superflex went to a quarterback 97.9%
#: of the time and the receiver flex to a receiver every single time, so those
#: are carried as whole numbers; only the ordinary flex is genuinely contested.
#: WRRB_FLEX is unused here and so unmeasured, and splits evenly.
#:
#: These are measured rather than assumed, and that is worth defending because
#: it is exactly the sort of table someone later "simplifies". Splitting each
#: flex evenly across its eligible positions instead would put quarterback
#: replacement at the 15th quarterback rather than the 24th — fifty points of
#: difference — and tight end replacement at the 25th tight end rather than the
#: 12th. That factor of two comes from assuming a tight end is as likely to
#: take the superflex as a quarterback is. If the template ever changes, these
#: get re-measured: run drafts, tally which position filled each slot,
#: normalise per slot.
MEASURED_FLEX_SHARES = {
    "FLEX": {"RB": 0.77, "WR": 0.23},
    "SUPER_FLEX": {"QB": 1.00},
    "REC_FLEX": {"WR": 1.00},
    "WRRB_FLEX": {"WR": 0.50, "RB": 0.50},
}

#: Ten starters and six bench, in the order a roster is laid out for display.
STANDARD_TEMPLATE = (
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


def who_fills(slot: str) -> Dict[str, float]:
    """How one filled slot divides among the positions that compete for it.

    A concrete slot is all its own position. A flex splits by the measured
    shares. Asked of one slot rather than a whole template, because what a
    league still wants is counted a slot at a time — the ones nobody has filled
    yet.
    """
    return MEASURED_FLEX_SHARES.get(slot, {slot: 1.0})


def slot_accepts(slot: str, position: str) -> bool:
    """Would this starting slot take a player at this position?

    A flex takes any of the positions that compete for it; every other slot
    takes only its own. Asked about a bench slot the answer is no, which is
    true in the only sense that matters here: the bench is where leftovers go,
    not somewhere a lineup is filled.
    """
    if slot in FLEX_ELIGIBILITY:
        return position in FLEX_ELIGIBILITY[slot]
    return slot == position


@dataclass(frozen=True)
class LeagueRules:
    """Seats, money, and the sixteen slots each seat has to fill."""

    seats: int = 12
    budget: int = 200
    minimum_bid: int = 1
    roster_template: Tuple[str, ...] = STANDARD_TEMPLATE

    def __post_init__(self) -> None:
        if self.seats < 1:
            raise ValueError("a league with nobody at the table is not a league")
        if not self.roster_template:
            raise ValueError("a league with no roster slots is not a league")
        if self.minimum_bid < 1:
            raise ValueError(
                f"the minimum bid must be at least a dollar; got {self.minimum_bid}"
            )
        dollar_a_slot = self.roster_size * self.minimum_bid
        if self.budget < dollar_a_slot:
            # Every slot a seat has yet to fill reserves a dollar out of its
            # money before it may bid. A budget below that floor leaves a seat
            # structurally unable to bid the minimum, so refuse the league
            # outright rather than let an auction fail halfway through.
            raise ValueError(
                f"a budget of ${self.budget} cannot afford a dollar for each "
                f"of {self.roster_size} slots"
            )

    @property
    def roster_size(self) -> int:
        return len(self.roster_template)

    @property
    def starting_slots(self) -> Tuple[str, ...]:
        """The slots a seat is scored on, in template order."""
        return tuple(slot for slot in self.roster_template if slot != BENCH)

    def slots_a_position_can_fill(self, position: str) -> int:
        """How many starting slots could ever hold a player at this position.

        A position's own slots plus every flex that accepts it: receivers 5,
        backs 4, tight ends 4, quarterbacks 2, defenses 1, kickers 0. This is a
        ceiling on how many bodies at a position could ever be useful, not a
        count of how many will start — nine receivers still only start five.
        """
        return sum(1 for slot in self.starting_slots if slot_accepts(slot, position))

    def wanted_per_seat(self) -> Dict[str, float]:
        """How many players at each position one seat's lineup wants.

        Every concrete slot wants one player at its own position; every flex
        wants one player too, divided among the positions that compete for it
        by the measured shares. Here that reads 2.00 quarterbacks, 2.77 running
        backs, 3.23 receivers, 1.00 tight end, 1.00 defense and no kickers.

        The fractions are the point and they are carried, unrounded, all the
        way to the replacement bar. At twelve seats a fifth of a slot is more
        than two players of movement in the rankings, so rounding here instead
        would move the bar by more than the fraction looks worth.
        """
        wanted = {position: 0.0 for position in POSITIONS}
        for slot in self.starting_slots:
            for position, share in who_fills(slot).items():
                wanted[position] += share
        return wanted

    def wanted_per_seat_rounded(self) -> Dict[str, int]:
        """"How many should I buy?" — the shares rounded to whole players.

        Here that reads 2 quarterbacks, 3 backs, 3 receivers, 1 tight end and 1
        defense, which fills the ten starting slots exactly.

        Halves round up rather than to the nearest even number, as Python's own
        round() would: half a slot is still a slot, and rounding it away would
        leave a starting spot nobody was told to shop for.
        """
        return {
            position: math.floor(share + 0.5)
            for position, share in self.wanted_per_seat().items()
        }

    def wanted_per_seat_floored(self) -> Dict[str, int]:
        """"How many bodies does the template guarantee a home?"

        Here that reads 2 quarterbacks, 2 backs, 3 receivers, 1 tight end and 1
        defense — nine, with neither the backs nor the receivers laying claim
        to the contested flex.

        This is a different question from the rounded one and both get asked. A
        body past this count may well start; what it is not is a slot the roster
        was owed.

        The hair added before flooring absorbs the wobble of summing shares in
        binary floating point, so that a share which is arithmetically 3.0
        cannot floor to 2. No template can provoke that wobble out of the
        shares as measured today; the guard is there for the table as
        re-measured, since a future set of shares that sums to a whole number
        in decimal may well not in binary.
        """
        return {
            position: math.floor(share + 1e-9)
            for position, share in self.wanted_per_seat().items()
        }
