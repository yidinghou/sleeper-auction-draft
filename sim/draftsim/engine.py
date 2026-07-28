"""Deterministic auction-draft engine.

Runs a full nomination-style auction to completion. Each round one manager (in
fixed seat rotation, skipping full rosters) nominates a player; every manager
with room submits a sealed bid; the highest bid wins and pays it (first-price,
per `domain/auction.ts`). The nominator is forced to open at `MIN_BID`, so every
round removes exactly one player from the pool and the draft always terminates.

Everything here is a pure function of (agents, config, players): seat order is
fixed (sorted manager ids), bids are gathered in seat order, and agents are
themselves deterministic. Same inputs -> identical `DraftResult`, byte for byte.
The public `DraftState` an agent sees carries only what any manager could
observe (budgets, rosters, the pool) — never the other agents' internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .auction import MIN_BID, Bid, max_bid, resolve_auction_round
from .config import DraftConfig
from .roster import is_lineup_legal, open_slots
from .valuation import Player, replacement_points

# Structural type for a draftable agent; concrete agents live in agents.py.
# Imported lazily under TYPE_CHECKING to keep engine <-> agents acyclic.
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .agents import DraftAgent


@dataclass
class ManagerState:
    """A manager's public draft state: leftover budget and acquired players."""

    manager_id: str
    budget: int
    roster: List[Player] = field(default_factory=list)

    def open_slots(self, config: DraftConfig) -> int:
        return open_slots(len(self.roster), config)


@dataclass
class DraftState:
    """Everything an agent may observe when nominating or bidding."""

    config: DraftConfig
    managers: Dict[str, ManagerState]
    available: List[Player]
    replacement: Dict[str, float]  # position -> replacement points, for VORP


@dataclass(frozen=True)
class Pick:
    round: int
    nominator_id: str
    player: Player
    winner_id: str
    price: int


@dataclass(frozen=True)
class DraftResult:
    picks: Tuple[Pick, ...]
    managers: Dict[str, ManagerState]  # final rosters + leftover budget
    config: DraftConfig

    def spend(self, manager_id: str) -> int:
        return self.config.budget - self.managers[manager_id].budget


def _next_nominator(
    manager_ids: Sequence[str],
    managers: Mapping[str, ManagerState],
    config: DraftConfig,
    start: int,
) -> Optional[Tuple[str, int]]:
    """Next manager (from `start`, wrapping) that still has an open slot.

    Returns (manager_id, pointer_after) or None if every roster is full.
    """
    n = len(manager_ids)
    for step in range(n):
        idx = (start + step) % n
        mid = manager_ids[idx]
        if managers[mid].open_slots(config) > 0:
            return mid, (idx + 1) % n
    return None


def run_draft(
    agents: Mapping[str, "DraftAgent"],
    config: DraftConfig,
    players: Sequence[Player],
) -> DraftResult:
    """Run one auction draft to completion, deterministically."""
    manager_ids = sorted(agents)  # fixed seat order
    seat_of = {mid: i for i, mid in enumerate(manager_ids)}
    managers = {mid: ManagerState(mid, config.budget) for mid in manager_ids}
    available: List[Player] = list(players)
    replacement = replacement_points(list(players), config)
    state = DraftState(config, managers, available, replacement)

    picks: List[Pick] = []
    nom_ptr = 0
    round_no = 0

    while available:
        nxt = _next_nominator(manager_ids, managers, config, nom_ptr)
        if nxt is None:
            break  # every roster full
        nominator_id, nom_ptr = nxt

        player = agents[nominator_id].nominate(state, nominator_id)
        if player is None:
            # Nominator has room but declines everything in the pool; nothing can
            # progress this seat, so end the draft rather than spin.
            break

        bids: List[Bid] = []
        for mid in manager_ids:
            m = managers[mid]
            ceiling = max_bid(m.budget, m.open_slots(config))
            if ceiling < MIN_BID:
                continue  # roster full or can't afford the reserve
            amount = agents[mid].bid(state, player, mid)
            amount = min(amount, ceiling)
            if mid == nominator_id:
                amount = max(amount, MIN_BID)  # you open your own nomination
            if amount >= MIN_BID:
                bids.append(Bid(mid, amount, seat_of[mid]))

        outcome = resolve_auction_round(bids)
        if outcome is None:
            # Only reachable if the nominator itself couldn't bid, which the
            # reserve rule forbids while it has an open slot; guard anyway.
            available[:] = [p for p in available if p is not player]
            continue

        winner_id, price = outcome
        winner = managers[winner_id]
        winner.budget -= price
        winner.roster.append(player)
        picks.append(Pick(round_no, nominator_id, player, winner_id, price))
        round_no += 1
        available[:] = [p for p in available if p is not player]

    return DraftResult(picks=tuple(picks), managers=managers, config=config)


def result_invariants_ok(result: DraftResult) -> List[str]:
    """Return a list of invariant violations (empty == healthy). Used by the
    Stage-3 human-check script and the invariant tests."""
    problems: List[str] = []
    config = result.config
    for mid, m in result.managers.items():
        if m.budget < 0:
            problems.append(f"{mid}: negative budget {m.budget}")
        if len(m.roster) > config.roster_size:
            problems.append(f"{mid}: {len(m.roster)} players over roster size")
        if not is_lineup_legal(m.roster, config):
            problems.append(f"{mid}: illegal lineup ({len(m.roster)} players)")
    for pk in result.picks:
        if pk.price < MIN_BID:
            problems.append(f"pick {pk.round}: price {pk.price} below MIN_BID")
    return problems
