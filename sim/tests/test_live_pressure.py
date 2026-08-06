"""Position scarcity: tier cuts, demand, and the tiles the board reorders."""

import json
import math
import re
from pathlib import Path

import pytest

from draftsim.live_pressure import TIERS, PositionPressure, pressure, split_tier
from draftsim.live_render import (
    _pressure_detail,
    _short_name,
    render_page,
    render_pressure,
)
from draftsim.live_state import DRAFT_TARGETS, reconstruct
from draftsim.sleeper import config_from_draft
from draftsim.valuation import Player, load_players, market_value

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
def mock_config():
    return config_from_draft(_load("draft-mock"))


@pytest.fixture(scope="module")
def midway(mock_config, pool, catalog):
    """The mock rewound to pick 60 -- a board with real runs in progress."""
    picks = sorted(_load("picks-mock"), key=lambda p: p["pick_no"])[:60]
    return reconstruct(picks, mock_config, pool, catalog=catalog)


@pytest.fixture(scope="module")
def finished(mock_config, pool, catalog):
    """The mock played out: rosters are deep, and positions have settled."""
    return reconstruct(_load("picks-mock"), mock_config, pool, catalog=catalog)


def _wr(points: float, name: str = "x") -> Player:
    return Player(id=f"{name}-{points}", name=name, pos="WR", team="NE", points=points)


# -- the tier cut -------------------------------------------------------------


def test_the_cut_falls_to_the_highest_floor_anyone_still_clears():
    # WR floors are 240/205/175/145/120. Nobody is over 240, so the cut is 205
    # and the tier is the two above it -- not everyone left at the position.
    pool = [_wr(230), _wr(210), _wr(190), _wr(150)]
    current, beneath, drop = split_tier("WR", pool)
    assert [p.points for p in current] == [230.0, 210.0]
    assert [p.points for p in beneath] == [190.0, 150.0]
    assert drop == 20  # 210 -> 190, what you fall to when the tier empties


def test_the_cut_follows_the_pool_down_as_the_top_band_drains():
    # The same position once the 205+ band is gone: the cut drops a floor by
    # itself, so the card keeps describing what is actually on the block.
    current, _, _ = split_tier("WR", [_wr(190), _wr(180), _wr(150)])
    assert [p.points for p in current] == [190.0, 180.0]


def test_a_pool_beneath_every_floor_is_one_undifferentiated_bin():
    # Past the last floor nobody is distinguishable by tier, and saying so beats
    # inventing a break. No tier below means no cliff to quote.
    current, beneath, drop = split_tier("WR", [_wr(110), _wr(90)])
    assert [p.points for p in current] == [110.0, 90.0]
    assert beneath == [] and drop == 0


def test_an_empty_pool_is_empty_rather_than_an_error():
    assert split_tier("WR", []) == ([], [], 0)


def test_a_tier_with_nothing_under_it_reports_no_cliff():
    current, beneath, drop = split_tier("WR", [_wr(230), _wr(210)])
    assert len(current) == 2 and beneath == [] and drop == 0


def test_the_cut_only_ever_sees_its_own_position():
    qb = Player(id="q", name="q", pos="QB", team="NE", points=310.0)
    current, _, _ = split_tier("WR", [qb, _wr(230)])
    assert [p.pos for p in current] == ["WR"]


# -- demand -------------------------------------------------------------------


def _one(state, pos: str) -> PositionPressure:
    return next(p for p in pressure(state) if p.pos == pos)


