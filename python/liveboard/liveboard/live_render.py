"""Render the live draft board.

A header strip naming whatever is on the block, then every seat's roster as a
card -- one player per line, in the slot they would actually start in, with what
they cost. Each card carries two panes over the same data (LINEUP, BN),
picked by the segmented control in its header, and cards can be dragged into
whatever order you want to read them in.

The card header is money and nothing else: what a seat has left, the most it can
still bid, and a bar of the two.

The page is served by `live.py` and refreshes itself by refetching `/api/state`
and swapping in the fragments below — so everything here renders from a
`LeagueState` and is safe to call repeatedly.

This module builds fragments. The page they arrive into -- its shell, its
stylesheet and its client -- lives in `static/` and is assembled by
`render_page()`.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple, Union

from draftsim.rules import BENCH
from .live_pressure import PositionPressure, pressure
from .live_state import (
    DRAFT_TARGETS,
    LeagueState,
    PositionLine,
    Seat,
    SeatPick,
    position_summary,
)
from draftsim.lineup import display_slots, is_legal
from .sleeper import Nomination
from .theme import (
    BASE_CSS_LIGHT,
    POS_COLOR_LIGHT,
    POS_FALLBACK_LIGHT,
    SLOT_LABEL,
    badge,
)
from draftsim.player import Player

def market_value(player: Player) -> float:
    """Sleeper's projected auction dollar; a player with none prices at 0.

    Lived in draftsim until the rebuild, which correctly declined to keep a
    "what the crowd thinks" model in a domain library. It is one field read, and
    the board is the only thing that wanted it.
    """
    dollar = player.market.proj_dollar
    return float(dollar) if dollar is not None else 0.0


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


def _ordinal(n: int) -> str:
    """1 -> 1st. Used once, for where you stand in the room -- "4th of 12" is
    read at a glance where "rank 4" needs a beat to place."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# How much of a manager's name survives where twelve of them sit side by side --
# the ledger's footer tags and the run-pressure tiles. First word only, because a
# team name's first word is the part that is theirs, and clipped because twelve
# of anything longer is a wall.
_TAG_CHARS = 8


def _seat_label(seat: Seat) -> str:
    """What to call a seat: the manager, or the slot number if nobody knows.

    Escaped here rather than at every call site -- a name is the one string on
    this board that came from a text box and from Sleeper, and it is drawn in
    seven places.
    """
    return _esc(seat.name) if seat.name else f"S{seat.slot}"


def _seat_tag(seat: Seat) -> str:
    """The same, for the places twelve seats sit shoulder to shoulder."""
    if not seat.name:
        return f"S{seat.slot}"
    first = seat.name.split()[0] if seat.name.split() else seat.name
    return _esc(first[:_TAG_CHARS])


def _seat_tip(seat: Seat) -> str:
    """Unescaped "Marc · S5", for tooltips.

    The slot rides along in every tooltip even where the label is a name: the
    board is dragged out of seat order and read next to Sleeper's own, and the
    number is what the two have in common. Callers escape.
    """
    return f"{seat.name} · S{seat.slot}" if seat.name else f"S{seat.slot}"


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
    if player.position == "DEF":
        return parts[-1]
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


# The last three regular-season games (12-14) decide division seeding and
# 15-17 are the playoffs -- a bye in that stretch benches a starter for the
# games that matter most, which a bare number doesn't say.
LATE_BYE_FROM = 12


def _is_late_bye(player: Player) -> bool:
    return player.market.bye is not None and player.market.bye >= LATE_BYE_FROM


def _late_bye_active(state: LeagueState, my_seat: Optional[int]) -> bool:
    """Whether the late-bye flag is still worth raising.

    Once my own starting lineup has no open slot left, a late bye no longer
    changes a decision -- so the flag goes quiet everywhere (roster cards,
    pool, log) rather than keep marking bench-only picks. Unresolved or
    unknown seats fail open: the flag stays on rather than silently vanishing.
    """
    if my_seat is None:
        return True
    seat = state.seats.get(my_seat)
    if seat is None:
        return True
    return not is_legal(seat.by_position, state.rules)


