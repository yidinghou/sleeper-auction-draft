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
    SeatPick,
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
    total = round(DRAFT_TARGETS.get(pr.pos, 0.0) * state.config.teams)

    tiles = "".join(
        _seat_tile(state.seats[slot], pr.lines[slot], color)
        for slot in sorted(state.seats)
    )

    # Every position gets the board list, TE included. It was cut from TE to buy
    # the other three some width, back when a narrow card was the only way to
    # find any -- and "who is left at tight end" is a question a one-starter
    # position asks more sharply, not less.
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
    runs = (
        f'<div class="ppane runs">'
        f'<i class="plbl">{len(pr.need_seats)} teams still need {_esc(pr.pos)}</i>'
        f'<div class="tiles">{tiles}</div>'
        f"{board}"
        f'<div class="pfoot">{foot}</div></div>'
    )
    # Both panes ship and CSS shows one, so switching costs no fetch and the two
    # can never describe different moments of the draft -- the same bargain the
    # roster cards make with LINEUP / BN / NEED.
    return (
        f'<section class="pcard sev-{pr.severity}"'
        f' style="--pos:{color}" data-pos="{pr.pos}"'
        ' title="double-click to fold">'
        f'<div class="phd">{badge(pr.pos, light=True)}'
        f'<span class="pcount"><b>{pr.drafted}</b>/{total}</span>'
        f'<span class="pverd">{pr.left} left</span>'
        '<span class="seg pseg">'
        '<button data-pane="runs" type="button">RUNS</button>'
        '<button data-pane="tier" type="button">TIER</button></span>'
        "</div>"
        f"{runs}{_pressure_detail(pr)}</section>"
    )


# How much of each band the detail panel lists. The panel is the answer to "which
# ones?", so it is generous -- but a position with thirty bodies under the cliff
# would turn it into a scrolling player list, which is not what it is for.
_TIER_SHOWN = 10


def _tier_row(player: Player, rank: int, below: bool = False) -> str:
    """One available player: rank, name, team, points, $PROJ.

    `$PROJ` is `market_value`, the same figure the nomination strip quotes, so
    what you read here is what you see when they hit the block.
    """
    proj = market_value(player)
    return (
        f'<div class="trow{" below" if below else ""}">'
        f'<i class="tr">{rank}</i>'
        f'<b>{_esc(_short_name(player))}</b>'
        f'<i class="ttm">{_esc(player.team or "FA")}</i>'
        f'<i class="tpt">{player.points:.0f}</i>'
        f'<i class="tpr">{f"${proj:.0f}" if proj else "—"}</i></div>'
    )


def _pressure_detail(pr: PositionPressure) -> str:
    """One position's tier and the tier under it, with the cliff between.

    The question the card provokes and cannot answer: *which quarterbacks, and
    what do I drop to if I wait?* Both bands are listed rather than counted, and
    the divider is what makes waiting a decision rather than a shrug.
    """
    rows = "".join(
        _tier_row(p, i + 1) for i, p in enumerate(pr.avail[:_TIER_SHOWN])
    ) or '<div class="trow gone">nobody left in this tier</div>'

    if pr.next_tier:
        divider = (
            f'<div class="tcliff">▾ −{pr.cliff_drop} pts to the next tier</div>'
        )
        below = "".join(
            _tier_row(p, len(pr.avail) + i + 1, below=True)
            for i, p in enumerate(pr.next_tier[:_TIER_SHOWN])
        )
    else:
        # No band underneath: the drop is off the end of the position, which is
        # worth saying plainly rather than printing "−0".
        divider = '<div class="tcliff none">nothing below this tier</div>'
        below = ""

    short = f"{len(pr.need_seats)} teams short" if pr.need_seats else "nobody short"
    # No close button: the card's own RUNS/TIER toggle is what closes this, and a
    # second control doing the same job is a second thing to explain.
    return (
        f'<div class="ppane pdet" data-det="{pr.pos}">'
        f'<i class="plbl">{_num(round(pr.wanted, 1))} wanted · {short}</i>'
        f'<div class="pdbody">{rows}{divider}{below}</div></div>'
    )


