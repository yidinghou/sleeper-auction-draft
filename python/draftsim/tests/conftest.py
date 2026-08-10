"""Shared builders. Imported as `from conftest import ...` -- pytest puts the
tests directory on sys.path."""

from __future__ import annotations

from typing import Dict, Tuple

from draftsim import Player


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
