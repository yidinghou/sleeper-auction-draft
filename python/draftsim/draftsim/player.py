"""Players: facts about a football player, and loading them from the CSV.

A `Player` knows nothing about any draft. It has no `drafted` flag, no owner and
no price -- those are answers the ledger already has, and a second copy of a fact
is a chance for the two to disagree.

Projections live outside `Player` too, in a `player_id -> points` mapping. That
keeps the player table reusable across projection sets: re-running a draft
against a different season forecast means passing a different `proj`, not
rebuilding every row.

Source: `data/projections-2026.csv` (produced by `npm run export:projections`).
Columns: player_id, player, position, team, sleeper_rank, bye_week,
sleeper_proj_dollar, season_pts_half_ppr, week1..N_pts_half_ppr.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .rules import Position

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "data" / "projections-2026.csv"


@dataclass(frozen=True)
class MarketData:
    """What Sleeper's draft board says about a player -- what the market thinks,
    not what the player is. Kept apart from the football facts so a valuation
    that leans on the market has to say so."""

    proj_dollar: Optional[int] = None
    """Sleeper's projected auction price. Computed in Sleeper's browser client,
    not served by any API -- see AGENTS.md."""
    rank: Optional[int] = None
    bye: Optional[int] = None
    sleeper_id: Optional[str] = None
    """Sleeper's own player_id, the join key for live draft picks. None when
    reading a CSV exported before the column existed."""


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    position: Position
    team: str
    """NFL team abbreviation -- not a draft seat. Empty for free agents."""
    market: MarketData = field(default_factory=MarketData)
    week: Optional["WeeklyPoints"] = None


@dataclass(frozen=True)
class WeeklyPoints:
    """Division-round projections. None rather than 0.0 when the CSV predates
    these columns -- a body with no early-week read is not the same as one
    projected for zero points in it."""

    week1: Optional[float] = None
    week2: Optional[float] = None
    week3: Optional[float] = None


def make_id(name: str, position: Position, team: str) -> str:
    """The simulator's own player key. Name + position + team is unique in
    practice for fantasy-relevant players, and unlike Sleeper's `player_id` it
    survives a CSV that predates the `player_id` column."""
    return f"{name}|{position}|{team}".lower().replace(" ", "-")


def by_sleeper_id(players: Iterable[Player]) -> Dict[str, Player]:
    """Index players by Sleeper's player_id, for matching live draft picks.

    Rows exported before the `player_id` column are simply absent from the
    index; callers fall back to `make_id` on the pick's own name/pos/team.
    """
    return {p.market.sleeper_id: p for p in players if p.market.sleeper_id}


def _int_or_none(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if raw == "" or raw == "-":
        return None
    return int(float(raw))


def _float_or_zero(raw: str) -> float:
    raw = (raw or "").strip()
    return float(raw) if raw else 0.0


def _float_or_none(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    return float(raw) if raw else None


def load_players(
    path: Optional[Path] = None, *, free_agents: bool = False
) -> List[Player]:
    """Read the projections CSV into draftable Player rows.

    Rows missing a position or name are skipped; missing numeric cells become
    None.

    Free agents -- an empty `team` cell -- are skipped too, unless `free_agents`
    is set. They are 2185 of the CSV's 3223 rows: retired or unsigned players
    (Stefon Diggs, Joe Mixon, Najee Harris) who cannot score for anyone this
    season. Nothing draftable is lost by dropping them -- the highest $PROJ among
    them is $1 -- while keeping them makes them the bulk of the pool, where they
    crowd out real players in any tie broken below the $1 price floor.

    Pass `free_agents=True` for the raw sheet (a waiver-wire model, or eyeballing
    the whole export).

    Projections are *not* returned here; call `load_projections` on the same
    path for the `player_id -> points` mapping a draft is scored against.
    """
    players: List[Player] = []
    for row in _rows(path, free_agents=free_agents):
        name = row["player"].strip()
        position = row["position"].strip()
        team = row["team"].strip()
        players.append(
            Player(
                id=make_id(name, position, team),
                name=name,
                position=position,
                team=team,
                market=MarketData(
                    proj_dollar=_int_or_none(row.get("sleeper_proj_dollar", "")),
                    rank=_int_or_none(row.get("sleeper_rank", "")),
                    bye=_int_or_none(row.get("bye_week", "")),
                    sleeper_id=(row.get("player_id") or "").strip() or None,
                ),
                week=WeeklyPoints(
                    week1=_float_or_none(row.get("week1_pts_half_ppr", "")),
                    week2=_float_or_none(row.get("week2_pts_half_ppr", "")),
                    week3=_float_or_none(row.get("week3_pts_half_ppr", "")),
                ),
            )
        )
    return players


def load_projections(
    path: Optional[Path] = None, *, free_agents: bool = False
) -> Dict[str, float]:
    """Season points by player id -- the `proj` a draft is scored against.

    Read from the same rows and keyed the same way as `load_players`, so the two
    are usable together without a join. A player missing from `proj` scores zero
    rather than raising: an unprojected body on a roster is worth nothing, which
    is the right answer and not an error.
    """
    return {
        make_id(
            row["player"].strip(), row["position"].strip(), row["team"].strip()
        ): _float_or_zero(row.get("season_pts_half_ppr", ""))
        for row in _rows(path, free_agents=free_agents)
    }


def _rows(path: Optional[Path], *, free_agents: bool) -> Iterable[Dict[str, str]]:
    """Draftable CSV rows -- the shared filter behind the two loaders above."""
    csv_path = Path(path) if path is not None else DEFAULT_CSV
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("player") or "").strip()
            position = (row.get("position") or "").strip()
            team = (row.get("team") or "").strip()
            if not name or not position:
                continue
            if not team and not free_agents:
                continue
            yield row
