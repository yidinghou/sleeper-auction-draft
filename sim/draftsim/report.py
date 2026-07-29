"""Render a finished draft as a self-contained HTML board you can open in a browser.

One public function, `render_html(result, seed)`, returns a full dark-themed page
(inline CSS, no external assets) laid out like Sleeper's post-draft rosters: a
grid of team cards, each showing its slotted starting lineup (via the optimal,
order-invariant `roster.starters()`) plus bench, one row per player with a
colored position badge and the price paid. Colors mirror the Next.js app
(`lib/positions.ts` / `app/globals.css`) so it reads like the real thing.
"""

from __future__ import annotations

import html
from typing import List, Optional, Tuple

from .config import BENCH, DraftConfig
from .engine import DraftResult
from .roster import starters
from .valuation import Player

# Position badge colors, mirrored from the app's Sleeper theme.
_POS_COLOR = {
    "QB": "#ff6482",
    "RB": "#28e757",
    "WR": "#00d7ff",
    "TE": "#ffab0e",
}
_POS_FALLBACK = "#98b3d6"  # K, DEF, anything else

# Compact labels for the long flex slot names (matches `slotShortLabel()`).
_SLOT_LABEL = {"SUPER_FLEX": "SFLX", "REC_FLEX": "RFLX", "WRRB_FLEX": "W/R"}


def _slot_rows(
    roster: List[Player], config: DraftConfig
) -> List[Tuple[str, Optional[Player]]]:
    """Assign the roster to display slots: starter slots take the optimal lineup
    (slot-aligned), bench slots take the leftovers in order."""
    lineup = starters(roster, config)  # aligned to config.starter_slots
    used = {id(p) for p in lineup if p is not None}
    bench = [p for p in roster if id(p) not in used]

    rows: List[Tuple[str, Optional[Player]]] = []
    si = bi = 0
    for slot in config.roster_slots:
        if slot == BENCH:
            rows.append((slot, bench[bi] if bi < len(bench) else None))
            bi += 1
        else:
            rows.append((slot, lineup[si] if si < len(lineup) else None))
            si += 1
    rows.extend((BENCH, p) for p in bench[bi:])  # overflow, if any
    return rows


def _player_row(slot: str, player: Optional[Player], price: int) -> str:
    label = _SLOT_LABEL.get(slot, slot)
    if player is None:
        return (
            f'<div class="row empty"><span class="slot">{label}</span>'
            f'<span class="name">—</span></div>'
        )
    color = _POS_COLOR.get(player.pos, _POS_FALLBACK)
    bye = f" · BYE {player.bye}" if player.bye else ""
    meta = html.escape(f"{player.team}{bye}")
    return (
        f'<div class="row">'
        f'<span class="slot">{label}</span>'
        f'<span class="badge" style="color:{color};background:{color}33">{html.escape(player.pos)}</span>'
        f'<span class="name">{html.escape(player.name)}</span>'
        f'<span class="meta">{meta}</span>'
        f'<span class="price">${price}</span>'
        f"</div>"
    )


def _team_card(result: DraftResult, manager_id: str) -> str:
    config = result.config
    manager = result.managers[manager_id]
    price_of = {pk.player.id: pk.price for pk in result.picks if pk.winner_id == manager_id}

    lineup = [p for p in starters(manager.roster, config) if p is not None]
    starter_points = sum(p.points for p in lineup)

    rows = "".join(
        _player_row(slot, player, price_of.get(player.id, 0) if player else 0)
        for slot, player in _slot_rows(manager.roster, config)
    )
    return (
        f'<section class="card">'
        f'<header><span class="team">{html.escape(manager_id)}</span>'
        f'<span class="totals">spent ${result.spend(manager_id)} · '
        f'left ${manager.budget} · {starter_points:.0f} pts</span></header>'
        f"{rows}</section>"
    )


def render_html(result: DraftResult, seed: Optional[int] = None) -> str:
    """Full self-contained HTML page for a finished draft."""
    config = result.config
    seed_txt = "" if seed is None else f" · seed {seed}"
    cards = "".join(_team_card(result, mid) for mid in sorted(result.managers))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Draft results{seed_txt}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: #05091d; color: #fafafa;
    font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .sub {{ color: #98b3d6; margin: 0 0 20px; font-size: 13px; }}
  .grid {{
    display: grid; gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  }}
  .card {{ background: #131b38; border: 1px solid #414566; border-radius: 10px; padding: 10px 12px; }}
  .card header {{ display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #252942; }}
  .team {{ font-weight: 700; }}
  .totals {{ color: #98b3d6; font-size: 11px; }}
  .row {{ display: grid; grid-template-columns: 40px 34px 1fr auto auto;
    align-items: center; gap: 6px; padding: 3px 0; }}
  .row.empty {{ color: #4a5170; }}
  .slot {{ color: #98b3d6; font-size: 10px; text-transform: uppercase; }}
  .badge {{ font-size: 10px; font-weight: 700; text-align: center;
    padding: 2px 0; border-radius: 5px; }}
  .name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .meta {{ color: #98b3d6; font-size: 11px; }}
  .price {{ color: #ffab0e; font-weight: 700; min-width: 34px; text-align: right; }}
</style></head>
<body>
  <h1>Auction draft results</h1>
  <p class="sub">{config.teams} teams · ${config.budget} budget · {config.roster_size} slots{seed_txt} · {len(result.picks)} picks</p>
  <div class="grid">{cards}</div>
</body></html>
"""
