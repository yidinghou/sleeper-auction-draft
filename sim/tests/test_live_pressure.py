"""Position scarcity: tier cuts, demand, and the tiles the board reorders."""

import json
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


def test_a_finished_seat_dims_out_of_the_grid(midway):
    html = render_pressure(midway)
    dimmed = 0
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1]
        for slot in midway.seats:
            tile = card.split(f'data-seat="{slot}"')[0].rsplit("<span", 1)[1]
            assert ("done" in tile) is (not pr.lines[slot].need)
            dimmed += "done" in tile
    assert dimmed, "no seat had met a target -- the assertion above proved nothing"


def test_te_renders_minor_and_the_rest_carry_a_board_list(midway):
    html = render_pressure(midway)
    te = html.split('data-pos="TE"')[1]
    assert "minor" in html.split('data-pos="TE"')[0].rsplit("<section", 1)[1]
    assert "on the board" not in te
    for pos in ("QB", "RB", "WR"):
        assert "on the board" in html.split(f'data-pos="{pos}"')[1]


def test_the_card_says_how_many_teams_are_short(midway):
    html = render_pressure(midway)
    for pr in pressure(midway):
        card = html.split(f'data-pos="{pr.pos}"')[1]
        assert f"{len(pr.need_seats)} teams still need {pr.pos}" in card


# -- the tier detail ----------------------------------------------------------


def _detail(html: str, pos: str) -> str:
    return html.split(f'data-det="{pos}"')[1].split("</div></div>")[0]


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


# -- opening and closing ------------------------------------------------------


def test_double_click_opens_and_the_open_position_survives_a_refresh():
    page = render_page("123")
    assert 'pressureEl.addEventListener("dblclick"' in page
    # On the container, not in the markup it holds -- that markup is replaced
    # every 2s, and state stored inside it would close the panel twice a second.
    assert "pressureEl.dataset.open = pos" in page


def test_escape_closes_the_panel_before_it_touches_the_board():
    page = render_page("123")
    assert "if (pressureEl.dataset.open) openTier(null); else setMaxed(false);" in page


def test_the_panel_covers_its_own_band_and_not_the_whole_column():
    # The panel is anchored inside `.band`, which is the positioned ancestor --
    # so the block above it and the pool/log below stay visible. It used to be
    # anchored to `.side`, which is what made opening it feel like leaving.
    page = render_page("123")
    assert ".band {" in page and "position: relative;" in page
    assert ".side {" in page
    side = page.split(".side {")[1].split("}")[0]
    assert "position: relative" not in side


# -- collapsing ---------------------------------------------------------------


def test_a_card_advertises_both_of_its_gestures(midway):
    # A gesture leaves nothing on screen saying it exists, so the title is the
    # only place a card can admit what clicking it does.
    html = render_pressure(midway)
    for pos in TIERS:
        tag = html.split(f'data-pos="{pos}"')[1].split(">")[0]
        assert f"click for the {pos} tier" in tag
        assert "double-click to fold" in tag


def test_a_collapsed_card_hands_its_width_to_the_others():
    # Flex, not a grid template: that is what makes collapsing TE widen QB/RB/WR
    # instead of leaving a narrow gap where TE was.
    page = render_page("123")
    assert ".pcard.collapsed { flex: 0 0 auto;" in page.replace("{{", "{")
    assert ".pgrid { display: flex;" in page.replace("{{", "{")


def test_folding_a_card_does_not_open_its_tier_on_the_way_past():
    # A double-click fires two clicks first. Without holding the single-click
    # action, folding a card would open its tier panel every time.
    page = render_page("123")
    opener = page.split('pressureEl.addEventListener("click"')[1].split("});")[0]
    assert "setTimeout(" in opener and "clearTimeout(clickHold)" in opener
    folder = page.split('pressureEl.addEventListener("dblclick"')[1].split("});")[0]
    assert "clearTimeout(clickHold)" in folder
    assert 'toggleCollapsed("pos:"' in folder


def test_a_band_header_folds_on_double_click():
    page = render_page("123")
    handler = page.split('e.target.closest(".bandhd")')[1].split("});")[0]
    assert 'toggleCollapsed("band:"' in handler
