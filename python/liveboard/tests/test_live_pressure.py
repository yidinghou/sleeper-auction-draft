"""Position scarcity: tier cuts, demand, and the tiles the board reorders."""

import json
import math
import re
from pathlib import Path

import pytest

from liveboard.live_pressure import TIERS, PositionPressure, pressure, split_tier
from liveboard.live_render import (
    _pressure_detail,
    _short_name,
    market_value,
    render_page,
    render_pressure,
)
from liveboard.live_state import DRAFT_TARGETS, reconstruct
from liveboard.sleeper import rules_from_draft
from draftsim.player import Player, load_players, load_projections

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def pool():
    return load_players()


@pytest.fixture(scope="module")
def catalog():
    return load_players(free_agents=True)


@pytest.fixture(scope="module")
def proj():
    return load_projections(free_agents=True)


@pytest.fixture(scope="module")
def mock_rules():
    return rules_from_draft(_load("draft-mock"))


@pytest.fixture(scope="module")
def midway(mock_rules, pool, proj, catalog):
    """The mock rewound to pick 60 -- a board with real runs in progress."""
    picks = sorted(_load("picks-mock"), key=lambda p: p["pick_no"])[:60]
    return reconstruct(picks, mock_rules, pool, proj, catalog=catalog)


@pytest.fixture(scope="module")
def finished(mock_rules, pool, proj, catalog):
    """The mock played out: rosters are deep, and positions have settled."""
    return reconstruct(_load("picks-mock"), mock_rules, pool, proj, catalog=catalog)


_PROJ: dict = {}
"""Points for the hand-built players below.

Projections live outside `Player` now, so a body and its points travel as two
things. Building one registers its points here, and this is the sheet the tier
cut is handed -- which keeps each test reading as one list of numbers rather
than a body list zipped against a points list.
"""


def _wr(points: float, name: str = "x") -> Player:
    player = Player(id=f"{name}-{points}", name=name, position="WR", team="NE")
    _PROJ[player.id] = points
    return player


# -- the tier cut -------------------------------------------------------------


def test_the_cut_falls_to_the_highest_floor_anyone_still_clears():
    # WR floors are 240/205/175/145/120. Nobody is over 240, so the cut is 205
    # and the tier is the two above it -- not everyone left at the position.
    pool = [_wr(230), _wr(210), _wr(190), _wr(150)]
    current, beneath, drop = split_tier("WR", pool, _PROJ)
    assert [_PROJ[p.id] for p in current] == [230.0, 210.0]
    assert [_PROJ[p.id] for p in beneath] == [190.0, 150.0]
    assert drop == 20  # 210 -> 190, what you fall to when the tier empties


def test_the_cut_follows_the_pool_down_as_the_top_band_drains():
    # The same position once the 205+ band is gone: the cut drops a floor by
    # itself, so the card keeps describing what is actually on the block.
    current, _, _ = split_tier("WR", [_wr(190), _wr(180), _wr(150)], _PROJ)
    assert [_PROJ[p.id] for p in current] == [190.0, 180.0]


def test_a_pool_beneath_every_floor_is_one_undifferentiated_bin():
    # Past the last floor nobody is distinguishable by tier, and saying so beats
    # inventing a break. No tier below means no cliff to quote.
    current, beneath, drop = split_tier("WR", [_wr(110), _wr(90)], _PROJ)
    assert [_PROJ[p.id] for p in current] == [110.0, 90.0]
    assert beneath == [] and drop == 0


def test_an_empty_pool_is_empty_rather_than_an_error():
    assert split_tier("WR", [], _PROJ) == ([], [], 0)


def test_a_tier_with_nothing_under_it_reports_no_cliff():
    current, beneath, drop = split_tier("WR", [_wr(230), _wr(210)], _PROJ)
    assert len(current) == 2 and beneath == [] and drop == 0


def test_the_cut_only_ever_sees_its_own_position():
    qb = Player(id="q", name="q", position="QB", team="NE")
    current, _, _ = split_tier("WR", [qb, _wr(230)], {**_PROJ, "q": 310.0})
    assert [p.position for p in current] == ["WR"]


# -- demand -------------------------------------------------------------------


def _one(state, pos: str) -> PositionPressure:
    return next(p for p in pressure(state) if p.pos == pos)


