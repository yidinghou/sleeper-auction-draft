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

from typing import Callable, List, Optional, Protocol, Sequence, TypeVar

from .config import FLEX_ELIGIBILITY, DraftConfig


class SlotPlayer(Protocol):
    """What roster functions require of a player object."""

    pos: str


T = TypeVar("T", bound=SlotPlayer)


def _slot_accepts(slot: str, player: SlotPlayer) -> bool:
    eligible = FLEX_ELIGIBILITY.get(slot)
    if eligible is not None:
        return bool(player.pos) and player.pos in eligible
    return player.pos == slot


def _match_starters(
    players: Sequence[T],
    config: DraftConfig,
    weight: Callable[[T], float],
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


def starters(players: Sequence[T], config: DraftConfig) -> List[Optional[T]]:
    """Optimal starting lineup (max projected points), slot-aligned.

    Reads `.points` directly — a player object missing it raises rather than
    silently weighting everything to zero.
    """
    weight: Callable[[T], float] = lambda p: float(p.points)  # type: ignore[attr-defined]
    assigned = _match_starters(players, config, weight)
    return [players[pi] if pi is not None else None for pi in assigned]


def is_lineup_legal(players: Sequence[T], config: DraftConfig) -> bool:
    """True when every starting slot can be simultaneously filled by an eligible
    player — a max-cardinality matching that covers all slots. Order-invariant."""
    assigned = _match_starters(players, config, weight=lambda _p: 1.0)
    return all(pi is not None for pi in assigned)


def open_slots(filled_count: int, config: DraftConfig) -> int:
    """How many roster slots remain empty, given how many are already filled."""
    return max(0, config.roster_size - filled_count)
