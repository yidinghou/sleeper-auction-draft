"""Rebuilding league standings from a pick feed, and rendering them."""

import json
from pathlib import Path

import pytest

from draftsim.config import BENCH, DraftConfig
from draftsim.live_render import (
    _SUMMARY_SKIP,
    _pair_starters,
    _short_name,
    render_nomination,
    render_page,
    render_rosters,
)
from draftsim.live_state import (
    contenders,
    position_summary,
    reconstruct,
    seat_value_of,
    spend_by_position,
)
from draftsim.roster import starters
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


def test_summary_need_is_the_same_need_the_seat_already_reports(midway):
    # Two ways to the same number; if they part company the summary card starts
    # contradicting the roster card next to it.
    for seat in midway.seats.values():
        for line in position_summary(seat, midway.config):
            assert line.need == seat.needs.get(line.pos, 0)


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
    # A finished roster is deeper than its lineup at some position, and depth
    # must never read as a hole.
    assert any(line.have > line.want for line in lines.values())
    assert all(line.need == 0 for line in lines.values())


def test_a_position_nobody_starts_or_owns_is_left_out(midway):
    # The default lineup has no kicker slot, so K is noise until someone drafts
    # one -- at which point the body has to show up somewhere.
    for seat in midway.seats.values():
        positions = {line.pos for line in position_summary(seat, midway.config)}
        has_kicker = any(p.pos == "K" for p in seat.roster)
        assert ("K" in positions) == has_kicker