def test_demand_sums_the_fractional_shortfalls(midway):
    # Targets are fractional (WR 3.5), and rounding them here is exactly what
    # made a seat holding three receivers look finished.
    wr = _one(midway, "WR")
    expected = sum(
        max(0.0, DRAFT_TARGETS["WR"] - sum(1 for p in seat.roster if p.position == "WR"))
        for seat in midway.seats.values()
    )
    assert wr.wanted == pytest.approx(expected)
    assert wr.wanted % 1 != 0  # a half-body of demand survived the sum


def test_a_seat_past_its_target_adds_nothing_and_is_not_listed(midway):
    for pr in pressure(midway):
        for seat in pr.need_seats:
            assert pr.lines[seat.slot].need > 0
        # Depth is not demand: a fourth running back never makes RB look shorter.
        assert pr.wanted == pytest.approx(
            sum(pr.lines[s.slot].need for s in pr.need_seats)
        )


def test_every_seat_gets_a_line_even_when_it_wants_nothing(midway):
    # The tile grid has to keep the roster board's shape, so a filled seat is a
    # dimmed tile, never a hole.
    for pr in pressure(midway):
        assert set(pr.lines) == set(midway.seats)


# -- severity -----------------------------------------------------------------


def _pr(wanted: float, left: int) -> PositionPressure:
    return PositionPressure(
        pos="WR", avail=[_wr(200 + i) for i in range(left)], next_tier=[],
        cliff_drop=0, drafted=0, wanted=wanted, need_seats=[], lines={},
    )


@pytest.mark.parametrize(
    "wanted,left,expected",
    [
        (2.0, 4, "safe"),    # 0.5x
        (4.0, 4, "safe"),    # exactly 1.0x -- supply still covers it
        (4.5, 4, "tight"),   # just past parity
        (7.9, 4, "tight"),
        (8.0, 4, "run"),     # exactly 2.0x is already a run
    ],
)
def test_severity_thresholds(wanted, left, expected):
    assert _pr(wanted, left).severity == expected


def test_an_empty_tier_with_demand_is_the_most_pressure_there_is():
    gone = _pr(3.0, 0)
    assert gone.ratio == float("inf")
    assert gone.severity == "run"


def test_an_empty_tier_nobody_wants_is_not_a_run():
    assert _pr(0.0, 0).severity == "safe"


# -- rendering ----------------------------------------------------------------


def test_every_seat_appears_once_per_card_carrying_its_slot(midway):
    # The invariant the shared ordering rests on: the client reorders on
    # `data-seat`, so a seat missing from one card would silently stop moving
    # with its roster card.
    html = render_pressure(midway)
    cards = html.split('<section class="pcard')[1:]
    assert len(cards) == len(TIERS)
    for card in cards:
        found = sorted(int(s) for s in re.findall(r'data-seat="(\d+)"', card))
        assert found == sorted(midway.seats)


def test_tiles_ship_in_seat_order_so_the_client_can_rearrange_them(midway):
    # Same bargain the roster cards make: the server always emits seat order and
    # never knows what the board was dragged into.
    card = render_pressure(midway).split('data-pos="RB"')[1]
    slots = [int(s) for s in re.findall(r'data-seat="(\d+)"', card)][: len(midway.seats)]
    assert slots == sorted(midway.seats)


def test_a_finished_seat_settles_out_of_the_grid(midway):
    html = render_pressure(midway)
    dimmed = 0
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1]
        for slot in midway.seats:
            tile = card.split(f'data-seat="{slot}"')[0].rsplit("<span", 1)[1]
            assert ("done" in tile) is (not pr.lines[slot].need)
            dimmed += "done" in tile
    assert dimmed, "no seat had met a target -- the assertion above proved nothing"
    # And it settles as a filled shape, not as a fade. At the size a tile is
    # actually read at, a 0.3 opacity was a subtlety -- and which seats are still
    # hunting is the one question the grid exists to answer.
    page = render_page("123")
    assert "opacity: 0.3" not in page


def test_every_card_carries_a_board_list_tight_end_included(midway):
    # TE used to be cut down to buy the other three width. One starter is still a
    # position you can be run out of, and "who is left at tight end" is a
    # question it asks more sharply than the others, not less.
    html = render_pressure(midway)
    for pos in TIERS:
        card = html.split(f'data-pos="{pos}"')[1].split("</section>")[0]
        board = card.split('<div class="bd">')[1].split("</div>")[0]
        # A taste of who is left, drawn as compact `_tier_row`s -- or the one
        # honest line when the tier really has emptied. Never nothing.
        assert '<div class="trow' in board or "tier is gone" in board
    assert "minor" not in html