def _player_row(
    label: str,
    player: Player,
    price: int,
    points: float,
    bench: bool = False,
    late_bye: bool = False,
) -> str:
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
    color = POS_COLOR_LIGHT.get(player.position, POS_FALLBACK_LIGHT)
    flag = late_bye and _is_late_bye(player)
    tip = f"{player.name} · {player.position} · {player.team or 'FA'}"
    if flag:
        tip += " · LATE BYE"
    elif player.market.bye:
        tip += f" · BYE {player.market.bye}"
    tip += f" · {points:.0f} pts · ${price}"
    return (
        f'<div class="ln{" bench" if bench else ""}" style="--pos:{color}"'
        f' title="{_esc(tip)}">'
        f'<i class="ms">{_esc(label)}</i>'
        f'<b class="mn">{_esc(_short_name(player))}</b>'
        f'<b class="mnf">{_esc(player.name)}</b>'
        f'<i class="mb{" late" if flag else ""}">{player.market.bye or "—"}</i>'
        f'<i class="mp">{points:.0f}</i>'
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
    identically when they mean opposite things.

    The run stops at the target and never draws surplus. Depth is a roster
    question, not a run-pressure one: inside a pressure tile a fourth
    quarterback only made one of twelve tiles wider than its neighbours, and
    what the run pane wants from that seat is the single fact that it has
    stopped buying -- which the dimming says. Where depth *is* the question,
    `_fold_cell` draws it as a line of its own beneath the run.
    """
    whole = int(want)
    pips = "".join(
        f'<span class="pip{" on" if i < have else ""}"></span>'
        for i in range(whole)
    )
    if want > whole:
        pips += f'<span class="pip half{" on" if have > whole else ""}"></span>'
    return f'<span class="pips" style="--pos:{color}">{pips}</span>'


# Positions the fold summary and starter count leave out. Both are settled
# early and stay settled -- you buy one defense, one kicker, and neither is a
# decision you make at the podium -- so they cost a row each without ever
# changing what you would bid. The LINEUP pane still shows them.
_NEED_SKIP = ("DEF", "K")


def _fold_cell(line: PositionLine, owned: int) -> str:
    """One position of the folded strip: its run of slots, and its depth beneath.

    Two lines rather than one, because a surplus pip drawn *after* the run made
    every position a different width -- a seat with a fourth receiver pushed the
    tight end onto a second row, and which positions a card had left to fill
    stopped being something you could read across four folded cards at once.
    Stacked, row one is always the same eight slots in the same eight places, and
    depth is a line under whichever position has it.
    """
    color = POS_COLOR_LIGHT.get(line.pos, POS_FALLBACK_LIGHT)
    stack = _pips(line.have, owned, color)
    # Depth wraps at twice the slots owned, so a position carrying everything it
    # could plausibly carry is still two lines: four spare running backs under
    # two RB slots, not two rows of two. The line is wider than the run above it
    # and that is fine -- the strip lays its positions out in equal grid tracks
    # (see `.foldsum` in board.css), so a deep position fills its own track
    # rather than shoving the position beside it along. It was a flex row once,
    # and there a seat with five backs put its receivers somewhere no other
    # card's were.
    left = line.have - owned
    per_row = max(1, 2 * owned)
    while left > 0:
        stack += (
            f'<span class="pips fx" style="--pos:{color}">'
            + '<span class="pip extra"></span>' * min(left, per_row)
            + "</span>"
        )
        left -= per_row
    return (
        f'<span class="fpos"><i class="ms">{_esc(line.pos)}</i>'
        f'<span class="fstack">{stack}</span></span>'
    )


def _fold_summary(state: LeagueState, seat: Seat) -> str:
    """What a card still says once its row is folded: which positions it has
    filled, which it has not, and where it is carrying depth.

    Position targets come from `DRAFT_TARGETS`, fractional -- what a seat
    should still buy. Folded, a card is not being shopped for; it is being read
    off, and the question is what a seat *has*. So the run here is
    `owned_starters()`: the whole slots the lineup seats a position no matter
    what, 2 QB / 2 RB / 3 WR / 1 TE. Everything past it is depth, and depth is
    exactly what the second line draws.

    It ships in every card and CSS shows it only when folded, the same bargain
    the LINEUP/BN panes make.
    """
    owned = state.rules.owned_starters()
    cells = "".join(
        _fold_cell(line, owned.get(line.pos, 0))
        for line in position_summary(seat, state.rules, state.proj)
        if line.pos not in _NEED_SKIP
    )
    return f'<div class="foldsum">{cells}</div>'


def _card_header(state: LeagueState, seat: Seat) -> str:
    """Money, and nothing else: what is left, the most it can still bid, and a
    bar of both.

    The bar splits what is spendable from what is already owed -- a dollar per
    slot still to fill is in the account but not available, and a seat with $80
    and eight holes is not the threat its balance says it is.
    """
    budget = state.rules.budget
    held = max(0, seat.open_slots - 1)
    spendable = max(0, seat.budget_left - held)
    # The name leads and the slot stays beside it, small. Dropping the number
    # would cost the card the one thing it can be checked against -- Sleeper's
    # own board is numbered, this one can be dragged out of order, and "S5" is
    # what the two have in common. Unnamed, the number is the whole label rather
    # than a badge next to a blank.
    #
    # Short and full both ship, and CSS shows one: the segmented control takes
    # two thirds of this row on the compact board, so a name gets about four
    # characters there and the whole of it maximized. The same bargain the
    # player rows make a few lines down, and for the same reason -- the swap is
    # free, and a fetch to widen a card would not be.
    if seat.name:
        who = (
            f'<span class="team">{_seat_tag(seat)}</span>'
            f'<span class="teamf">{_esc(seat.name)}</span>'
            f'<span class="slot">S{seat.slot}</span>'
        )
    else:
        who = f'<span class="team">S{seat.slot}</span>'
    return (
        '<header>'
        f'<div class="top{" named" if seat.name else ""}"'
        f' title="{_esc(_seat_tip(seat))} · right-click to rename">{who}'
        '<span class="seg">'
        '<button data-pane="lineup" type="button">LINEUP</button>'
        '<button data-pane="bench" type="button">BN</button></span></div>'
        f'<div class="top"><span class="big">${seat.budget_left}</span>'
        f'<span class="max">max <b>${seat.max_bid}</b></span></div>'
        '<div class="budget">'
        f'<i style="width:{100 * spendable / budget:.1f}%"></i>'
        f'<i class="held" style="width:{100 * held / budget:.1f}%"></i>'
        "</div>"
        f"{_fold_summary(state, seat)}"
        "</header>"
    )


def _card_footer(
    layout: Sequence[Tuple[str, Optional[Player]]], proj: Mapping[str, float]
) -> str:
    """How much of the lineup is bought, and what it projects.

    The two card-level numbers. They sit below the panes rather than in the
    header because the header is money and nothing else -- and below the panes
    they are the same numbers whichever pane is showing, which is what a
    card-level figure should be.

    Both are read off the same optimal lineup the LINEUP pane is drawn from, so
    the footer cannot disagree with the rows above it.

    The count leaves out the same positions the fold summary does: a defense is
    bought once, late, by everyone, so counting it only moves every seat's
    denominator by one and makes 9/10 mean "nothing missing but a defense" on
    some cards and "missing a receiver" on others.
    """
    starters = [(slot, p) for slot, p in layout if slot != BENCH]
    counted = [(slot, p) for slot, p in starters if slot not in _NEED_SKIP]
    filled = sum(1 for _, player in counted if player)
    total = sum(proj.get(player.id, 0.0) for _, player in starters if player)
    figure = f"{total:,.0f}" if total else "—"
    short = "" if filled == len(counted) else " short"
    return (
        '<div class="proj" title="starters filled &#xb7;'
        ' starters&#x27; projected points">'
        f'<span class="pl str">STARTERS</span>'
        f'<b class="fill{short}">{filled}<i>/{len(counted)}</i></b>'
        f'<span class="pl pts">PROJ</span><b>{figure}</b></div>'
    )


def _roster_card(state: LeagueState, seat: Seat, row: int, late_bye: bool = False) -> str:
    """One seat, as two panes over the same roster.

    LINEUP is every starting slot, one player per line, in the slot they would
    actually start in. BN is the bench. Both ship in the markup and CSS shows
    one, so switching panes costs no fetch and cannot show a different moment
    of the draft than the pane it replaced -- the same bargain the short/full
    name swap makes.

    `data-row` is which row of the board the card starts in. It tints the card to
    its row from the first paint; the client rewrites it after a drag, because
    after a drag the row a card is in is a question only the client can answer.
    """
    price_of = {pick.player.id: pick.price for pick in seat.picks}
    layout = display_slots(seat.lineup, seat.roster, state.rules)

    lineup = _column_header() + "".join(
        _open_row(slot)
        if player is None
        else _player_row(
            SLOT_LABEL.get(slot, slot), player, price_of.get(player.id, 0),
            state.points(player), late_bye=late_bye,
        )
        for slot, player in layout
        if slot != BENCH
    )
    bench_players = [p for slot, p in layout if slot == BENCH and p]
    bench = "".join(
        _player_row(
            p.position or BENCH, p, price_of.get(p.id, 0), state.points(p),
            bench=True, late_bye=late_bye,
        )
        for p in bench_players
    )
    if not bench:
        bench = '<div class="empty">no bench yet</div>'

    broke = " broke" if seat.max_bid <= _OUT_OF_MARKET else ""
    return (
        f'<section class="card{broke}" data-seat="{seat.slot}"'
        f' data-row="{row}" draggable="true">'
        f"{_card_header(state, seat)}"
        '<div class="body">'
        f'<div class="pane lineup" data-pane="lineup">{lineup}</div>'
        f'<div class="pane" data-pane="bench">{bench}</div>'
        "</div>"
        f"{_card_footer(layout, state.proj)}"
        "</section>"
    )


# How many cards a row of the board holds. The grid's own
# `grid-template-columns` says the same thing, and the client says it a third
# time as `GRID_COLS` -- all three have to agree, because the row tints are cut
# from this number and the client folds by row.
_GRID_COLS = 4


# The chart's box, split between the plot and the room kept above it for the
# three figures. A bar is drawn against the *budget*, not against the tallest bar
# in the room: a seat that can still bid half of what it started with should look
# like half whether or not someone else can bid all of it, and a relative scale
# would have every bar grow as the room went broke.
#
# In percent, not pixels. The band is a fixed share of the column's height now,
# so the plot is whatever is left after the labels and the tags -- a number the
# server cannot know and does not need to: a bar that is 40% of its column is
# 40% of the room's budget at any height the browser hands it.
_LEDGER_H = 100.0
_LEDGER_HEAD = 24.0


def render_ledger(
    state: LeagueState,
    my_seat: Optional[int],
    note: str = "",
    user: str = "",
) -> str:
    """What the room has spent, and what each seat can still do about it.

    The band this replaces spent its height on a sentence of league constants --
    teams, budget, roster size -- which were true before the draft opened and
    stayed true all night. Only one number in it moved. So: that number, drawn,
    and then the question the board actually exists to answer, which is not how
    much money is left in the room but *who can outbid me*.

    Hence twelve columns of `max_bid` rather than of `budget_left`. Balance
    overstates a seat holding eight open slots -- a dollar of it per hole is
    owed -- and the auction is decided on what can go on one player.

    Ranked, deepest pocket first, so the answer is the leftmost column and the
    fall from the leaders to the broke tail is the shape of the chart. Only the
    top three carry a figure; annotating twelve would be a table, and the point
    of a chart is the nine you *don't* have to read. Every bar is drawn
    identically -- the figure is a label, not a louder mark.

    The dashed line is your own ceiling, so every column standing above it is a
    seat that can take a player off you. Without a seat there is no line: half
    the budget was arbitrary and the field's average answers a question nobody
    asks mid-auction.
    """
    rules = state.rules
    seats = sorted(state.seats.values(), key=lambda s: (-s.max_bid, s.slot))
    scale = _LEDGER_H - _LEDGER_HEAD
    # Before the first pick every seat holds the same $200 and the ranking is
    # entirely an artefact of the tie-break. Naming three of twelve identical
    # bars as the leaders is worse than naming none, and this is the state the
    # board sits in for the whole half hour before a draft opens.
    level = seats[0].max_bid == seats[-1].max_bid
    top3 = set() if level else {seat.slot for seat in seats[:3]}
    mine = next((s for s in seats if s.slot == my_seat), None)

    cols = []
    tags = []
    for seat in seats:
        state_cls = (
            "me"
            if seat.slot == my_seat
            else "broke"
            if seat.max_bid <= _OUT_OF_MARKET
            else ""
        )
        # Bidding is a third fact and not one of the three, so it is added
        # rather than chosen between: your own seat can be bidding, and so can a
        # seat that is nearly broke -- which is the pair you most want to see at
        # once. What a seat *can* spend is the column; whether it is spending it
        # right now is the tint behind it.
        if seat.bidding:
            state_cls = f"{state_cls} bid-{seat.bidding}".strip()
        # A seat that is out of the market still gets a mark -- at $3 the honest
        # height rounds to nothing, and nothing is not what $3 means. The floor
        # is `min-height` in the stylesheet rather than a number here: it is two
        # pixels, and pixels are the one unit this function no longer speaks.
        height = scale * seat.max_bid / rules.budget
        figure = (
            f'<span class="amt">${seat.max_bid}</span>' if seat.slot in top3 else ""
        )
        cols.append(
            f'<span class="col {state_cls}" title="{_esc(_seat_tip(seat))}'
            f" &#xb7; spent ${seat.spent} &#xb7; ${seat.budget_left} left "
            f'&#xb7; max bid ${seat.max_bid}">{figure}'
            f'<span class="bar" style="height:{height:.1f}%"></span></span>'
        )
        # Twelve tags across the width of the band, so the name is cut to its
        # first word: enough to find your column, and the tooltip has the whole
        # of it next to the seat number. No tooltip on an unnamed seat -- one
        # that repeats the two characters already on screen is noise.
        tip = f' title="{_esc(_seat_tip(seat))}"' if seat.name else ""
        tags.append(f'<span class="{state_cls}"{tip}>{_seat_tag(seat)}</span>')

    if mine is not None:
        # The account is named, not just the seat. A username resolves through
        # two lookups before it reaches a slot, and if either one lands on the
        # wrong account the board would mark a seat that is not yours and say
        # nothing -- "S5" is not checkable, "yidinghou · S5" is.
        who = f"<b>{_esc(user)}</b> · " if user else "You are "
        # The account named the seat; this says what the *league* calls it. Both,
        # because they check different halves of the same lookup -- the username
        # proves the seat is yours, and the team name proves the scan put the
        # right name on it. Silent when they are the same word.
        as_named = (
            f' · <span class="asnm">{_esc(mine.name)}</span>'
            if mine.name and mine.name != user
            else ""
        )
        # A place in the room only means something once the room has spread out.
        standing = (
            "level with the room"
            if level
            else f"{_ordinal(seats.index(mine) + 1)} of {len(seats)}"
        )
        line = (
            f'<span class="me">{who}<b>S{mine.slot}</b>{as_named} — '
            f"${mine.budget_left} left, max bid <b>${mine.max_bid}</b> "
            f"({standing})</span>"
        )
        gridline = (
            f'<span class="gl" style="top:'
            f'{_LEDGER_H - scale * mine.max_bid / rules.budget:.1f}%"></span>'
        )
        legend = "dashed = your ${}".format(mine.max_bid)
    else:
        line = f'<span class="me unseated">{_esc(note)}</span>' if note else ""
        gridline = ""
        legend = "seat unknown"

    return (
        '<div class="led">'
        '<div class="ch">'
        # This line is the panel's title as well as its legend. A separate
        # heading over it would be a second row saying less, and in a band held
        # to a fifth of the column every row is taken off the plot.
        f'<div class="chhd"><span>Buying power · max bid as % of '
        f"${rules.budget} · {legend}</span></div>"
        f'<div class="plot">{gridline}{"".join(cols)}</div>'
        f'<div class="chft">{"".join(tags)}</div>'
        # Under the chart rather than beside the legend: in three fifths of the
        # band there is no row wide enough to hold both, and of the two this is
        # the sentence that is read.
        f"{line}"
        "</div></div>"
    )


def render_spend(state: LeagueState) -> str:
    """What the room has spent, for the band's own header.

    One line summarising the whole draft, which is what a band header is for --
    and it was costing the chart under it thirty pixels of height in a band that
    is now a fixed share of the column. The rail rides along: it is the same
    fact drawn, and three pixels of it fit on a header row.
    """
    rules = state.rules
    spent = sum(seat.spent for seat in state.seats.values())
    pool = rules.budget * rules.teams
    pct = 100 * spent / pool if pool else 0
    return (
        f'<span class="ledbig">${spent:,}</span>'
        f'<span class="ledof">of ${pool:,}</span>'
        f'<span class="rail"><i style="width:{pct:.1f}%"></i></span>'
        f'<span class="ledpct">pick <b>{len(state.picks)}</b> of '
        f"{rules.teams * rules.roster_size} · <b>{pct:.0f}%</b> gone</span>"
    )


def render_rosters(state: LeagueState, my_seat: Optional[int] = None) -> str:
    """Every seat's roster as a card, in seat order, under a header per row.

    Seat order, and never sorted by anything that moves: these are read to look
    something up ("what does seat 4 still need at receiver?"), so a card has to
    stay where you last saw it rather than jumping every time someone bids.

    The client can drag cards into a different order, and that ordering is
    presentational only -- CSS `order` on the grid items, so this markup stays in
    seat order. Which is the point: the one thing allowed to move a card is the
    hand that dragged it, and a rearranged board still knows what seat order was.

    Each card ships stamped with the row it starts in, so the board is already
    tinted into rows on the first paint -- before any client code has said a word
    about order. One flat grid and nothing else in it: cards have to be draggable
    from any row to any other, and a grid per row could not do that. Which rows
    are open is the filter's business, up in the menu bar.
    """
    late_bye = _late_bye_active(state, my_seat)
    cards = [
        _roster_card(state, state.seats[slot], index // _GRID_COLS, late_bye=late_bye)
        for index, slot in enumerate(sorted(state.seats))
    ]
    return f'<div class="grid">{"".join(cards)}</div>'


def _seat_tile(
    seat: Seat, line: PositionLine, color: str, lit: bool = True
) -> str:
    """One seat's fill at one position: who, what's left, and the pips.

    `data-seat` is what the client's `applyOrder()` reorders on, so this tile
    lands wherever that seat's roster card was dragged to.

    A seat that has met its target settles: the tile fills grey and the pips give
    way to a check. Which seats are still hunting is the one question the grid
    exists to answer, and it used to be asked with a 0.3 opacity on 8px type --
    a subtlety at the size the tile is actually read at. A filled shape carries
    across the grid at a glance; a faded one has to be looked at to be seen.
    The check is the same width as the pips it replaces, so the 4x3 grid does not
    shuffle every time a seat fills up.
    """
    done = "" if line.need else " done"
    # A seat in the bidding is marked here too, and the same amber it takes in
    # the money chart. This grid is read while a lot is live -- "who else still
    # needs a receiver" is asked *about the player on the block* -- so which of
    # these twelve are actually in on him belongs on the same tiles.
    #
    # `lit` is what keeps that sentence true. The bidding is on one player, and
    # that player has one position, so the same amber repeated across all four
    # cards said "these seats are bidding" four times over and answered the
    # question the grid is for -- who is in on *this* run -- in none of them. On
    # the nominee's card alone it reads as one fact about one lot; everywhere
    # else it was decoration that moved whenever the bidding did.
    bidding = f" bid-{seat.bidding}" if lit and seat.bidding else ""
    body = (
        '<span class="tfull">✓</span>'
        if not line.need
        else _pips(line.have, line.want, color)
    )
    tip = (
        f"{_seat_tip(seat)} — {line.have}/{_num(line.want)} {line.pos} · "
        + ("full" if not line.need else f"{_num(line.need)} to go")
        + f" · ${seat.budget_left} left, max ${seat.max_bid}"
    )
    return (
        f'<span class="tile{done}{bidding}" data-seat="{seat.slot}"'
        f' title="{_esc(tip)}">'
        f'<span class="ttop"><b>{_seat_tag(seat)}</b>'
        f"<i>${seat.max_bid}</i></span>"
        f"{body}</span>"
    )


def _health_bar(pr: PositionPressure, total: int) -> str:
    """How much of the league's want at this position is already bought.

    The same bargain the roster card's budget bar makes, and drawn the same way:
    a track with the story told in percentage widths, so the state reads before
    the counter beside it is parsed. Filled is what actually answers somebody's
    target -- capped per seat, so a fourth quarterback on one roster cannot fill
    the bar for the eleven teams still without one. That makes the coloured
    stretch exactly `total - wanted`, so the bar and the TIER pane's "N wanted"
    can never tell different stories about the same moment.

    Grey is the surplus: bodies gone off the board that answered nobody's need.
    It is supply that is spent either way, which is why it sits inside the bar
    rather than in the empty track, and grey rather than coloured, because it is
    not progress.
    """
    if total <= 0:
        return ""
    # Clamped, because `total` is the rounded target and the fractional wants
    # need not round with it -- 3.5 across eleven seats is 38.5 of a bar of 38.
    met = min(float(total), sum(min(line.have, line.want) for line in pr.lines.values()))
    over = min(max(0.0, pr.drafted - met), total - met)
    return (
        '<div class="phealth">'
        f'<i style="width:{100 * met / total:.1f}%"></i>'
        f'<i class="over" style="width:{100 * over / total:.1f}%"></i>'
        "</div>"
    )


# Enough supply to see whether the tier is a tier or a rump, without the card
# turning into a player list -- the detail panel has the rest.
_BOARD_SHOWN = 3


def _pressure_card(
    state: LeagueState, pr: PositionPressure, nom_pos: str = ""
) -> str:
    """One position: supply on the board over the league's fill state.

    `nom_pos` is the position of whatever is on the block. Only that card lights
    its bidders -- see `_seat_tile`.
    """
    color = POS_COLOR_LIGHT.get(pr.pos, POS_FALLBACK_LIGHT)
    total = round(DRAFT_TARGETS.get(pr.pos, 0.0) * state.rules.teams)

    lit = bool(nom_pos) and pr.pos == nom_pos
    tiles = "".join(
        _seat_tile(state.seats[slot], pr.lines[slot], color, lit)
        for slot in sorted(state.seats)
    )

    # A taste of who is left, not the whole tier -- the full list is one click
    # away in the TIER pane, and RUNS is sized to its content now rather than
    # stretched to match it, so a handful of rows costs nothing to show and
    # nothing to leave empty either. Compact `_tier_row`s so the two panes can
    # never disagree about who is available -- one renderer, two densities.
    board_rows = "".join(
        _tier_row(p, i + 1, state.proj.get(p.id, 0.0), compact=True)
        for i, p in enumerate(pr.avail[:_BOARD_SHOWN])
    )
    if not board_rows:
        board_rows = '<div class="trow gone">tier is gone</div>'
    more = (
        f'<div class="bmore">+{len(pr.avail) - _BOARD_SHOWN} more</div>'
        if len(pr.avail) > _BOARD_SHOWN
        else ""
    )
    board = f'<div class="bd">{board_rows}{more}</div>'

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
    # roster cards make with LINEUP / BN.
    return (
        f'<section class="pcard sev-{pr.severity}{done}"'
        f' style="--pos:{color}" data-pos="{pr.pos}">'
        # No longer a control: which cards are open is the band's filter to say,
        # and a header that folded on click as well would be a second answer to
        # a question that now has one. So no tooltip and no cursor -- it is a
        # line of type again, and the only buttons on it are the pane's own.
        f'<div class="phd">{badge(pr.pos, light=True)}'
        f'<span class="pcount"><b>{pr.drafted}</b>/{total}</span>'
        f'<span class="pverd">{pr.left} left</span>'
        '<span class="seg pseg">'
        '<button data-pane="runs" type="button">RUNS</button>'
        '<button data-pane="tier" type="button">TIER</button></span>'
        "</div>"
        # Outside the header, so it folds away with the rest: three pixels
        # squeezed onto a rail is a smear, not a reading.
        f"{_health_bar(pr, total)}"
        # The two panes in a box of their own, which does nothing at all on a
        # narrow card -- it is a column of the same two children the card would
        # have laid out itself. It earns its keep at width, where the panes go
        # side by side and the header and the health bar must still run the full
        # width above them. Without it the card cannot be split, because a flex
        # row would take the header into the row with them.
        f'<div class="pbody">{runs}{_pressure_detail(pr, state.proj)}</div></section>'
    )


# How much of each band the detail panel lists. The panel is the answer to "which
# ones?", so it is generous -- but a position with thirty bodies under the cliff
# would turn it into a scrolling player list, which is not what it is for.
_TIER_SHOWN = 10


def _tier_row(
    player: Player,
    rank: int,
    points: float,
    below: bool = False,
    compact: bool = False,
) -> str:
    """One available player: rank, name, team, points, $PROJ.

    `$PROJ` is `market_value`, the same figure the nomination strip quotes, so
    what you read here is what you see when they hit the block.

    The hover carries the full name, the team and the points always, because
    the name in the row is the short form whatever the width -- the tooltip
    is where the row is complete, at every size.

    `compact` drops team and points from the row itself -- rank, name and
    $PROJ, the three that answer "which ones, and what do they cost" -- so
    the same renderer serves the RUNS pane's short preview as well as the
    TIER pane's full list, rather than the two keeping separate markup for
    what is the same fact at two densities.
    """
    dollar = market_value(player)
    cls = "trow"
    if below:
        cls += " below"
    if compact:
        cls += " compact"
    body = [f'<i class="tr">{rank}</i>', f'<b>{_esc(_short_name(player))}</b>']
    if not compact:
        body.append(f'<i class="ttm">{_esc(player.team or "FA")}</i>')
        body.append(f'<i class="tpt">{points:.0f}</i>')
    body.append(f'<i class="tpr">{f"${dollar:.0f}" if dollar else "—"}</i>')
    return (
        f'<div class="{cls}" title="{_esc(_meta_tip(player, points))}">'
        f'{"".join(body)}</div>'
    )


def _pressure_detail(pr: PositionPressure, proj: Mapping[str, float]) -> str:
    """One position's tier and the tier under it, with the cliff between.

    The question the card provokes and cannot answer: *which quarterbacks, and
    what do I drop to if I wait?* Both bands are listed rather than counted, and
    the divider is what makes waiting a decision rather than a shrug.
    """
    rows = "".join(
        _tier_row(p, i + 1, proj.get(p.id, 0.0))
        for i, p in enumerate(pr.avail[:_TIER_SHOWN])
    ) or '<div class="trow gone">nobody left in this tier</div>'

    if pr.next_tier:
        divider = (
            f'<div class="tcliff">▾ −{pr.cliff_drop} pts to the next tier</div>'
        )
        below = "".join(
            _tier_row(p, len(pr.avail) + i + 1, proj.get(p.id, 0.0), below=True)
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


def render_pressure(state: LeagueState, nom_pos: str = "") -> str:
    """Run pressure, one card per position, in `TIERS` order.

    `nom_pos` is the position of the player on the block, and the only card that
    lights its bidders. Empty between lots, when nothing is being bid on and so
    nothing should be lit.

    Fixed order and fixed positions, for the same reason the roster cards are in
    seat order: a card that reshuffles itself the moment a run starts is a card
    you have to hunt for at exactly the moment you needed it.

    All four are always on screen, whatever state each is in: a card can be
    showing its pressure view or its tier, and can be folded, without any of that
    costing you sight of the other three. Comparing positions is the whole point
    of putting them side by side, so nothing here is allowed to cover them.

    Two containers. The rail comes first and every card is rendered into the
    second: the rail is pinned down the left edge of the band, and the open cards
    take whatever is right of it. Pinned rather than trailing because it is the
    one part of the band that does not move -- four positions are always in it or
    beside it, so the eye should find them in the same place whichever ones you
    have open, and a rail that slides left every time you open another card is a
    landmark that moves.

    It ships empty because which cards are folded is the client's business -- the
    band's filter, kept in the browser like the seat order and the per-card pane,
    and never told to the server. The client moves the cards it has put away into
    the rail after each swap.

    The rail cannot be had from one container: a flex row will not stack a subset
    of its children into a column at one edge, and a grid cannot hand the open
    cards a row of their own to divide. So the shape is two boxes, and the only
    thing the server owes them is that both exist, in that order.
    """
    cards = "".join(_pressure_card(state, pr, nom_pos) for pr in pressure(state))
    return f'<div class="prail"></div><div class="pgrid">{cards}</div>'


# Deep enough to scroll to anyone worth a dollar. The sheet runs to thousands of
# bodies, nearly all of them undraftable, so this is a limit on payload rather
# than a judgment: past three hundred names ordered by price, what is left is
# kickers and third-string tight ends.
_POOL_SHOWN = 300

# ...but the pane filters by position, and three hundred names ordered by price
# is not three hundred names *at a position*. Late on, the top 300 can hold four
# tight ends, which makes the TE filter a pane that answers "almost nobody" when
# the truth is "nobody worth having". This many per position are carried on top
# of the overall cut so every filtered view is deep enough to be worth opening.
_POOL_PER_POS = 40


def _meta_tip(player: Player, points: float, late_bye: bool = False) -> str:
    """The row's small columns, spelled out for the hover.

    Team, bye and points are two or three characters each in the list, which is
    all the room they get and rather less than they need to explain themselves.
    """
    if late_bye and _is_late_bye(player):
        bye = "LATE BYE"
    else:
        bye = f"BYE {player.market.bye}" if player.market.bye else "no bye listed"
    return (
        f"{player.name} · {player.position} · {player.team or 'FA'} · {bye}"
        f" · {points:.0f} pts"
    )


# A week has to clear this margin off the player's season pace -- points ÷ 17
# -- before the pool calls it out. Division-round byes and matchup noise put
# most weeks a few percent either side of pace; coloring those would make the
# green/red read as decoration rather than a signal worth nominating around.
_EARLY_WEEK_MARGIN = 0.15


def _wk(player: Player, n: int) -> Optional[float]:
    """One early-week projection, or None when the sheet predates the columns."""
    return getattr(player.week, f"week{n}", None) if player.week else None


def _early_week_tag(week: Optional[float], pace: float) -> str:
    """The class suffix a division-round week earns against the player's own
    season pace: green above it, red below it, nothing inside the margin.

    Shared by the pool's columns and the block panel's strip so the two cannot
    drift into disagreeing about which weeks are worth calling out -- the same
    player has to read the same way whether you are scanning for him or bidding
    on him.
    """
    if week is None or pace <= 0:
        return ""
    gap = (week - pace) / pace
    if gap >= _EARLY_WEEK_MARGIN:
        return " g"
    if gap <= -_EARLY_WEEK_MARGIN:
        return " r"
    return ""


def _early_week_cell(week: Optional[float], pace: float) -> str:
    """One of the three division-round columns, held back unless the gap from
    the player's own season pace is real. `pace` is `points / 17`, so a bye or
    a light early slate for an otherwise even producer reads as red without the
    season total itself being in question."""
    if week is None:
        return '<i class="pwk">—</i>'
    return f'<i class="pwk{_early_week_tag(week, pace)}">{week:.1f}</i>'


def _early_week_strip(player: Optional[Player], points: float) -> str:
    """The block panel's own read of the division round: three labelled figures
    in their own side column, coloured off season pace exactly as the pool's
    columns are.

    The pool can lean on a header row for its column names; this panel has one
    player in it and no header to hang WK1/WK2/WK3 off, so each figure carries
    its own label. Nothing is drawn at all for a lot with no early-week read --
    a CSV that predates the columns, or a nomination that is not in projections
    -- since three dashes would take the width without answering anything.
    """
    if player is None:
        return ""
    wk = player.week
    weeks = (wk.week1, wk.week2, wk.week3) if wk else (None, None, None)
    if all(week is None for week in weeks):
        return ""
    pace = points / 17
    cells = "".join(
        f'<span class="onwkc"><i class="onwkl">WK{n}</i>'
        f'<i class="onwkv{_early_week_tag(week, pace)}">'
        f'{"—" if week is None else f"{week:.1f}"}</i></span>'
        for n, week in enumerate(weeks, start=1)
    )
    return f'<div class="onwk">{cells}</div>'


# The pool's own column header -- unlike the roster card's `_column_header`,
# this one is not gated behind maximized: the pane has the width for it at any
# size, and it is the only place these nine columns are named at all. Written
# once per render rather than once per row, since it never varies with the data.
_POOL_HEADER = (
    '<div class="prow phdr">'
    '<i></i><i class="phdname">PLAYER</i><i class="ptm">TM</i>'
    '<i class="pby">BYE</i><i class="ppt">PTS</i>'
    '<i class="pwk">WK1</i><i class="pwk">WK2</i><i class="pwk">WK3</i>'
    '<i class="ppr">$</i></div>'
)


def render_pool(state: LeagueState, my_seat: Optional[int] = None) -> str:
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

    Team, bye and points ride alongside the price because the pane is where you
    decide who to nominate, and "another receiver on my quarterback's bye" is
    part of that decision rather than something to go and look up.
    """
    ranked = sorted(
        state.available, key=lambda p: (-market_value(p), -state.points(p), p.name)
    )
    # The overall cut, plus a floor per position so the filters have something to
    # show. Both are taken from the one sorted list and re-read from it, so the
    # pane stays in `$PROJ` order however the two selections overlap.
    keep = {p.id for p in ranked[:_POOL_SHOWN]}
    per_pos: Dict[str, int] = {}
    for player in ranked:
        seen = per_pos.get(player.position, 0)
        if seen < _POOL_PER_POS:
            per_pos[player.position] = seen + 1
            keep.add(player.id)
    shown = [p for p in ranked if p.id in keep]
    late_bye = _late_bye_active(state, my_seat)

    rows = "".join(
        f'<div class="prow" data-pos="{_esc(player.position)}"'
        f' title="{_esc(_meta_tip(player, state.points(player), late_bye))}">'
        f"{badge(player.position, light=True)}"
        f'<b>{_esc(player.name)}</b>'
        f'<i class="ptm">{_esc(player.team or "FA")}</i>'
        f'<i class="pby{" late" if late_bye and _is_late_bye(player) else ""}">'
        f'{player.market.bye or "—"}</i>'
        f'<i class="ppt">{state.points(player):.0f}</i>'
        f'{_early_week_cell(_wk(player, 1), state.points(player) / 17)}'
        f'{_early_week_cell(_wk(player, 2), state.points(player) / 17)}'
        f'{_early_week_cell(_wk(player, 3), state.points(player) / 17)}'
        f'<i class="ppr">{f"${market_value(player):.0f}" if market_value(player) else "—"}</i>'
        "</div>"
        for player in shown
    )
    if not rows:
        return f'<div class="poollist">{_POOL_HEADER}<div class="pnone">the board is empty</div></div>'
    return f'<div class="poollist">{_POOL_HEADER}{rows}</div>'


def _log_price(pick: SeatPick) -> str:
    """What it went for, against what it was projected to go for.

    The gap is the number worth watching -- it says whether the room is paying up
    or the board is falling. Most of the sheet carries no `$PROJ`, and those show
    the price alone rather than a delta against a number that isn't there.
    """
    dollar = pick.player.market.proj_dollar
    if dollar is None or dollar <= 0:
        return (
            f'<i class="lpr">${pick.price}</i>'
            '<i class="ldl muted">—</i>'
        )
    diff = (pick.price - dollar) / dollar
    direction = "over" if diff > 0 else "under" if diff < 0 else "even"
    return (
        f'<i class="lpr">${pick.price}</i>'
        f'<i class="ldl {direction}">{diff:+.0%}</i>'
    )


def _pos_ranks(state: LeagueState) -> Dict[int, str]:
    """`{pick_no: "QB1"}` -- how many at that position had gone before this one.

    Counted in draft order, so QB1 is the first quarterback off the board rather
    than the best one still on it. That is the number a price wants to be read
    against: $54 for QB1 and $54 for QB9 are not the same event.
    """
    ranks: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for pick in sorted(state.picks, key=lambda p: p.pick_no):
        pos = pick.player.position
        seen[pos] = seen.get(pos, 0) + 1
        ranks[pick.pick_no] = f"{pos}{seen[pos]}"
    return ranks


def render_log(state: LeagueState, my_seat: Optional[int] = None) -> str:
    """Every sale, newest first.

    Newest first because the rows read *during* a draft are the last few -- what
    just went, for how much, and whether that was over the odds. The position
    rides in the same pill the pool uses, because "what has the room paid for
    running backs" is a question about a colour, not about fifteen surnames you
    have to recognise one at a time. The pill also carries the position's draft
    rank -- QB1, RB7 -- because that and the position are one fact, and spending
    a whole column on a number that is always two characters wide would take the
    room from the name. Names run in full, as they do in the pool
    beside it and for the same reason -- the width is there. Uncapped,
    because the rows read *between* nominations are the ones from an hour ago,
    when you are trying to remember what a receiver went for; a draft is at most
    a couple of hundred picks, so the whole thing is cheap to carry.
    """
    ranks = _pos_ranks(state)
    late_bye = _late_bye_active(state, my_seat)
    # Who bought it, in the log's one narrow column: the slot leads here rather
    # than the name, because these rows are read in a column and a number is
    # scanned down one where a name has to be read. The name follows when there
    # is one, and the tooltip carries both.
    def buyer(pick: SeatPick) -> str:
        seat = state.seats.get(pick.slot)
        if seat is None or not seat.name:
            return f'<i class="lst">S{pick.slot}</i>'
        return (
            f'<i class="lst" title="{_esc(_seat_tip(seat))}">'
            f"S{pick.slot} · {_seat_tag(seat)}</i>"
        )

    rows = "".join(
        f'<div class="lrow" data-pos="{_esc(pick.player.position)}"'
        f' title="{_esc(_meta_tip(pick.player, state.points(pick.player), late_bye))}'
        f' · ${pick.price}">'
        f'<i class="lno">#{pick.pick_no}</i>'
        f"{buyer(pick)}"
        f"{badge(pick.player.position, light=True, label=ranks.get(pick.pick_no))}"
        f'<b>{_esc(pick.player.name)}</b>'
        f'<i class="ltm">{_esc(pick.player.team or "FA")}</i>'
        f'<i class="lby{" late" if late_bye and _is_late_bye(pick.player) else ""}">'
        f'{pick.player.market.bye or "—"}</i>'
        f'<i class="lpt">{state.points(pick.player):.0f}</i>'
        f"{_log_price(pick)}</div>"
        for pick in sorted(state.picks, key=lambda p: -p.pick_no)
    )
    if not rows:
        rows = '<div class="pnone">no picks yet</div>'
    # The buyer column is two characters wide until somebody has a name, and
    # then it needs room for one. Said once on the list rather than per row, so
    # every row keeps the same grid and the column still reads as a column.
    named = " named" if any(seat.name for seat in state.seats.values()) else ""
    return f'<div class="loglist{named}">{rows}</div>'


def _rung(
    state: LeagueState,
    slot: int,
    amount: Optional[int],
    cls: str,
    note: str,
) -> str:
    """One card on the timeline: a seat, and the figure it took the lot to.

    Read down rather than across -- the tag over the money -- because the strip
    is read as a sequence and a card that is taller than it is wide is a beat in
    one. Twelve of them side by side in a row of text all look like the same
    word; twelve of these look like a ladder.
    """
    seat = state.seats.get(slot)
    tag = _seat_tag(seat) if seat is not None else f"S{slot}"
    tip = _seat_tip(seat) if seat is not None else f"S{slot}"
    # A figure rides in the tooltip too: the tag clips and the seats that get
    # squeezed are the ones you most want the number for.
    if amount is None:
        figure = "—"
    else:
        figure = f"${amount}"
        tip += f" · {note} ${amount}"
    return (
        f'<span class="{("rung " + cls).strip()}" title="{_esc(tip)}">'
        f'<span class="rn">{tag}</span>'
        f'<span class="ra">{figure}</span>'
        "</span>"
    )


def _bid_timeline(
    state: LeagueState,
    events: Sequence[Tuple[int, Optional[int]]],
    bidders: Union[Mapping[int, Optional[int]], Sequence[int]],
    high: Optional[int],
    sold: Optional[Tuple[int, int]] = None,
) -> str:
    """The bidding as one line of it: every raise in order, oldest on the left.

    This was two rows -- a chip per seat collapsed to its best figure, and a
    quieter arrow-joined trail under it saying in what order. Both were runs of
    text on the axis the panel has least of, and both gave up at exactly the
    moment a lot got interesting: the chips clipped the late arrivals, the trail
    ellipsised the *recent* raises off its right end. One strip that scrolls
    sideways has neither problem, and the repetition the chips collapsed away --
    the same seat taking the lead, losing it, taking it back -- is the thing you
    were reading the trail for.

    Figures are floors, not bids anybody reported: Sleeper publishes the offer
    on the clock and no history, so a raise that happened between two polls was
    never visible to anybody but the room. The qualification lives in the
    tooltips, which is where it was always going to be read -- nobody parses a
    label mid-auction, they read `S6 $34`.

    `events` is the board's own record and the preferred source -- watched live
    by this process or a previous one, via `bid_log.BidLog`. Without it --
    a lot no run of the board was ever up for -- `bidders` is all there is: one
    card per seat in entry order, which is what the chip row showed. The panel
    must never sit under a price it cannot account for, so the fallback is a
    fallback and not a blank.

    `sold` closes the strip on a settled lot: the winner at what it actually
    went for, which is the one figure a finished pick can vouch for on its own.
    """
    rungs = []
    if events:
        # The last raise is the one on the clock. Not `high`: the tip of the
        # timeline is a fact about the strip, and a lot whose leader the payload
        # has not caught up with should still show its most recent rung as live.
        last = len(events) - 1
        for i, (slot, amount) in enumerate(events):
            cls = "now" if i == last and sold is None else ""
            rungs.append(_rung(state, slot, amount, cls, "raised to"))
    elif bidders:
        led = dict(bidders) if isinstance(bidders, dict) else {s: None for s in bidders}
        for slot, amount in led.items():
            # From the nomination rather than from `seat.bidding`: who holds the
            # offer is a fact on the draft payload, and the strip must say the
            # same thing as the price above it even when nobody has decorated a
            # seat.
            cls = "now" if slot == high and sold is None else ""
            rungs.append(_rung(state, slot, amount, cls, "led at"))
    if sold is not None:
        slot, price = sold
        # The figures in `events` are what the poller last saw; the price is
        # what the pick says. They normally agree -- when they do, the closing
        # rung is the last raise wearing its result rather than a duplicate of
        # it one pixel to the right.
        if rungs and events and events[-1] == (slot, price):
            rungs.pop()
        rungs.append(_rung(state, slot, price, "sold", "won at"))
    if not rungs:
        return '<div class="rungs"><span class="blbl">no bids yet</span></div>'
    return f'<div class="rungs">{"".join(rungs)}</div>'


def _idle_block(state: LeagueState) -> str:
    return (
        '<div class="block idle">Nothing nominated — '
        f"{len(state.picks)} picks in.</div>"
    )


def _block_panel(
    player: Optional[Player],
    name: str,
    who: str,
    money_label: str,
    money: str,
    money_cls: str,
    timeline: str,
) -> str:
    """The block panel, live or settled.

    Both states are the same screen at different moments of the same draft, so
    they are the same markup: identity on the left, and on the right the only
    two figures you act on, stacked in a fixed column so `$PROJ` and the figure
    beneath it line up digit for digit.

    What differs is four strings. `who` is the seat fact under the name --
    who nominated it while it is open, who won it once it is not. `money_label`
    and `money` are the second figure, "Bid" against "Sold". `name` is passed
    in rather than read off `player` because an unknown nomination has an id
    and no player at all.
    """
    if player is None:
        pill = ""
        proj_txt = "—"
        facts = ['<span class="onfact">not in projections</span>']
    else:
        pill = badge(player.position, light=True)
        dollar = market_value(player)
        proj_txt = f"${proj:.0f}" if proj else "—"
        # Free agents carry no team, and an empty span between two separators
        # draws a dot with nothing on one side of it. Absent facts leave, and
        # take their separator with them.
        facts = []
        if player.team:
            facts.append(f'<span class="onfact">{_esc(player.team)}</span>')
        facts.append(f'<span class="onfact">{points:.0f} pts</span>')
    # Empty when the draft names a nominating seat the board has never heard
    # of; an empty span here would draw a separator dot with nothing beside it.
    if who:
        facts.append(f'<span class="onfact nm">{who}</span>')
    sub = '<span class="onsep"></span>'.join(facts)
    return (
        '<div class="block">'
        '<div class="onmain">'
        '<div class="onid">'
        f'<div class="onhead">{pill}<span class="who">{name}</span></div>'
        f'<div class="onsub">{sub}</div>'
        "</div>"
        f"{_early_week_strip(player, points)}"
        '<div class="onmoney">'
        '<div class="onrow"><span class="figl">$PROJ</span>'
        f'<span class="figv onproj">{proj_txt}</span></div>'
        f'<div class="onrow"><span class="figl">{money_label}</span>'
        f'<span class="{money_cls}">{money}</span></div>'
        "</div>"
        "</div>"
        f"{timeline}"
        "</div>"
    )


def render_nomination(
    state: LeagueState,
    nom: Nomination,
    player: Optional[Player],
    bidders: Union[Mapping[int, Optional[int]], Sequence[int]] = (),
    bid_events: Sequence[Tuple[int, Optional[int]]] = (),
) -> str:
    """The left panel: what is on the block, at what, and who wants it.

    A strip across the whole band until it moved beside the money chart, where
    it has two fifths of the width and the height of the chart to fill -- so the
    price and the room's interest in it get room of their own rather than
    sharing a line with the player's name.

    Laid out by what each fact is *for* rather than by how much room it needs.
    The run of `·`-separated spans this used to be put six unlike things on one
    wrapping line, where the gaps moved with every player and the pill sat off
    the baseline of the text beside it. Now: identity on the left, and on the
    right the only two figures you act on, in a fixed right-aligned column so
    $PROJ and the live bid line up digit for digit. The three questions asked
    mid-auction are still in the order they are asked -- what is it worth, what
    is it at, who is still in -- but the first two are read down, not across.
    """
    if not nom.is_live:
        return _idle_block(state)
    # A bid of nothing is not a small green amount of money: at this size the
    # dash in money green reads as a filled bar, so the placeholder greys out.
    bid = f"${nom.high_bid}" if nom.high_bid is not None else "—"
    bid_cls = "figv bidamt" if nom.high_bid is not None else "figv bidamt none"
    # Who nominated rides with the team and the points, not with the money --
    # it is a fact about the lot, settled when it opened and never changing
    # again, and up in the figures it was a third thing competing with two
    # that move. The strip below carries who is *bidding*.
    nominator = state.seats.get(nom.nominating_slot or 0)
    who = f"nom. {_seat_label(nominator)}" if nominator is not None else ""
    # The poller's memory is the fuller answer and normally arrives. Without it,
    # the two slots the draft itself publishes are everything there is to know
    # -- and a panel reading "$17 · S8" above "no bids yet" would be lying about
    # a fact printed one line up. The offering seat gets the live figure even on
    # this path: it is the one rung whose amount the draft payload states
    # outright rather than the poller having had to watch for it.
    if isinstance(bidders, dict):
        seen: Dict[int, Optional[int]] = dict(bidders)
    else:
        seen = {slot: None for slot in bidders}
    if not seen:
        for slot in (nom.nominating_slot, nom.offering_slot):
            if slot is not None:
                seen.setdefault(slot, None)
        if nom.offering_slot is not None and nom.high_bid is not None:
            seen[nom.offering_slot] = nom.high_bid
    return _block_panel(
        player,
        _esc(player.name) if player else f"player {_esc(nom.player_id)}",
        who,
        "Bid",
        bid,
        bid_cls,
        _bid_timeline(state, bid_events, seen, nom.offering_slot),
    )


def render_settled_lot(
    state: LeagueState,
    bidders: Optional[Mapping[int, Optional[int]]] = None,
    bid_events: Optional[Sequence[Tuple[int, Optional[int]]]] = None,
) -> str:
    """The block panel for a checkpoint: not a lot in progress -- a checkpoint
    is always after a bid cleared -- but the pick that just settled, so the
    panel is never simply blank.

    Same markup and classes as `render_nomination`'s live panel, on purpose:
    a checkpoint is a different moment of the same draft, not a different kind
    of screen. The number is what it sold for rather than what it is at, and the
    timeline is `DraftPoller.bids`'s record of that lot if the poller was
    running to see it close -- the same rungs the live panel showed while it was
    open, closed off with the sale. Without that record -- an older checkpoint
    from before this process started, or a `--replay` rehearsal -- there is only
    the one rung a settled pick can vouch for on its own: who won it, and at
    what.
    """
    if not state.picks:
        return _idle_block(state)
    pick = max(state.picks, key=lambda p: p.pick_no)
    seat = state.seats.get(pick.slot)
    label = _seat_label(seat) if seat is not None else f"S{pick.slot}"
    # The strip ends on the sale rather than on whatever the poller last saw the
    # winner holding: the price is the one number this pick can vouch for on its
    # own, and the two normally agree but need not (a poll can land between the
    # winning offer and the pick itself). Without any record at all that closing
    # rung is the whole strip, which is still the truth and not a blank.
    timeline = _bid_timeline(
        state, bid_events or (), bidders or {}, pick.slot, sold=(pick.slot, pick.price)
    )
    return _block_panel(
        pick.player,
        _esc(pick.player.name),
        f"won by {label}",
        "Sold",
        f"${pick.price}",
        "figv bidamt",
        timeline,
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
