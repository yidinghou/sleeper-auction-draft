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
from typing import List, Optional, Tuple

from .config import BENCH
from .live_state import LeagueState, Seat
from .roster import display_slots, starters
from .sleeper import Nomination
from .theme import BASE_CSS, POS_COLOR, POS_FALLBACK, SLOT_LABEL, badge
from .valuation import Player, market_value


def _esc(text: object) -> str:
    return html.escape(str(text))


# How starter slots pair up, two per row. Chosen so each row reads as one
# decision: your two quarterback seats, then the two flex-ish RB/WR seats twice,
# then the pure flexes, then the leftovers. Slots absent from a league are
# skipped and anything unlisted is chunked in twos, so a non-default lineup
# still renders.
_PREFERRED_PAIRS = (
    ("QB", "SUPER_FLEX"),
    ("RB", "WR"),
    ("RB", "WR"),
    ("FLEX", "REC_FLEX"),
    ("DEF", "TE"),
)

Row = Tuple[str, Optional[Player]]


def _pair_starters(rows: List[Row]) -> List[Tuple[Optional[Row], Optional[Row]]]:
    """Fold slot rows into pairs, following _PREFERRED_PAIRS where possible."""
    remaining = list(rows)

    def take(slot: str) -> Optional[Row]:
        for i, (s, _) in enumerate(remaining):
            if s == slot:
                return remaining.pop(i)
        return None

    paired: List[Tuple[Optional[Row], Optional[Row]]] = []
    for left, right in _PREFERRED_PAIRS:
        a, b = take(left), take(right)
        if a or b:
            paired.append((a, b))
    while remaining:
        a = remaining.pop(0)
        b = remaining.pop(0) if remaining else None
        paired.append((a, b))
    return paired


def _cell(row: Optional[Row], price: int) -> str:
    """One slot in a paired row. The position shows as the cell's own color
    rather than a badge -- at this size a badge costs more width than the name
    it labels, and the color carries the same information."""
    if row is None:
        return '<span class="cell gone"></span>'
    slot, player = row
    label = SLOT_LABEL.get(slot, slot)
    if player is None:
        return (
            f'<span class="cell open"><i class="ms">{_esc(label)}</i>'
            '<b class="mn">—</b></span>'
        )
    color = POS_COLOR.get(player.pos, POS_FALLBACK)
    tip = f"{player.name} · {player.pos} · {player.team or 'FA'}"
    if player.bye:
        tip += f" · BYE {player.bye}"
    tip += f" · {player.points:.0f} pts · ${price}"
    return (
        f'<span class="cell" style="--pos:{color}" title="{_esc(tip)}">'
        f'<i class="ms">{_esc(label)}</i>'
        f'<b class="mn">{_esc(player.name)}</b>'
        f'<i class="mb">{player.bye or "—"}</i>'
        f'<i class="mp">{player.points:.0f}</i>'
        f'<i class="mc">${price}</i></span>'
    )


def _bench_cell(player: Optional[Player], price: int) -> str:
    """A bench body. Bench slots are fungible, so they carry no slot label."""
    if player is None:
        return '<span class="cell gone"></span>'
    color = POS_COLOR.get(player.pos, POS_FALLBACK)
    return (
        f'<span class="cell" style="--pos:{color}" '
        f'title="{_esc(player.name)} · {_esc(player.pos)}">'
        f'<i class="ms">BN</i><b class="mn">{_esc(player.name)}</b>'
        f'<i class="mb">{player.bye or "—"}</i>'
        f'<i class="mp">{player.points:.0f}</i>'
        f'<i class="mc">${price}</i></span>'
    )


