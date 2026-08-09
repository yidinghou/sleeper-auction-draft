"""Rebuilding league standings from a pick feed, and rendering them."""

import json
import math
import re
from pathlib import Path

import pytest

from draftsim.config import BENCH, DraftConfig
from draftsim.live_render import (
    _NEED_SKIP,
    _short_name,
    render_nomination,
    render_page,
    render_rosters,
    render_settled_lot,
)
from draftsim.live_state import (
    DRAFT_TARGETS,
    contenders,
    position_summary,
    reconstruct,
    seat_value_of,
    spend_by_position,
)
from draftsim.roster import display_slots, starters
from draftsim.sleeper import Nomination, config_from_draft
from draftsim.valuation import Player, load_players

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
def finished(mock_config, pool, catalog):
    return reconstruct(_load("picks-mock"), mock_config, pool, catalog=catalog)


@pytest.fixture(scope="module")
def midway(mock_config, pool, catalog):
    """The same mock rewound to pick 60 -- the state the board exists for."""
    picks = sorted(_load("picks-mock"), key=lambda p: p["pick_no"])[:60]
    return reconstruct(picks, mock_config, pool, catalog=catalog)


# -- a finished draft is the strongest end-state assertion --------------------


def test_every_seat_ends_full_and_solvent(finished, mock_config):
    assert len(finished.seats) == mock_config.teams
    for seat in finished.seats.values():
        assert seat.filled == mock_config.roster_size
        assert seat.open_slots == 0
        assert seat.budget_left >= 0
        assert seat.max_bid == 0  # no room left to bid into


def test_league_spend_never_exceeds_the_pooled_budget(finished, mock_config):
    spent = sum(seat.spent for seat in finished.seats.values())
    assert 0 < spent <= mock_config.budget * mock_config.teams
    assert spent == sum(pick.price for pick in finished.picks)


def test_every_pick_resolves_to_a_projection(finished):
    # The full sheet, not the draftable pool, is what picks resolve against --
    # seats really do spend $1 on unsigned free agents.
    assert finished.unknown_player_ids == []
    assert all(pick.player.name for pick in finished.picks)


def test_drafted_players_leave_the_available_pool(finished, pool):
    assert len(finished.available) == len(pool) - len(
        {pick.player.id for pick in finished.picks} & {p.id for p in pool}
    )
    drafted = {pick.player.id for pick in finished.picks}
    assert not any(p.id in drafted for p in finished.available)


# -- mid-draft is where the numbers have to be right -------------------------


def test_midway_seats_have_money_and_room(midway, mock_config):
    assert sum(s.filled for s in midway.seats.values()) == 60
    assert any(s.open_slots > 0 for s in midway.seats.values())
    for seat in midway.seats.values():
        assert seat.budget_left == mock_config.budget - seat.spent


def test_max_bid_reserves_a_dollar_for_every_other_open_slot(midway):
    for seat in midway.seats.values():
        assert seat.max_bid == max(0, seat.budget_left - (seat.open_slots - 1))
        assert seat.max_bid <= seat.budget_left


def test_needs_shrink_as_a_seat_fills(midway, finished):
    def outstanding(state):
        return sum(sum(s.needs.values()) for s in state.seats.values())

    assert outstanding(midway) > outstanding(finished) == 0


def test_spend_by_position_totals_the_feed(midway):
    by_pos = spend_by_position(midway)
    assert sum(by_pos.values()) == sum(p.price for p in midway.picks)


# -- valuation wiring --------------------------------------------------------


def test_a_seat_that_cannot_start_a_player_values_them_at_zero(midway):
    seat = next(iter(midway.seats.values()))
    # A kicker has no startable slot in this lineup, so no seat can ever gain
    # from one -- the check that "value" means lineup gain, not raw points.
    kicker = Player(id="k", name="Kicker", pos="K", team="KC", points=200.0)
    assert seat_value_of(midway, seat, kicker) == 0.0


def test_a_strong_player_is_worth_points_to_a_seat_with_room(midway):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    seat = next(s for s in midway.seats.values() if s.needs.get("WR", 0) > 0)
    assert seat_value_of(midway, seat, star) > 0.0


def test_contenders_are_seats_that_can_both_pay_and_play(midway):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    shortlist = contenders(midway, star)
    assert shortlist
    assert all(s.max_bid > 0 for s in shortlist)
    reaches = [s.max_bid for s in shortlist]
    assert reaches == sorted(reaches, reverse=True)


def test_nobody_contends_for_a_player_nobody_can_start(midway):
    kicker = Player(id="k", name="Kicker", pos="K", team="KC", points=200.0)
    assert contenders(midway, kicker) == []


def test_a_finished_league_has_no_contenders(finished):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    assert contenders(finished, star) == []


# -- feed shapes that would otherwise fail silently --------------------------


def test_an_empty_feed_is_a_full_board_of_untouched_seats(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    assert len(state.seats) == mock_config.teams
    for seat in state.seats.values():
        assert seat.spent == 0
        assert seat.budget_left == mock_config.budget
        assert seat.max_bid == mock_config.budget - (mock_config.roster_size - 1)


def test_a_pick_from_an_impossible_seat_is_an_error(pool):
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "WR", "BN"))
    picks = [{"pick_no": 1, "draft_slot": 9, "player_id": "4984", "metadata": {}}]
    with pytest.raises(ValueError, match="outside 1..2"):
        reconstruct(picks, config, pool)


def test_an_unknown_player_still_costs_money_and_a_slot(mock_config, pool):
    picks = [
        {
            "pick_no": 1,
            "draft_slot": 1,
            "player_id": "999999",
            "metadata": {
                "first_name": "Ghost",
                "last_name": "Player",
                "position": "WR",
                "team": "KC",
                "amount": "23",
            },
        }
    ]
    state = reconstruct(picks, mock_config, pool)
    seat = state.seats[1]
    assert state.unknown_player_ids == ["999999"]
    assert seat.spent == 23
    assert seat.filled == 1
    assert seat.roster[0].name == "Ghost Player"
    assert seat.roster[0].points == 0.0


def test_a_pick_with_no_price_is_free_not_a_crash(mock_config, pool):
    picks = [
        {"pick_no": 1, "draft_slot": 1, "player_id": "4984", "metadata": {}},
    ]
    state = reconstruct(picks, mock_config, pool)
    assert state.seats[1].spent == 0
    assert state.seats[1].filled == 1