def test_an_empty_roster_summarizes_as_all_holes(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    lines = position_summary(state.seats[1], mock_config)
    assert [(l.pos, l.have, l.want) for l in lines] == [
        ("QB", 0, 2), ("RB", 0, 3), ("WR", 0, 3), ("TE", 0, 1), ("DEF", 0, 1),
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
    assert "seat 8" in html


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


def test_page_shell_is_a_full_html_document():
    page = render_page("123")
    assert page.startswith("<!doctype html>")
    assert "/api/state" in page
    assert "123" in page


# -- roster cards ------------------------------------------------------------


def test_every_seat_gets_a_roster_card(midway):
    html = render_rosters(midway)
    for slot in midway.seats:
        assert f'data-roster="{slot}"' in html


def test_cards_are_in_seat_order_not_reach_order(midway):
    # The table sorts by reach; these are read to look a seat up, and a grid
    # that reshuffles on every bid is useless for that.
    html = render_rosters(midway)
    order = [int(c.split('"')[0]) for c in html.split('data-roster="')[1:]]
    assert order == sorted(midway.seats)


def test_a_card_shows_each_player_with_price_points_and_bye(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
    card = card.split("</section>")[0]
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card
        assert f"${pick.price}" in card
        if pick.player.bye:
            assert f">{pick.player.bye}<" in card


def test_a_card_totals_only_the_starting_lineup(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
    lineup = [p for p in starters(seat.roster, midway.config) if p is not None]
    assert f"{sum(p.points for p in lineup):.0f}" in card.split("</header>")[0]
    # Not the whole roster: a bench body must not inflate the headline.
    assert sum(p.points for p in lineup) <= sum(p.points for p in seat.roster)


def test_unfilled_starter_slots_are_shown(midway):
    seat = next(s for s in midway.seats.values() if s.open_slots > 6)
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
    card = card.split("</section>")[0]
    # A hole at a starting slot is the point of the card, so it stays visible.
    assert 'class="cell open"' in card
    # The header carries how much bench is still to come.
    assert "open</span>" in card


def test_a_full_roster_has_no_open_bench_note(finished):
    card = render_rosters(finished).split('data-roster="1"')[1]
    card = card.split("</section>")[0]
    assert "open</span>" not in card
    assert "—" not in card


def test_empty_rosters_still_render_their_shape(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    html = render_rosters(state)
    assert html.count("data-roster=") == mock_config.teams
    # Every seat shows the full lineup shape, all of it empty.
    assert html.count('class="cell open"') == mock_config.teams * len(
        [s for s in mock_config.roster_slots if s != "BN"]
    )


# -- starter pairing ---------------------------------------------------------


def _rows(*slots):
    return [(slot, None) for slot in slots]


def test_default_lineup_pairs_as_documented(mock_config):
    starter_rows = [
        (slot, None) for slot in mock_config.roster_slots if slot != "BN"
    ]
    pairs = _pair_starters(starter_rows)
    assert [(a[0], b[0]) for a, b in pairs] == [
        ("QB", "SUPER_FLEX"),
        ("RB", "WR"),
        ("RB", "WR"),
        ("FLEX", "REC_FLEX"),
        ("DEF", "TE"),
    ]


def test_pairing_never_drops_or_duplicates_a_slot(mock_config):
    starter_rows = [
        (slot, None) for slot in mock_config.roster_slots if slot != "BN"
    ]
    flat = [row for pair in _pair_starters(starter_rows) for row in pair if row]
    assert sorted(s for s, _ in flat) == sorted(s for s, _ in starter_rows)


def test_an_unlisted_lineup_falls_back_to_chunks_of_two():
    # A league whose slots aren't in _PREFERRED_PAIRS still renders, two per
    # row, rather than losing the slots the table doesn't name.
    pairs = _pair_starters(_rows("K", "K", "IDP", "IDP", "IDP"))
    assert [(a[0], b[0] if b else None) for a, b in pairs] == [
        ("K", "K"),
        ("IDP", "IDP"),
        ("IDP", None),
    ]


def test_a_lineup_missing_half_a_pair_still_renders_the_half_it_has():
    pairs = _pair_starters(_rows("QB", "RB", "WR"))
    assert [(a[0] if a else None, b[0] if b else None) for a, b in pairs] == [
        ("QB", None),
        ("RB", "WR"),
    ]


def test_cells_carry_position_colour_and_price(midway):
    from draftsim.theme import POS_COLOR

    seat = next(s for s in midway.seats.values() if s.roster)
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
    card = card.split("</section>")[0]
    # Position reads as the cell's colour, not a badge -- so no badge markup,
    # but every drafted player's colour must be present.
    assert 'class="badge"' not in card
    for pick in seat.picks:
        assert f"--pos:{POS_COLOR[pick.player.pos]}" in card
        assert f"${pick.price}" in card


def test_bench_is_shown_but_marked_so_it_can_be_dimmed(finished):
    card = render_rosters(finished).split('data-roster="1"')[1]
    card = card.split("</section>")[0]
    # Bench is visible alongside the starters -- the `bench` class is what lets
    # CSS grey it back, so a glance still separates starters from depth.
    assert card.count('class="prow bench"') == 3  # 6 bench slots, two to a row
    assert 'class="prow bench"' in card


def test_bench_players_are_dimmed_not_hidden():
    page = render_page("123")
    assert ".prow.bench .cell" in page
    assert "opacity" in page
    # The old behaviour -- hidden until maximized -- must not come back.
    assert ".prow.bench {" not in page.replace("{{", "{")


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
    # which would have pushed the bottom row of cards off screen.
    assert "#rosters { flex: 1; min-height: 0; }" in page


def test_page_splits_state_left_from_rosters_right():
    page = render_page("123")
    assert 'id="max"' in page
    assert "body.maxed" in page
    # The nomination strip and controls live in the left column; the roster
    # grid gets its own column so it can take the full viewport height.
    assert 'class="side"' in page
    assert 'class="board"' in page
    assert page.index('class="side"') < page.index('class="board"')


# -- the summary view --------------------------------------------------------


def _card(state, slot):
    card = render_rosters(state).split('data-roster="%d"' % slot)[1]
    return card.split("</section>")[0]


def test_every_card_carries_both_views_and_the_arrows_between_them(midway):
    html = render_rosters(midway)
    teams = len(midway.seats)
    assert html.count('class="sum"') == teams
    assert html.count('class="vnav"') == teams
    assert html.count('class="vnext"') == teams
    assert html.count('class="vprev"') == teams


def test_the_summary_ships_alongside_the_roster_rows_not_instead_of_them(midway):
    # Both views in one card is what makes flipping free: no fetch, so the two
    # views can never show different moments of the draft.
    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    assert 'class="sum"' in card
    assert 'class="prow"' in card
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card


def _summarized(state, seat):
    """The rows a seat's summary view actually shows."""
    return [
        line
        for line in position_summary(seat, state.config)
        if line.pos not in _SUMMARY_SKIP
    ]


def test_summary_shows_each_positions_fill_and_points(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    summary = _card(midway, seat.slot).split('class="sum"')[1].split("</div></div>")[0]
    for line in _summarized(midway, seat):
        assert f">{line.have}<i>/{line.want}</i>" in summary
        if line.starter_points:
            assert f">{line.starter_points:.0f}<" in summary


def test_summary_flags_only_the_positions_still_short(midway):
    for seat in midway.seats.values():
        summary = _card(midway, seat.slot).split('class="sum"')[1]
        short = [line for line in _summarized(midway, seat) if line.need]
        assert summary.count('class="srow short"') == len(short)


def test_summary_leaves_out_the_rows_that_are_never_a_decision(midway):
    # One defense, bought once, and bench depth you don't decide at the podium:
    # rows that cost space without changing a bid. The full roster still has them.
    for seat in midway.seats.values():
        card = _card(midway, seat.slot)
        summary, rows = card.split('class="sum"')[1].split('<div class="prow')[:2]
        assert ">DEF</i>" not in summary
        assert ">BN</i>" not in summary
        assert summary.count('class="srow') == len(_summarized(midway, seat))


def test_the_roster_view_still_shows_what_the_summary_drops(finished):
    # Dropping them from the summary must not lose them: a full roster's
    # defense and bench are still there in the view that lists everything.
    roster_rows = _card(finished, 1).split('<div class="prow', 1)[1]
    assert ">DEF</i>" in roster_rows
    assert f">{BENCH}</i>" in roster_rows


def test_the_fill_meter_never_overflows_its_track(finished):
    # A fourth running back is depth; a bar past 100% would read as a fault.
    for slot in finished.seats:
        widths = [
            int(chunk.split("%")[0])
            for chunk in _card(finished, slot).split("width:")[1:]
        ]
        assert widths and all(0 <= w <= 100 for w in widths)


def test_view_state_is_client_side_and_survives_a_refresh():
    page = render_page("123")
    # Roster view is what the board loads on; summary is behind the arrows.
    assert ".sum { display: none; }" in page
    assert '.card.view-summary .sum { display: block; }' in page
    assert '.card.view-summary .prow { display: none; }' in page
    # The cards are replaced every tick, so the choice must be re-applied after
    # each swap and the arrow handler must be delegated, not per-button.
    assert "applyViews()" in page
    assert 'getElementById("rosters").addEventListener("click"' in page
    assert "draftsim.views" in page


# -- dragging cards into a different order -----------------------------------


def test_cards_are_draggable(midway):
    html = render_rosters(midway)
    assert html.count('draggable="true"') == len(midway.seats)


def test_dragging_does_not_change_what_the_server_sends(midway):
    # Order is presentational -- CSS `order` over markup that stays in seat
    # order, so the server has no idea the board was rearranged and seat order
    # is always recoverable.
    page = render_page("123")
    assert "card.style.order" in page
    assert "draftsim.order" in page
    order = [int(c.split('"')[0]) for c in render_rosters(midway).split('data-roster="')[1:]]
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
    assert "if (dragging === null) {" in page
    assert "rosters.innerHTML = s.rosters_html;" in page


def test_a_saved_order_is_reconciled_against_the_seats_on_the_board():
    page = render_page("123")
    # A stored order from a different league size must not hide a seat.
    assert "function normalizeOrder()" in page
    assert "if (!order.includes(slot)) order.push(slot);" in page


def test_there_is_a_way_back_to_seat_order():
    page = render_page("123")
    assert 'id="reorder"' in page
    assert 'localStorage.removeItem("draftsim.order")' in page


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
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
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
    card = render_rosters(midway).split('data-roster="1"')[1]
    assert ">BYE<" in card
    assert ">PTS<" in card
    assert 'class="prow colhead"' in card


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
    # Tabular figures are what make the numbers read as columns.
    assert "tabular-nums" in page
