"""Render the live draft board: who can still outbid you, and on what.

One table, one row per seat, sorted by reach (max bid) so the seats that can
actually take the current player off you sit at the top. A header strip names
whatever is on the block, because the table is unreadable without knowing what
the room is bidding on.

The page is served by `live.py` and refreshes itself by refetching `/api/state`
and swapping in the fragments below — so everything here renders from a
`LeagueState` and is safe to call repeatedly.
"""

from __future__ import annotations

import html
from typing import List, Optional

from .config import CONCRETE_POSITIONS
from .live_state import LeagueState, Seat, seat_value_of
from .roster import startable_slots
from .sleeper import Nomination
from .theme import BASE_CSS, badge
from .valuation import Player, market_value


def _need_positions(state: LeagueState) -> List[str]:
    """Positions worth a needs column. A position with no startable slot (K in
    the default lineup) is dropped: you can never be *short* one."""
    return [
        pos
        for pos in CONCRETE_POSITIONS
        if startable_slots(pos, state.config) > 0
    ]


def _esc(text: object) -> str:
    return html.escape(str(text))


def _needs_cell(seat: Seat, positions: List[str]) -> str:
    """The seat's unfilled starting positions, dimmed once satisfied."""
    parts = []
    for pos in positions:
        want = seat.needs.get(pos, 0)
        cls = "need" if want else "need done"
        parts.append(f'<span class="{cls}">{_esc(pos)}{want if want else ""}</span>')
    return f'<span class="needs">{"".join(parts)}</span>'


def _seat_row(
    state: LeagueState, seat: Seat, player: Optional[Player], positions: List[str]
) -> str:
    """One seat's money, room and reach — plus what the nominee is worth to it."""
    value = seat_value_of(state, seat, player)
    if player is None:
        threat, threat_cls = "—", "muted"
    elif seat.max_bid <= 0:
        threat, threat_cls = "out", "muted"
    elif value <= 0.0:
        # Can afford them but cannot start them: not a real bidder.
        threat, threat_cls = "no fit", "muted"
    else:
        threat, threat_cls = f"+{value:.0f} pts", "fit"

    broke = " broke" if seat.budget_left <= 0 and seat.open_slots > 0 else ""
    return (
        f'<tr data-slot="{seat.slot}" class="seat{broke}">'
        f'<td class="rowhead">Seat {seat.slot}</td>'
        f'<td class="num money">${seat.budget_left}</td>'
        f'<td class="num reach">${seat.max_bid}</td>'
        f'<td class="num">{seat.filled}/{state.config.roster_size}</td>'
        f'<td class="num muted">${seat.spent}</td>'
        f"<td>{_needs_cell(seat, positions)}</td>"
        f'<td class="num {threat_cls}">{threat}</td>'
        "</tr>"
    )


def render_table(state: LeagueState, player: Optional[Player]) -> str:
    """The 12-seat board, richest reach first."""
    positions = _need_positions(state)
    seats = sorted(
        state.seats.values(), key=lambda s: (-s.max_bid, -s.budget_left, s.slot)
    )
    rows = "".join(_seat_row(state, seat, player, positions) for seat in seats)
    return (
        '<table class="board"><thead><tr>'
        "<th>Seat</th><th>Left</th><th>Max bid</th><th>Filled</th>"
        "<th>Spent</th><th>Needs</th><th>Value of nominee</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_nomination(
    state: LeagueState, nom: Nomination, player: Optional[Player]
) -> str:
    """The header strip: what's on the block and what the room has bid."""
    if not nom.is_live:
        return (
            '<div class="block idle">Nothing nominated — '
            f"{len(state.picks)} picks in.</div>"
        )
    if player is None:
        name = f"player {_esc(nom.player_id)}"
        meta = '<span class="muted">not in projections</span>'
    else:
        name = _esc(player.name)
        proj = market_value(player)
        proj_txt = f"${proj:.0f}" if proj else "—"
        meta = (
            f'{badge(player.pos)} <span class="muted">{_esc(player.team)}</span> '
            f'· <span class="muted">$PROJ</span> <span class="proj">{proj_txt}</span> '
            f'· <span class="muted">{player.points:.0f} pts</span>'
        )
    bid = f"${nom.high_bid}" if nom.high_bid is not None else "—"
    by = (
        f'<span class="muted">seat {nom.offering_slot}</span>'
        if nom.offering_slot
        else '<span class="muted">no bids</span>'
    )
    nominator = (
        f'<span class="muted">nom. by seat {nom.nominating_slot}</span>'
        if nom.nominating_slot
        else ""
    )
    return (
        '<div class="block">'
        f'<div class="onblock"><span class="who">{name}</span> {meta}</div>'
        f'<div class="bidnow"><span class="bidamt">{bid}</span> {by} {nominator}</div>'
        "</div>"
    )


