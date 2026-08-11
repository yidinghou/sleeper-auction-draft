"""What a player is worth to *you*, and what you can spend on him.

Value here is marginal, not absolute: a player is worth what he adds to your
starting lineup. A third elite tight end scores zero for you and a lot for the
team with none. Paired with `TeamState.max_bid`, this gives the two numbers a
bidder actually needs -- what he's worth, and what you can legally spend.

Marginal *to what*, though, is the question this module turns on. The baseline is
the freely-available body you would otherwise start -- never an empty slot, which
scores zero and would make a player worth his whole projection. That baseline is
also the only one that squares with `dollars_per_point`: the rate is dollars per
point *above replacement*, so the points figure has to be above replacement too.
Measuring from an empty slot instead priced this league's 192 drafted players at
five times the money that exists.

The bar moves. `Market.of(state)` reads it off the ledger, so replacement level
and the exchange rate both reflect who is left and who still needs one, and a
fresh call after each pick is the whole of that behaviour.
"""

from __future__ import annotations

from collections import ChainMap
from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from .lineup import best_lineup, insort
from .player import Player
from .rules import CONCRETE_POSITIONS, Position, slot_shares
from .state import DraftState


_REPLACEMENT_ID = "__replacement__"


def marginal_points(
    state: DraftState,
    team_id: str,
    player: Player,
    replacement: Mapping[Position, float],
) -> float:
    """What this player adds to `team_id`'s STARTING lineup over a replacement body.

    Not his projection, and not the improvement over an empty slot either: a
    re-solve of the lineup with him on the roster, against the same lineup with a
    freely-available body at his position. You are never going to field an empty
    slot, so an empty slot is not the thing he has to beat -- the $1 guy is.

    Both arms go through the lineup solver rather than subtracting a replacement
    figure from the gain, so the awkward cases answer themselves: a player who
    cannot crack the lineup scores the same either way and comes out at zero, and
    a player at a position you have already filled is measured against whoever he
    actually displaces.

    Never negative -- a player below the bar is worth nothing, not less than
    nothing. Two matchings over pre-sorted buckets, both microseconds.
    """
    ts = state.teams[team_id]

    def lineup_with(p: Player, proj: Mapping[str, float]) -> float:
        buckets = dict(ts.by_position)
        buckets[p.position] = insort(buckets.get(p.position, ()), p, proj)
        return best_lineup(buckets, state.rules, proj).points

    bar = replacement.get(player.position, 0.0)
    if bar == float("inf"):
        return 0.0  # a position the lineup can never start -- no arithmetic needed

    filler = Player(
        id=_REPLACEMENT_ID, name="replacement", position=player.position, team=""
    )
    # ChainMap rather than a merged dict: this runs once per candidate across the
    # whole pool, and copying every projection each time would dwarf the solve.
    with_a_body = lineup_with(filler, ChainMap({_REPLACEMENT_ID: bar}, state.proj))
    return max(0.0, lineup_with(player, state.proj) - with_a_body)


def remaining_starter_shares(state: DraftState) -> Dict[Position, float]:
    """Starting spots still unfilled across the whole league, by position.

    Counted from the slots themselves rather than by subtracting each team's
    starters from its share. Subtracting double-counts: a team that started four
    running backs has consumed the superflex its quarterback share was counting
    on, and walking unfilled slots handles that for free -- the slot is simply
    gone.

    Self-consistent by construction. At the open every slot is unfilled, so this
    returns exactly `starter_shares() * teams`; it drifts from there only as real
    slots get filled. Reads `lineup.slots`, which `_sort_lineup` makes a stable
    fact rather than one arbitrary labelling of the same matching.
    """
    shares: Dict[Position, float] = {p: 0.0 for p in CONCRETE_POSITIONS}
    for ts in state.teams.values():
        for slot, filled in zip(state.rules.slots, ts.lineup.slots):
            if filled is None:
                for position, share in slot_shares(slot).items():
                    shares[position] = shares.get(position, 0.0) + share
    return shares


