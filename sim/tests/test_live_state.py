"""Rebuilding league standings from a pick feed, and rendering them."""

import json
from pathlib import Path

import pytest

from draftsim.config import DraftConfig
from draftsim.live_render import render_nomination, render_page, render_table
from draftsim.live_state import (
    contenders,
    reconstruct,
    seat_value_of,
    spend_by_position,
)
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


# -- rendering ---------------------------------------------------------------


def test_table_lists_every_seat_richest_reach_first(midway):
    html = render_table(midway, None)
    for slot in midway.seats:
        assert f'data-slot="{slot}"' in html
    order = [
        int(chunk.split('"')[0])
        for chunk in html.split('data-slot="')[1:]
    ]
    reaches = [midway.seats[s].max_bid for s in order]
    assert reaches == sorted(reaches, reverse=True)


def test_table_marks_seats_that_cannot_use_the_nominee(midway):
    kicker = Player(id="k", name="Kicker", pos="K", team="KC", points=200.0)
    assert "no fit" in render_table(midway, kicker)


def test_table_shows_lineup_gain_for_a_usable_nominee(midway):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    assert "pts" in render_table(midway, star)
    assert "no fit" not in render_table(midway, star)


def test_finished_seats_read_as_out_of_the_bidding(finished):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    html = render_table(finished, star)
    assert html.count("out") >= finished.config.teams


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
