"""draftsim -- a fantasy football auction draft as a ledger.

A draft is a ledger of sales, a cache built from that ledger, and an auction that
appends to it. Everything else is computed.

    from draftsim import Draft, DraftRules, Team, load_players, load_projections

    players = {p.id: p for p in load_players()}
    draft = Draft(
        rules=DraftRules(),
        players=players,
        teams=[Team(f"t{i}", f"Team {i}") for i in range(12)],
        proj=load_projections(),
    )
    draft.record_pick(draft.nominator().id, "ja'marr-chase|wr|cin", 61, 0.0)

Layering, top to bottom: `rules` -> `player`/`team`/`ledger` -> `lineup` ->
`state` -> `draft` -> `evaluation`. Only `Draft` writes.
"""

from .draft import Draft
from .ledger import Bid, InvalidPick, Lot, Pick
from .lineup import Lineup, best_lineup, display_slots, is_legal
from .player import (
    MarketData,
    Player,
    by_sleeper_id,
    load_players,
    load_projections,
    make_id,
)
from .rules import BENCH, CONCRETE_POSITIONS, DEFAULT_ROSTER_SLOTS, DraftRules, Position
from .state import DraftState, TeamState
from .team import Team

__all__ = [
    "BENCH",
    "CONCRETE_POSITIONS",
    "DEFAULT_ROSTER_SLOTS",
    "Bid",
    "Draft",
    "DraftRules",
    "DraftState",
    "InvalidPick",
    "Lineup",
    "Lot",
    "MarketData",
    "Pick",
    "Player",
    "Position",
    "Team",
    "TeamState",
    "best_lineup",
    "by_sleeper_id",
    "display_slots",
    "is_legal",
    "load_players",
    "load_projections",
    "make_id",
]
