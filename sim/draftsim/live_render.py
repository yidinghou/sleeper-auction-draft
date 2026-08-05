"""Render the live draft board.

A header strip naming whatever is on the block, then every seat's roster as a
card -- one player per line, in the slot they would actually start in, with what
they cost. Each card carries three panes over the same data (LINEUP, BN, NEED),
picked by the segmented control in its header, and cards can be dragged into
whatever order you want to read them in.

The card header is money and nothing else: what a seat has left, the most it can
still bid, and a bar of the two. That is the question the board exists to answer
-- *who can outbid me* -- and points and slot counts were competing with it, so
they moved into the NEED pane where there is room to label them.

The page is served by `live.py` and refreshes itself by refetching `/api/state`
and swapping in the fragments below — so everything here renders from a
`LeagueState` and is safe to call repeatedly.
"""

from __future__ import annotations

import html
import math
from typing import Optional

from .config import BENCH
from .live_pressure import PositionPressure, pressure
from .live_state import (
    DRAFT_TARGETS,
    LeagueState,
    PositionLine,
    Seat,
    position_summary,
)
from .roster import display_slots
from .sleeper import Nomination
from .theme import (
    BASE_CSS_LIGHT,
    POS_COLOR_LIGHT,
    POS_FALLBACK_LIGHT,
    SLOT_LABEL,
    badge,
)
from .valuation import Player, market_value

# A max bid at or under this cannot win a player anyone else wants, so the seat
# has stopped being someone you bid against. The card says so in red rather than
# leaving you to notice the number.
_OUT_OF_MARKET = 5


def _esc(text: object) -> str:
    return html.escape(str(text))


def _num(value: float) -> str:
    """Trim a target or a rate to the shortest honest form: 2.5 stays 2.5, 3.0
    prints as 3. A trailing `.0` down every column reads as false precision."""
    return f"{value:g}"


def _short_name(player: Player) -> str:
    """First initial + surname, for the compact view.

    "Joe Burrow" -> "J. Burrow", and "Amon-Ra St. Brown" -> "A. St. Brown",
    which is what actually stops names ellipsising at 115px. Team defenses are
    named for their city ("Kansas City Chiefs"), where an initial reads as
    nonsense -- they get the nickname instead.
    """
    parts = player.name.split()
    if len(parts) < 2:
        return player.name
    if player.pos == "DEF":
        return parts[-1]
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _player_row(label: str, player: Player, price: int, bench: bool = False) -> str:
    """One player, one line: slot chip, name, then the numeric columns.

    Both name forms are emitted and CSS picks one -- short when compact, full
    when maximized. Rendering both costs a few bytes and keeps the swap free;
    re-fetching to change name length would not.

    Bench rows carry the player's own position as the chip rather than a
    fungible BN. In the bench pane there is nothing else saying what these
    players are, and "three of my four bench bodies are receivers" is what the
    pane is read for. CSS outlines that chip instead of filling it, so depth
    still reads differently from the lineup.
    """
    color = POS_COLOR_LIGHT.get(player.pos, POS_FALLBACK_LIGHT)
    tip = f"{player.name} · {player.pos} · {player.team or 'FA'}"
    if player.bye:
        tip += f" · BYE {player.bye}"
    tip += f" · {player.points:.0f} pts · ${price}"
    return (
        f'<div class="ln{" bench" if bench else ""}" style="--pos:{color}"'
        f' title="{_esc(tip)}">'
        f'<i class="ms">{_esc(label)}</i>'
        f'<b class="mn">{_esc(_short_name(player))}</b>'
        f'<b class="mnf">{_esc(player.name)}</b>'
        f'<i class="mb">{player.bye or "—"}</i>'
        f'<i class="mp">{player.points:.0f}</i>'
        f'<i class="mc">${price}</i></div>'
    )


def _open_row(slot: str) -> str:
    """An unfilled starter slot. The hole is the point of the card, so it takes
    a full line like anything else rather than being collapsed away."""
    label = SLOT_LABEL.get(slot, slot)
    return (
        f'<div class="ln open"><i class="ms">{_esc(label)}</i>'
        '<b class="mn">—</b><b class="mnf">—</b>'
        '<i class="mb"></i><i class="mp"></i><i class="mc">·</i></div>'
    )


def _column_header() -> str:
    """Column labels, shown only when maximized -- compact has no room and only
    one number to label anyway."""
    return (
        '<div class="ln colhead"><i class="ms"></i>'
        '<b class="mn"></b><b class="mnf"></b>'
        '<i class="mb">BYE</i><i class="mp">PTS</i><i class="mc">$</i></div>'
    )


def _pips(have: int, want: float, color: str) -> str:
    """The target drawn at its true length: one pip per whole starter it asks
    for, the fractional remainder drawn as a half-width pip.

    This replaced a percentage-fill bar, which lied at the top end -- a ratio
    pins at 100% the moment you reach the target, so 4/2.5 and 2.5/2.5 drew
    identically when they mean opposite things. Here surplus bodies show as
    narrow outlined pips *past* the run, so depth reads as depth.
    """
    whole = int(want)
    pips = "".join(
        f'<span class="pip{" on" if i < have else ""}"></span>'
        for i in range(whole)
    )
    if want > whole:
        pips += f'<span class="pip half{" on" if have > whole else ""}"></span>'
    pips += '<span class="pip extra"></span>' * max(0, have - math.ceil(want))
    return f'<span class="pips" style="--pos:{color}">{pips}</span>'