def test_a_tile_never_draws_more_than_the_target_asks_for(finished):
    # Depth is a roster question. Inside a pressure tile a fourth quarterback
    # only made one of twelve tiles wider than its neighbours, and what the run
    # pane wants from that seat is that it has stopped buying -- which the
    # settled tile says. Both `_pips` callers pass `cap=True` now that the pane
    # that wanted surplus drawn in full is gone, so this holds board-wide.
    html = render_pressure(finished)
    assert "pip extra" not in html
    deep = short = 0
    for pr in pressure(finished):
        card = html.split(f'data-pos="{pr.pos}"')[1].split("</section>")[0]
        for slot in finished.seats:
            line = pr.lines[slot]
            tile = card.split(f'data-seat="{slot}"')[1].split("</span></span>")[0]
            if not line.need:
                # A seat that is full draws no pips at all -- pips are what is
                # still owed, and it owes nothing. Which also settles the depth
                # question: every seat with a surplus is a full one, so surplus
                # cannot reach the grid whatever it does to a roster.
                assert 'class="tfull"' in tile
                assert 'class="pip' not in tile
                deep += line.have > math.ceil(line.want)
                continue
            filled = tile.count('class="pip on"') + tile.count('class="pip half on"')
            assert filled == min(line.have, math.ceil(line.want))
            short += 1
    assert deep, "no seat was over its target -- the assertion above proved nothing"
    assert short, "no seat was short -- the pip count above was never checked"


def test_a_position_nobody_is_short_at_recedes(finished, midway):
    # It cannot run any more, so the card steps back from the three that can.
    # Recessive, not hidden: `.pcard.done` is an opacity, and hover restores it.
    for state in (finished, midway):
        html = render_pressure(state)
        for pr in pressure(state):
            tag = html.split(f'data-pos="{pr.pos}"')[0].rsplit("<section", 1)[1]
            assert (" done" in tag) is (not pr.need_seats)
    page = render_page("123")


def _health(card: str) -> list:
    bar = card.split('class="phealth"')[1].split("</div>")[0]
    return [float(w.split("%")[0]) for w in bar.split("width:")[1:]]


def test_the_health_bar_is_need_answered_over_need_total(midway):
    # The same shape the roster card's budget bar makes, so the state reads
    # before the counter beside it is parsed. Filled is capped per seat: a fourth
    # quarterback on one roster must not fill the bar for the eleven teams still
    # without one -- which is exactly what makes the coloured stretch
    # `total - wanted`, so the bar and the TIER pane cannot disagree.
    html = render_pressure(midway)
    grew = 0
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1].split("</section>")[0]
        met, over = _health(card)
        total = round(DRAFT_TARGETS[pr.pos] * midway.rules.teams)
        assert met == pytest.approx(100 * (total - pr.wanted) / total, abs=0.1)
        assert met + over <= 100.0 + 1e-6
        grew += met > 0
    assert grew, "no position had been drafted into -- every bar was empty"


def test_the_health_bar_greys_what_answered_nobody(midway, finished):
    # Supply spent on somebody's fourth is gone off the board either way, so it
    # sits inside the bar -- but grey, because it is not progress.
    html = render_pressure(midway)
    surplus = 0
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1].split("</section>")[0]
        met, over = _health(card)
        total = round(DRAFT_TARGETS[pr.pos] * midway.rules.teams)
        bought = sum(min(l.have, l.want) for l in pr.lines.values())
        assert over == pytest.approx(
            100 * min(max(0.0, pr.drafted - bought), total - bought) / total, abs=0.1
        )
        assert met + over <= 100.0 + 1e-6
        surplus += over > 0
    assert surplus, "nobody drafted past a target -- the grey was never checked"
    # And once a position is answered the grey has nowhere to go: RB finishes at
    # 62 bodies against a want of 30, and the bar is full rather than overrun.
    done = render_pressure(finished).split('data-pos="RB"')[1].split("</section>")[0]
    assert _health(done) == [100.0, 0.0]
    page = render_page("123")


def test_the_health_bar_folds_away_with_the_card(midway):
    # Three pixels squeezed onto a rail is a smear, not a reading. It is a
    # sibling of the header rather than part of it, which is what puts it under
    # the rule that empties a folded card.
    page = render_page("123")
    card = render_pressure(midway).split('data-pos="QB"')[1].split("</section>")[0]
    assert card.index('class="phealth"') > card.index("</div>")