# -- the per-seat summary ----------------------------------------------------


def test_summary_targets_are_the_draft_plan_not_the_slot_count(midway):
    # The pane answers "should this seat buy another receiver?", which is a
    # draft target, not a legality check -- so it deliberately parts company
    # with seat.needs, which counts what makes a lineup legal.
    for seat in midway.seats.values():
        for line in position_summary(seat, midway.config):
            if line.pos in DRAFT_TARGETS:
                assert line.want == DRAFT_TARGETS[line.pos]


def test_a_position_with_no_target_falls_back_to_the_slot_count(midway):
    # DEF has no entry in the plan. It must still report a target rather than
    # silently reading as "wants none" beside the positions that have one.
    counts = midway.config.starter_counts()
    for seat in midway.seats.values():
        for line in position_summary(seat, midway.config):
            if line.pos not in DRAFT_TARGETS:
                assert line.want == counts.get(line.pos, 0)


def test_targets_stay_fractional_all_the_way_to_the_line(midway):
    # Rounding 2.5 up to 3 is what made a seat holding two and a flex read as
    # short; the half has to survive into the PositionLine.
    assert any(t % 1 for t in DRAFT_TARGETS.values())
    for seat in midway.seats.values():
        lines = {line.pos: line for line in position_summary(seat, midway.config)}
        for pos, target in DRAFT_TARGETS.items():
            if pos in lines:
                assert lines[pos].want == pytest.approx(target)


def test_summary_points_add_up_to_the_starting_lineup(midway):
    for seat in midway.seats.values():
        lineup = [p for p in starters(seat.roster, midway.config) if p is not None]
        total = sum(line.starter_points for line in position_summary(seat, midway.config))
        assert total == pytest.approx(sum(p.points for p in lineup))


def test_summary_counts_bench_depth_as_have_but_never_as_need(finished):
    seat = finished.seats[1]
    lines = {line.pos: line for line in position_summary(seat, finished.config)}
    for pos, line in lines.items():
        assert line.have == sum(1 for p in seat.roster if p.pos == pos)
    # A finished roster is deeper than its target at some position, and depth
    # must never read as a hole.
    assert any(line.have > line.want for line in lines.values())
    assert all(line.need == 0 for line in lines.values() if line.have >= line.want)
    # A full roster can still miss the plan -- sixteen bodies bought without a
    # third quarterback is a legal lineup and a missed target, and the pane is
    # the thing that says so. Legality is seat.needs' job, and it is zero here.
    assert sum(seat.needs.values()) == 0


def test_a_position_nobody_starts_or_owns_is_left_out(midway):
    # The default lineup has no kicker slot, and the plan names no kicker
    # target, so K is noise until someone drafts one -- at which point the body
    # has to show up somewhere.
    for seat in midway.seats.values():
        positions = {line.pos for line in position_summary(seat, midway.config)}
        has_kicker = any(p.pos == "K" for p in seat.roster)
        assert ("K" in positions) == has_kicker


def test_an_empty_roster_summarizes_as_all_holes(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    lines = position_summary(state.seats[1], mock_config)
    assert [(l.pos, l.have, l.want) for l in lines] == [
        ("QB", 0, 3.0), ("RB", 0, 2.5), ("WR", 0, 3.5), ("TE", 0, 1.0),
        ("DEF", 0, 1.0),
    ]
    assert all(line.starter_points == 0.0 for line in lines)
    assert all(line.need == line.want for line in lines)


# -- rendering ---------------------------------------------------------------


def test_nomination_strip_names_the_player_and_the_bid(midway):
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=17, offering_slot=8
    )
    html = render_nomination(midway, nom, star)
    assert "Star Guy" in html
    assert "$17" in html
    assert "$44" in html  # the crowd's price, to bid against
    assert ">S8<" in html  # who holds it, by the same label the board uses
    assert "nom. S3" in html
    # Called without the poller's memory, the panel still cannot claim "no bids
    # yet" over a price and a seat printed one line above it: the two slots the
    # draft itself publishes are what it has, and it shows them.
    assert "no bids yet" not in html
    assert html.count('<span class="rung') == 2


def test_the_two_figures_share_one_column(midway):
    """$PROJ and the live bid are the only numbers you act on, and the read is
    the gap between them -- which only exists if they are stacked in the same
    fixed-width cell. Separately positioned, they are two facts; in a column,
    they are one."""
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=17, offering_slot=8
    )
    html = render_nomination(midway, nom, star)
    money = html.split('class="onmoney"')[1].split("</div></div>")[0]
    assert money.index("$44") < money.index("$17")  # opinion above fact
    assert money.count('class="figv') == 2
    # The dot between the facts is drawn, not typed. As a `·` character it sat
    # on the text baseline and inherited its neighbours' leading, which is what
    # made the old line wander; an empty element cannot.
    assert '<span class="onsep"></span>' in html
    assert "·" not in html.split('class="onsub"')[1].split("</div>")[0]


def test_a_teamless_player_does_not_draw_a_dangling_separator(midway):
    """Free agents carry no team. The dot between the facts is drawn rather than
    typed, so an empty span between two of them is a visible dot with nothing on
    one side -- which is what the old `·` run did too, less noticeably."""
    fa = Player(
        id="fa", name="Free Agent", pos="RB", team="", points=12.0,
        proj_dollar=1,
    )
    nom = Nomination(player_id="fa", nominating_slot=3, high_bid=1, offering_slot=3)
    sub = render_nomination(midway, nom, fa).split('class="onsub"')[1]
    sub = sub.split("</div>")[0]
    assert sub.count('class="onsep"') == 1  # pts | nom, and nothing before pts
    assert not sub.lstrip('">').startswith('<span class="onsep"')


def test_a_bid_of_nothing_is_not_money_green(midway):
    """At 20.8px the em dash in #1a7f37 reads as a filled bar -- a small green
    amount rather than the absence of one."""
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=None, offering_slot=None
    )
    html = render_nomination(midway, nom, star)
    assert 'class="figv bidamt none">—<' in html
    assert ".bidamt.none { color: #949494" in render_page("123")


