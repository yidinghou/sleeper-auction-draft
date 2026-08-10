"""What a player is worth to *you*, and what you can spend on him.

Value here is marginal, not absolute: a player is worth what he adds to your
starting lineup. A third elite tight end scores zero for you and a lot for the
team with none. Paired with `TeamState.max_bid`, this gives the two numbers a
bidder actually needs -- what he's worth, and what you can legally spend.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

from .lineup import best_lineup, insort
from .player import Player
from .rules import DraftRules, Position
from .state import DraftState


def marginal_points(state: DraftState, team_id: str, player: Player) -> float:
    """What this player would add to `team_id`'s STARTING lineup.

    Not his projection: a re-solve of the lineup with him on the roster, minus
    the cached baseline. One matching over pre-sorted buckets, which is cheap
    enough to run across the whole remaining pool inside a single bid.

    Never negative -- a player who cannot crack the lineup adds zero, he does not
    subtract.
    """
    ts = state.teams[team_id]
    hypothetical = dict(ts.by_position)
    hypothetical[player.position] = insort(
        ts.by_position.get(player.position, ()), player, state.proj
    )
    improved = best_lineup(hypothetical, state.rules, state.proj)
    return max(0.0, improved.points - ts.lineup.points)  # cached baseline


def replacement_points(
    players: Iterable[Player], rules: DraftRules, proj: Mapping[str, float]
) -> Dict[Position, float]:
    """The points a freely-available player at each position scores.

    The bar is the first player *past* what the league starts: with 12 teams
    starting 2.77 running backs each, RB #33 is roughly what you can have for a
    dollar, so anything above his points is what you are actually paying for.

    Uses fractional `starter_shares` rather than whole counts -- at 12 teams a
    fifth of a slot is more than two players' worth of movement in the ranking.
    Positions the lineup cannot start at all (K, on the default template) get
    `inf`: no score at that position is worth anything.
    """
    buckets: Dict[Position, list] = {}
    for p in players:
        buckets.setdefault(p.position, []).append(p)

    out: Dict[Position, float] = {}
    for position, per_team in rules.starter_shares().items():
        if per_team <= 0 or rules.startable_slots(position) == 0:
            out[position] = float("inf")
            continue
        ranked = sorted(
            buckets.get(position, ()), key=lambda p: -proj.get(p.id, 0.0)
        )
        n = round(per_team * rules.teams)
        out[position] = proj.get(ranked[n].id, 0.0) if n < len(ranked) else 0.0
    return out


def dollars_per_point(
    players: Iterable[Player],
    rules: DraftRules,
    proj: Mapping[str, float],
    replacement: Optional[Mapping[Position, float]] = None,
) -> float:
    """The league's exchange rate: discretionary dollars per point above
    replacement.

    Every team must reserve `min_bid` for each roster slot, so only the surplus
    is genuinely biddable; that surplus buys the total value-over-replacement of
    the players who will actually be drafted. The result is what turns a marginal
    points figure into a price.
    """
    players = list(players)
    if replacement is None:
        replacement = replacement_points(players, rules, proj)

    biddable = rules.teams * (rules.budget - rules.roster_size * rules.min_bid)
    surplus = sorted(
        (
            max(0.0, proj.get(p.id, 0.0) - replacement.get(p.position, float("inf")))
            for p in players
        ),
        reverse=True,
    )[: rules.teams * rules.roster_size]
    total = sum(surplus)
    if total <= 0:
        return 0.0
    return biddable / total


def max_sensible_bid(
    state: DraftState,
    team_id: str,
    player: Player,
    dollars_per_point_: float,
) -> int:
    """The most it makes sense to pay -- capped by what is legal.

        max_bid  = remaining - (open_slots - 1) * min_bid
        sensible = min(max_bid, min_bid + marginal_points * dollars_per_point)

    A player worth nothing to this roster is still worth the $1 minimum, because
    a body in an empty slot beats an empty slot.
    """
    ts = state.teams[team_id]
    worth = state.rules.min_bid + marginal_points(state, team_id, player) * (
        dollars_per_point_
    )
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