def test_the_card_says_how_many_teams_are_short(midway):
    html = render_pressure(midway)
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1]
        assert f"{len(pr.need_seats)} teams still need {pr.pos}" in card


# -- the tier detail ----------------------------------------------------------


def _detail(html: str, pos: str) -> str:
    return html.split(f'data-det="{pos}"')[1].split("</div></div>")[0]


def _card(html: str, pos: str) -> str:
    """One card's markup, from its data-pos to the start of the next card."""
    return html.split(f'data-pos="{pos}"')[1].split("<section")[0]


def test_every_position_ships_a_detail_panel(midway):
    # All four ship and CSS shows one, so opening a panel costs no fetch and
    # cannot show a different moment of the draft than the card behind it.
    html = render_pressure(midway)
    for pos in TIERS:
        assert f'data-det="{pos}"' in html


def test_the_panel_names_the_players_the_card_only_counted(midway):
    html = render_pressure(midway)
    for pr in pressure(midway):
        panel = _detail(html, pr.pos)
        for player in pr.avail[:10]:
            assert _short_name(player) in panel


def test_the_next_tier_is_listed_under_the_cliff(midway):
    html = render_pressure(midway)
    for pr in pressure(midway):
        if not pr.next_tier:
            continue
        panel = _detail(html, pr.pos)
        head, _, tail = panel.partition("tcliff")
        # Below the cliff means below it on screen, not merely mentioned.
        assert _short_name(pr.next_tier[0]) in tail
        assert _short_name(pr.next_tier[0]) not in head
        assert f"−{pr.cliff_drop} pts" in panel


def test_a_tier_with_nothing_under_it_says_so_rather_than_printing_zero():
    pr = PositionPressure(
        pos="WR", avail=[_wr(230, "a"), _wr(210, "b")], next_tier=[],
        cliff_drop=0, drafted=0, wanted=2.0, need_seats=[], lines={},
    )
    panel = _pressure_detail(pr, _PROJ)
    assert "nothing below this tier" in panel
    assert "−0" not in panel


def test_an_emptied_tier_says_so():
    pr = PositionPressure(
        pos="WR", avail=[], next_tier=[_wr(120, "c")], cliff_drop=0,
        drafted=0, wanted=4.0, need_seats=[], lines={},
    )
    assert "nobody left in this tier" in _pressure_detail(pr, _PROJ)


def test_the_rows_quote_the_same_dollars_the_block_does(midway):
    # $PROJ is market_value, so the tier list and the nomination strip cannot
    # disagree about what a player is expected to cost.
    pr = next(p for p in pressure(midway) if p.avail)
    panel = _pressure_detail(pr, midway.proj)
    dollar = market_value(pr.avail[0])
    assert (f"${dollar:.0f}" if dollar else "—") in panel


def test_the_panel_lists_more_than_the_card_does(midway):
    # The card shows three; the cap that used to live in split_tier() would have
    # silently held the panel to the same three.
    deep = max(pressure(midway), key=lambda p: len(p.next_tier))
    assert len(deep.next_tier) > 6  # uncapped in the data
    assert _short_name(deep.next_tier[6]) in _detail(render_pressure(midway), deep.pos)


# -- two panes per card -------------------------------------------------------


def test_every_card_ships_both_panes_and_a_toggle(midway):
    html = render_pressure(midway)
    for pos in TIERS:
        card = _card(html, pos)
        assert 'class="ppane runs"' in card
        assert f'data-det="{pos}"' in card
        assert '<button data-pane="runs"' in card
        assert '<button data-pane="tier"' in card


def test_all_four_stay_on_screen_whatever_state_they_are_in(midway):
    # The promise the overlay broke: comparing positions is the point of putting
    # them side by side, so nothing may cover them. There is no longer any
    # element that could -- the tier is inside its own card.
    html = render_pressure(midway)
    assert html.count('class="pcard') == len(TIERS)
    assert "pdets" not in html
    assert "position: absolute" not in html


def test_the_pane_choice_is_remembered_per_position():
    # The twin of `views`: that one is keyed by seat, this by position, and both
    # exist because the markup they describe is rebuilt every two seconds.
    page = render_page("123")
    assert "draftsim.pviews" in page
    assert 'PVIEWS = ["runs", "tier"]' in page
    assert "applyPViews();" in page


