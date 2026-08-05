"""Position scarcity: what's left on the board against what the room still wants.

The roster cards answer *who can outbid me*. They say nothing about *what is
about to run out*, which is the other half of an auction: a seat with $80 tells
you nothing if the last startable tight end went two picks ago.

The read here is a ratio. Supply is the players still available **in the current
tier** -- not everyone left at the position, because the fifteenth-best receiver
is not a substitute for the second and counting them together says a position is
healthy right up until it isn't. Demand is how many bodies the twelve seats still
want at that position, summed from the same fractional `DRAFT_TARGETS` the NEED
pane draws. Demand over supply is run pressure.

Nothing here re-derives a seat's state; it reads `position_summary()` off the
seats `live_state` already reconstructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .live_state import (
    DRAFT_TARGETS,
    LeagueState,
    PositionLine,
    Seat,
    position_summary,
)
from .valuation import Player

# Tier floors, highest first: a position's pool cut into price bands.
#
# Hand-maintained on purpose. A tier break is a judgment about where the market
# stops treating players as interchangeable, and every automatic version of it --
# biggest gap, replacement level, a fixed threshold -- gets that wrong somewhere
# a human wouldn't. These are the seam that would move to a data file if they
# ever need to change without a deploy.
#
# Read as: a QB with 240+ projected points is in the top band, 205-239 the next,
# and so on. Positions absent here get one tier holding everyone, which is the
# honest answer for K and DEF -- nobody runs on a kicker.
TIERS: Dict[str, Tuple[float, ...]] = {
    "QB": (300.0, 270.0, 245.0, 220.0, 190.0),
    "RB": (240.0, 200.0, 165.0, 135.0, 110.0),
    "WR": (240.0, 205.0, 175.0, 145.0, 120.0),
    "TE": (200.0, 160.0, 130.0, 105.0),
}

# Above this ratio the position is going; above 1.0 it will not go around.
_RUN = 2.0
_TIGHT = 1.0

# Deliberately uncapped. Both bands come back whole and the renderer slices them
# -- the card wants three names, the detail panel wants as many as fit, and a cap
# baked in here would silently be the smaller of the two.


@dataclass(frozen=True)
class PositionPressure:
    """One position's supply, demand, and the gap between them."""

    pos: str
    avail: List[Player]
    """The current tier, best first -- what you can still buy at this price."""
    next_tier: List[Player]
    """The whole band below it, best first -- what you drop to if you wait."""
    cliff_drop: int
    """Points between the worst of the current tier and the best of the next.
    Zero when there is no tier below, which is its own kind of cliff."""
    drafted: int
    """Bodies at this position already off the board, league-wide."""
    wanted: float
    """Bodies the room still wants, summing every seat's fractional shortfall."""
    need_seats: List[Seat]
    """Seats with a shortfall, in seat order. The board reorders presentationally."""
    lines: Dict[int, PositionLine]
    """Every seat's have/want at this position, keyed by slot -- what the tile
    grid draws. Every seat, not just the short ones: a filled seat still takes a
    tile, dimmed, because the grid's shape has to match the roster board's."""

    @property
    def left(self) -> int:
        return len(self.avail)

    @property
    def ratio(self) -> float:
        """Wanted per body left. Infinite demand on an empty tier reads as the
        maximum rather than a division error -- the tier being gone *is* the
        most pressure a position can be under."""
        if not self.avail:
            return float("inf") if self.wanted else 0.0
        return self.wanted / self.left

    @property
    def severity(self) -> str:
        if self.ratio >= _RUN:
            return "run"
        return "tight" if self.ratio > _TIGHT else "safe"


def split_tier(pos: str, available: Sequence[Player]) -> Tuple[List[Player], List[Player], int]:
    """Cut a position's pool at the highest tier floor anyone still clears.

    Returns the current tier, the one beneath it, and the points between them.
    As the top band drains the cut falls to the next floor by itself, so the
    panel keeps describing what is actually on the block rather than a band that
    emptied ten picks ago.
    """
    pool = sorted(
        (p for p in available if p.pos == pos),
        key=lambda p: p.points,
        reverse=True,
    )
    if not pool:
        return [], [], 0

    floors = TIERS.get(pos, ())
    # The first floor with anyone above it. Past the last floor every remaining
    # player is in one undifferentiated bin, which is the truth down there.
    cut = next((f for f in floors if pool[0].points >= f), None)
    if cut is None:
        return pool, [], 0

    current = [p for p in pool if p.points >= cut]
    beneath = [p for p in pool if p.points < cut]
    drop = round(current[-1].points - beneath[0].points) if beneath else 0
    return current, beneath, drop


def pressure(state: LeagueState) -> List[PositionPressure]:
    """Run pressure for every position `TIERS` knows about, in `TIERS` order.

    Order is fixed rather than sorted by severity: these are read by position
    ("what's left at receiver?"), so a card that jumps to the front the moment a
    run starts is a card you have to find again mid-auction.
    """
    seats = sorted(state.seats.values(), key=lambda s: s.slot)
    # One summary per seat, reused across all four positions rather than rebuilt
    # per card -- it walks the whole roster to compute starter points.
    summaries = {
        seat.slot: {line.pos: line for line in position_summary(seat, state.config)}
        for seat in seats
    }

    drafted: Dict[str, int] = {}
    for pick in state.picks:
        drafted[pick.player.pos] = drafted.get(pick.player.pos, 0) + 1

    out = []
    for pos in TIERS:
        avail, next_tier, drop = split_tier(pos, state.available)
        wanted = 0.0
        need_seats = []
        lines: Dict[int, PositionLine] = {}
        for seat in seats:
            line = summaries[seat.slot].get(pos)
            if line is None:
                # A position nobody in the league owns or starts. Give the tile
                # an empty line rather than a hole, so the grid keeps its shape.
                line = PositionLine(
                    pos=pos, have=0, want=DRAFT_TARGETS.get(pos, 0.0),
                    starter_points=0.0,
                )
            lines[seat.slot] = line
            if line.need:
                wanted += line.need
                need_seats.append(seat)
        out.append(
            PositionPressure(
                pos=pos,
                avail=avail,
                next_tier=next_tier,
                cliff_drop=drop,
                drafted=drafted.get(pos, 0),
                wanted=wanted,
                need_seats=need_seats,
                lines=lines,
            )
        )
    return out
