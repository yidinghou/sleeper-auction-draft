"""Group each position's players into tiers and render them as an HTML board.

A tier is a run of players close enough in projected points to be worth roughly
the same to you. The board answers the question a draft asks over and over: if I
miss this guy, is the next one the same player or a step down?

Two public pieces — `tier_breaks`, the rule, and `render_html`, the page.
`scripts/tiers.py` is the CLI over them. Like `report.py` this is a
self-contained page: inline CSS, no external assets, no JS. It uses the *light*
palette from `theme.py`, the same one the live board uses, because it is a
reference you keep open beside Sleeper's dark app rather than a post-mortem.
"""

from __future__ import annotations

import html
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from .theme import (
    L_DIM,
    L_FAINT,
    L_RULE,
    L_TEXT,
    POS_COLOR_LIGHT,
    POS_FALLBACK_LIGHT,
)
from .valuation import Player

# How deep to go at each position by default. Deeper than the draft actually
# goes -- the tail is there so you can see where it stops mattering.
TOP_N = {"QB": 36, "RB": 36, "WR": 48, "TE": 24, "DEF": 12}

# A new tier starts after any gap at least this many times the position's median
# gap. Median, not mean: one Josh-Allen-sized cliff would drag a mean-based bar
# up past every real break in the list.
#
# 2.5 is tuned against the 2026 sheet for tiers you can hold in your head -- 4 to
# 13 per position, a handful of players each. Lower it and WR fragments into
# pairs; much higher and the whole RB middle collapses into one block.
DEFAULT_GAP_FACTOR = 2.5


def tier_breaks(points: Sequence[float], gap_factor: float) -> List[int]:
    """Indices after which a new tier begins, for points sorted descending.

    The gap at index `i` is the drop to `i + 1`, so the last player has no gap
    and can never start a tier. A list whose gaps are all equal (or all zero) is
    one tier: nothing in it is a cliff relative to the rest.
    """
    if len(points) < 2:
        return []
    gaps = [points[i] - points[i + 1] for i in range(len(points) - 1)]
    threshold = gap_factor * statistics.median(gaps)
    if threshold <= 0:
        return []
    return [i for i, gap in enumerate(gaps) if gap >= threshold]


def tier_count(points: Sequence[float], gap_factor: float) -> int:
    """How many tiers `points` falls into; 0 for an empty list."""
    if not points:
        return 0
    return len(tier_breaks(points, gap_factor)) + 1


def rank_by_position(
    players: Sequence[Player], top_n: Mapping[str, int]
) -> Dict[str, List[Player]]:
    """The top N at each requested position, best first, in `top_n`'s order."""
    return {
        pos: sorted((p for p in players if p.pos == pos), key=lambda p: -p.points)[
            :count
        ]
        for pos, count in top_n.items()
    }


# -- rendering ---------------------------------------------------------------

# Widest a gap bar may draw, in px. Sized so the bar plus the number beside it
# fit inside the cell: as a percentage the longest bar fills the cell and shoves
# its own number out over the next column.
BAR_MAX_PX = 48

CSS = f"""
  body {{ font: 13px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
          margin: 1rem; color: {L_TEXT}; background: #fff; }}
  h1 {{ font-size: 1.1rem; margin: 0 0 0.2rem; }}
  p.sub {{ color: {L_DIM}; font-size: 0.85rem; margin: 0 0 1rem; }}
  .board {{ display: flex; gap: 1rem; align-items: flex-start; overflow-x: auto; }}
  .col {{ flex: 0 0 auto; border-top: 3px solid var(--accent); }}
  h2 {{ font-size: 0.9rem; margin: 0.3rem 0 0.2rem; color: var(--accent); }}
  h2 small {{ color: {L_FAINT}; font-weight: normal; }}
  table {{ border-collapse: collapse; }}
  td {{ padding: 2px 6px; text-align: right; white-space: nowrap; }}
  td.name {{ text-align: left; max-width: 150px; overflow: hidden;
             text-overflow: ellipsis; }}
  td.rank {{ color: {L_FAINT}; }}
  td.cost {{ color: {L_DIM}; }}
  td.gap {{ width: {BAR_MAX_PX + 44}px; }}
  .bar {{ display: inline-block; height: 9px; background: var(--accent);
          opacity: 0.5; vertical-align: middle; margin-right: 4px; }}
  .gapnum {{ color: {L_DIM}; font-size: 0.85em; }}
  tr.tierbreak td {{ text-align: left; font-size: 0.72rem; font-weight: 700;
                     letter-spacing: 0.04em; color: var(--accent);
                     border-top: 2px solid var(--accent);
                     padding-top: 3px; text-transform: uppercase; }}
  tr:not(.tierbreak):hover {{ background: {L_RULE}; }}
"""

# Cells per row: rank, name, points, $PROJ, gap bar.
_COLSPAN = 5


def _rows(players: Sequence[Player], gap_factor: float) -> str:
    """One position's table body: a row per player, `Tier N` rules between."""
    points = [p.points for p in players]
    breaks = set(tier_breaks(points, gap_factor))
    # The last player's gap is 0.0: there is no one below him to fall to.
    gaps = [points[i] - points[i + 1] for i in range(len(points) - 1)] + [0.0]
    # Bars scale within the position, so widths are never comparable across
    # columns -- which is the honest reading, since a 20-point cliff means
    # different things at QB and at TE.
    widest = max(gaps) or 1.0

    out: List[str] = []
    tier = 1
    for i, player in enumerate(players):
        if i and (i - 1) in breaks:
            tier += 1
            out.append(
                f'<tr class="tierbreak"><td colspan="{_COLSPAN}">Tier {tier}</td></tr>'
            )
        cost = f"${player.proj_dollar}" if player.proj_dollar is not None else "—"
        out.append(
            f'<tr><td class="rank">{i + 1}</td>'
            f'<td class="name">{html.escape(player.name)}</td>'
            f'<td class="pts">{player.points:.1f}</td>'
            f'<td class="cost">{cost}</td>'
            f'<td class="gap">'
            f'<span class="bar" style="width:{round(BAR_MAX_PX * gaps[i] / widest)}px"></span>'
            f'<span class="gapnum">{gaps[i]:.1f}</span></td></tr>'
        )
    return "".join(out)


def _column(pos: str, players: Sequence[Player], gap_factor: float) -> str:
    accent = POS_COLOR_LIGHT.get(pos, POS_FALLBACK_LIGHT)
    tiers = tier_count([p.points for p in players], gap_factor)
    return (
        f'<section class="col" style="--accent:{accent}">'
        f"<h2>{html.escape(pos)} <small>{len(players)} · {tiers} tiers</small></h2>"
        f"<table><tbody>{_rows(players, gap_factor)}</tbody></table></section>"
    )


def render_html(
    by_pos: Mapping[str, Sequence[Player]], *, gap_factor: float, source: Path
) -> str:
    """The whole page: one column per position, in `by_pos` order.

    `source` is the projections CSV the rows came from; it goes in the header,
    because a board saved to disk should say which projections it is showing.
    """
    columns = "".join(
        _column(pos, players, gap_factor)
        for pos, players in by_pos.items()
        if players
    )
    depth = ", ".join(f"{len(v)} {k}" for k, v in by_pos.items() if v)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        "<title>Position tiers</title>"
        f"<style>{CSS}</style></head><body>"
        "<h1>Position tiers</h1>"
        f'<p class="sub">{html.escape(source.name)} · {depth} · '
        f"tier break at {gap_factor:g}× the position's median gap</p>"
        f'<div class="board">{columns}</div>'
        "</body></html>\n"
    )