def test_escape_leaves_the_innermost_thing_open():
    # Escape means "leave the maximized board" -- unless the seat menu is open,
    # which is the one thing on this page that covers anything. Innermost first,
    # so naming a seat on a maximized board and changing your mind does not also
    # throw you out of the board.
    page = render_page("123")
    assert 'if (e.key === "Escape" && menuSeat === null) setMaxed(false);' in page
    assert "openTier" not in page


# -- the filter, and the rail it folds into -----------------------------------


def test_the_band_ships_a_row_and_a_rail(midway):
    # Two boxes, because one cannot do it: a flex row lays its children out in a
    # line, so a folded card had to stay in the line as a sliver between two
    # cards you were reading. The rail is where they go instead.
    html = render_pressure(midway)
    # The rail first, because it is pinned down the left edge: it is the part of
    # the band that does not move, and the open cards take what is right of it.
    assert html.startswith('<div class="prail"></div>')
    assert html.endswith("</section></div>")
    # Every card is rendered into the row, and the rail ships empty. Which cards
    # belong in it is the client's business -- the filter lives in the browser
    # and the server is never told, like the seat order and the per-card pane.
    assert html.split('<div class="pgrid">')[1].count('class="pcard') == len(TIERS)


def test_the_rail_is_a_column_that_takes_only_what_it_needs():
    page = render_page("123")
    rule = page.split(".prail { display: flex")[1].split("}")[0]
    assert "flex-direction: column" in rule
    # `flex: 0 0 auto` on the cross axis of a column is what keeps the rail as
    # narrow as its widest header, so everything it gives up goes to the cards
    # still open -- the bargain the row already struck, now paid in a column.
    assert "flex: 0 0 auto" in rule


def test_the_rail_is_always_in_position_order():
    # Walked in `POSITIONS` order rather than in the order you clicked, so a card
    # you put away is found where you last left it rather than where you happened
    # to close it.
    page = render_page("123")
    body = page.split("function applyRuns()")[1].split("\n}")[0]
    assert "POSITIONS.forEach" in body
    assert "(open ? grid : rail).appendChild(card)" in body


def test_the_filter_says_which_positions_are_open():
    page = render_page("123")
    # Five buttons in the shell, not in the fragment: the fragment is replaced
    # every two seconds and a button rebuilt that often can hold neither focus
    # nor pressed state.
    seg = page.split('<span class="seg rseg">')[1].split("</span>")[0]
    assert seg.count("<button") == len(TIERS) + 1
    assert '<button data-pos="" type="button">ALL</button>' in seg
    for pos in TIERS:
        assert f'<button data-pos="{pos}" type="button">{pos}</button>' in seg
    # Multi-select: a click toggles one position rather than replacing the set,
    # which is what makes it a different control from the pool's. The toggling
    # itself lives in `toggleRun`, which the card headers pull too.
    body = page.split("function toggleRun(")[1].split("\n}")[0]
    assert "runsel.filter((p) => p !== pos)" in body
    assert "runsel.concat(pos)" in body
    # ALL restores every position, so an empty selection is never a dead end.
    seg = page.split('closest(".rseg button")')[1].split("\n});")[0]
    assert "setRuns(POSITIONS.slice())" in seg


def test_the_selection_survives_a_refresh_and_a_reload():
    page = render_page("123")
    assert 'localStorage.setItem("draftsim.runsel"' in page
    assert 'localStorage.getItem("draftsim.runsel")' in page
    # Re-applied after every swap, because the swap rebuilt all four cards into
    # the row and the folded ones have to be walked back to the rail. The pool's
    # filter needs none of this: its attribute rides out the swap on a wrapper
    # that is never replaced.
    assert page.count("applyRuns();") == 3
    # And once before the first fetch, so the band never opens on ALL and then
    # visibly folds three cards a moment later.
    assert "applyFilters();\n" in page.split("applyRuns();\ntick();")[0]


def test_all_is_pressed_only_when_every_position_is():
    # It reports the set rather than being a member of it.
    page = render_page("123")
    body = page.split("function applyRuns()")[1].split("\n}")[0]
    assert "runsel.length === POSITIONS.length" in body