def _need_row(line: PositionLine) -> str:
    """One position: chip, have/target, the pips, and what it starts."""
    color = POS_COLOR_LIGHT.get(line.pos, POS_FALLBACK_LIGHT)
    pts = f"{line.starter_points:.0f}" if line.starter_points else "—"
    return (
        f'<div class="nrow{" short" if line.need else ""}" style="--pos:{color}">'
        f'<i class="ms">{_esc(line.pos)}</i>'
        f'<b class="nct">{line.have}<i>/{_num(line.want)}</i></b>'
        f"{_pips(line.have, line.want, color)}"
        f'<i class="npt">{pts}</i></div>'
    )


# Positions the NEED pane leaves out. Both are settled early and stay settled --
# you buy one defense, one kicker, and neither is a decision you make at the
# podium -- so they cost a row each without ever changing what you would bid.
# The LINEUP pane still shows them.
_NEED_SKIP = ("DEF", "K")


def _need_pane(state: LeagueState, seat: Seat) -> str:
    """What this seat still wants, and how fast it has to spend to get it.

    The pace line is the pane's footer because budget alone does not say whether
    a seat is ahead or behind: $37 across three slots and $37 across twelve are
    different seats. Dollars per remaining slot is the number that says when to
    start dumping.
    """
    rows = "".join(
        _need_row(line)
        for line in position_summary(seat, state.config)
        if line.pos not in _NEED_SKIP
    )
    per_slot = seat.budget_left / seat.open_slots if seat.open_slots else 0.0
    return (
        f'<div class="pane need" data-pane="need">{rows}'
        '<div class="pace">'
        f'<span><b>{seat.open_slots}</b> slots left</span>'
        f'<span><b>${_num(round(per_slot, 1))}</b> / slot</span>'
        "</div></div>"
    )


def _card_header(state: LeagueState, seat: Seat) -> str:
    """Money, and nothing else: what is left, the most it can still bid, and a
    bar of both.

    The bar splits what is spendable from what is already owed -- a dollar per
    slot still to fill is in the account but not available, and a seat with $80
    and eight holes is not the threat its balance says it is. Points and slot
    counts used to sit here and moved to the NEED pane; they were read
    occasionally and competed with the two numbers that are read constantly.
    """
    budget = state.config.budget
    held = max(0, seat.open_slots - 1)
    spendable = max(0, seat.budget_left - held)
    return (
        '<header>'
        f'<div class="top"><span class="team">S{seat.slot}</span>'
        '<span class="seg">'
        '<button data-pane="lineup" type="button">LINEUP</button>'
        '<button data-pane="bench" type="button">BN</button>'
        '<button data-pane="need" type="button">NEED</button></span></div>'
        f'<div class="top"><span class="big">${seat.budget_left}</span>'
        f'<span class="max">max <b>${seat.max_bid}</b></span></div>'
        '<div class="budget">'
        f'<i style="width:{100 * spendable / budget:.1f}%"></i>'
        f'<i class="held" style="width:{100 * held / budget:.1f}%"></i>'
        "</div></header>"
    )


def _roster_card(state: LeagueState, seat: Seat) -> str:
    """One seat, as three panes over the same roster.

    LINEUP is every starting slot, one player per line, in the slot they would
    actually start in. BN is the bench. NEED is the position targets. All three
    ship in the markup and CSS shows one, so switching panes costs no fetch and
    cannot show a different moment of the draft than the pane it replaced -- the
    same bargain the short/full name swap makes.
    """
    price_of = {pick.player.id: pick.price for pick in seat.picks}
    layout = display_slots(seat.roster, state.config)

    lineup = _column_header() + "".join(
        _open_row(slot)
        if player is None
        else _player_row(
            SLOT_LABEL.get(slot, slot), player, price_of.get(player.id, 0)
        )
        for slot, player in layout
        if slot != BENCH
    )
    bench_players = [p for slot, p in layout if slot == BENCH and p]
    bench = "".join(
        _player_row(p.pos or BENCH, p, price_of.get(p.id, 0), bench=True)
        for p in bench_players
    )
    if not bench:
        bench = '<div class="empty">no bench yet</div>'

    broke = " broke" if seat.max_bid <= _OUT_OF_MARKET else ""
    return (
        f'<section class="card{broke}" data-seat="{seat.slot}"'
        ' draggable="true">'
        f"{_card_header(state, seat)}"
        '<div class="body">'
        f'<div class="pane lineup" data-pane="lineup">{lineup}</div>'
        f'<div class="pane" data-pane="bench">{bench}</div>'
        f"{_need_pane(state, seat)}"
        "</div></section>"
    )


def render_rosters(state: LeagueState) -> str:
    """Every seat's roster as a card, in seat order.

    Seat order, and never sorted by anything that moves: these are read to look
    something up ("what does seat 4 still need at receiver?"), so a card has to
    stay where you last saw it rather than jumping every time someone bids.

    The client can drag cards into a different order, and that ordering is
    presentational only -- CSS `order` on the grid items, so this markup stays in
    seat order. Which is the point: the one thing allowed to move a card is the
    hand that dragged it, and a rearranged board still knows what seat order was.
    """
    cards = "".join(
        _roster_card(state, state.seats[slot]) for slot in sorted(state.seats)
    )
    return f'<div class="grid">{cards}</div>'