def render_pressure(state: LeagueState) -> str:
    """Run pressure, one card per position, in `TIERS` order.

    Fixed order and fixed positions, for the same reason the roster cards are in
    seat order: a card that reshuffles itself the moment a run starts is a card
    you have to hunt for at exactly the moment you needed it.

    All four are always on screen, whatever state each is in: a card can be
    showing its pressure view or its tier, and can be folded, without any of that
    costing you sight of the other three. Comparing positions is the whole point
    of putting them side by side, so nothing here is allowed to cover them.
    """
    cards = "".join(_pressure_card(state, pr) for pr in pressure(state))
    return f'<div class="pgrid">{cards}</div>'


# Deep enough to scroll to anyone worth a dollar. The sheet runs to thousands of
# bodies, nearly all of them undraftable, so this is a limit on payload rather
# than a judgment: past three hundred names ordered by price, what is left is
# kickers and third-string tight ends.
_POOL_SHOWN = 300


def render_pool(state: LeagueState) -> str:
    """Who is left, dearest first.

    Ordered by `$PROJ` -- the same figure the nomination strip and the tier list
    quote, so the three panes cannot disagree about what a player should cost.
    Players with no projection sort last rather than being dropped: an unpriced
    body is still a body somebody can nominate.

    Names run in full here, unlike everywhere else on the board. The initial is
    a concession to a 220px card, and this pane is not one -- it has 250px for a
    name, which is more than "Christian McCaffrey" needs. It is also the pane
    where the distinction earns its keep: the pool is where two men who share a
    short name sit in the same list, and "B. Robinson" cannot tell you which.
    """
    ranked = sorted(
        state.available, key=lambda p: (-market_value(p), -p.points, p.name)
    )
    rows = "".join(
        f'<div class="prow">{badge(player.pos, light=True)}'
        f'<b>{_esc(player.name)}</b>'
        f'<i class="ptm">{_esc(player.team or "FA")}</i>'
        f'<i class="ppt">{player.points:.0f}</i>'
        f'<i class="ppr">{f"${market_value(player):.0f}" if market_value(player) else "—"}</i>'
        "</div>"
        for player in ranked[:_POOL_SHOWN]
    )
    if not rows:
        rows = '<div class="pnone">the board is empty</div>'
    return f'<div class="poollist">{rows}</div>'


def _log_price(pick: SeatPick) -> str:
    """What it went for, against what it was projected to go for.

    The gap is the number worth watching -- it says whether the room is paying up
    or the board is falling. Most of the sheet carries no `$PROJ`, and those show
    the price alone rather than a delta against a number that isn't there.
    """
    proj = pick.player.proj_dollar
    if proj is None or proj <= 0:
        return (
            f'<i class="lpr">${pick.price}</i>'
            '<i class="ldl muted">—</i>'
        )
    diff = (pick.price - proj) / proj
    direction = "over" if diff > 0 else "under" if diff < 0 else "even"
    return (
        f'<i class="lpr">${pick.price}</i>'
        f'<i class="ldl {direction}">{diff:+.0%}</i>'
    )