def test_the_block_reads_the_division_round_off_season_pace(midway):
    """The three early weeks are why you would pay up for a body you otherwise
    rate the same as the next one, so they belong on the panel you bid from --
    coloured by the same rule as the pool's columns, since a week called out
    while you were scanning has to still be called out once he is on the block.

    Star Guy paces 400/17 = 23.5: week 1 clears the 15% margin, week 2 sits
    inside it, week 3 falls through the bottom of it.
    """
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44, week1=30.0, week2=24.0, week3=15.0,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=17, offering_slot=8
    )
    strip = render_nomination(midway, nom, star).split('class="onwk"')[1]
    strip = strip.split("</div>")[0]
    assert 'class="onwkv g">30.0<' in strip
    assert 'class="onwkv">24.0<' in strip
    assert 'class="onwkv r">15.0<' in strip
    # Each figure names itself: this panel holds one player and has no header
    # row to take WK1/WK2/WK3 from, unlike the pool.
    assert [n for n in ("WK1", "WK2", "WK3") if n in strip] == ["WK1", "WK2", "WK3"]


def test_the_block_draws_no_week_strip_without_a_read(midway):
    """A CSV predating the week columns leaves all three None. Three dashes
    would take the height without answering anything, so the strip stays away
    rather than standing in for a read nobody has."""
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=17, offering_slot=8
    )
    assert 'class="onwk"' not in render_nomination(midway, nom, star)
    # Nor for a lot that is not on the sheet at all -- there is no player to
    # take weeks from, and the panel already says so.
    off_sheet = Nomination(
        player_id="99999", nominating_slot=1, high_bid=3, offering_slot=1
    )
    assert 'class="onwk"' not in render_nomination(midway, off_sheet, None)


def test_nomination_strip_is_idle_between_lots(midway):
    nom = Nomination(
        player_id=None, nominating_slot=None, high_bid=None, offering_slot=None
    )
    html = render_nomination(midway, nom, None)
    assert "Nothing nominated" in html


def test_nomination_strip_survives_a_player_off_the_sheet(midway):
    nom = Nomination(
        player_id="99999", nominating_slot=1, high_bid=3, offering_slot=1
    )
    html = render_nomination(midway, nom, None)
    assert "not in projections" in html
    assert "$3" in html


def test_settled_lot_shows_the_pick_that_just_landed(midway):
    # `midway` is rewound to pick 60 -- TreVeyon Henderson, to S12, for $16.
    html = render_settled_lot(midway)
    assert "TreVeyon Henderson" in html
    assert "won by S12" in html
    assert "$16" in html
    assert html.count('class="rung sold"') == 1
    # Unlike a live lot, there is exactly one rung: who else was in the
    # bidding on a closed lot is memory only the live poller ever had.
    assert html.count('<span class="rung') == 1
    assert "no bids yet" not in html


def test_settled_lot_is_idle_before_the_first_pick(mock_config, pool, catalog):
    empty = reconstruct([], mock_config, pool, catalog=catalog)
    html = render_settled_lot(empty)
    assert "Nothing nominated" in html
    assert "bid-high" not in html


def test_page_shell_is_a_full_html_document():
    page = render_page("123")
    assert page.startswith("<!doctype html>")
    assert "/api/state" in page
    assert "123" in page


# -- roster cards ------------------------------------------------------------


def test_every_seat_gets_a_roster_card(midway):
    html = render_rosters(midway)
    for slot in midway.seats:
        assert f'data-seat="{slot}"' in html


def test_cards_are_in_seat_order_not_reach_order(midway):
    # The table sorts by reach; these are read to look a seat up, and a grid
    # that reshuffles on every bid is useless for that.
    html = render_rosters(midway)
    order = [int(c.split('"')[0]) for c in html.split('data-seat="')[1:]]
    assert order == sorted(midway.seats)