def render_page(draft_id: str) -> str:
    """The page shell. Contents arrive from /api/state and refresh in place."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live draft board</title>
<style>{BASE_CSS}
  .block {{ background: #131b38; border: 1px solid #414566; border-radius: 10px;
    padding: 10px 14px; margin: 0 0 16px; display: flex; gap: 16px;
    justify-content: space-between; align-items: center; flex-wrap: wrap; }}
  .block.idle {{ color: #98b3d6; }}
  .who {{ font-size: 17px; font-weight: 700; margin-right: 6px; }}
  .bidamt {{ color: #ffab0e; font-weight: 700; font-size: 17px; }}
  .proj {{ color: #ffab0e; font-weight: 700; }}

  /* Capped, not full-bleed: seven short columns stretched across a wide
     monitor put the money and the needs an eye-movement apart. */
  .board {{ border-collapse: collapse; font-size: 13px;
    width: 100%; max-width: 900px; }}
  .block {{ max-width: 900px; }}
  .board th, .board td {{ border: 1px solid #252942; padding: 5px 10px;
    text-align: left; white-space: nowrap; }}
  .board thead th {{ color: #98b3d6; font-weight: 600; background: #131b38;
    font-size: 11px; text-transform: uppercase; }}
  .board .num {{ text-align: right; }}
  .rowhead {{ font-weight: 700; }}
  .money {{ color: #28e757; font-weight: 700; }}
  .reach {{ color: #ffab0e; font-weight: 700; }}
  .fit {{ color: #00d7ff; font-weight: 700; }}
  tr.seat.broke .money {{ color: #ff6482; }}
  tr.seat.me {{ outline: 2px solid #00d7ff; outline-offset: -2px; }}
  tr.seat.me .rowhead::after {{ content: " (you)"; color: #00d7ff; font-weight: 400; }}

  .needs {{ display: inline-flex; gap: 4px; }}
  .need {{ font-size: 10px; font-weight: 700; padding: 2px 5px; border-radius: 5px;
    background: #ff648233; color: #ff6482; }}
  .need.done {{ background: #252942; color: #4a5170; }}

  .bar {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
    margin: 0 0 16px; font-size: 13px; color: #98b3d6; }}
  select {{ background: #131b38; color: #fafafa; border: 1px solid #414566;
    border-radius: 6px; padding: 4px 8px; font: inherit; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #28e757;
    display: inline-block; }}
  .dot.stale {{ background: #ff6482; }}
</style></head>
<body>
  <h1>Live draft board</h1>
  <p class="sub" id="sub">connecting to draft {_esc(draft_id)}…</p>

  <div class="bar">
    <span><span class="dot" id="dot"></span> <span id="pulse">polling</span></span>
    <label>Your seat
      <select id="seat"><option value="">—</option></select>
    </label>
    <span id="warn" class="warn"></span>
  </div>

  <div id="block"></div>
  <div class="tablewrap" id="table"></div>
  <p class="legend">
    <strong>Max bid</strong> reserves $1 for every other open slot, so it is the
    most a seat can legally spend on one player.
    <strong>Value of nominee</strong> is the points they would add to that seat's
    starting lineup — a seat showing “no fit” has money but nowhere to play them.
  </p>

<script>
const seatSel = document.getElementById("seat");
// Seat options are built once, from the league size the server reports.
function fillSeats(teams) {{
  if (seatSel.options.length > 1) return;
  for (let i = 1; i <= teams; i++) {{
    const o = document.createElement("option");
    o.value = i; o.textContent = "Seat " + i; seatSel.appendChild(o);
  }}
  seatSel.value = localStorage.getItem("draftsim.seat") || "";
}}
seatSel.addEventListener("change", () => {{
  localStorage.setItem("draftsim.seat", seatSel.value);
  highlight();
}});

function highlight() {{
  const mine = seatSel.value;
  document.querySelectorAll("tr.seat").forEach((tr) => {{
    tr.classList.toggle("me", mine !== "" && tr.dataset.slot === mine);
  }});
}}

async function tick() {{
  const dot = document.getElementById("dot");
  try {{
    const res = await fetch("/api/state", {{ cache: "no-store" }});
    const s = await res.json();
    fillSeats(s.teams);
    document.getElementById("sub").textContent = s.subtitle;
    document.getElementById("block").innerHTML = s.nomination_html;
    document.getElementById("table").innerHTML = s.table_html;
    document.getElementById("pulse").textContent = s.polled_at;
    document.getElementById("warn").textContent = s.warning || "";
    dot.classList.remove("stale");
    highlight();
  }} catch (err) {{
    dot.classList.add("stale");
    document.getElementById("pulse").textContent = "server unreachable";
  }}
}}
tick();
setInterval(tick, 2000);
</script>
</body></html>
"""
