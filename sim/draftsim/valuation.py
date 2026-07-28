"""Load projected players from the exported CSV and expose value models.

Source: `data/projections-2026.csv` (produced by `npm run export:projections`).
Columns: player, position, team, sleeper_rank, bye_week, sleeper_proj_dollar,
season_pts_half_ppr, week1..N_pts_half_ppr.

Two value models:
- `market`  -> Sleeper's projected auction dollar (`$PROJ`), the crowd's price.
- `vorp`    -> projected points above positional replacement level.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .config import DraftConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "data" / "projections-2026.csv"


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    pos: str
    team: str
    points: float
    proj_dollar: Optional[int] = None
    bye: Optional[int] = None
    rank: Optional[int] = None


def _make_id(name: str, pos: str, team: str) -> str:
    """The projections CSV has no player_id, so synthesize a stable one. Name +
    position + team is unique in practice for fantasy-relevant players."""
    return f"{name}|{pos}|{team}".lower().replace(" ", "-")


def _int_or_none(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if raw == "":
        return None
    return int(float(raw))


def _float_or_zero(raw: str) -> float:
    raw = (raw or "").strip()
    return float(raw) if raw else 0.0


def load_players(path: Optional[Path] = None) -> List[Player]:
    """Read the projections CSV into Player rows. Rows missing a position or name
    are skipped; missing numeric cells become None (dollar/bye/rank) or 0.0 (points)."""
    csv_path = Path(path) if path is not None else DEFAULT_CSV
    players: List[Player] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("player") or "").strip()
            pos = (row.get("position") or "").strip()
            team = (row.get("team") or "").strip()
            if not name or not pos:
                continue
            players.append(
                Player(
                    id=_make_id(name, pos, team),
                    name=name,
                    pos=pos,
                    team=team,
                    points=_float_or_zero(row.get("season_pts_half_ppr", "")),
                    proj_dollar=_int_or_none(row.get("sleeper_proj_dollar", "")),
                    bye=_int_or_none(row.get("bye_week", "")),
                    rank=_int_or_none(row.get("sleeper_rank", "")),
                )
            )
    return players


# --- value models ---------------------------------------------------------


def market_value(player: Player) -> float:
    """Sleeper's projected auction dollar; players off the board price at 0."""
    return float(player.proj_dollar) if player.proj_dollar is not None else 0.0


def replacement_points(
    players: List[Player], config: DraftConfig
) -> Dict[str, float]:
    """Points of the last startable player at each position across the league.

    Replacement rank per position = per-team startable spots * number of teams.
    Positions with no startable spots (or too few players) get 0.0.
    """
    counts = config.starter_counts()
    by_pos: Dict[str, List[float]] = {}
    for p in players:
        by_pos.setdefault(p.pos, []).append(p.points)

    replacement: Dict[str, float] = {}
    for pos, per_team in counts.items():
        # A position with no startable slot (e.g. K in a no-kicker league) is
        # undraftable: replacement is infinite, so VORP floors to 0. This is
        # distinct from a startable position that simply has no players (0.0).
        if per_team == 0:
            replacement[pos] = float("inf")
            continue
        pts = sorted(by_pos.get(pos, []), reverse=True)
        n = per_team * config.teams
        if not pts:
            replacement[pos] = 0.0
        elif n <= len(pts):
            replacement[pos] = pts[n - 1]
        else:
            replacement[pos] = pts[-1]
    return replacement


def vorp_value(player: Player, replacement: Dict[str, float]) -> float:
    """Projected points above the position's replacement level (floored at 0)."""
    base = replacement.get(player.pos, 0.0)
    return max(0.0, player.points - base)
