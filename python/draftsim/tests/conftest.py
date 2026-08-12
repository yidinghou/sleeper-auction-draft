"""Shared builders. Imported as `from conftest import ...` -- pytest puts the
tests directory on sys.path."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from draftsim import Draft, DraftRules, Player, Team


def make_player(name: str, position: str, points: float = 0.0) -> Tuple[Player, float]:
    """A player and his projection. Points live outside `Player`, so tests get
    both and feed the second into a `proj` mapping."""
    return Player(id=f"{name}|{position}", name=name, position=position, team="XX"), points


def make_pool(*specs: Tuple[str, str, float]) -> Tuple[Dict[str, Player], Dict[str, float]]:
    """Build `(players, proj)` from (name, position, points) triples."""
    players: Dict[str, Player] = {}
    proj: Dict[str, float] = {}
    for name, position, points in specs:
        player, pts = make_player(name, position, points)
        players[player.id] = player
        proj[player.id] = pts
    return players, proj


def deep_pool(
    per_position: int = 40,
    positions: Sequence[str] = ("QB", "RB", "WR", "TE", "DEF"),
) -> Tuple[Dict[str, Player], Dict[str, float]]:
    """A pool deep enough to fill a full 12x16 draft, points descending within
    each position so rankings are unambiguous."""
    specs = [
        (f"{position}{i:02d}", position, float(per_position - i))
        for position in positions
        for i in range(per_position)
    ]
    return make_pool(*specs)


def make_teams(n: int) -> List[Team]:
    return [Team(id=f"t{i}", name=f"Team {i}") for i in range(n)]


def make_draft(
    players: Optional[Dict[str, Player]] = None,
    proj: Optional[Dict[str, float]] = None,
    *,
    teams: int = 12,
    rules: Optional[DraftRules] = None,
) -> Draft:
    if players is None or proj is None:
        players, proj = deep_pool()
    rules = rules or DraftRules(teams=teams)
    return Draft(
        rules=rules, players=players, teams=make_teams(teams), proj=proj
    )


def roster_of(draft: Draft, team_id: str) -> Iterable[str]:
    return [p.id for p in draft.team_state(team_id).roster]