def test_a_card_shows_each_player_with_price_points_and_bye(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card
        assert f"${pick.price}" in card
        if pick.player.bye:
            assert f">{pick.player.bye}<" in card


def test_a_card_leads_with_money_and_reach(midway):
    # The header answers "can this seat outbid me" and nothing else: what is
    # left, and the most of it that can go on one player.
    seat = next(s for s in midway.seats.values() if s.roster)
    header = _card(midway, seat.slot).split("</header>")[0]
    assert f'class="big">${seat.budget_left}<' in header
    assert f"<b>${seat.max_bid}</b>" in header


def test_the_header_carries_no_points_or_slot_counts(midway):
    # Both were read occasionally and competed with the two numbers read
    # constantly, so the header gave them up to the fold strip below it.
    for slot in midway.seats:
        header = _card(midway, slot).split("</header>")[0]
        assert "pts" not in header
        assert "open" not in header


def test_a_card_totals_what_its_starters_project(midway):
    # The one number that says whether the money bought anything. Starters only,
    # off the same optimal lineup the LINEUP pane draws, so the footer cannot
    # disagree with the rows above it.
    for seat in midway.seats.values():
        card = _card(midway, seat.slot)
        assert 'class="proj"' in card
        total = sum(p.points for p in starters(seat.roster, midway.config) if p)
        figure = card.split('class="proj"')[1].split("<b>")[1].split("</b>")[0]
        assert figure == f"{total:,.0f}"


def test_a_card_counts_the_starters_it_has_bought(midway):
    # How much of the lineup is bought, next to what it projects. Defenses and
    # kickers are left out, the same ones `_NEED_SKIP` drops: bought once,
    # late, by everyone, so counting them only makes the same denominator mean
    # different things on different cards.
    wanted = [s for s in midway.config.starter_slots if s not in _NEED_SKIP]
    for seat in midway.seats.values():
        foot = _card(midway, seat.slot).split('class="proj"')[1]
        have = sum(
            1
            for slot, player in display_slots(seat.roster, midway.config)
            if slot != BENCH and slot not in _NEED_SKIP and player
        )
        assert f">{have}<i>/{len(wanted)}</i>" in foot
        assert ">STARTERS<" in foot
    # A ratio says what it is and a bare 1,323 does not, so compact labels only
    # the projection and the word comes back with the overlay.
    page = render_page("123")
    assert ".proj .str { display: none; }" in page
    assert "body.maxed .proj .str { display: inline; }" in page


def test_a_lineup_with_a_hole_in_it_says_so(midway):
    # The count is only worth a line if a short one is legible without being
    # read: amber, and not the out-of-market red, which would be on eleven of
    # twelve cards all draft and mean nothing by the time it mattered.
    cards = [_card(midway, slot) for slot in midway.seats]
    assert any('class="fill short"' in c for c in cards)
    assert ".proj .fill.short { color: #b26a00; }" in render_page("123")


def test_an_empty_seat_projects_nothing_rather_than_zero(mock_config, pool):
    # A seat that has bought nothing has no projection; a bold 0 reads as a
    # measurement rather than as absence.
    state = reconstruct([], mock_config, pool)
    card = render_rosters(state).split('data-seat="1"')[1].split("</section>")[0]
    assert "<b>—</b>" in card.split('class="proj"')[1]


def test_the_points_total_sits_below_the_panes_not_in_the_header(midway):
    # The header is money and nothing else, and below the panes the total is the
    # same number whichever pane happens to be showing.
    card = _card(midway, next(iter(midway.seats)))
    assert 'class="proj"' not in card.split("</header>")[0]
    assert card.index("</div>") < card.index('class="proj"')


def test_a_folded_card_drops_its_points_total(midway):
    # Folded, a card is a strip of money and pips -- the fold summary owns that
    # height.
    assert ".card.collapsed .proj { display: none; }" in render_page("123")


def test_the_budget_bar_splits_spendable_from_reserved(midway):
    # A dollar per unfilled slot is in the account but already owed; a seat with
    # $80 and eight holes is not the threat its balance says it is.
    for seat in midway.seats.values():
        bar = _card(midway, seat.slot).split('class="budget"')[1].split("</div>")[0]
        widths = [float(w.split("%")[0]) for w in bar.split("width:")[1:]]
        assert len(widths) == 2
        assert sum(widths) <= 100.0 + 1e-6
        held = max(0, seat.open_slots - 1)
        assert widths[1] == pytest.approx(100 * held / midway.config.budget, abs=0.1)


def test_a_seat_out_of_the_market_is_flagged(mock_config, pool):
    # A max bid that cannot win anyone is a seat you have stopped bidding
    # against, and the card says so rather than leaving you to notice.
    state = reconstruct([], mock_config, pool)
    rich = render_rosters(state)
    assert 'class="card broke"' not in rich
    for seat in state.seats.values():
        seat.max_bid = 3
    assert render_rosters(state).count('class="card broke"') == mock_config.teams


def test_unfilled_starter_slots_are_shown(midway):
    seat = next(s for s in midway.seats.values() if s.open_slots > 6)
    card = _card(midway, seat.slot)
    # A hole at a starting slot is the point of the card, so it stays visible.
    assert 'class="ln open"' in card


def test_empty_rosters_still_render_their_shape(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    html = render_rosters(state)
    assert html.count("data-seat=") == mock_config.teams
    # Every seat shows the full lineup shape, all of it empty.
    assert html.count('class="ln open"') == mock_config.teams * len(
        [s for s in mock_config.roster_slots if s != "BN"]
    )


def test_a_seat_with_no_bench_says_so_rather_than_showing_a_blank_pane(
    mock_config, pool
):
    state = reconstruct([], mock_config, pool)
    assert render_rosters(state).count('class="empty"') == mock_config.teams


def test_rows_carry_position_colour_and_price(midway):
    from draftsim.theme import POS_COLOR_LIGHT

    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    # Position reads as the row's own tint, not a badge -- so no badge markup,
    # but every drafted player's colour must be present to mix that tint from.
    assert 'class="badge"' not in card
    for pick in seat.picks:
        assert f"--pos:{POS_COLOR_LIGHT[pick.player.pos]}" in card
        assert f"${pick.price}" in card


def test_the_row_is_tinted_and_the_slot_label_is_not(midway):
    # The colour is the field the name sits on. A coloured chip put the loudest
    # thing in the row right beside the thing you actually read.
    page = render_page("123")
    assert "background: color-mix(in srgb, var(--pos, #7c90a0) 26%, #fff)" in page
    label = page.split(".ms {")[1].split("}")[0]
    assert "var(--pos" not in label  # same grey on every row


def test_an_empty_slot_reads_as_absence_not_as_a_position(midway):
    # A hole is flat grey: tinting it would say "receiver" about a seat that
    # has no receiver.
    page = render_page("123")
    assert ".ln.open { background: #f4f4f4; }" in page


def test_one_player_per_line(midway):
    # The old card paired two players to a row; a row is one player now, so the
    # lineup pane has exactly one line per starting slot.
    seat = next(s for s in midway.seats.values() if s.roster)
    lineup = _pane(midway, seat.slot, "lineup")
    starter_slots = len(midway.config.starter_slots)
    assert lineup.count('<div class="ln') == starter_slots + 1  # + column labels


def test_bench_is_its_own_pane_labelled_by_real_position(finished):
    # In a pane of its own nothing else says what these players are, so the chip
    # is the player's position rather than a fungible BN.
    seat = finished.seats[1]
    bench = _pane(finished, 1, "bench")
    starting = {id(p) for p in starters(seat.roster, finished.config) if p}
    depth = [p for p in seat.roster if id(p) not in starting]
    assert bench.count('<div class="ln bench"') == len(depth)
    assert f">{BENCH}</i>" not in bench
    for player in depth:
        assert f'class="ms">{player.pos}<' in bench


def test_bench_rows_keep_their_position_tint_but_sit_back():
    page = render_page("123")
    # Depth is worth seeing -- it keeps the position tint, since in a pane of
    # its own nothing else says what these bodies are -- but a step back from
    # the lineup. The old "hidden until maximized" behaviour must not come back.
    assert ".ln.bench { opacity: 0.75; }" in page
    assert ".ln.bench { display: none" not in page


def test_board_controls_sit_in_a_menu_bar_over_the_grid():
    page = render_page("123")
    # The controls belong to the board, so they live above it rather than across
    # the aisle in the state column -- and they stay reachable when the board
    # takes the whole viewport.
    bar = page.index('class="menubar"')
    assert page.index('class="board"') < bar < page.index('id="rosters"')
    assert bar < page.index('id="max"') < page.index('id="rosters"')
    assert bar < page.index('id="reorder"') < page.index('id="rosters"')
    # The grid takes what the bar leaves rather than a flat 100% of the column,
    # which would have pushed the bottom row of cards off screen. Read as two
    # declarations rather than one exact string: the rule has picked up padding
    # since, and what this is pinning is the sizing, not the whole rule.
    rosters = page.split("#rosters {")[1].split("}")[0]
    assert "flex: 1" in rosters
    assert "min-height: 0" in rosters


def test_the_grid_is_four_across_three_down_in_both_sizes():
    # Twelve seats either way; a row per starter wants height, so the card is
    # wider than it is tall. Maximizing keeps the shape -- a card must be where
    # it was, only bigger -- and all twelve still fit one screen.
    page = render_page("123")
    grid = page.split(".grid {")[1].split("}")[0]
    maxed = page.split("body.maxed .grid {")[1].split("}")[0]
    assert "repeat(4, minmax(0, 1fr))" in grid
    assert "repeat(4, minmax(0, 1fr))" in maxed
    assert "height: 100%" in maxed  # rows share the viewport, nothing scrolls off


def test_lineup_rows_share_the_card_rather_than_a_fixed_height():
    # A fixed row height had to be tuned to one viewport: tall enough to fill a
    # big screen, it scrolled a short one. The rows flex instead, so the same
    # card fills a 1000px window and still fits an 820px one.
    page = render_page("123")
    assert ".pane.lineup .ln { flex: 1 1 auto; }" in page


def test_the_live_board_is_light_and_the_report_stays_dark():
    # The board sits open beside Sleeper's dark app for three hours; looking
    # unlike it is the point. The report is a different surface and keeps the
    # dark palette, so the two must not silently re-converge on one theme.
    from draftsim import theme

    page = render_page("123")
    assert theme.BASE_CSS_LIGHT in page
    assert theme.BASE_CSS not in page
    assert f"background: {theme.L_PAGE}" in page
    assert theme.BG not in page  # no stray dark-palette page background


def test_the_current_pane_stays_legible_when_maximized():
    # The pressed segment is dark-on-light; a blanket recolour in the overlay
    # would paint its label the same colour as the pill under it.
    page = render_page("123")
    seg = page.split('.seg button[aria-pressed="true"] {')[1].split("}")[0]
    assert "#fff" in seg and "#1a1a1a" in seg  # dark fill, light label


def test_page_splits_state_left_from_rosters_right():
    page = render_page("123")
    assert 'id="max"' in page
    assert "body.maxed" in page
    # The nomination strip and controls live in the left column; the roster
    # grid gets its own column so it can take the full viewport height.
    assert 'class="side"' in page
    assert 'class="board"' in page
    assert page.index('class="side"') < page.index('class="board"')


# -- the three panes ---------------------------------------------------------


def _card(state, slot):
    card = render_rosters(state).split('data-seat="%d"' % slot)[1]
    return card.split("</section>")[0]


def _pane(state, slot, pane):
    """One pane's markup. The pane name appears twice in a card -- on the button
    that picks it and on the pane itself -- so match the pane, not the first hit.
    """
    chunks = _card(state, slot).split('<div class="pane')[1:]
    return next(c for c in chunks if f'data-pane="{pane}"' in c)


def test_every_card_carries_both_panes_and_the_control_between_them(midway):
    html = render_rosters(midway)
    teams = len(midway.seats)
    for pane in ("lineup", "bench"):
        # Once as the button that picks it, once as the pane itself.
        assert html.count(f'data-pane="{pane}"') == teams * 2
    assert html.count('class="seg"') == teams


def test_the_panes_ship_together_so_switching_needs_no_fetch(midway):
    # Both panes in one card is what makes switching free: no fetch, so the two
    # can never show different moments of the draft.
    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    assert 'class="pane lineup"' in card
    assert 'data-pane="bench"' in card
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card
def test_the_lineup_pane_still_shows_what_the_summaries_drop(finished):
    # `_NEED_SKIP` keeps defenses and kickers out of the fold strip and the
    # starter count, and dropping them there must not lose them: a full
    # roster's defense is still in the pane that lists every slot.
    assert ">DEF</i>" in _pane(finished, 1, "lineup")
def test_an_unfilled_pip_is_neutral_not_a_tint_of_the_position():
    # Tinted, a slot you do not own was still QB pink or WR blue, and the two
    # ends of a row differed by saturation -- which reads as "a bit filled", and
    # reads differently at each position, because the four are not equally light.
    # The position colour now means one thing: you own this one.
    page = render_page("123")
    base = page.split("  .pip {")[1].split("}")[0]
    assert "background: #ececec;" in base
    assert "var(--pos" not in base
    assert ".pip.on { background: var(--pos, #7c90a0); box-shadow: none; }" in page
    # Surplus is a slot you own, so it keeps the accent -- outlined, past the run.
    assert "var(--pos" in page.split("  .pip.extra {")[1].split("}")[0]
def test_pane_state_is_client_side_and_survives_a_refresh():
    page = render_page("123")
    # Lineup is what the board loads on; a card with no stored choice shows it.
    assert ".card .pane.lineup { display: flex;" in page
    assert '.card.view-bench .pane[data-pane="bench"]' in page
    # The cards are replaced every tick, so the choice must be re-applied after
    # each swap and the handler must be delegated, not per-button.
    assert "applyViews()" in page
    assert 'getElementById("rosters").addEventListener("click"' in page
    assert "draftsim.views" in page
    # The pressed segment is re-marked too, since the buttons are new markup.
    assert 'b.setAttribute("aria-pressed"' in page


# -- dragging cards into a different order -----------------------------------


def test_cards_are_draggable(midway):
    html = render_rosters(midway)
    assert html.count('draggable="true"') == len(midway.seats)


def test_dragging_does_not_change_what_the_server_sends(midway):
    # Order is presentational -- CSS `order` over markup that stays in seat
    # order, so the server has no idea the board was rearranged and seat order
    # is always recoverable.
    page = render_page("123")
    # Applied by attribute, not by card class: the roster cards and the pressure
    # tiles are both keyed by seat, and one array has to move both.
    assert 'querySelectorAll("[data-seat]")' in page
    assert "el.style.order" in page
    assert "draftsim.order" in page
    order = [int(c.split('"')[0]) for c in render_rosters(midway).split('data-seat="')[1:]]
    assert order == sorted(midway.seats)


def test_a_drop_inserts_and_shifts_the_rest_down():
    page = render_page("123")
    # Remove then re-insert at the target's index: that is what makes the rest
    # shift down instead of the two cards swapping places.
    assert "order.splice(order.indexOf(dragging), 1)" in page
    assert "order.splice(order.indexOf(target), 0, dragging)" in page


def test_the_refresh_holds_still_while_a_card_is_in_hand():
    # The board polls every 2s; swapping #rosters mid-drag would delete the
    # element being dragged and the drop would land nowhere.
    page = render_page("123")
    assert "if (dragging === null &&" in page
    assert "rosters.innerHTML = s.rosters_html;" in page


def test_the_refresh_holds_still_across_a_double_click_too():
    # A double-click is two clicks on the same element. A swap between them
    # replaces that element and the gesture is eaten, so a press on a foldable
    # header buys the same pause a drag does.
    page = render_page("123")
    assert "Date.now() - heldAt > 500" in page
    assert 'addEventListener("mousedown"' in page


def test_a_saved_order_is_reconciled_against_the_seats_on_the_board():
    page = render_page("123")
    # A stored order from a different league size must not hide a seat.
    assert "function normalizeOrder()" in page
    assert "if (!order.includes(slot)) order.push(slot);" in page


def test_there_is_a_way_back_to_seat_order():
    page = render_page("123")
    assert 'id="reorder"' in page
    assert 'localStorage.removeItem("draftsim.order")' in page


# -- folding a row -----------------------------------------------------------


def test_every_card_ships_the_strip_it_folds_down_to(midway):
    # Shipped in the markup and shown by CSS, the same bargain the three panes
    # make: folding costs no fetch and cannot show a different moment of the
    # draft than the pane it replaced.
    html = render_rosters(midway)
    assert html.count('class="foldsum"') == len(midway.seats)


def _strip(state, slot):
    return _card(state, slot).split('class="foldsum"')[1].split("</div>")[0]


def _pips(markup):
    """Pips in a chunk of strip -- the `pips` wrapper is not one of them."""
    return len(re.findall(r'class="pip[ "]', markup))


def test_the_strip_says_what_is_filled_and_what_is_not(midway):
    # Every position `position_summary` reports except the two `_NEED_SKIP`
    # drops, drawn as pips. What the run counts against is a separate question,
    # on purpose; see below.
    strip = _strip(midway, 1)
    for line in position_summary(midway.seats[1], midway.config):
        if line.pos in _NEED_SKIP:
            assert f">{line.pos}<" not in strip
        else:
            assert f">{line.pos}<" in strip
    assert 'class="pips"' in strip


def test_the_strips_run_is_the_slots_a_position_owns_outright(mock_config, pool):
    # Folded, a card is read off rather than shopped for: the question is what a
    # seat *has*, so the run is `owned_starters()` -- 2 QB / 2 RB / 3 WR / 1 TE,
    # the whole slots the lineup seats no matter what -- not the fractional
    # DRAFT_TARGETS a draft plan buys against.
    assert mock_config.owned_starters() == {
        "QB": 2, "RB": 2, "WR": 3, "TE": 1, "DEF": 1, "K": 0
    }
    state = reconstruct([], mock_config, pool)
    strip = _strip(state, 1)
    runs = dict(zip(re.findall(r">(\w+)</i>", strip), strip.split('class="pips"')[1:]))
    for pos, want in (("QB", 2), ("RB", 2), ("WR", 3), ("TE", 1)):
        assert _pips(runs[pos]) == want
    # An empty seat is one line: nothing is owned, so nothing is depth.
    assert "fx" not in strip
    assert '<span class="pip half' not in strip  # whole slots, so no half pip


def test_depth_stacks_under_its_position_rather_than_widening_the_run(finished):
    # Inline, a surplus pip made every position a different width and pushed the
    # tight end onto a row of its own -- so which positions a card still had to
    # fill stopped being readable across four folded cards at once. Stacked, line
    # one is the same eight slots in the same eight places on every card.
    seat = next(
        s
        for s in finished.seats.values()
        if any(
            line.have > finished.config.owned_starters().get(line.pos, 0)
            for line in position_summary(s, finished.config)
            if line.pos not in _NEED_SKIP
        )
    )
    strip = _strip(finished, seat.slot)
    owned = finished.config.owned_starters()
    for line in position_summary(seat, finished.config):
        if line.pos in _NEED_SKIP:
            continue
        cell = next(c for c in strip.split('class="fpos"')[1:] if f">{line.pos}<" in c)
        run, _, extra = cell.partition('class="pips fx"')
        # The run is exactly the slots owned, however many bodies are on it.
        assert _pips(run) == owned[line.pos]
        assert extra.count('class="pip extra"') == max(0, line.have - owned[line.pos])


def test_a_depth_line_holds_twice_the_slots_it_sits_under(finished):
    # A depth line holds twice the slots it sits under, so five running backs fit
    # the one line beneath two RB slots. A position only takes a third line when
    # a seat holds more than twice what it can start, which is deeper than seats
    # go: every position of a finished 16-man roster is at most two lines.
    owned = finished.config.owned_starters()
    for seat in finished.seats.values():
        strip = _strip(finished, seat.slot)
        for line in position_summary(seat, finished.config):
            if line.pos in _NEED_SKIP:
                continue
            cell = next(c for c in strip.split('class="fpos"')[1:] if f">{line.pos}<" in c)
            depth = line.have - owned[line.pos]
            assert cell.count('class="pips fx"') == math.ceil(
                max(0, depth) / (2 * owned[line.pos])
            )
        assert strip.count('class="pips fx"') <= 4  # one per position, no more


def test_the_strip_stacks_its_two_lines_rather_than_running_them_together():
    page = render_page("123")
    assert ".foldsum .fstack { display: flex; flex-direction: column;" in page


def test_folding_is_by_row_because_a_lone_card_frees_nothing():
    # Four cards side by side share a row's height: fold one and its neighbours
    # still need the room. A row is the smallest thing whose height can move --
    # so the filter's unit is a row, and it is what the fold is keyed by.
    page = render_page("123")
    assert 'localStorage.setItem("draftsim.rowsel", JSON.stringify(rowsel));' in page
    assert "const shut = foldable && !rowsel.includes(r);" in page
    assert "function applyRows()" in page
    assert "applyRows();" in page.split("function applyCollapsed()")[1]
    # Explicit rows, or `grid-auto-rows: minmax(0, 1fr)` hands a folded row its
    # full third back however little is left in it.
    assert "grid.style.gridTemplateRows" in page


def test_nothing_folds_in_the_overlay_and_the_folds_survive_it():
    # Maximized, the board is the whole viewport and twelve open cards fit it,
    # so a folded row would be putting away something there was room for.
    # Folding buys height on the compact board and there is none to buy here.
    page = render_page("123")
    # `applyRows` ignores the stored folds rather than clearing them, so
    # minimizing comes back to the board you left.
    assert 'const foldable = !document.body.classList.contains("maxed");' in page
    assert "const shut = foldable && !rowsel.includes(r);" in page
    # And it reruns on the toggle, or the overlay would open onto folded rows.
    assert "applyRows();" in page.split("function setMaxed(")[1].split("}")[0]
    # The filter says so rather than recording a fold you would only see on
    # minimizing -- and it stays put while it says it, since a control that
    # vanishes when you maximize is one you go looking for afterwards.
    assert "b.disabled = !foldable;" in page
    assert ".seg button:disabled { color: #ccc;" in page


def test_an_open_row_takes_what_the_folded_ones_gave_up():
    # The point of folding a row is the room it hands the rows you kept -- an
    # open row shares out what the folded ones freed, rather than the `1fr`
    # scramble that also resized the type on every card to put four away.
    page = render_page("123")
    assert 'tmpl.push(shut ? "auto" : "minmax(0, var(--rowfull))");' in page
    assert '"1fr"' not in page.split("function applyRows()")[1].split("\n}")[0]
    assert "function rowHeight(grid, rows, shutRows)" in page
    assert "${shutRows} * ${s}px) / ${open}" in page
    # With the tracks now free to sum to less than the pane, the default
    # `stretch` would blow three folded strips back up to fill the whole of it.
    assert "align-content: start;" in page
    # One track per row and a gap between them -- the bars the arithmetic used
    # to subtract are gone, and so is the `--rowbar` that measured them.
    assert "return `(${rows - 1} * 5px)`;" in page
    assert "--rowbar" not in page


def test_the_row_height_is_seeded_before_the_template_that_reads_it():
    # The grid is thrown away and rebuilt every two seconds, and the one that
    # arrives has no `--rowfull` of its own. A template referencing it would be a
    # declaration with an undefined variable in it, which CSS drops whole --
    # `grid-auto-rows` would size all 2N tracks evenly, and the strip height read
    # back off them would be a sixth of the board instead of a folded row. That
    # is a row that settles at the wrong height a beat after every refresh.
    page = render_page("123")
    js = page.split("function applyRows()")[1].split("\n}")[0]
    assert js.index('setProperty("--rowfull", evenRows(rows))') < js.index(
        "grid.style.gridTemplateRows"
    )
    # And the strip is read only once the folded rows are `auto` tracks.
    assert js.index("grid.style.gridTemplateRows") < js.index(
        'setProperty("--rowfull", rowHeight('
    )


def test_an_open_row_stops_growing_at_the_two_open_height():
    # Past that the type is fixed and the lineup is eight slots long, so a taller
    # card is white space -- and the last row standing would be a different
    # object from the one you were reading a moment ago.
    page = render_page("123")
    assert "min(calc((100% - ${chrome} - ${shutRows} * ${s}px) / ${open})," in page
    assert "calc((100% - ${chrome} - ${s}px) / 2))" in page
    # The board with nothing folded is the plain even split, with no cap in it.
    assert "if (!s) return evenRows(rows);" in page
    assert "return `calc((100% - ${rowChrome(rows)}) / ${rows})`;" in page


def test_a_folded_row_is_positional_so_it_stays_where_you_folded_it():
    # Keyed by where the row renders, not by which seats are in it: the board
    # can be dragged into any order, and you folded the row you were looking at.
    page = render_page("123")
    assert "+a.style.order" in page
    assert "GRID_COLS" in page
    # A card dragged across a boundary takes the state of the row it lands in.
    assert "applyRows();" in page.split("order.splice(order.indexOf(target)")[1]


def test_a_folded_card_shows_nothing_but_its_strip_whichever_pane_it_was_on():
    # `.body` goes, not the panes inside it -- with the parent gone,
    # `.card.view-need .pane.need` has no specificity fight left to win.
    page = render_page("123")
    assert ".card.collapsed > .body { display: none;" in page
    assert ".card .foldsum { display: none;" in page
    assert ".card.collapsed .foldsum { display: grid;" in page


def test_the_fold_is_remembered_alongside_every_other_fold():
    page = render_page("123")
    # One list, one key: the bands and the panes are the same gesture. (The
    # pressure cards were too, until the run pressure filter took folding over
    # from them, and the roster rows until theirs did -- those answer to
    # `draftsim.runsel` and `draftsim.rowsel` now.)
    assert page.count('localStorage.setItem("draftsim.collapsed"') == 1
    # Re-applied after every swap, and once before the first fetch so a folded
    # row never flashes open. Not pinned to an exact run of lines: that only
    # broke this test every time an unrelated apply pass joined the list.
    assert page.count("applyCollapsed();") == 3
    assert "\napplyCollapsed();\n" in page.rsplit("\ntick();", 1)[0]


def test_the_board_is_nothing_but_cards_in_one_flat_grid(midway):
    # The three bars that used to divide it are gone: they were three rows of
    # chrome advertising a double-click, and which rows are open is the filter's
    # business now.
    html = render_rosters(midway)
    assert "rowhd" not in html
    assert html.count("<section") == len(midway.seats)
    # One flat grid, not a grid per row: a card has to be draggable from any row
    # to any other.
    assert html.count('<div class="grid">') == 1
    assert "grid-column: 1 / -1" not in render_page("123")


def test_the_rows_are_filtered_from_the_menu_bar(midway):
    # The same control the pool, the log and run pressure carry, in the header
    # over the thing it filters -- and it says it is there, which three bars
    # whose only gesture was a double-click did not.
    page = render_page("123")
    seg = page.split('class="seg rowseg"')[1].split("</span>")[0]
    for label in ("ALL", "R1", "R2", "R3", "R4"):
        assert f">{label}<" in seg
    # In the shell above the grid, not in the fragment: #rosters is replaced
    # twice a second and a button rebuilt that often holds no pressed state.
    assert page.index('class="menubar"') < page.index('class="seg rowseg"')
    assert page.index('class="seg rowseg"') < page.index('id="rosters"')
    assert "rowseg" not in render_rosters(midway)
    # Filter with the title it filters, actions at the other end.
    assert page.index('class="seg rowseg"') < page.index('id="max"')
    assert ".menubar .seg { margin-left: 0; }" in page
    assert ".menubar #max { margin-left: auto; }" in page


def test_a_league_is_not_offered_a_row_it_does_not_have():
    # Four buttons ship because sixteen seats is four rows; twelve seats is
    # three, and a greyed fourth would be an offer the board cannot make.
    page = render_page("123")
    assert "b.hidden = r !== null && r >= rows;" in page
    # Shipped hidden, since the client cannot correct it until the first fetch
    # lands and three rows is the ordinary board.
    assert 'data-row="3" type="button" aria-pressed="true" hidden' in page


def test_all_is_pressed_only_when_every_row_is():
    # It reports the set rather than being a member of it -- the same as run
    # pressure's ALL.
    page = render_page("123")
    assert "r === null ? openRows(rows).length === rows : rowsel.includes(r)" in page
    # And it counts only the rows this league has: a fourth row left in storage
    # by a 16-seat board must not make ALL look unpressed on a 12-seat one.
    assert "return rowsel.filter((r) => r < rows);" in page


def test_the_filter_folds_the_row_and_the_card_header_still_drags():
    # The fold is a button in the menu bar, clear of the grid entirely: a card
    # header is a grab handle, and a click that ends a one-pixel drag must not
    # put four cards away.
    page = render_page("123")
    assert 'closest(".rowseg button")' in page
    dbl = page.split('addEventListener("dblclick"')[-1]
    assert 'closest("section.card > header")' not in dbl
    assert 'closest(".rowhd")' not in page
    assert ".card header { cursor: grab" in page


def test_the_cards_take_the_order_they_are_dragged_into():
    # No bars in the grid any more, so a card's place in the order is its place
    # in the list -- there is nothing to leave a gap for.
    page = render_page("123")
    assert "el.style.order = order.indexOf(el.dataset.seat);" in page
    assert "slotOrder" not in page


def test_folding_a_row_does_not_resize_the_type_in_the_others():
    # The type used to step up as rows were folded away, which was affordable
    # only while the survivors also inherited the folded rows' height. Pinned to
    # a third, a 1.5x card loses the bottom two lineup slots off the end of the
    # box -- and every card on the board changed size to put four of them away.
    # A pressure card does not do this, and now neither does a roster row.
    page = render_page("123")
    assert "DENSITY" not in page
    assert 'setProperty("--fs"' not in page
    # One knob, one value: `--fs` is what every size on the card is a multiple
    # of, and nothing on the board writes it a second time.
    assert page.count("--fs: 1.17;") == 1
    assert ".card.collapsed { background: #fafafa; }" in page


def test_the_four_cards_of_a_row_share_a_band(midway):
    # With no bar over them, the tint is the only thing left saying these four
    # are a row -- so it is on the cards from the first paint, before any client
    # code has said a word about order.
    html = render_rosters(midway)
    cards = [c.split('"')[0] for c in html.split('data-row="')[1:]]
    assert cards[:5] == ["0", "0", "0", "0", "1"]
    page = render_page("123")
    # A colour per row, not two alternating -- three rows are few enough to each
    # be their own, and the hues stay clear of the four position colours.
    for row in ("0", "1", "2"):
        assert f'.card[data-row="{row}"] {{ --band:' in page
    assert "box-shadow: inset 0 3px 0 var(--bandline);" in page


def test_a_dragged_card_rebands_to_the_row_it_landed_in():
    # The server never hears about the drag, so which row a card is in -- and the
    # tint that groups it with the others -- is the client's answer to give.
    page = render_page("123")
    assert "c.dataset.row = r;" in page


def test_the_filter_names_the_seats_in_each_row_from_the_client():
    # Which seats are in a row is the client's answer to give -- a dragged card
    # changes it, and the server never hears about the drag. It went to the
    # buttons' tooltips when the bars that used to print it left.
    page = render_page("123")
    assert "function seatRange(names)" in page
    assert 'names[0] + "–" + names[names.length - 1]' in page
    assert 'seats.push(seatRange(mine.map((c) => "S" + c.dataset.seat)));' in page
    assert 'b.title = "Row " + (r + 1) + ": " + seats[r];' in page


# -- compact names -----------------------------------------------------------


def _P(name, pos="WR"):
    return Player(id=name, name=name, pos=pos, team="KC", points=0.0)


def test_short_name_is_initial_plus_surname():
    assert _short_name(_P("Joe Burrow", "QB")) == "J. Burrow"
    assert _short_name(_P("Ja'Marr Chase")) == "J. Chase"


def test_short_name_keeps_compound_surnames_whole():
    # "A. Brown" would be the wrong player entirely.
    assert _short_name(_P("Amon-Ra St. Brown")) == "A. St. Brown"
    assert _short_name(_P("Jaxon Smith-Njigba")) == "J. Smith-Njigba"


def test_a_defense_gets_its_nickname_not_an_initial():
    # Defenses are named for their city, where an initial reads as nonsense --
    # "K. City Chiefs" helps nobody.
    assert _short_name(_P("Kansas City Chiefs", "DEF")) == "Chiefs"
    assert _short_name(_P("New York Giants", "DEF")) == "Giants"


def test_a_one_word_name_is_left_alone():
    assert _short_name(_P("Cher")) == "Cher"


def test_cards_carry_both_name_forms_so_maximizing_needs_no_refetch(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = render_rosters(midway).split('data-seat="%d"' % seat.slot)[1]
    card = card.split("</section>")[0]
    player = seat.picks[0].player
    assert f'class="mn">{_short_name(player)}<' in card
    assert f'class="mnf">{_esc_name(player.name)}<' in card


def _esc_name(name):
    import html as _h

    return _h.escape(name)


def test_cards_label_their_numeric_columns(midway):
    # The labels ship in every card but only show when maximized, where there
    # is room for the bye and points columns they name.
    card = render_rosters(midway).split('data-seat="1"')[1]
    assert ">BYE<" in card
    assert ">PTS<" in card
    assert 'class="ln colhead"' in card


def test_the_overlay_has_its_own_way_out():
    # Maximize sits in the left column, which the overlay covers -- without a
    # close control inside it, Esc is the only exit and nothing says so.
    page = render_page("123")
    assert 'id="close"' in page
    assert "body.maxed .closebtn" in page
    assert 'getElementById("close")' in page
    assert 'aria-label="Minimize"' in page


def test_the_page_hides_column_labels_until_maximized():
    page = render_page("123")
    assert "body.maxed .colhead" in page
    # Per-player bye and points stay a maximized column. The card's own total is
    # the number compact has room for.
    assert ".mb, .mp { display: none; color: #666; }" in page
    assert "body.maxed .mb, body.maxed .mp { display: block; }" in page
    # Tabular figures are what make the numbers read as columns.
    assert "tabular-nums" in page
