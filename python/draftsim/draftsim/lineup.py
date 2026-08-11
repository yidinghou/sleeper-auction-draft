"""Choosing the best legal starting lineup from a roster.

Teams are scored on their *starters*, so lineup selection has to be correct, not
merely plausible: independent of the order players were acquired, and optimal.
Both come from a max-weight bipartite matching, not a greedy first-fit.

Order-invariance is not a nicety here. `DraftState` maintains lineups two ways --
incrementally in `apply()` and from scratch in `rebuild()` -- and asserts the two
agree. A greedy fill would make them disagree by construction, since the two
paths see players in different orders.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .player import Player
from .rules import BENCH, DraftRules, Position

# by_position: each position's players, sorted best-first by `sort_key`.
ByPosition = Mapping[Position, Tuple[Player, ...]]


@dataclass(frozen=True)
class Lineup:
    points: float
    slots: Tuple[Optional[Player], ...]
    """Aligned to `rules.slots`; None where the slot is unfilled."""


def sort_key(player: Player, proj: Mapping[str, float]) -> Tuple[float, str]:
    """Best-first ordering key: most points first, player id breaking ties.

    The id tiebreak is what makes `insort` and a plain `sorted()` produce the
    same sequence for equal projections -- without it, two players at 0.0 points
    could land in either order and `assert_matches` would fail on rosters that
    are in fact identical.
    """
    return (-proj.get(player.id, 0.0), player.id)


def insort(
    players: Tuple[Player, ...], player: Player, proj: Mapping[str, float]
) -> Tuple[Player, ...]:
    """Insert `player` into an already-sorted tuple, keeping `sort_key` order."""
    keys = [sort_key(p, proj) for p in players]
    i = bisect_left(keys, sort_key(player, proj))
    return players[:i] + (player,) + players[i:]


def group_by_position(
    players: Iterable[Player], proj: Mapping[str, float]
) -> Dict[Position, Tuple[Player, ...]]:
    """Bucket players by position, each bucket sorted best-first.

    Written as a from-scratch sort rather than repeated `insort`, so that
    `DraftState.rebuild` is genuinely independent of `DraftState.apply`.
    """
    buckets: Dict[Position, List[Player]] = {}
    for p in players:
        buckets.setdefault(p.position, []).append(p)
    return {
        pos: tuple(sorted(bucket, key=lambda p: sort_key(p, proj)))
        for pos, bucket in buckets.items()
    }


def _match(
    players: Sequence[Player],
    rules: DraftRules,
    weights: Sequence[float],
) -> List[Optional[int]]:
    """Assign players to starting slots to maximise total weight.

    Exploits the transversal-matroid structure of the problem: process players in
    descending weight and add each via a Kuhn augmenting path -- greedy-by-weight
    over a matroid is optimal. Returns slot_idx -> player_idx (or None).

    Sizes are tiny (~10 slots, ~16 players), which is what lets this run once per
    candidate player inside a single bid.
    """
    slots = rules.slots
    player_of_slot: List[Optional[int]] = [None] * len(slots)

    # Descending weight; input index breaks ties, so callers control determinism
    # by controlling the order they pass players in (see `sort_key`).
    order = sorted(range(len(players)), key=lambda i: (-weights[i], i))

    def augment(pi: int, visited: set) -> bool:
        for si, slot in enumerate(slots):
            if si in visited or not rules.accepts(slot, players[pi].position):
                continue
            visited.add(si)
            if player_of_slot[si] is None or augment(player_of_slot[si], visited):
                player_of_slot[si] = pi
                return True
        return False

    for pi in order:
        augment(pi, set())
    return player_of_slot


def _sort_lineup(
    assigned: List[Optional[int]],
    players: Sequence[Player],
    rules: DraftRules,
    proj: Mapping[str, float],
) -> List[Optional[int]]:
    """Reorder players across slots so the lineup reads best-first.

    `_match` maximises points but not slot labels: an RB is worth the same in RB2
    as in FLEX, so which one he lands in falls out of the augmenting-path order.
    That order inverts what a reader expects -- each new player takes the first
    eligible slot and pushes the incumbent further down the template, leaving the
    best RB in FLEX and the worst in RB1.

    A sort, but only between slots that can legally trade players: swap any pair
    where the earlier slot holds the worse player, until none are left. Points are
    untouched -- same players, same count, only the labels move. Template order
    puts concrete slots ahead of flex ones, so the result reads best-first down
    the page and the overflow spills into the flex slots.
    """
    slots = rules.slots

    def rank(pi: Optional[int]) -> Tuple[float, str]:
        # Empty slots sort worst, so a filled slot always wins a move up.
        return (float("inf"), "") if pi is None else sort_key(players[pi], proj)

    def fits(si: int, pi: Optional[int]) -> bool:
        return pi is None or rules.accepts(slots[si], players[pi].position)

    swapped = True
    while swapped:  # tiny (10 slots); settles in a couple of passes
        swapped = False
        for i in range(len(slots)):
            for j in range(i + 1, len(slots)):
                a, b = assigned[i], assigned[j]
                if rank(b) < rank(a) and fits(i, b) and fits(j, a):
                    assigned[i], assigned[j] = b, a
                    swapped = True
    return assigned


def _flatten(by_position: ByPosition, proj: Mapping[str, float]) -> List[Player]:
    """All players from every bucket, in one deterministic best-first order."""
    everyone = [p for bucket in by_position.values() for p in bucket]
    everyone.sort(key=lambda p: sort_key(p, proj))
    return everyone


def best_lineup(
    by_position: ByPosition, rules: DraftRules, proj: Mapping[str, float]
) -> Lineup:
    """The highest-scoring legal starting lineup these players can field."""
    players = _flatten(by_position, proj)
    weights = [proj.get(p.id, 0.0) for p in players]
    assigned = _sort_lineup(_match(players, rules, weights), players, rules, proj)
    slots = tuple(players[pi] if pi is not None else None for pi in assigned)
    return Lineup(
        points=sum(proj.get(p.id, 0.0) for p in slots if p is not None),
        slots=slots,
    )


def empty_lineup(rules: DraftRules) -> Lineup:
    """The lineup of a team that has drafted nobody: no points, every slot open."""
    return Lineup(points=0.0, slots=(None,) * len(rules.slots))


def is_legal(by_position: ByPosition, rules: DraftRules) -> bool:
    """True when every starting slot can be filled simultaneously by an eligible
    player -- a max-cardinality matching that covers all slots."""
    players = _flatten(by_position, {})
    assigned = _match(players, rules, [1.0] * len(players))
    return all(pi is not None for pi in assigned)


def display_slots(
    lineup: Lineup, roster: Sequence[Player], rules: DraftRules
) -> List[Tuple[str, Optional[Player]]]:
    """Lay a roster out for display: one row per roster slot, in template order.

    Starter slots take the optimal lineup, slot-aligned, so a player appears in
    the slot they would actually start in rather than the one they happened to be
    bought for. Bench slots take the leftovers in acquisition order. Unfilled
    slots come back as None so a half-built roster still shows its shape, and any
    overflow past the bench is appended rather than dropped.
    """
    started = {p.id for p in lineup.slots if p is not None}
    bench = [p for p in roster if p.id not in started]

    rows: List[Tuple[str, Optional[Player]]] = []
    si = bi = 0
    for slot in rules.roster_slots:
        if slot == BENCH:
            rows.append((slot, bench[bi] if bi < len(bench) else None))
            bi += 1
        else:
            rows.append((slot, lineup.slots[si] if si < len(lineup.slots) else None))
            si += 1
    rows.extend((BENCH, p) for p in bench[bi:])  # overflow, if any
    return rows