def replacement_points(state: DraftState) -> Dict[Position, float]:
    """The points a freely-available player at each position scores, right now.

    The bar is the first player *past* what the league still starts: with 12 teams
    needing 2.77 running backs each, RB #33 is roughly what you can have for a
    dollar at the open, so anything above his points is what you are paying for.

    Both halves move as the draft runs. Supply shrinks because only available
    players are ranked, and demand shrinks because the index is the *remaining*
    league-wide need -- once nine of twelve teams have their tight end, the bar
    falls to the third-best one left, which is what makes a mediocre tight end
    genuinely worth something to the three teams still short.

    Fractional shares, not whole counts: at 12 teams a fifth of a slot is more
    than two players' worth of movement in the ranking. Positions the lineup can
    never start (K, on the default template) get `inf` -- no score there is worth
    anything, and that is structural, so it does not move.
    """
    need = remaining_starter_shares(state)

    buckets: Dict[Position, list] = {}
    for player in state.players.values():
        if player.id not in state.owner:
            buckets.setdefault(player.position, []).append(player)

    out: Dict[Position, float] = {}
    for position in set(need) | set(buckets):
        if state.rules.startable_slots(position) == 0:
            out[position] = float("inf")
            continue
        ranked = sorted(
            buckets.get(position, ()), key=lambda p: -state.proj.get(p.id, 0.0)
        )
        # No remaining need rounds n to 0, making the best player left the bar --
        # nobody has a slot for him, so nobody is paying above a dollar for him.
        n = round(need.get(position, 0.0))
        out[position] = state.proj.get(ranked[n].id, 0.0) if n < len(ranked) else 0.0
    return out


def dollars_per_point(
    state: DraftState,
    replacement: Optional[Mapping[Position, float]] = None,
) -> float:
    """The league's exchange rate right now: money left per point of value left.

    Every team must reserve `min_bid` for each slot it has yet to fill, so only
    the surplus is genuinely biddable; that surplus buys the total
    value-over-replacement of the players who will actually still be bought. The
    result is what turns a points figure into a price.

    Recomputed from the ledger's cache, so it is an inflation index as much as an
    exchange rate. A league that blows its budget early leaves everyone bidding
    into a cheaper market; a disciplined early market makes the back half dear.
    At the open it equals the static form -- `teams * (budget - roster_size)` over
    the surplus of the players who go -- because nothing has been spent yet.
    """
    rules = state.rules
    if replacement is None:
        replacement = replacement_points(state)

    open_slots = sum(ts.open_slots(rules) for ts in state.teams.values())
    if open_slots == 0:
        return 0.0
    money_left = sum(ts.remaining(rules) for ts in state.teams.values())
    biddable = max(0, money_left - open_slots * rules.min_bid)

    surplus = sorted(
        (
            max(
                0.0,
                state.proj.get(p.id, 0.0)
                - replacement.get(p.position, float("inf")),
            )
            for p in state.players.values()
            if p.id not in state.owner
        ),
        reverse=True,
    )[:open_slots]
    total = sum(surplus)
    if total <= 0:
        return 0.0
    return biddable / total


@dataclass(frozen=True)
class Market:
    """What the league is paying, as of one moment in the ledger.

    The two numbers are coupled -- the rate's denominator is the surplus measured
    over *this* replacement bar -- so they travel together rather than as two
    arguments a caller has to remember to derive from the same moment.

    Rebuild it after a pick; it is cheap and it is the whole of the "prices move"
    behaviour. Lives here rather than on `DraftState` because state sits below
    evaluation in the layering and must not know how anything is valued.
    """

    replacement: Mapping[Position, float]
    dollars_per_point: float

    @classmethod
    def of(cls, state: DraftState) -> "Market":
        replacement = replacement_points(state)
        return cls(replacement, dollars_per_point(state, replacement))


def max_sensible_bid(
    state: DraftState,
    team_id: str,
    player: Player,
    market: Market,
) -> int:
    """The most it makes sense to pay -- capped by what is legal.

        max_bid  = remaining - (open_slots - 1) * min_bid
        sensible = min(max_bid, min_bid + value_over_replacement * dollars_per_point)

    Both halves of that product are measured from the same bar. The rate is
    dollars per point *above replacement*, so the points figure has to be above
    replacement too -- feeding it absolute lineup improvement instead prices the
    league's 192 drafted players at five times the money that exists.

    A player worth nothing to this roster is still worth the $1 minimum, because
    a body in an empty slot beats an empty slot.
    """
    ts = state.teams[team_id]
    worth = state.rules.min_bid + marginal_points(
        state, team_id, player, market.replacement
    ) * market.dollars_per_point
    return max(0, min(ts.max_bid(state.rules), int(worth)))


def positional_need(
    state: DraftState, team_id: str
) -> Dict[Position, int]:
    """How many more players of each position this team needs for a legal
    starting lineup. Extra bodies beyond the counts are depth, never a need."""
    ts = state.teams[team_id]
    have = {pos: len(bucket) for pos, bucket in ts.by_position.items()}
    return {
        position: max(0, want - have.get(position, 0))
        for position, want in state.rules.starter_counts().items()
    }