# TE gets a narrow card. It is one starter with no flex claim, so its pressure
# is a single pip per seat and a number -- and the width that buys goes to the
# three positions where a run actually costs you a lineup.
_MINOR = ("TE",)

# Enough supply to see whether the tier is a tier or a rump, without the card
# turning into a player list. The detail panel has the rest.
_BOARD_SHOWN = 3


def _seat_tile(seat: Seat, line: PositionLine, color: str) -> str:
    """One seat's fill at one position: who, what's left, and the pips.

    `data-seat` is what the client's `applyOrder()` reorders on, so this tile
    lands wherever that seat's roster card was dragged to. A seat that has met
    its target dims: what is left standing in the grid is the live demand.
    """
    done = "" if line.need else " done"
    tip = (
        f"S{seat.slot} — {line.have}/{_num(line.want)} {line.pos} · "
        + ("full" if not line.need else f"{_num(line.need)} to go")
        + f" · ${seat.budget_left} left, max ${seat.max_bid}"
    )
    return (
        f'<span class="tile{done}" data-seat="{seat.slot}" title="{_esc(tip)}">'
        f'<span class="ttop"><b>S{seat.slot}</b><i>${seat.budget_left}</i></span>'
        f"{_pips(line.have, line.want, color)}</span>"
    )


def _pressure_card(state: LeagueState, pr: PositionPressure) -> str:
    """One position: supply on the board over the league's fill state."""
    color = POS_COLOR_LIGHT.get(pr.pos, POS_FALLBACK_LIGHT)
    minor = pr.pos in _MINOR
    total = round(DRAFT_TARGETS.get(pr.pos, 0.0) * state.config.teams)

    tiles = "".join(
        _seat_tile(state.seats[slot], pr.lines[slot], color)
        for slot in sorted(state.seats)
    )

    board = ""
    if not minor:
        rows = "".join(
            f'<div class="brow"><b>{_esc(_short_name(p))}</b>'
            f'<i>{p.points:.0f}</i></div>'
            for p in pr.avail[:_BOARD_SHOWN]
        )
        if not rows:
            rows = '<div class="brow empty">tier is gone</div>'
        more = (
            f'<div class="bmore">+{len(pr.avail) - _BOARD_SHOWN} more</div>'
            if len(pr.avail) > _BOARD_SHOWN
            else ""
        )
        board = f'<i class="plbl">on the board</i><div class="bd">{rows}{more}</div>'

    # The cliff is what makes the tier count mean something -- "3 left" is only
    # frightening next to what the fourth-best is worth.
    foot = f"−{pr.cliff_drop} cliff" if pr.cliff_drop else "no tier below"
    return (
        f'<section class="pcard sev-{pr.severity}{" minor" if minor else ""}"'
        f' style="--pos:{color}" data-pos="{pr.pos}">'
        f'<div class="phd">{badge(pr.pos, light=True)}'
        f'<span class="pcount"><b>{pr.drafted}</b>/{total}</span>'
        f'<span class="pverd">{pr.left} left</span></div>'
        f'<i class="plbl">{len(pr.need_seats)} teams still need {_esc(pr.pos)}</i>'
        f'<div class="tiles">{tiles}</div>'
        f"{board}"
        f'<div class="pfoot">{foot}</div></section>'
    )


