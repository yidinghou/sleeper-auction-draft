"""Render the live draft board.

A header strip naming whatever is on the block, then every seat's roster as a
card -- each player in the slot they would actually start in, with what they
cost.

The page is served by `live.py` and refreshes itself by refetching `/api/state`
and swapping in the fragments below — so everything here renders from a
`LeagueState` and is safe to call repeatedly.
"""

from __future__ import annotations

import html
from typing import List, Optional

from .config import BENCH
from .live_state import LeagueState, Seat
from .roster import display_slots, starters
from .sleeper import Nomination
from .theme import BASE_CSS, SLOT_LABEL, badge
from .valuation import Player, market_value


def _esc(text: object) -> str:
    return html.escape(str(text))


def _empty_row(label: str, text: str) -> str:
    """A slot with nobody in it. The blank second cell is the badge column —
    without it the text lands there and gets clipped to the badge's width."""
    return (
        f'<div class="prow empty"><span class="slot">{_esc(label)}</span>'
        f'<span></span><span class="pname">{_esc(text)}</span></div>'
    )


def _roster_row(slot: str, player: Optional[Player], price: int) -> str:
    label = SLOT_LABEL.get(slot, slot)
    if player is None:
        return _empty_row(label, "—")
    bye = str(player.bye) if player.bye else "—"
    return (
        '<div class="prow">'
        f'<span class="slot">{label}</span>'
        f"{badge(player.pos)}"
        f'<span class="pname" title="{_esc(player.team or "FA")}">'
        f"{_esc(player.name)}</span>"
        f'<span class="bye">{bye}</span>'
        f'<span class="pts">{player.points:.0f}</span>'
        f'<span class="price">${price}</span>'
        "</div>"
    )


def _roster_card(state: LeagueState, seat: Seat) -> str:
    """One seat's roster, laid out in the slots they would actually start in."""
    price_of = {pick.player.id: pick.price for pick in seat.picks}
    lineup = [p for p in starters(seat.roster, state.config) if p is not None]
    start_pts = sum(p.points for p in lineup)
    # Empty starter slots are shown -- a hole at RB is the whole point of the
    # card. Empty bench slots are not: bench seats are fungible, and six rows of
    # dashes early on make every card tall and unreadable at exactly the moment
    # you need to scan twelve of them.
    layout = [
        (slot, player)
        for slot, player in display_slots(seat.roster, state.config)
        if player is not None or slot != BENCH
    ]
    rows = "".join(
        _roster_row(slot, player, price_of.get(player.id, 0) if player else 0)
        for slot, player in layout
    )
    bench_open = state.config.roster_slots.count(BENCH) - sum(
        1 for slot, _ in layout if slot == BENCH
    )
    if bench_open:
        rows += _empty_row(BENCH, f"+{bench_open} open")
    return (
        f'<section class="card" data-roster="{seat.slot}">'
        f'<header><span class="team">Seat {seat.slot}</span>'
        f'<span class="totals">${seat.budget_left} left · '
        f"{start_pts:.0f} pts</span></header>"
        f'<div class="prow head"><span class="slot"></span><span></span>'
        f'<span class="pname"></span><span class="bye">BYE</span>'
        f'<span class="pts">PTS</span><span class="price">$</span></div>'
        f"{rows}</section>"
    )


def render_rosters(state: LeagueState) -> str:
    """Every seat's roster as a card, in seat order.

    Seat order, and never sorted by anything that moves: these are read to look
    something up ("what does seat 4 still need at receiver?"), so a card has to
    stay where you last saw it rather than jumping every time someone bids.
    """
    cards = "".join(
        _roster_card(state, state.seats[slot]) for slot in sorted(state.seats)
    )
    return f'<div class="grid">{cards}</div>'


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

  /* Fixed four across, so a 12-seat league lays out as 3 rows of 4 and every
     card keeps the same place on screen between refreshes -- an auto-fill grid
     reflows as the window changes and you lose track of which card is whose.
     Narrower viewports still step down rather than crushing the rows. */
  .grid {{ display: grid; gap: 14px;
    grid-template-columns: repeat(4, minmax(0, 1fr)); }}
  @media (max-width: 1100px) {{
    .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  }}
  @media (max-width: 620px) {{
    .grid {{ grid-template-columns: minmax(0, 1fr); }}
  }}
  .card {{ background: #131b38; border: 1px solid #414566; border-radius: 10px;
    padding: 10px 12px; }}
  .card header {{ display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 6px; padding-bottom: 6px; border-bottom: 1px solid #252942; }}
  .card.me {{ border-color: #00d7ff; }}
  .card.me .team::after {{ content: " (you)"; color: #00d7ff; font-weight: 400; }}
  .team {{ font-weight: 700; }}
  .totals {{ color: #98b3d6; font-size: 11px; }}
  .prow {{ display: grid; grid-template-columns: 38px 34px 1fr 26px 34px 36px;
    align-items: center; gap: 6px; padding: 2px 0; }}
  .prow.empty {{ color: #4a5170; }}
  .prow.head {{ color: #4a5170; font-size: 9px; text-transform: uppercase;
    padding-bottom: 2px; }}
  .slot {{ color: #98b3d6; font-size: 10px; text-transform: uppercase; }}
  .pname {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    cursor: default; }}
  .bye, .pts {{ color: #98b3d6; font-size: 11px; text-align: right; }}
  .price {{ color: #ffab0e; font-weight: 700; font-size: 12px; text-align: right; }}

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

  <h2>Rosters</h2>
  <p class="sub">Each player sits in the slot they would actually start in, not
    the one they were bought for. PTS is the season projection; the card total is
    the starting lineup only.</p>
  <div id="rosters"></div>

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
  document.querySelectorAll("section.card").forEach((card) => {{
    card.classList.toggle("me", mine !== "" && card.dataset.roster === mine);
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
    document.getElementById("rosters").innerHTML = s.rosters_html;
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