def _roster_card(state: LeagueState, seat: Seat) -> str:
    """One seat's roster: starters paired two to a row, then the bench.

    Bench rows carry a `bench` class so CSS can grey them back. They stay
    visible rather than hidden -- depth is worth seeing, and dimming separates
    it from the lineup faster than reading the BN label would.
    """
    price_of = {pick.player.id: pick.price for pick in seat.picks}
    lineup = [p for p in starters(seat.roster, state.config) if p is not None]
    start_pts = sum(p.points for p in lineup)

    def price(row: Optional[Row]) -> int:
        if row is None or row[1] is None:
            return 0
        return price_of.get(row[1].id, 0)

    layout = display_slots(seat.roster, state.config)
    starter_rows = [r for r in layout if r[0] != BENCH]
    bench = [player for slot, player in layout if slot == BENCH and player]

    rows = "".join(
        f'<div class="prow">{_cell(a, price(a))}{_cell(b, price(b))}</div>'
        for a, b in _pair_starters(starter_rows)
    )
    for i in range(0, len(bench), 2):
        pair = bench[i : i + 2]
        cells = "".join(_bench_cell(p, price_of.get(p.id, 0)) for p in pair)
        if len(pair) == 1:
            cells += '<span class="cell gone"></span>'
        rows += f'<div class="prow bench">{cells}</div>'

    bench_open = state.config.roster_slots.count(BENCH) - len(bench)
    open_txt = f" · {bench_open} open" if bench_open else ""
    return (
        f'<section class="card" data-roster="{seat.slot}">'
        f'<header><span class="team">S{seat.slot}</span>'
        f'<span class="totals">${seat.budget_left} · {start_pts:.0f}'
        f"{open_txt}</span></header>"
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
    """The page shell. Contents arrive from /api/state and refresh in place.

    Two halves: the left is reserved space, the right holds the whole board
    inside one viewport height -- twelve seats visible at once, nothing to
    scroll mid-auction. The maximize toggle is pure CSS over the same markup,
    so opening the overlay costs no fetch and cannot show a different moment of
    the draft than the compact view behind it.
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live draft board</title>
<style>{BASE_CSS}
  html, body {{ height: 100%; }}
  body {{ margin: 0; padding: 0; overflow: hidden;
    display: grid; grid-template-columns: 1fr 1fr; }}

  .reserved {{ border-right: 1px solid #252942;
    display: flex; align-items: center; justify-content: center;
    color: #252942; font-size: 12px; letter-spacing: 0.18em;
    text-transform: uppercase; }}

  /* The board column is a flex stack whose last child (the grid) absorbs the
     leftover height, so the twelve cards always end exactly at the fold. */
  .live {{ display: flex; flex-direction: column; min-width: 0;
    padding: 8px 10px; gap: 6px; }}
  .live h1 {{ font-size: 13px; margin: 0; }}
  .live .sub {{ margin: 0; font-size: 11px; }}

  .bar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    font-size: 11px; color: #98b3d6; }}
  select {{ background: #131b38; color: #fafafa; border: 1px solid #414566;
    border-radius: 6px; padding: 2px 6px; font: inherit; }}
  button {{ background: #131b38; color: #00d7ff; border: 1px solid #414566;
    border-radius: 6px; padding: 2px 8px; font: inherit; cursor: pointer; }}
  button:hover {{ border-color: #00d7ff; }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: #28e757;
    display: inline-block; }}
  .dot.stale {{ background: #ff6482; }}

  .block {{ background: #131b38; border: 1px solid #414566; border-radius: 8px;
    padding: 4px 10px; display: flex; gap: 10px;
    justify-content: space-between; align-items: center; flex-wrap: wrap; }}
  .block.idle {{ color: #98b3d6; font-size: 12px; }}
  .who {{ font-size: 14px; font-weight: 700; margin-right: 4px; }}
  .bidamt {{ color: #ffab0e; font-weight: 700; font-size: 14px; }}
  .proj {{ color: #ffab0e; font-weight: 700; }}

  /* Three across, four down: twelve seats, one screen, fixed positions so a
     card stays where you last saw it between refreshes. */
  #rosters {{ flex: 1; min-height: 0; }}
  .grid {{ display: grid; gap: 5px; height: 100%;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    grid-auto-rows: minmax(0, 1fr); }}
  .card {{ background: #131b38; border: 1px solid #414566; border-radius: 8px;
    padding: 4px 6px; min-width: 0; overflow: hidden;
    display: flex; flex-direction: column; }}
  .card header {{ display: flex; justify-content: space-between;
    align-items: baseline; gap: 6px; margin-bottom: 2px; }}
  .card.me {{ border-color: #00d7ff; }}
  .card.me .team::after {{ content: " (you)"; color: #00d7ff; font-weight: 400; }}
  .team {{ font-weight: 700; font-size: 11px; }}
  .totals {{ color: #98b3d6; font-size: 10px; white-space: nowrap; }}

  .prow {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3px;
    margin-bottom: 1px; min-width: 0; }}
  /* Bench sits back: dimmed, so a glance separates who a seat starts from who
     it is merely holding, without having to read the BN label. */
  .prow.bench .cell {{ opacity: 0.45; }}

  /* One cell = one lineup seat. Only the slot chip carries the position color;
     tinting the whole cell made twelve cards read as a wall of blocks with the
     names competing against their own backgrounds. The chip still says which
     slot it is, so a WR sitting in FLEX shows "FLEX" in receiver blue. */
  .cell {{ display: grid; grid-template-columns: auto 1fr auto;
    align-items: center; gap: 3px; min-width: 0;
    padding: 0 2px; font-size: 10px; line-height: 1.45; }}
  .ms {{ font-style: normal; font-size: 8px; text-transform: uppercase;
    font-weight: 700; text-align: center; min-width: 24px;
    border-radius: 3px; padding: 0 3px;
    color: var(--pos, #98b3d6);
    background: color-mix(in srgb, var(--pos, #414566) 20%, transparent); }}
  .cell.open .ms {{ color: #4a5170; background: #1a2038; }}
  .cell.open {{ color: #4a5170; }}
  .mn {{ font-weight: 400; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }}
  .mc {{ font-style: normal; color: #ffab0e; font-weight: 700; font-size: 9px; }}
  /* Bye and points are the first things to go when space is short; they live
     in the hover tooltip until the overlay has room for them. */
  .mb, .mp {{ display: none; font-style: normal; color: #98b3d6; font-size: 9px; }}

  /* Maximized: the board leaves its half and takes the viewport. Nothing is
     re-rendered -- the bench rows and the hidden columns were always there. */
  body.maxed .live {{ position: fixed; inset: 0; z-index: 10;
    background: #05091d; padding: 14px 16px; }}
  body.maxed .grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }}
  body.maxed .card {{ padding: 6px 8px; }}
  body.maxed .team {{ font-size: 13px; }}
  body.maxed .totals {{ font-size: 11px; }}
  body.maxed .cell {{ font-size: 11px; padding: 1px 5px;
    grid-template-columns: auto 1fr auto auto auto; gap: 5px; }}
  body.maxed .ms {{ font-size: 9px; }}
  body.maxed .mb, body.maxed .mp {{ display: inline; }}
  body.maxed .mc {{ font-size: 11px; }}
</style></head>
<body>
  <aside class="reserved">reserved</aside>

  <main class="live">
    <h1>Live draft board</h1>
    <p class="sub" id="sub">connecting to draft {_esc(draft_id)}…</p>

    <div class="bar">
      <span><span class="dot" id="dot"></span> <span id="pulse">polling</span></span>
      <label>Seat <select id="seat"><option value="">—</option></select></label>
      <button id="max" type="button">Maximize</button>
      <span id="warn" class="warn"></span>
    </div>

    <div id="block"></div>
    <div id="rosters"></div>
  </main>

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

const maxBtn = document.getElementById("max");
function setMaxed(on) {{
  document.body.classList.toggle("maxed", on);
  maxBtn.textContent = on ? "Minimize" : "Maximize";
}}
maxBtn.addEventListener("click", () => {{
  setMaxed(!document.body.classList.contains("maxed"));
}});
document.addEventListener("keydown", (e) => {{
  if (e.key === "Escape") setMaxed(false);
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
