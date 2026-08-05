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

This module builds fragments. The page they arrive into -- its shell, its
stylesheet and its client -- lives in `static/` and is assembled by
`render_page()`.
"""

from __future__ import annotations

import html
import math
from pathlib import Path
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

# The shell, the stylesheet and the client. Beside the module rather than on a
# search path: they are this board's, and nothing else ever loads them.
_STATIC = Path(__file__).parent / "static"


def _asset(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


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


def _pips(have: int, want: float, color: str, cap: bool = False) -> str:
    """The target drawn at its true length: one pip per whole starter it asks
    for, the fractional remainder drawn as a half-width pip.

    This replaced a percentage-fill bar, which lied at the top end -- a ratio
    pins at 100% the moment you reach the target, so 4/2.5 and 2.5/2.5 drew
    identically when they mean opposite things. Here surplus bodies show as
    narrow outlined pips *past* the run, so depth reads as depth.

    `cap` stops at the target and draws no surplus. Depth is a roster question,
    not a run-pressure one: inside a pressure tile a fourth quarterback only made
    one of twelve tiles wider than its neighbours, and what the run pane wants
    from that seat is the single fact that it has stopped buying -- which the
    dimming says. The roster's NEED rows and fold strips still draw it in full.
    """
    whole = int(want)
    pips = "".join(
        f'<span class="pip{" on" if i < have else ""}"></span>'
        for i in range(whole)
    )
    if want > whole:
        pips += f'<span class="pip half{" on" if have > whole else ""}"></span>'
    if not cap:
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


def _fold_summary(state: LeagueState, seat: Seat) -> str:
    """What a card still says once its row is folded: which positions it has
    filled and which it has not.

    The NEED pane's own pips, at strip size -- built from the same
    `position_summary` call the pane is built from, so a folded card cannot
    disagree with the pane it is hiding. It ships in every card and CSS shows it
    only when folded, the same bargain the three panes make.
    """
    cells = "".join(
        f'<span class="fpos">'
        f'<i class="ms">{_esc(line.pos)}</i>'
        f"{_pips(line.have, line.want, POS_COLOR_LIGHT.get(line.pos, POS_FALLBACK_LIGHT))}"
        "</span>"
        for line in position_summary(seat, state.config)
        if line.pos not in _NEED_SKIP
    )
    return f'<div class="foldsum">{cells}</div>'


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
        "</div>"
        f"{_fold_summary(state, seat)}"
        "</header>"
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


# How many cards a row of the board holds. The grid's own
# `grid-template-columns` says the same thing, and the client says it a third
# time as `GRID_COLS` -- all three have to agree, because the row headers are cut
# from this number and the client folds by row.
_GRID_COLS = 4


def _row_header(row: int) -> str:
    """The bar over one row of cards: a divider, and the fold control.

    The same furniture the bands down the left column carry -- a tinted strip
    with a caret that turns -- because it does the same job. Its note is filled
    in by the client rather than here: it lists the seats in the row, and which
    seats those are is a question only the client can answer once a card has been
    dragged somewhere else.
    """
    return (
        f'<div class="rowhd" data-row="{row}">'
        f"<h2>Row {row + 1}</h2><span class=\"note\"></span></div>"
    )


def render_rosters(state: LeagueState) -> str:
    """Every seat's roster as a card, in seat order, under a header per row.

    Seat order, and never sorted by anything that moves: these are read to look
    something up ("what does seat 4 still need at receiver?"), so a card has to
    stay where you last saw it rather than jumping every time someone bids.

    The client can drag cards into a different order, and that ordering is
    presentational only -- CSS `order` on the grid items, so this markup stays in
    seat order. Which is the point: the one thing allowed to move a card is the
    hand that dragged it, and a rearranged board still knows what seat order was.

    The row headers ship interleaved, one before each run of four, so the board
    is already divided into rows on the first paint -- before any client code has
    said a word about order. They are grid items like the cards, spanning the
    full width, which is what keeps one flat grid: cards have to be draggable
    from any row to any other, and a grid per row could not do that.
    """
    slots = sorted(state.seats)
    parts = []
    for index, slot in enumerate(slots):
        if index % _GRID_COLS == 0:
            parts.append(_row_header(index // _GRID_COLS))
        parts.append(_roster_card(state, state.seats[slot]))
    return f'<div class="grid">{"".join(parts)}</div>'


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
        f"{_pips(line.have, line.want, color, cap=True)}</span>"
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
    # Nobody left short: the position is settled and cannot run any more, so the
    # whole card recedes rather than sitting at full strength competing with the
    # three that can still cost you a lineup. It stays on screen and stays
    # legible -- a settled position is still a thing you look up.
    done = "" if pr.need_seats else " done"
    # Both panes ship and CSS shows one, so switching costs no fetch and the two
    # can never describe different moments of the draft -- the same bargain the
    # roster cards make with LINEUP / BN / NEED.
    return (
        f'<section class="pcard sev-{pr.severity}{done}"'
        f' style="--pos:{color}" data-pos="{pr.pos}">'
        # The header is the fold control, so it is the header that says so --
        # a title on the whole card promised the gesture worked anywhere on it.
        f'<div class="phd" title="click to fold">{badge(pr.pos, light=True)}'
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

    The stylesheet, the client and this shell are real CSS, JS and HTML in
    `static/`, not a string in here. They interpolate nothing -- the stylesheet
    took one value and the client took none -- so all a Python string ever bought
    them was a doubled brace on every rule and every function, and no editor able
    to read either.

    Read per request, not cached at import: `/` is fetched once a session and
    everything after it is `/api/state`, so the cost is nil and a colour can be
    edited and reloaded without restarting the poller mid-draft.
    """
    page = _asset("board.html")
    return (
        page.replace("/*__CSS__*/", BASE_CSS_LIGHT + _asset("board.css"))
        .replace("/*__JS__*/", _asset("board.js"))
        .replace("__DRAFT_ID__", _esc(draft_id))
    )