def test_demand_sums_the_fractional_shortfalls(midway):
    # Targets are fractional (WR 3.5), and rounding them here is exactly what
    # made a seat holding three receivers look finished.
    wr = _one(midway, "WR")
    expected = sum(
        max(0.0, DRAFT_TARGETS["WR"] - sum(1 for p in seat.roster if p.pos == "WR"))
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
    assert ".tile.done { background: #e7e7e7; border-color: #dadada; }" in page
    assert "opacity: 0.3" not in page


def test_every_card_carries_a_board_list_tight_end_included(midway):
    # TE used to be cut down to buy the other three width. One starter is still a
    # position you can be run out of, and "who is left at tight end" is a
    # question it asks more sharply than the others, not less.
    html = render_pressure(midway)
    for pos in TIERS:
        assert "on the board" in html.split(f'data-pos="{pos}"')[1]
    assert "minor" not in html


def test_a_tile_never_draws_more_than_the_target_asks_for(finished):
    # Depth is a roster question. Inside a pressure tile a fourth quarterback
    # only made one of twelve tiles wider than its neighbours, and what the run
    # pane wants from that seat is that it has stopped buying -- which the
    # settled tile says. The NEED rows still draw the surplus in full
    # (test_live_state: test_surplus_shows_as_extra_pips_outside_the_run).
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
    assert ".pcard.done { opacity: 0.55; }" in page
    assert ".pcard.done:hover { opacity: 1; }" in page


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
        total = round(DRAFT_TARGETS[pr.pos] * midway.config.teams)
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
        total = round(DRAFT_TARGETS[pr.pos] * midway.config.teams)
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
    assert ".phealth i { display: block; height: 100%; background: var(--pos" in page
    assert ".phealth i.over { background: #ccc; }" in page


def test_the_health_bar_folds_away_with_the_card(midway):
    # Three pixels squeezed onto a rail is a smear, not a reading. It is a
    # sibling of the header rather than part of it, which is what puts it under
    # the rule that empties a folded card.
    page = render_page("123")
    assert ".pcard.collapsed > *:not(.phd) { display: none; }" in page
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
    panel = _pressure_detail(pr)
    assert "nothing below this tier" in panel
    assert "−0" not in panel


def test_an_emptied_tier_says_so():
    pr = PositionPressure(
        pos="WR", avail=[], next_tier=[_wr(120, "c")], cliff_drop=0,
        drafted=0, wanted=4.0, need_seats=[], lines={},
    )
    assert "nobody left in this tier" in _pressure_detail(pr)


def test_the_rows_quote_the_same_dollars_the_block_does(midway):
    # $PROJ is market_value, so the tier list and the nomination strip cannot
    # disagree about what a player is expected to cost.
    pr = next(p for p in pressure(midway) if p.avail)
    panel = _pressure_detail(pr)
    proj = market_value(pr.avail[0])
    assert (f"${proj:.0f}" if proj else "—") in panel


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


def test_escape_has_one_job_again():
    # Nothing covers anything now, so Escape means "leave the maximized board".
    page = render_page("123")
    assert 'if (e.key === "Escape") setMaxed(false);' in page
    assert "openTier" not in page


# -- collapsing ---------------------------------------------------------------


def test_a_card_advertises_its_gesture(midway):
    # The fold is a click on the header, and the header is where the card has to
    # say so -- a title on the whole card promised it worked anywhere on it.
    html = render_pressure(midway)
    for pos in TIERS:
        card = html.split(f'data-pos="{pos}"')[1]
        head = card.split('class="phd"')[1].split(">")[0]
        assert "click to fold" in head


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
    assert ".pcard.collapsed { flex: 0 0 auto;" in page


def test_a_folded_card_still_reports_the_position(midway):
    # Badge, how much of the position is gone, how many are left. Only the pane
    # toggle goes with the fold -- it controls something no longer on screen.
    page = render_page("123")
    assert ".pcard.collapsed .pseg { display: none; }" in page
    assert ".pcard.collapsed .pcount" not in page
    # And the count is per position, against that position's own target.
    html = render_pressure(midway)
    for pr in pressure(midway):
        head = html.split(f'data-pos="{pr.pos}"')[1].split("</div>")[0]
        total = round(DRAFT_TARGETS[pr.pos] * midway.config.teams)
        assert f'<span class="pcount"><b>{pr.drafted}</b>/{total}</span>' in head


def test_the_pane_toggle_does_not_fold_the_card():
    # The toggle is a button inside the header, and the header is the fold. The
    # switch has to claim the click first, or switching a pane would fold the
    # card you were switching.
    page = render_page("123")
    handler = page.split('pressureEl.addEventListener("click"')[1].split("\n});")[0]
    assert handler.index('closest(".pseg button")') < handler.index('closest(".phd")')
    assert 'if (e.target.closest(".pseg")) return;' in handler
    assert 'toggleCollapsed("pos:"' in handler
    # One gesture per action: the card no longer answers a double-click at all.
    assert 'pressureEl.addEventListener("dblclick"' not in page


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
    assert (
        ".pcard:not(.collapsed) { container-type: inline-size; "
        "container-name: pcard; }" in page
    )
    assert "@container pcard (min-width: 520px)" in page


def test_containment_never_touches_a_folded_card():
    # `container-type: inline-size` forbids an element sizing to its contents,
    # and a folded card is exactly that -- `flex: 0 0 auto`, a rail as wide as
    # its badge. Contain it and the rail collapses to nothing.
    page = render_page("123")
    assert ".pcard { container-type" not in page
    assert ".pcard.collapsed { flex: 0 0 auto;" in page


def test_a_wide_card_shows_both_panes_and_drops_the_toggle():
    # The toggle exists because 230px holds one pane at a time. Given room for
    # both, it has nothing left to switch.
    page = render_page("123")
    wide = page.split("@container pcard (min-width: 520px)")[1].split("\n  }")[0]
    assert ".pbody { flex-direction: row;" in wide
    assert ".pcard .pseg { display: none; }" in wide
    assert (
        ".pcard:not(.collapsed) .pbody > .ppane.runs,\n"
        "    .pcard:not(.collapsed) .pbody > .ppane.pdet { display: flex; }" in wide
    )


def test_the_pane_a_card_was_left_on_cannot_skew_the_split():
    # The toggle's rules are four classes -- `.pcard.view-tier .ppane.pdet` sets
    # `flex: 1` -- and so is `.pcard .pbody > .ppane.pdet`. A tie goes to source
    # order, which worked right up until it didn't: a card left on TIER split
    # 5:1 instead of 5:6 and wrapped every name in the tier list onto two lines.
    # `:not(.collapsed)` is the fifth class that wins it outright.
    page = render_page("123")
    wide = page.split("@container pcard (min-width: 520px)")[1].split("\n  }")[0]
    assert ".pcard:not(.collapsed) .pbody > .ppane.runs { flex: 5 1 0; }" in wide
    assert ".pcard:not(.collapsed) .pbody > .ppane.pdet { flex: 6 1 0;" in wide
    # Every rule the block uses to overrule the toggle carries the extra class.
    for line in wide.splitlines():
        if ".ppane.runs" in line and "> .ppane" in line:
            assert ":not(.collapsed)" in line


def test_a_wide_card_stops_printing_the_same_fact_twice(midway):
    # ON THE BOARD is the tier list's first three rows with two columns missing,
    # and `.pfoot` is `.tcliff` without the arrow. Stacked, one summarized a
    # pane you could not see; side by side they are a duplicate.
    page = render_page("123")
    wide = page.split("@container pcard (min-width: 520px)")[1].split("\n  }")[0]
    for dupe in (".ppane.runs .tiles ~ .plbl", ".ppane.runs .bd", ".ppane.runs .pfoot"):
        assert dupe in wide
    # Hidden, not dropped: the markup is the same at both widths, so shrinking a
    # card back brings the board list with it and costs no fetch.
    html = render_pressure(midway)
    for pos in TIERS:
        assert "on the board" in _card(html, pos)


def test_a_wide_card_spends_its_room_on_a_readable_fill():
    # The complaint the split was answering: twelve tiles at 185px apiece, each
    # showing four pixels of roster fill. One breakpoint does this, not a second
    # one further out -- three rails cost ~300px, so a card tops out around 750
    # and a threshold much past the split's would simply never fire.
    page = render_page("123")
    wide = page.split("@container pcard (min-width: 520px)")[1].split("\n  }")[0]
    # The row gets a real height and the pips take what the seat and its money
    # leave, rather than being pinned to a hairline.
    assert ".ppane.runs .tiles { gap: 3px; grid-auto-rows: calc(24px * var(--fs)); }" in wide
    assert ".ppane.runs .pips { flex: 1 1 auto; align-items: stretch; }" in wide
    assert ".ppane.runs .tile .pip { height: auto; }" in wide
    assert ".phealth { height: 5px; }" in wide
    # The check is sized by the same box as the pips it stands in for, so the
    # 4x3 grid cannot reshuffle as seats fill up -- at either width.
    assert ".ppane.runs .tfull { height: auto; flex: 1 1 auto; }" in wide
    # And the narrow ration is untouched, exactly once.
    assert page.count(".tile .pip { height: calc(4px * var(--fs));") == 1
