"""Load projected players from the exported CSV and expose value models.

Source: `data/projections-2026.csv` (produced by `npm run export:projections`).
Columns: player_id, player, position, team, sleeper_rank, bye_week,
sleeper_proj_dollar, season_pts_half_ppr, week1..N_pts_half_ppr.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
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
    sleeper_id: Optional[str] = None
    """Sleeper's own player_id, the join key for live draft picks. None when
    reading a CSV exported before the column existed -- see `by_sleeper_id`."""
    week1: Optional[float] = None
    week2: Optional[float] = None
    week3: Optional[float] = None
    """Division-round projections. None rather than 0.0 when the CSV predates
    these columns -- a body with no early-week read is not the same as one
    projected for zero points in it."""


def _make_id(name: str, pos: str, team: str) -> str:
    """The simulator's own player key. Name + position + team is unique in
    practice for fantasy-relevant players, and unlike Sleeper's `player_id` it
    survives a CSV that predates the `player_id` column."""
    return f"{name}|{pos}|{team}".lower().replace(" ", "-")


def by_sleeper_id(players: List[Player]) -> Dict[str, Player]:
    """Index players by Sleeper's player_id, for matching live draft picks.

    Rows exported before the `player_id` column are simply absent from the
    index; callers fall back to `_make_id` on the pick's own name/pos/team.
    """
    return {p.sleeper_id: p for p in players if p.sleeper_id}


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
    None (dollar/bye/rank) or 0.0 (points).

    Free agents — an empty `team` cell — are skipped too, unless `free_agents`
    is set. They are 2185 of the CSV's 3223 rows: retired or unsigned players
    (Stefon Diggs, Joe Mixon, Najee Harris) who cannot score for anyone this
    season. Nothing draftable is lost by dropping them — the highest $PROJ among
    them is $1 — while keeping them makes them the bulk of the pool, where they
    crowd out real players in any tie broken below the $1 price floor.

    Pass `free_agents=True` for the raw sheet (a waiver-wire model, or eyeballing
    the whole export).
    """
    csv_path = Path(path) if path is not None else DEFAULT_CSV
    players: List[Player] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("player") or "").strip()
            pos = (row.get("position") or "").strip()
            team = (row.get("team") or "").strip()
            if not name or not pos:
                continue
            if not team and not free_agents:
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
                    sleeper_id=(row.get("player_id") or "").strip() or None,
                    week1=_float_or_none(row.get("week1_pts_half_ppr", "")),
                    week2=_float_or_none(row.get("week2_pts_half_ppr", "")),
                    week3=_float_or_none(row.get("week3_pts_half_ppr", "")),
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
    """Points of the best *freely available* player at each position.

    Replacement level is what you can have for nothing once the league has taken
    its starters, so it is the first player past them: with `per_team *
    config.teams` startable spots, that's index `n`, not `n - 1`. Taking the
    last starter instead overstates the bar every player has to clear and
    understates everyone's VORP -- by 3.7 points at QB and 5.9 at WR in the
    default league.

    Positions with no startable spots (or too few players) get 0.0.
    """
    # Shares, not counts: a flex slot that goes to a running back 77% of the time
    # moves the bar by a fifth of a slot, and rounding that away is exactly the
    # error this is measuring.
    counts = config.starter_shares()
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
        n = round(per_team * config.teams)  # fractional shares, whole players
        if not pts:
            replacement[pos] = 0.0
        elif n < len(pts):
            replacement[pos] = pts[n]
        else:
            # Every player at this position starts somewhere; nothing is free,
            # so the worst one is the closest thing to a replacement.
            replacement[pos] = pts[-1]
    return replacement


def vorp_value(player: Player, replacement: Dict[str, float]) -> float:
    """Projected points above the position's replacement level (floored at 0)."""
    base = replacement.get(player.pos, 0.0)
    return max(0.0, player.points - base)


def points_per_dollar(
    players: List[Player], replacement: Dict[str, float], config: DraftConfig
) -> float:
    """Exchange rate between the two value models: VORP points per auction dollar.

    The models are in different units — `market_value` is dollars, `vorp_value`
    is points — so blending them needs a conversion. The league spends its whole
    budget on the players it drafts, so total draftable VORP divided by total
    league budget is the rate at which points are actually bought.

    Returns 0.0 when there is nothing to buy or no money, which callers should
    read as "no conversion available" and fall back to the market anchor.
    """
    league_budget = config.teams * config.budget
    draftable = config.teams * config.roster_size
    if league_budget <= 0 or draftable <= 0:
        return 0.0
    top_vorp = sorted(
        (vorp_value(p, replacement) for p in players), reverse=True
    )[:draftable]
    return sum(top_vorp) / league_budget