def render_log(state: LeagueState) -> str:
    """Every sale, newest first.

    Newest first because the rows read *during* a draft are the last few -- what
    just went, for how much, and whether that was over the odds. The position
    rides in the same pill the pool uses, because "what has the room paid for
    running backs" is a question about a colour, not about fifteen surnames you
    have to recognise one at a time. Names run in full, as they do in the pool
    beside it and for the same reason -- the width is there. Uncapped,
    because the rows read *between* nominations are the ones from an hour ago,
    when you are trying to remember what a receiver went for; a draft is at most
    a couple of hundred picks, so the whole thing is cheap to carry.
    """
    rows = "".join(
        f'<div class="lrow">'
        f'<i class="lno">#{pick.pick_no}</i>'
        f'<i class="lst">S{pick.slot}</i>'
        f"{badge(pick.player.pos, light=True)}"
        f'<b>{_esc(pick.player.name)}</b>'
        f"{_log_price(pick)}</div>"
        for pick in sorted(state.picks, key=lambda p: -p.pick_no)
    )
    if not rows:
        rows = '<div class="pnone">no picks yet</div>'
    return f'<div class="loglist">{rows}</div>'


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
     16-man roster still clears the fold. The left column overrides it on
     `.side`, so this value is the roster board's alone. */
  :root {{ --fs: 1.17; }}
  html, body {{ height: 100%; }}
  /* 60 / 40, not half and half: the left column now carries four position cards
     that each hold a tier list, and that needs the room more than a twelfth
     roster column does. The rosters stay four across -- names ellipsize at this
     width, which is the price paid for the tier being readable. */
  body {{ margin: 0; padding: 0; overflow: hidden;
    display: grid; grid-template-columns: 3fr 2fr; }}

  /* Left: what is happening right now -- the draft's state and whatever is on
     the block. Right: the rosters. Splitting them this way gives the grid the
     whole viewport height instead of sharing it with the chrome, which is what
     lets a full 16-man roster fit. Space under the header is left free. */
  /* Three bands down the left: what is happening now (1/5), run pressure (2/5),
     then the pool and the log sharing the last 2/5. Flex rather than fixed grid
     rows, because a collapsed band then hands its height to its siblings with
     no arithmetic -- `flex: 0 0 auto` and the others simply grow. */
  /* The left column runs a size of its own. Its rows carry more per line than
     the roster cards do -- five columns inside a ~230px card -- and were squeezed
     down to 7px labels to fit. The knob is scoped here so raising it cannot move
     a roster card by a pixel. */
  .side {{ --fs-base: 1.30; --fs: var(--fs-base);
    display: flex; flex-direction: column; min-width: 0;
    padding: 8px 10px; gap: 6px; border-right: 1px solid #eee; overflow: hidden; }}
  /* Each band is a box, not a run of text with a heading over it. Folding is a
     double-click with no button to leave behind, so without a frame a shut band
     would vanish into the gap between its neighbours -- the border and the
     tinted header strip are what stay on screen to say it is still there. */
  .band {{ display: flex; flex-direction: column; min-height: 0; min-width: 0;
    position: relative; border: 1px solid #e0e0e0; border-radius: 8px;
    background: #fff; overflow: hidden; }}
  /* Sized to its content, not to a share. Band 1 holds three fixed things -- the
     league line, the seat picker, the block -- so a share only parked white space
     under them, and worse: folding a band below handed this one a third of the
     freed height, which pushed the header you had just double-clicked a hundred
     pixels down the screen. Content-sized, it never moves and never takes. */
  .band-live {{ flex: 0 0 auto; }}
  .band-runs {{ flex: 2 1 0; }}
  /* A wrapper, not a section: the pool and the log carry their own headers and
     their own frames, and a box around the pair would only double every line. */
  .band-panes {{ flex: 2 1 0; border: none; background: none; overflow: visible; }}
  .band > .bandhd {{ display: flex; align-items: baseline; gap: 6px; flex: none;
    padding: 3px 7px; background: #f7f7f7; border-bottom: 1px solid #e8e8e8; }}
  .bandhd h2 {{ font-size: calc(10px * var(--fs)); margin: 0; color: #555;
    text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700; }}
  .bandhd .note {{ font-size: calc(9px * var(--fs)); color: #bbb;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .band > .bandbody {{ flex: 1; min-height: 0; display: flex;
    flex-direction: column; gap: 5px; padding: 5px 6px; }}
  /* Shut, a band is its title bar and nothing else -- so the strip stops being a
     lid and becomes the whole box, and the fill says at a glance which of the
     four you have put away. */
  .band.collapsed {{ background: #fafafa; }}
  .band.collapsed > .bandhd {{ border-bottom: none; }}

  /* The header *is* the control -- double-click it to fold. No caret, because a
     16px button is a small target to hunt for mid-auction and there are six of
     them. The cost is that a gesture leaves nothing on screen saying it exists,
     so the header lights up under the cursor and says what it does: the hover
     state is the entire affordance, and without it this would be a secret. */
  .band > .bandhd {{ cursor: pointer; user-select: none; border-radius: 3px; }}
  .band > .bandhd:hover {{ background: #ededed; }}
  .band > .bandhd:hover h2 {{ color: #1a1a1a; }}
  .band > .bandhd::after {{ content: "\\25BE"; margin-left: auto; flex: none;
    font-size: calc(8px * var(--fs)); color: #bbb; line-height: 1.6; }}
  .band > .bandhd:hover::after {{ color: #888; }}
  .collapsed > .bandbody {{ display: none; }}
  .band.collapsed {{ flex: 0 0 auto; }}
  .collapsed > .bandhd::after {{ transform: rotate(-90deg); }}

  /* Band 1 is the one place on the left that was never too small, so its
     multipliers come down as the column's knob goes up: the room the bigger type
     needs is taken from the band that did not need the size, not from the pool. */
  .side h1 {{ font-size: calc(12px * var(--fs)); margin: 0; }}
  .side .sub {{ margin: 0; font-size: calc(10px * var(--fs)); }}

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
    font-size: calc(10px * var(--fs)); color: #666; }}
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
  .block.idle {{ color: #888; font-size: calc(11px * var(--fs)); }}
  .who {{ font-size: calc(13px * var(--fs)); font-weight: 700; margin-right: 4px; }}
  .bidamt {{ color: #1a7f37; font-weight: 700; font-size: calc(13px * var(--fs)); }}
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
     All four cards are the same size, TE included: one starter is still a
     position you can be run out of, and the room is there. */
  #pressure {{ min-height: 0; flex: 1; display: flex; overflow: hidden; }}
  /* Cards size to their content and sit at the top of the band. Stretching them
     to the band's full height just puts a hand of white space under the board
     list and makes four short cards look like four half-empty ones. */
  .pgrid {{ display: flex; gap: 5px; align-items: flex-start; flex: 1;
    min-width: 0; }}
  /* zoom-in, not pointer: a single click does nothing here, and a cursor that
     promises one is a cursor that lies. */
  /* Capped at the band's height, not stretched to it: a card sizes to whatever
     it holds, but a tier list of twenty names must scroll inside the card
     rather than growing it down over the pool and the log below. */
  /* A fixed quarter (less the three 5px gaps), never a share of what is going.
     Cards used to be `flex: 1 1 0` and divide the row between them, so an open
     card was three times its usual width if its neighbours were folded and the
     same card was a different size every time you came back to it. Pinned, an
     open card is the width you last read it at whatever the other three do --
     and what folding buys is quiet, not room, which is what it was for. */
  .pcard {{ border: 1px solid #ddd; border-radius: 6px; padding: 4px 5px 5px;
    min-width: 0; display: flex; flex-direction: column; gap: 3px;
    cursor: zoom-in; flex: 0 0 calc((100% - 15px) / 4); max-height: 100%; }}
  /* Folded, a card is its header and nothing else: the badge and how many are
     left, which is the one line you would still want from a position you have
     stopped working on. It shrinks to what those two need -- a rail, so the eye
     reads "put away" from the shape without having to read the card. */
  .pcard.collapsed {{ flex: 0 0 auto; cursor: zoom-in; }}
  .pcard.collapsed > *:not(.phd) {{ display: none; }}
  /* A folded card keeps the badge and how many are left. The pane toggle goes
     with the panes -- it is a control for something you have put away. */
  .pcard.collapsed .pcount, .pcard.collapsed .pseg {{ display: none; }}
  /* The pill is pushed right by `margin-left: auto` in a full-width header. On a
     rail there is no right to push to, so it hugs the badge instead. */
  .pcard.collapsed .pverd {{ margin-left: 4px; }}
  .pcard:hover {{ border-color: #bbb; }}
  .phd {{ display: flex; align-items: center; gap: 4px; min-width: 0; }}
  .phd .badge {{ min-width: calc(20px * var(--fs)); font-size: calc(8px * var(--fs));
    padding: 1px 3px; }}
  /* The first thing in the header to give way when the card is narrow: the
     verdict pill and the pane buttons are both worth more than "16/36". */
  .pcount {{ font-size: calc(9px * var(--fs)); color: #888;
    font-variant-numeric: tabular-nums; white-space: nowrap;
    min-width: 0; overflow: hidden; text-overflow: ellipsis; }}
  .pcount b {{ color: #1a1a1a; }}
  /* Severity is a status, so it is never the colour alone: the pill always
     says how many are left, and the ramp only sharpens what the number says. */
  .pverd {{ margin-left: auto; font-size: calc(8px * var(--fs)); font-weight: 700;
    padding: 0 4px; border-radius: 3px; white-space: nowrap;
    background: var(--sevbg); color: var(--sev); }}
  .sev-run {{ --sev: #c0392b; --sevbg: #fdecec; }}
  .sev-tight {{ --sev: #b7791f; --sevbg: #fdf6e6; }}
  .sev-safe {{ --sev: #1a7f37; --sevbg: #eefaf0; }}
  .plbl {{ font-style: normal; font-size: calc(8px * var(--fs)); color: #aaa;
    text-transform: uppercase; letter-spacing: 0.03em;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

  /* Four across, three down -- the roster grid's shape, so a seat sits in the
     same place in both halves of the screen and the eye can carry across. */
  .tiles {{ display: grid; gap: 2px; grid-template-columns: repeat(4, minmax(0, 1fr)); }}
  .tile {{ border: 1px solid #eee; border-radius: 3px; padding: 1px 3px 2px;
    min-width: 0; display: flex; flex-direction: column; gap: 2px; }}
  .ttop {{ display: flex; align-items: baseline; gap: 2px; min-width: 0; }}
  .ttop b {{ font-size: calc(8px * var(--fs)); font-weight: 700; color: #444; }}
  .ttop i {{ font-style: normal; margin-left: auto; font-size: calc(8px * var(--fs));
    color: #1a7f37; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .tile .pips {{ gap: 1px; }}
  .tile .pip {{ height: calc(4px * var(--fs)); width: 100%; }}
  /* A seat that has met its target has stopped buying. Receding it leaves the
     live demand as the only thing standing in the grid, which is the read. */
  .tile.done {{ opacity: 0.3; }}

  .bd {{ display: flex; flex-direction: column; }}
  .brow {{ display: flex; justify-content: space-between; gap: 4px;
    font-size: calc(9px * var(--fs)); line-height: 1.2; color: #444; }}
  .brow:not(:last-child) {{ border-bottom: 1px solid #f4f4f4; }}
  .brow b {{ font-weight: 400; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }}
  .brow i {{ font-style: normal; color: #aaa; font-variant-numeric: tabular-nums; }}
  .brow.empty {{ color: #c0392b; }}
  .bmore {{ font-size: calc(8px * var(--fs)); color: #1a7f37; font-weight: 700; }}
  .pfoot {{ font-size: calc(8px * var(--fs)); color: #aaa;
    font-variant-numeric: tabular-nums; }}

  /* Two panes over one position, switched by the card's own RUNS/TIER buttons.
     Both ship in every card and CSS shows one -- so switching costs no fetch,
     the two can never describe different moments of the draft, and above all
     the other three positions stay on screen. The tier used to be an overlay
     that covered them, which is precisely when you want to compare. */
  .ppane {{ display: none; flex-direction: column; gap: 3px; min-height: 0; }}
  .pcard .ppane.runs {{ display: flex; }}
  .pcard.view-tier .ppane.runs {{ display: none; }}
  .pcard.view-tier .ppane.pdet {{ display: flex; flex: 1; }}
  /* Last word on the panes, and it has to win twice over: `.pcard .ppane.runs`
     ties `.pcard.collapsed .ppane` on specificity and is beaten on order, but
     `.pcard.view-tier .ppane.pdet` is a class heavier and beats order -- which
     is why a folded card used to go on rendering its whole tier list squeezed
     into a rail. Hence the long selector: it has to out-weigh that rule, not
     merely follow it. Folded is folded, whichever pane the card was left on --
     and that pane comes back untouched when you unfold, because `pviews` never
     heard about any of this. */
  .pcard.collapsed.view-tier .ppane.pdet,
  .pcard.collapsed .ppane {{ display: none; }}
  .pseg {{ margin-left: 0; }}
  .pseg button {{ padding: 0 3px; font-size: calc(8px * var(--fs)); }}
  .pdbody {{ overflow-y: auto; scrollbar-width: thin;
    scrollbar-color: #ccc transparent; min-height: 0; flex: 1; }}

  /* Rank, name, team, points, $PROJ -- the same five things in the same order
     as the maximized roster row, so the eye does not have to re-learn a table
     between the two halves of the screen.
     Tight gaps and narrow numerals because this now lives inside a ~230px card
     rather than a 340px panel: at the old spacing the fixed columns ate 149px
     and left the name 73px, which is where "M. Stafford" starts losing its. */
  /* Leading tighter than the page's 1.4: the type went up a size and the row
     stayed where it was, so the list reads bigger without showing fewer names. */
  .trow {{ display: grid; align-items: baseline; gap: 4px; padding: 1px 2px;
    font-size: calc(10px * var(--fs)); line-height: 1.2;
    grid-template-columns:
      calc(12px * var(--fs)) minmax(0, 1fr) calc(18px * var(--fs))
      calc(20px * var(--fs)) calc(22px * var(--fs)); }}
  .trow:not(:last-child) {{ border-bottom: 1px solid #f6f6f6; }}
  .trow i {{ font-style: normal; font-variant-numeric: tabular-nums;
    text-align: right; color: #888; font-size: calc(10px * var(--fs)); }}
  .trow .tr {{ text-align: left; color: #ccc; }}
  .trow .ttm {{ text-align: left; color: #aaa; }}
  .trow .tpr {{ color: #1a7f37; font-weight: 700; }}
  /* Below the cliff is a different class of player, not merely a later one. */
  .trow.below {{ opacity: 0.55; }}
  .trow.gone {{ display: block; color: #c0392b; }}
  .tcliff {{ margin: 5px 0; padding-top: 4px; border-top: 2px dotted #e0a89f;
    color: #c0392b; font-size: calc(9px * var(--fs)); font-weight: 700;
    text-align: center; }}
  .tcliff.none {{ border-top-color: #eee; color: #aaa; }}

  /* -- pool and log, sharing the bottom band ------------------------------
     Side by side so the two questions they answer -- who is left, and what the
     room just paid -- can be read against each other without a switch. */
  /* Flex, again for the collapse: fold the log away and the pool takes its
     width, rather than leaving half the band empty. Written as a child rule so
     it outranks the column direction every other band body gets. */
  .band > .bandbody.panes {{ display: flex; flex-direction: row; gap: 6px;
    flex: 1; min-height: 0; padding: 0; }}
  /* Border and padding come from `.band` -- a pane is one of the four foldable
     sections and is framed like the other three, not like a lesser inset. */
  .pane2 {{ display: flex; flex-direction: column; min-height: 0; min-width: 0;
    flex: 1 1 0; }}
  /* A collapsed pane keeps its title and drops everything else, the subtitle
     included -- that note otherwise sets the width and leaves a "collapsed"
     pane still holding a fifth of the band. */
  .pane2.collapsed {{ flex: 0 0 auto; }}
  .collapsed > .bandhd .note {{ display: none; }}
  .pane2 > .bandhd {{ flex: none; }}
  /* A fifth larger again than the rest of the column, and only inside the list
     -- the two headers stay the size the other bands' headers are, or the eye
     would read the pool as the more important section rather than the roomier
     one. These are the two panes you read a line at a time rather than scan as
     a shape, and they have the width to spend on it that a 220px card does not.
     Derived from the column's own knob so the two move together. */
  .pane2 .listwrap {{ --fs: calc(var(--fs-base) * 1.2);
    flex: 1; min-height: 0; overflow-y: auto;
    scrollbar-width: thin; scrollbar-color: #ccc transparent; }}

  .prow, .lrow {{ display: grid; align-items: center; gap: 5px; padding: 1px 2px;
    font-size: calc(10px * var(--fs)); line-height: 1.2; min-width: 0; }}
  .prow {{ grid-template-columns:
    calc(22px * var(--fs)) minmax(0, 1fr) calc(20px * var(--fs))
    calc(22px * var(--fs)) calc(24px * var(--fs)); }}
  .lrow {{ grid-template-columns:
    calc(22px * var(--fs)) calc(18px * var(--fs)) calc(20px * var(--fs))
    minmax(0, 1fr) calc(22px * var(--fs)) calc(26px * var(--fs)); }}
  .prow:not(:last-child), .lrow:not(:last-child) {{ border-bottom: 1px solid #f7f7f7; }}
  .prow b, .lrow b {{ font-weight: 400; min-width: 0; overflow: hidden;
    white-space: nowrap; text-overflow: ellipsis; }}
  .prow i, .lrow i {{ font-style: normal; font-variant-numeric: tabular-nums;
    text-align: right; font-size: calc(9px * var(--fs)); color: #999; }}
  .prow .badge, .lrow .badge {{ font-size: calc(8px * var(--fs)); min-width: 0;
    padding: 1px 0; }}
  .prow .ptm {{ text-align: left; color: #bbb; }}
  .prow .ppr {{ color: #1a7f37; font-weight: 700; }}
  .lrow .lno {{ text-align: left; color: #ccc; }}
  .lrow .lst {{ text-align: left; color: #888; font-weight: 700; }}
  .lrow .lpr {{ color: #1a1a1a; font-weight: 700; }}
  /* Over and under are a status pair, so they carry a sign as well as a hue --
     the percentage is legible without the colour and vice versa. */
  .ldl.over {{ color: #c0392b; }}
  .ldl.under {{ color: #1a7f37; }}
  .ldl.even {{ color: #aaa; }}
  .pnone {{ color: #aaa; font-size: calc(10px * var(--fs)); padding: 4px 2px; }}

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
    <!-- Three bands. The chrome -- the headers you double-click to fold --
         lives here in the shell rather than in the fragments, because the
         fragments are replaced every two seconds and a control destroyed that
         often can hold neither focus nor state. Only the insides are swapped. -->
    <section class="band band-live" data-band="live">
      <div class="bandhd"><h2>Live draft board</h2>
        <span class="note" id="pulsewrap"><span class="dot" id="dot"></span>
          <span id="pulse">polling</span></span></div>
      <div class="bandbody">
        <p class="sub" id="sub">connecting to draft {_esc(draft_id)}…</p>
        <div class="bar">
          <label>Seat <select id="seat"><option value="">—</option></select></label>
          <span id="warn" class="warn"></span>
        </div>
        <div id="block"></div>
      </div>
    </section>

    <section class="band band-runs" data-band="runs">
      <div class="bandhd"><h2>Run pressure</h2>
        <span class="note">RUNS / TIER per card · double-click to fold</span></div>
      <div class="bandbody"><div id="pressure"></div></div>
    </section>

    <section class="band band-panes" data-band="panes">
      <div class="bandbody panes">
        <div class="pane2 band" data-band="pool">
          <div class="bandhd"><h2>Player pool</h2>
            <span class="note">by $PROJ</span></div>
          <div class="bandbody listwrap" id="pool"></div>
        </div>
        <div class="pane2 band" data-band="log">
          <div class="bandhd"><h2>Draft log</h2>
            <span class="note">newest first</span></div>
          <div class="bandbody listwrap" id="log"></div>
        </div>
      </div>
    </section>
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

const pressureEl = document.getElementById("pressure");

// Which pane each position card is showing. The twin of `views` above: that one
// is keyed by seat, this one by position, and both exist because the markup they
// describe is thrown away and rebuilt every two seconds.
const PVIEWS = ["runs", "tier"];
let pviews = {{}};
try {{ pviews = JSON.parse(localStorage.getItem("draftsim.pviews")) || {{}}; }} catch (e) {{}}

function applyPViews() {{
  document.querySelectorAll(".pcard").forEach((card) => {{
    const view = PVIEWS.includes(pviews[card.dataset.pos])
      ? pviews[card.dataset.pos] : PVIEWS[0];
    card.classList.toggle("view-tier", view === "tier");
    card.querySelectorAll(".pseg button").forEach((b) => {{
      b.setAttribute("aria-pressed", String(b.dataset.pane === view));
    }});
  }});
}}

pressureEl.addEventListener("click", (e) => {{
  const btn = e.target.closest(".pseg button");
  if (!btn) return;
  pviews[btn.closest(".pcard").dataset.pos] = btn.dataset.pane;
  localStorage.setItem("draftsim.pviews", JSON.stringify(pviews));
  applyPViews();
}});

// One gesture, one meaning: double-clicking a card folds it. The toggle is a
// button and is exempt -- double-clicking it would otherwise switch the pane and
// then fold the card you were switching.
pressureEl.addEventListener("dblclick", (e) => {{
  if (e.target.closest(".pseg")) return;
  const card = e.target.closest(".pcard");
  if (card) toggleCollapsed("pos:" + card.dataset.pos);
}});

// Nothing on this page covers anything any more, so Escape has one job again.
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

// What is folded away, and remembered. Three consumers -- the bands down the
// left, the pool/log panes, and the four pressure cards -- because they are the
// same gesture: give the room to whatever you are actually reading.
//
// Keyed by a stable name (`data-band`, or the position for a card) rather than
// by index, so a collapsed thing stays collapsed even though the markup holding
// it is thrown away and rebuilt twice a second.
let collapsed = [];
try {{ collapsed = JSON.parse(localStorage.getItem("draftsim.collapsed")) || []; }} catch (e) {{}}

function applyCollapsed() {{
  document.querySelectorAll("[data-band]").forEach((el) => {{
    el.classList.toggle("collapsed", collapsed.includes("band:" + el.dataset.band));
  }});
  document.querySelectorAll(".pcard").forEach((el) => {{
    el.classList.toggle("collapsed", collapsed.includes("pos:" + el.dataset.pos));
  }});
}}

function toggleCollapsed(id) {{
  const at = collapsed.indexOf(id);
  if (at === -1) collapsed.push(id); else collapsed.splice(at, 1);
  localStorage.setItem("draftsim.collapsed", JSON.stringify(collapsed));
  applyCollapsed();
}}

// Double-click a band's header to fold it. Delegated from the document because
// the pane headers and the outer band headers are both `.bandhd`, and one
// listener beats four.
document.addEventListener("dblclick", (e) => {{
  const head = e.target.closest(".bandhd");
  if (!head) return;
  const band = head.closest("[data-band]");
  if (band) toggleCollapsed("band:" + band.dataset.band);
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

// Swap a scroller's rows without losing where you were reading.
//
// The log grows at the *top* -- newest first -- so a new pick would also shove
// the rows under your eye down by one. Anchoring to the height instead of the
// raw offset keeps the row you were looking at where it was, and a scroller
// still at the top stays pinned there so new picks arrive in view.
function swapKeepingScroll(id, html) {{
  const el = document.getElementById(id);
  const wasAtTop = el.scrollTop === 0;
  const before = el.scrollHeight;
  const top = el.scrollTop;
  el.innerHTML = html;
  el.scrollTop = wasAtTop ? 0 : top + (el.scrollHeight - before);
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
      // Replacing the rows empties the scroller for an instant, which drops it
      // back to the top. Unnoticeable over forty rows; over three hundred it
      // means you cannot read past the first screen without the board yanking
      // you back twice a second.
      swapKeepingScroll("pool", s.pool_html);
      swapKeepingScroll("log", s.log_html);
      highlight();
      applyViews();
      applyPViews();
      applyOrder();
      applyCollapsed();
    }}
    document.getElementById("pulse").textContent = s.polled_at;
    document.getElementById("warn").textContent = s.warning || "";
    dot.classList.remove("stale");
  }} catch (err) {{
    dot.classList.add("stale");
    document.getElementById("pulse").textContent = "server unreachable";
  }}
}}
// Before the first fetch, so a folded band never flashes open on load -- and so
// the fold survives a server that is down when the page opens.
applyCollapsed();
applyPViews();
tick();
setInterval(tick, 2000);
</script>
</body></html>
"""
