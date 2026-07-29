"""Roster slotting: optimal lineup selection + legality.

The simulator scores teams on their *starters*, so lineup selection has to be
correct, not merely plausible: it must be independent of the order players were
acquired and must pick the best legal lineup. Both come from a max-weight
bipartite matching (`_match_starters`), not a greedy first-fit.

(The TS `domain/roster.ts` `fillRosterSlots` greedy is deliberately NOT ported:
it feeds a UI table there, but here it would feed scoring, where its
order-sensitivity is a bug. Re-add a matching-seeded display helper if/when a
report layer actually needs the bench/overflow shape.)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Dict, List, Optional, Sequence

from .config import FLEX_ELIGIBILITY, DraftConfig
from .valuation import Player


def _slot_accepts(slot: str, player: Player) -> bool:
    eligible = FLEX_ELIGIBILITY.get(slot)
    if eligible is not None:
        return bool(player.pos) and player.pos in eligible
    return player.pos == slot


@lru_cache(maxsize=None)
def startable_slots(pos: str, config: DraftConfig) -> int:
    """How many starting slots this position could *ever* occupy.

    Counts the concrete slot plus every flex that accepts the position, which is
    the structural measure of how much a league actually plays a position. In
    the default 2QB lineup: WR 5, RB 4, TE 4, QB 2, DEF 1, K 0. Note this is a
    ceiling on useful bodies, not a starter count — you start one DEF, and since
    no flex accepts DEF, a second one can never enter the lineup.

    Cached: `DraftConfig` is a frozen dataclass, so it hashes, and the answer is
    fixed for a league. Callers hit this once per candidate per bid.
    """
    return sum(
        1
        for slot in config.starter_slots
        if pos in FLEX_ELIGIBILITY.get(slot, (slot,))
    )


# Points for the sentinel used to probe a position's marginal threshold. Any
# value that always wins its slot works; this one is far past a real season.
_SENTINEL_POINTS = 1e6


def _replacement_bench(
    config: DraftConfig, replacement: Dict[str, float]
) -> List[Player]:
    """Phantom replacement-level bodies, enough to fill every startable slot.

    An empty roster slot isn't worth the full points of whoever fills it — you
    could always have had a replacement-level player there for ~$1. Padding the
    lineup with these makes "what does this player add" measure the real gain
    over the waiver wire rather than over nothing.

    One phantom per slot the position can occupy is exactly enough to saturate
    the lineup, and no more: `startable_slots` is by definition the most of that
    position any lineup can start.
    """
    bench: List[Player] = []
    for pos, points in replacement.items():
        if points == float("inf"):  # position this lineup can't start at all
            continue
        for i in range(startable_slots(pos, config)):
            bench.append(
                Player(id=f"~repl:{pos}:{i}", name=f"replacement {pos}",
                       pos=pos, team="~", points=points)
            )
    return bench


def _lineup_points(players: Sequence[Player], config: DraftConfig) -> float:
    return sum(p.points for p in starters(players, config) if p is not None)


def marginal_thresholds(
    roster: Sequence[Player], config: DraftConfig, replacement: Dict[str, float]
) -> Dict[str, float]:
    """Points a player must clear, per position, to improve this lineup at all.

    The marginal value of any candidate is then a subtraction:

        max(0.0, player.points - thresholds[player.pos])

    which is what makes this affordable — scoring a 1000-player pool costs one
    matching per position here instead of one per candidate. The identity holds
    because the lineup is a matching over a transversal matroid: for a fixed
    position the gain is linear in the incoming player's points above whatever
    they displace, and flat at zero below it. `tests/test_roster.py` checks it
    against directly recomputing the lineup.

    Each threshold is read off by probing with a sentinel that always wins its
    slot: it gains `SENTINEL - threshold`, so the threshold falls out.

    This is what makes a second defense worthless without naming defenses
    anywhere. One DEF slot and no DEF flex means the incumbent is what a second
    one would have to displace, so the threshold is the incumbent's own points.
    """
    padded = list(roster) + _replacement_bench(config, replacement)
    base = _lineup_points(padded, config)

    thresholds: Dict[str, float] = {}
    for pos, points in replacement.items():
        if points == float("inf"):
            # Unstartable: nothing at this position can ever help, so no score
            # clears the bar.
            thresholds[pos] = _SENTINEL_POINTS
            continue
        sentinel = Player(id="~probe", name="probe", pos=pos, team="~",
                          points=_SENTINEL_POINTS)
        gain = _lineup_points(padded + [sentinel], config) - base
        thresholds[pos] = _SENTINEL_POINTS - gain
    return thresholds


def marginal_points(player: Player, thresholds: Dict[str, float]) -> float:
    """Points this player would add to the lineup `thresholds` was built from."""
    return max(0.0, player.points - thresholds.get(player.pos, _SENTINEL_POINTS))


def _match_starters(
    players: Sequence[Player],
    config: DraftConfig,
    weight: Callable[[Player], float],
) -> List[Optional[int]]:
    """Assign players to starting slots to maximise total `weight`.

    Computes an optimal assignment, so the result is independent of the order
    players appear in the input. Exploits the transversal-matroid structure:
    process players in descending weight and add each via a Kuhn augmenting path
    — greedy-by-weight over a matroid is optimal. Returns slot_idx -> player_idx
    (or None). Sizes are tiny (~10 slots, ~16 players), fast across many sims.
    """
    slots = config.starter_slots
    player_of_slot: List[Optional[int]] = [None] * len(slots)

    # Descending weight; original index breaks ties so results are deterministic.
    order = sorted(range(len(players)), key=lambda i: (-weight(players[i]), i))

    def augment(pi: int, visited: set) -> bool:
        for si, slot in enumerate(slots):
            if si in visited or not _slot_accepts(slot, players[pi]):
                continue
            visited.add(si)
            if player_of_slot[si] is None or augment(player_of_slot[si], visited):
                player_of_slot[si] = pi
                return True
        return False

    for pi in order:
        augment(pi, set())
    return player_of_slot


def starters(players: Sequence[Player], config: DraftConfig) -> List[Optional[Player]]:
    """Optimal starting lineup (max projected points), slot-aligned."""
    assigned = _match_starters(players, config, weight=lambda p: p.points)
    return [players[pi] if pi is not None else None for pi in assigned]


def is_lineup_legal(players: Sequence[Player], config: DraftConfig) -> bool:
    """True when every starting slot can be simultaneously filled by an eligible
    player — a max-cardinality matching that covers all slots. Order-invariant."""
    assigned = _match_starters(players, config, weight=lambda _p: 1.0)
    return all(pi is not None for pi in assigned)


def open_slots(filled_count: int, config: DraftConfig) -> int:
    """How many roster slots remain empty, given how many are already filled."""
    return max(0, config.roster_size - filled_count)


def positional_need(roster: Sequence[Player], config: DraftConfig) -> Dict[str, int]:
    """How many more players of each concrete position are still needed to reach
    a legal starting lineup, given what's on the roster.

    Uses `starter_counts()` (flex slots attributed to a representative position)
    as the target, so hitting every count guarantees a legal lineup: e.g. the
    default target 2 QB / 3 RB / 3 WR / 1 TE / 1 DEF fills exactly the ten
    starter slots. Extra bodies beyond the counts are depth (bench), never a
    need. Never negative.
    """
    target = config.starter_counts()
    have: Dict[str, int] = {}
    for p in roster:
        have[p.pos] = have.get(p.pos, 0) + 1
    return {pos: max(0, want - have.get(pos, 0)) for pos, want in target.items()}