def test_all_three_filters_read_the_same_way():
    # One treatment for the pool's, the log's and this one, sized and coloured
    # from a single rule. Run pressure's does the heavier job -- it decides which
    # cards are open at all rather than narrowing a list you are already reading
    # -- and it was given bigger, position-coloured buttons to say so. Three
    # filters down one column reading three different ways is a column with no
    # convention in it, and the position colour is already spoken by the badge on
    # every card, the rail and the health bar.
    page = render_page("123")
    # Sized from `.seg button` with everything else on the board; what a band
    # header buys them is tighter padding, and only that.
    assert "font-size: calc(8.5px * var(--fs)); line-height: 1.4;" in page
    # Nothing left that tints a button by position, and no palette handed to the
    # stylesheet to do it with -- every colour on this page is rendered inline by
    # Python again.
    assert "--pos, #1a1a1a" not in page
    assert '.rseg button[data-pos="QB"]' not in page
    assert "/*__POSCSS__*/" not in page
    assert "_filter_css" not in page
    # Pressed is the near-black every other segment on the page uses, inherited
    # from `.seg` rather than restated here.
    assert '.seg button[aria-pressed="true"] { color: #fff; background: #1a1a1a;' in page


def test_double_tapping_the_filter_does_not_fold_the_band():
    # Toggling positions off one at a time is the ordinary gesture here, so two
    # taps in a row on the same button is not even an accident.
    page = render_page("123")
    handler = page.split('document.addEventListener("dblclick"')[1].split("\n});")[0]
    assert 'e.target.closest(".rseg")' in handler
    assert "return;" in handler.split('closest(".rseg")')[1].split("\n")[0]


# -- collapsing ---------------------------------------------------------------


def test_a_card_header_folds_it_and_says_so(midway):
    # The gesture is back, so the affordance comes back with it: a header that
    # folds on a click has to look like something you can click.
    page = render_page("123")
    rule = page.split(".phd { display: flex")[1].split("}")[0]
    assert "cursor: pointer" in rule
    # No tooltip, though. The one gesture reads "fold" on an open card and
    # "unfold" on a railed one, and the server cannot know which a card will be
    # -- that is the client's list. A fixed title would be wrong half the time.
    html = render_pressure(midway)
    for pos in TIERS:
        card = html.split(f'data-pos="{pos}"')[1]
        head = card.split('class="phd"')[1].split(">")[0]
        assert "title=" not in head


def test_a_folded_card_shows_nothing_but_its_header_in_either_pane():
    # Order alone does not do it. `.pcard .ppane.runs` ties the collapsed rule on
    # specificity and loses on order, but `.pcard.view-tier .ppane.pdet` is a
    # class heavier and wins wherever it sits -- which is why a folded card used
    # to render its whole tier list squeezed into the rail. The rule has to both
    # follow it and out-weigh it.
    page = render_page("123")
    hide = page.index(
        ".pcard.collapsed.view-tier .ppane.pdet,\n  .pcard.collapsed .ppane "
        "{ display: none; }"
    )
    assert hide > page.index(".pcard .ppane.runs { display: flex; }")
    assert hide > page.index(".pcard.view-tier .ppane.pdet {")


def test_folding_a_card_hands_its_width_to_the_ones_you_kept():
    # Pinned to a fixed quarter, folding three cards bought quiet and nothing
    # else: three rails and a quarter-width card with half the band empty beside
    # it. The tier and board lists are name columns that ellipsize, and width is
    # the only thing that helps them.
    page = render_page("123")
    rule = page.split(".pcard { border")[1].split("}")[0]
    assert "flex: 1 1 0;" in rule
    assert "calc((100% - 15px) / 4)" not in page
    # Folded, a card shrinks to its own header -- a rail, and the room it gives
    # up is what the open cards divide.


def test_a_folded_card_still_reports_the_position(midway):
    # Badge, how much of the position is gone, how many are left. Only the pane
    # toggle goes with the fold -- it controls something no longer on screen.
    page = render_page("123")
    assert ".pcard.collapsed .pcount" not in page
    # And the count is per position, against that position's own target.
    html = render_pressure(midway)
    for pr in pressure(midway):
        head = html.split(f'data-pos="{pr.pos}"')[1].split("</div>")[0]
        total = round(DRAFT_TARGETS[pr.pos] * midway.rules.teams)
        assert f'<span class="pcount"><b>{pr.drafted}</b>/{total}</span>' in head