def render_pressure(state: LeagueState) -> str:
    """Run pressure, one card per position, in `TIERS` order.

    Fixed order and fixed positions, for the same reason the roster cards are in
    seat order: a card that reshuffles itself the moment a run starts is a card
    you have to hunt for at exactly the moment you needed it.
    """
    cards = "".join(_pressure_card(state, pr) for pr in pressure(state))
    return f'<div class="pgrid">{cards}</div>'


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
            f'{badge(player.pos, light=True)} <span class="muted">{_esc(player.team)}</span> '
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
    the draft than the compact view behind it. Per-card view is the same trick,
    and for the same reason; so is card order, which the client drags and stores
    but the server never knows about.
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live draft board</title>
<style>{BASE_CSS_LIGHT}
  /* One knob for board type size: every font-size and every width that has to
     hold digits is a multiple of it, so nudging density is one edit rather
     than a dozen. Raising it eats vertical room -- re-check that a full
     16-man roster still clears the fold. */
  :root {{ --fs: 1.17; }}
  html, body {{ height: 100%; }}
  body {{ margin: 0; padding: 0; overflow: hidden;
    display: grid; grid-template-columns: 1fr 1fr; }}

  /* Left: what is happening right now -- the draft's state and whatever is on
     the block. Right: the rosters. Splitting them this way gives the grid the
     whole viewport height instead of sharing it with the chrome, which is what
     lets a full 16-man roster fit. Space under the header is left free. */
  .side {{ display: flex; flex-direction: column; min-width: 0;
    padding: 10px 12px; gap: 6px; border-right: 1px solid #eee; }}
  .side h1 {{ font-size: calc(13px * var(--fs)); margin: 0; }}
  .side .sub {{ margin: 0; font-size: calc(11px * var(--fs)); }}

  /* A column, so the menu bar takes its own height off the top and the grid
     gets exactly what's left -- the grid sizes to that, and a bar measured in
     `height: 100%` terms would have pushed the bottom row off screen. */
  .board {{ min-width: 0; overflow: hidden; padding: 6px 8px;
    display: flex; flex-direction: column; gap: 4px; }}

  /* The board's own controls, over the thing they control rather than across
     the aisle in the state column. */
  .menubar {{ display: flex; align-items: center; gap: 6px; flex: none;
    min-width: 0; }}
  .menubar .hint {{ color: #aaa; font-size: calc(9px * var(--fs));
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

  .bar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    font-size: calc(11px * var(--fs)); color: #666; }}
  select {{ background: #fff; color: #1a1a1a; border: 1px solid #ddd;
    border-radius: 6px; padding: 2px 6px; font: inherit; }}
  button {{ background: #f6f6f6; color: #1a1a1a; border: 1px solid #ddd;
    border-radius: 6px; padding: 2px 8px; font: inherit; cursor: pointer; }}
  button:hover {{ border-color: #1a1a1a; }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: #1a7f37;
    display: inline-block; }}
  .dot.stale {{ background: #c0392b; }}

  /* What is on the block. The one panel on the page that is not white, because
     it is the thing you look up at between bids. */
  .block {{ background: #f6f6f6; border: 1px solid #ddd; border-radius: 8px;
    padding: 4px 10px; display: flex; gap: 10px;
    justify-content: space-between; align-items: center; flex-wrap: wrap; }}
  .block.idle {{ color: #888; font-size: calc(12px * var(--fs)); }}
  .who {{ font-size: calc(14px * var(--fs)); font-weight: 700; margin-right: 4px; }}
  .bidamt {{ color: #1a7f37; font-weight: 700; font-size: calc(14px * var(--fs)); }}
  .proj {{ color: #1a7f37; font-weight: 700; }}

  /* Four across, three down: twelve seats, one screen, fixed positions so a
     card stays where you last saw it between refreshes. Wider than it is tall,
     which is the shape the card wants now that the bench moved into its own
     pane -- a row per starter needs height, and three rows of four give each
     card a third more of it than four rows of three did. */
  #rosters {{ flex: 1; min-height: 0; }}
  .grid {{ display: grid; gap: 5px; height: 100%;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    grid-auto-rows: minmax(0, 1fr); }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 6px;
    padding: 4px 7px 6px; min-width: 0; overflow: hidden;
    display: flex; flex-direction: column; }}
  /* Drag to reorder. The card being carried fades so the gap it will leave is
     visible, and the card under the cursor shows a leading edge on the side the
     drop will insert at -- "this is where it lands", not merely "this is hovered". */
  .card.dragging {{ opacity: 0.4; }}
  .card.dropzone {{ box-shadow: inset 3px 0 0 #1a1a1a; border-color: #1a1a1a; }}
  .card header {{ cursor: grab; flex: none; margin-bottom: 3px;
    border-bottom: 1px solid #eee; padding-bottom: 3px; }}
  .card.dragging header {{ cursor: grabbing; }}
  .card.me {{ border-color: #1a1a1a; box-shadow: 0 0 0 1px #1a1a1a; }}
  .card.me .team::after {{ content: " (you)"; color: #666; font-weight: 400;
    font-size: calc(9px * var(--fs)); }}
  .card header .top {{ display: flex; align-items: baseline; gap: 5px; }}
  .team {{ font-weight: 700; font-size: calc(11px * var(--fs)); color: #444; }}

  /* The header is money and nothing else. Balance in the biggest type on the
     card, the reach beside it, and a bar of both -- everything else a seat
     could say moved into the NEED pane, where it can be labeled. */
  .big {{ font-size: calc(13px * var(--fs)); font-weight: 700; color: #1a7f37;
    line-height: 1.15; font-variant-numeric: tabular-nums; }}
  .max {{ margin-left: auto; font-size: calc(8.5px * var(--fs)); color: #888;
    white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .max b {{ color: #1a1a1a; font-weight: 700; font-size: calc(9.5px * var(--fs)); }}
  /* Green is spendable, grey is the dollar per unfilled slot that is in the
     account but already owed -- which is the whole difference between a seat
     with $80 and one hole and a seat with $80 and eight. */
  .budget {{ height: 3px; border-radius: 2px; margin-top: 3px; display: flex;
    background: #eee; overflow: hidden; }}
  .budget i {{ display: block; height: 100%; background: #1a7f37; }}
  .budget i.held {{ background: #ccc; }}
  /* Out of the market: whatever is left cannot win a player anyone else wants,
     so the seat stops being one you bid against. Figure and bar go together, so
     the state reads whether you take in the number or only the colour. */
  .card.broke .big {{ color: #c0392b; }}
  .card.broke .budget i:not(.held) {{ background: #c0392b; }}

  /* Three panes over one roster, as tabs across the card. On the dark board
     these hid until hover because twelve cards' worth of lit chrome drowned the
     data; on white they can simply be visible. */
  .seg {{ display: flex; gap: 2px; margin-left: auto; flex: none; }}
  .seg button {{ padding: 1px 5px; font-size: calc(7.5px * var(--fs)); line-height: 1.4;
    font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase;
    border: 1px solid #e2e2e2; background: #f6f6f6; color: #888;
    border-radius: 3px; }}
  .seg button:hover {{ color: #555; border-color: #cbd8ea; }}
  .seg button[aria-pressed="true"] {{ color: #fff; background: #1a1a1a;
    border-color: #1a1a1a; }}

  /* All three panes ship in every card and CSS shows one, so switching costs no
     fetch and two panes can never show different moments of the draft. No class
     means LINEUP, which is what the board loads on. */
  .body {{ flex: 1; min-height: 0; display: flex; flex-direction: column; }}
  /* A pane scrolls rather than clipping. Three rows of four fit the default
     ten-slot lineup with room to spare, but a deeper lineup must not silently
     hide a player. */
  .pane {{ display: none; overflow-y: auto; scrollbar-width: thin;
    scrollbar-color: #ccc transparent; }}
  .card .pane.lineup {{ display: flex; flex-direction: column;
    flex: 1; min-height: 0; }}
  .card.view-bench .pane.lineup, .card.view-need .pane.lineup {{ display: none; }}
  .card.view-bench .pane[data-pane="bench"] {{ display: block; }}
  .card.view-need .pane.need {{ display: flex; flex-direction: column; flex: 1;
    min-height: 0; }}
  .empty {{ color: #aaa; font-size: calc(9px * var(--fs)); padding: 4px 2px; }}

  /* One line per player, and the position is the *field the name sits on*: a
     pale tint of the position colour, mixed against white. A coloured chip put
     the loudest thing in the row next to the thing you actually read; this way
     the row is legible at a glance as "a receiver" without anything competing
     with the name. Fixed widths and tabular figures keep the numbers in real
     columns you can read down. */
  .ln {{ display: grid; align-items: center; gap: 3px; padding: 1px 3px;
    margin-bottom: 1px; border-radius: 3px;
    grid-template-columns: calc(22px * var(--fs)) minmax(0, 1fr) calc(26px * var(--fs));
    font-size: calc(10px * var(--fs)); line-height: 1.35; min-width: 0;
    background: color-mix(in srgb, var(--pos, #7c90a0) 26%, #fff); }}
  /* The lineup's rows share whatever height the card has: the line-height above
     is a floor, and `flex: 1` spends the rest of the card on the gaps between
     rows. A fixed row height had to be tuned to one viewport -- generous enough
     to fill a tall screen, it overflowed a short one. */
  .pane.lineup .ln {{ flex: 1 1 auto; }}
  .colhead {{ display: none; }}
  /* The slot label is a label: same grey on every row, so the eye reads the
     tint for position and this only for which seat of the lineup it is. */
  .ms {{ font-style: normal; font-size: calc(7.5px * var(--fs)); text-transform: uppercase;
    font-weight: 700; text-align: left; color: #555; }}
  /* A hole reads as absence, not as a position: flat grey, no tint. */
  .ln.open {{ background: #f4f4f4; }}
  .ln.open .ms {{ color: #aaa; }}
  .ln.open .mn, .ln.open .mnf {{ color: #bbb; }}
  /* Depth keeps its position tint -- in a pane of its own nothing else says what
     these bodies are -- but sits back a step from the lineup. */
  .ln.bench {{ opacity: 0.75; }}
  .mn, .mnf {{ font-weight: 400; color: #1a1a1a; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }}
  /* Compact shows the short name, maximized the full one. Both are in the
     markup so the swap is free. */
  .mnf {{ display: none; }}
  .mb, .mp, .mc {{ font-style: normal; font-size: calc(9px * var(--fs)); text-align: right;
    font-variant-numeric: tabular-nums; }}
  .mc {{ color: #1a7f37; font-weight: 700; }}
  .ln.open .mc {{ color: #ccc; font-weight: 400; }}
  /* Bye and points are the first things to go when space is short; they live
     in the hover tooltip until the overlay has room for them. */
  .mb, .mp {{ display: none; color: #666; }}

  /* The NEED pane. Targets are fractional and the indicator is a run of pips,
     not a fill bar: a ratio bar pins at 100% the moment you reach the target,
     so a seat one body short and a seat two deep drew the same picture. This is
     the one place the position colour stays at full strength -- the pips are
     the mark being read, not a field behind text. */
  .nrow {{ display: grid; align-items: center; gap: 5px; padding: 1px 3px;
    margin-bottom: 1px; border-radius: 3px;
    grid-template-columns:
      calc(24px * var(--fs)) calc(30px * var(--fs)) minmax(0, 1fr) calc(26px * var(--fs));
    line-height: 1.7;
    background: color-mix(in srgb, var(--pos, #7c90a0) 14%, #fff); }}
  /* have/target, with the target half sunk: what you own is the number being
     read, the target is context. Short positions take the accent -- that is the
     one thing this pane exists to surface. */
  .nct {{ font-size: calc(9.5px * var(--fs)); font-weight: 700; text-align: right;
    font-variant-numeric: tabular-nums; color: #666; }}
  .nct i {{ font-style: normal; font-weight: 400; color: #aaa; }}
  .nrow.short .nct {{ color: #c0392b; }}
  /* One pip per whole starter the target asks for, the half-slot drawn half as
     wide, so the row's own length is the requirement -- 2.5 is visible rather
     than merely readable. Surplus sits outside the run, outlined. */
  .pips {{ display: flex; align-items: center; gap: 2px; }}
  .pip {{ height: calc(6px * var(--fs)); width: calc(8px * var(--fs)); border-radius: 2px;
    background: color-mix(in srgb, var(--pos, #7c90a0) 22%, #fff);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--pos, #7c90a0) 35%, #fff); }}
  .pip.half {{ width: calc(4px * var(--fs)); }}
  .pip.on {{ background: var(--pos, #7c90a0); box-shadow: none; }}
  .pip.extra {{ background: none; width: calc(5px * var(--fs));
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--pos, #7c90a0) 45%, #fff); }}
  .npt {{ font-style: normal; font-size: calc(8.5px * var(--fs)); text-align: right;
    font-variant-numeric: tabular-nums; color: #666; }}
  /* Pace, pinned to the pane's floor. Budget alone does not say whether a seat
     is spending ahead or behind; dollars per slot still to fill does, and it is
     the number that says when to start dumping. */
  .pace {{ margin-top: auto; padding-top: 3px; border-top: 1px solid #eee;
    display: flex; justify-content: space-between; gap: 6px;
    font-size: calc(8px * var(--fs)); color: #888;
    font-variant-numeric: tabular-nums; }}
  .pace b {{ color: #1a1a1a; font-weight: 700; }}

  /* -- Run pressure -------------------------------------------------------
     One card per position, in the left column under the block. The card is
     supply (what's left in the tier) over demand (which seats still want one),
     and the twelve tiles are the same twelve seats as the board across the
     aisle, in the same arrangement -- drag a roster card and these follow.
     TE is deliberately narrow: one starter, no flex claim, so its pressure is
     a pip and a number, and the width goes where a run costs you a lineup. */
  #pressure {{ min-height: 0; overflow: hidden; }}
  .pgrid {{ display: grid; gap: 5px; grid-template-columns: 1fr 1fr 1fr 0.55fr;
    align-items: start; }}
  .pcard {{ border: 1px solid #ddd; border-radius: 6px; padding: 4px 5px 5px;
    min-width: 0; display: flex; flex-direction: column; gap: 3px;
    cursor: pointer; }}
  .pcard:hover {{ border-color: #bbb; }}
  .phd {{ display: flex; align-items: center; gap: 4px; min-width: 0; }}
  .phd .badge {{ min-width: calc(20px * var(--fs)); font-size: calc(7.5px * var(--fs));
    padding: 1px 3px; }}
  .pcount {{ font-size: calc(8px * var(--fs)); color: #888;
    font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .pcount b {{ color: #1a1a1a; }}
  /* Severity is a status, so it is never the colour alone: the pill always
     says how many are left, and the ramp only sharpens what the number says. */
  .pverd {{ margin-left: auto; font-size: calc(7.5px * var(--fs)); font-weight: 700;
    padding: 0 4px; border-radius: 3px; white-space: nowrap;
    background: var(--sevbg); color: var(--sev); }}
  .sev-run {{ --sev: #c0392b; --sevbg: #fdecec; }}
  .sev-tight {{ --sev: #b7791f; --sevbg: #fdf6e6; }}
  .sev-safe {{ --sev: #1a7f37; --sevbg: #eefaf0; }}
  .plbl {{ font-style: normal; font-size: calc(7px * var(--fs)); color: #aaa;
    text-transform: uppercase; letter-spacing: 0.03em;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

  /* Four across, three down -- the roster grid's shape, so a seat sits in the
     same place in both halves of the screen and the eye can carry across. */
  .tiles {{ display: grid; gap: 2px; grid-template-columns: repeat(4, minmax(0, 1fr)); }}
  .tile {{ border: 1px solid #eee; border-radius: 3px; padding: 1px 3px 2px;
    min-width: 0; display: flex; flex-direction: column; gap: 2px; }}
  .ttop {{ display: flex; align-items: baseline; gap: 2px; min-width: 0; }}
  .ttop b {{ font-size: calc(7px * var(--fs)); font-weight: 700; color: #444; }}
  .ttop i {{ font-style: normal; margin-left: auto; font-size: calc(6.5px * var(--fs));
    color: #1a7f37; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .tile .pips {{ gap: 1px; }}
  .tile .pip {{ height: calc(4px * var(--fs)); width: 100%; }}
  /* A seat that has met its target has stopped buying. Receding it leaves the
     live demand as the only thing standing in the grid, which is the read. */
  .tile.done {{ opacity: 0.3; }}
  .minor .ttop i {{ display: none; }}  /* no room, and the tooltip has it */

  .bd {{ display: flex; flex-direction: column; }}
  .brow {{ display: flex; justify-content: space-between; gap: 4px;
    font-size: calc(8px * var(--fs)); color: #444; }}
  .brow:not(:last-child) {{ border-bottom: 1px solid #f4f4f4; }}
  .brow b {{ font-weight: 400; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }}
  .brow i {{ font-style: normal; color: #aaa; font-variant-numeric: tabular-nums; }}
  .brow.empty {{ color: #c0392b; }}
  .bmore {{ font-size: calc(7px * var(--fs)); color: #1a7f37; font-weight: 700; }}
  .pfoot {{ font-size: calc(7px * var(--fs)); color: #aaa;
    font-variant-numeric: tabular-nums; }}

  /* Maximized: the board leaves its half and takes the viewport. Nothing is
     re-rendered -- the hidden columns and the other panes were always there. */
  body.maxed .board {{ position: fixed; inset: 0; z-index: 10;
    background: #fff; padding: 14px 16px; }}
  /* A second way out, at the corner where a full-screen thing is closed. The
     Minimize button rides along in the menu bar since that bar lives inside the
     board, but nobody looks there first. */
  .closebtn {{ display: none; }}
  body.maxed .closebtn {{ display: block; position: fixed; z-index: 11;
    top: 8px; right: 12px; width: 26px; height: 26px; padding: 0;
    font-size: 18px; line-height: 1; border-radius: 50%; color: #666; }}
  body.maxed .closebtn:hover {{ color: #1a1a1a; border-color: #1a1a1a; }}
  /* Same four across, three down as the compact board -- the overlay is the
     whole viewport, so twelve seats fit at full size with the names spelled
     out. Keeping the shape also means maximizing moves nothing: a card is where
     it was, only bigger. */
  body.maxed .grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px;
    height: 100%; grid-auto-rows: minmax(0, 1fr); }}
  /* A deeper lineup than this fits scrolls inside its own pane (see `.pane`)
     rather than off the bottom of the overlay: silently hiding a player is the
     one failure this must not have. */
  body.maxed #rosters {{ overflow: hidden; }}
  /* Keep the close button's corner clear of the hint text. */
  body.maxed .menubar {{ padding-right: 30px; }}
  body.maxed .card {{ padding: 6px 8px; }}
  body.maxed .team {{ font-size: calc(12px * var(--fs)); }}
  body.maxed .big {{ font-size: calc(15px * var(--fs)); }}
  body.maxed .max {{ font-size: calc(10px * var(--fs)); }}
  body.maxed .seg button {{ font-size: calc(8.5px * var(--fs)); }}
  /* Barely larger type than compact, because at four across on a laptop there
     is no room for more. What maximizing actually buys is the full names and
     the bye/points columns below, not size -- and all twelve seats still have
     to fit one screen, which a taller row would break. */
  body.maxed .ln {{ font-size: calc(10.5px * var(--fs)); padding: 0 5px; gap: 6px;
    line-height: 1.2;
    grid-template-columns: auto minmax(0, 1fr) calc(24px * var(--fs)) calc(30px * var(--fs)) calc(34px * var(--fs)); }}
  body.maxed .ms {{ font-size: calc(9px * var(--fs)); }}
  body.maxed .mn {{ display: none; }}
  body.maxed .mnf {{ display: block; }}
  body.maxed .mb, body.maxed .mp {{ display: block; }}
  body.maxed .mb, body.maxed .mp, body.maxed .mc {{ font-size: calc(10px * var(--fs)); }}
  body.maxed .colhead {{ display: grid; background: none; }}
  /* Column labels belong to the lineup rows; in another pane they would head an
     empty card. More specific than the rule above, so it wins in both. */
  .card.view-bench .colhead, .card.view-need .colhead {{ display: none; }}
  body.maxed .colhead i {{ color: #aaa; font-size: calc(8px * var(--fs)); font-weight: 700;
    text-transform: uppercase; }}
  body.maxed .nrow {{ line-height: 2.1; gap: 8px; padding: 1px 5px;
    grid-template-columns:
      calc(30px * var(--fs)) calc(36px * var(--fs)) minmax(0, 1fr) calc(32px * var(--fs)); }}
  body.maxed .nct {{ font-size: calc(12px * var(--fs)); }}
  body.maxed .npt {{ font-size: calc(11px * var(--fs)); }}
  body.maxed .pip {{ height: calc(8px * var(--fs)); width: calc(11px * var(--fs)); }}
  body.maxed .pip.half {{ width: calc(5.5px * var(--fs)); }}
  body.maxed .pip.extra {{ width: calc(7px * var(--fs)); }}
  body.maxed .pace {{ font-size: calc(10px * var(--fs)); padding-top: 4px; }}
</style></head>
<body>
  <aside class="side">
    <h1>Live draft board</h1>
    <p class="sub" id="sub">connecting to draft {_esc(draft_id)}…</p>

    <div class="bar">
      <span><span class="dot" id="dot"></span> <span id="pulse">polling</span></span>
      <label>Seat <select id="seat"><option value="">—</option></select></label>
      <span id="warn" class="warn"></span>
    </div>

    <div id="block"></div>
    <div id="pressure"></div>
  </aside>

  <main class="board">
    <button id="close" class="closebtn" type="button"
            aria-label="Minimize">&times;</button>
    <div class="menubar">
      <button id="max" type="button">Maximize</button>
      <button id="reorder" type="button"
              title="Put the cards back in seat order">Seat order</button>
      <span class="hint">drag a card to reorder · LINEUP / BN / NEED per card</span>
    </div>
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
document.getElementById("close").addEventListener("click", () => setMaxed(false));
document.addEventListener("keydown", (e) => {{
  if (e.key === "Escape") setMaxed(false);
}});

// Per-card pane. "lineup" first: it is what the board loads on, and it is the
// one a card with no stored choice must show.
const VIEWS = ["lineup", "bench", "need"];
let views = {{}};
try {{ views = JSON.parse(localStorage.getItem("draftsim.views")) || {{}}; }} catch (e) {{}}

// #rosters is replaced wholesale every tick, so the chosen pane lives here and
// is re-applied after each swap -- otherwise every card would snap back to the
// lineup twice a second. The segment's pressed state is set here too, for the
// same reason: the buttons are new markup on every refresh.
function applyViews() {{
  document.querySelectorAll("section.card").forEach((card) => {{
    const view = VIEWS.includes(views[card.dataset.seat])
      ? views[card.dataset.seat] : VIEWS[0];
    card.classList.toggle("view-bench", view === "bench");
    card.classList.toggle("view-need", view === "need");
    card.querySelectorAll(".seg button").forEach((b) => {{
      b.setAttribute("aria-pressed", String(b.dataset.pane === view));
    }});
  }});
}}

// Delegated: the segment buttons are destroyed and rebuilt on every refresh, so
// nothing may hold a reference to them.
document.getElementById("rosters").addEventListener("click", (e) => {{
  const btn = e.target.closest(".seg button");
  if (!btn) return;
  const slot = btn.closest("section.card").dataset.seat;
  views[slot] = btn.dataset.pane;
  localStorage.setItem("draftsim.views", JSON.stringify(views));
  applyViews();
}});

// Seat order, dragged by hand and remembered. Presentational: the server always
// sends seat order and this reorders the grid with CSS `order`, so nothing has
// to be re-fetched and seat order is never lost -- clearing the list restores it.
//
// One array, and every grid keyed by seat obeys it: the roster cards and the
// twelve tiles inside each run-pressure card all carry `data-seat`, so the seat
// you dragged to the top-left of the board is top-left everywhere. That is why
// this selects on the attribute rather than on `section.card` -- two grids
// syncing to each other would disagree for a frame after every drag; two grids
// reading the same array in the same pass cannot.
let order = [];
try {{ order = JSON.parse(localStorage.getItem("draftsim.order")) || []; }} catch (e) {{}}

// Seats as the roster cards report them. The cards are the authority on which
// seats exist -- a tile grid is a view of the same twelve and must not be able
// to introduce a thirteenth.
function slots() {{
  return [...document.querySelectorAll("section.card")].map((c) => c.dataset.seat);
}}

// Reconcile the stored list against the seats actually on the board: drop seats
// that are gone, append ones it has never seen. A saved order from a 10-team
// league must not hide seats 11 and 12 of a 12-team one.
function normalizeOrder() {{
  const present = slots();
  order = order.filter((slot) => present.includes(slot));
  present.forEach((slot) => {{ if (!order.includes(slot)) order.push(slot); }});
}}

function applyOrder() {{
  normalizeOrder();
  document.querySelectorAll("[data-seat]").forEach((el) => {{
    el.style.order = order.indexOf(el.dataset.seat);
  }});
}}

// True while a card is in hand. The board refetches every 2s, and replacing
// #rosters mid-drag would delete the element being dragged and drop nothing --
// so the swap waits, and the next tick catches up.
let dragging = null;

const rosters = document.getElementById("rosters");

rosters.addEventListener("dragstart", (e) => {{
  const card = e.target.closest("section.card");
  if (!card) return;
  dragging = card.dataset.seat;
  card.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox ignores a drag that carries no data.
  e.dataTransfer.setData("text/plain", dragging);
}});

rosters.addEventListener("dragover", (e) => {{
  const card = e.target.closest("section.card");
  if (dragging === null || !card) return;
  e.preventDefault();  // without this the drop event never fires
  e.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".dropzone").forEach((c) => c.classList.remove("dropzone"));
  if (card.dataset.seat !== dragging) card.classList.add("dropzone");
}});

rosters.addEventListener("drop", (e) => {{
  const card = e.target.closest("section.card");
  if (dragging === null || !card) return;
  e.preventDefault();
  const target = card.dataset.seat;
  if (target !== dragging) {{
    // Pull the card out, then put it back at the target's position: everything
    // from there down shifts one place, which is what dropping "onto" a slot
    // means. Removing first is what keeps the target index right when the card
    // came from above it.
    normalizeOrder();
    order.splice(order.indexOf(dragging), 1);
    order.splice(order.indexOf(target), 0, dragging);
    localStorage.setItem("draftsim.order", JSON.stringify(order));
    applyOrder();
  }}
  endDrag();
}});

function endDrag() {{
  dragging = null;
  document.querySelectorAll(".dragging, .dropzone").forEach((c) => {{
    c.classList.remove("dragging", "dropzone");
  }});
}}
rosters.addEventListener("dragend", endDrag);

// The way out of an arrangement you regret. Dragging persists, so without this a
// board shuffled at 2am stays shuffled with no obvious way back.
document.getElementById("reorder").addEventListener("click", () => {{
  order = [];
  localStorage.removeItem("draftsim.order");
  applyOrder();
}});

function highlight() {{
  const mine = seatSel.value;
  document.querySelectorAll("section.card").forEach((card) => {{
    card.classList.toggle("me", mine !== "" && card.dataset.seat === mine);
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
    // Everything else refreshes; the cards hold still until the drag lands, so
    // the element in hand isn't deleted out from under it. The pressure tiles
    // freeze with them rather than on their own: they are the same seats in the
    // same order, and half of that pair moving mid-drag is worse than neither.
    if (dragging === null) {{
      rosters.innerHTML = s.rosters_html;
      document.getElementById("pressure").innerHTML = s.pressure_html;
      highlight();
      applyViews();
      applyOrder();
    }}
    document.getElementById("pulse").textContent = s.polled_at;
    document.getElementById("warn").textContent = s.warning || "";
    dot.classList.remove("stale");
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