def test_the_pane_toggle_does_not_fold_the_card():
    # Two jobs on one listener, in the order that keeps them apart: the RUNS /
    # TIER buttons sit inside the header, so the pane switch has to claim the
    # click first, or switching a pane would fold the card you were switching.
    # The gaps between the buttons are excluded too -- a miss must not fold it.
    page = render_page("123")
    handler = page.split('pressureEl.addEventListener("click"')[1].split("\n});")[0]
    assert handler.index('closest(".pseg button")') < handler.index('closest(".phd")')
    assert 'if (e.target.closest(".pseg")) return;' in handler
    # One gesture per action: the card still does not answer a double-click.
    assert 'pressureEl.addEventListener("dblclick"' not in page


def test_the_header_and_the_filter_are_one_state():
    # The header folds through the same toggle the filter button pulls, so the
    # button un-presses when you fold from the card. Two lists would leave the
    # band showing one thing and the buttons claiming another, and the next swap
    # would decide which of them won.
    page = render_page("123")
    handler = page.split('pressureEl.addEventListener("click"')[1].split("\n});")[0]
    assert "toggleRun(head.closest(\".pcard\").dataset.pos)" in handler
    # The filter buttons reach the same function rather than a copy of it.
    seg = page.split('closest(".rseg button")')[1].split("\n});")[0]
    assert "toggleRun(btn.dataset.pos)" in seg
    assert page.count("function toggleRun(") == 1
    # And the old per-card fold keys stay gone -- one control, one state.
    assert 'toggleCollapsed("pos:"' not in page
    assert 'collapsed.includes("pos:"' not in page


def test_the_swap_cannot_eat_the_fold():
    # A click is a mousedown and a mouseup on one element, and these headers are
    # rebuilt every two seconds -- so a swap landing between them destroys the
    # header that was pressed and the click never fires at all.
    page = render_page("123")
    hold = page.split('document.addEventListener("mousedown"')[1].split("\n});")[0]
    assert 'e.target.closest(".phd")' in hold
    assert "heldAt = Date.now();" in hold


def test_a_band_header_folds_on_double_click():
    page = render_page("123")
    handler = page.split('e.target.closest(".bandhd")')[1].split("});")[0]
    assert 'toggleCollapsed("band:"' in handler


# -- the wide card ------------------------------------------------------------


def test_the_panes_ship_in_a_box_the_header_stays_out_of(midway):
    # The split needs somewhere to put the row. Without a box around just the
    # two panes, turning the card into a flex row would take the header and the
    # health bar into the row with them.
    html = render_pressure(midway)
    for pos in TIERS:
        card = _card(html, pos)
        body = card.split('<div class="pbody">')[1]
        assert 'class="ppane runs"' in body
        assert f'data-det="{pos}"' in body
        # The two things that must span the full width are above the box.
        head = card.split('<div class="pbody">')[0]
        assert 'class="phd"' in head
        assert 'class="phealth"' in head


def test_the_card_measures_itself_not_the_window():
    # What makes a card wide is how many of its siblings you folded, and no
    # media query can see that.
    page = render_page("123")
    assert "@container pcard (min-width: 330px)" in page


def test_two_open_cards_split_on_a_laptop_too():
    # Two positions open is the ordinary way this band is read, and on a 1440
    # screen that is ~350px a card -- under the old 520px threshold, so both
    # halves of both cards stayed behind a toggle at exactly the size the split
    # was for. Container queries measure the content box, so a 349px card is 337
    # inside: the threshold has to sit under that and over the ~318 three open
    # cards get on a wide screen, which is the window 330 threads.
    page = render_page("123")
    assert "@container pcard (min-width: 330px)" in page
    assert "@container pcard (min-width: 520px)" not in page
def test_nothing_is_dropped_that_the_hover_does_not_keep(midway):
    # Two columns leave the tier row at width, and the row still has to be able
    # to answer for them -- along with the full name, which the row abbreviates
    # at every size.
    html = render_pressure(midway)
    row = _card(html, "QB").split('class="trow')[1]
    assert "title=" in row.split(">")[0]
    for pr in pressure(midway):
        top = pr.avail[0]
        tip = _card(html, pr.pos).split('class="trow')[1].split('title="')[1].split('"')[0]
        assert top.name.split()[-1] in tip
        assert (top.team or "FA") in tip
        assert f"{midway.points(top):.0f} pts" in tip
def test_containment_never_touches_a_folded_card():
    # `container-type: inline-size` forbids an element sizing to its contents,
    # and a folded card is exactly that -- `flex: 0 0 auto`, a rail as wide as
    # its badge. Contain it and the rail collapses to nothing.
    page = render_page("123")
    assert ".pcard { container-type" not in page
