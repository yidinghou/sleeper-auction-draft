"""Who is sitting in each seat: scanned from the league, or typed by hand.

Two halves, and the seam between them is what most of these are about. The scan
is the API's answer and can be wrong or absent; the override is yours and always
wins; and neither may leave the board worse off than the twelve numbered seats
it drew before either existed.
"""

import pytest

from draftsim.sleeper import seat_names, slot_for_user_map


def _users(*pairs):
    """Sleeper's `/league/{id}/users` shape, from (user_id, display, team)."""
    return [
        {
            "user_id": uid,
            "display_name": display,
            "metadata": {"team_name": team} if team else {},
        }
        for uid, display, team in pairs
    ]


# -- reading the league -------------------------------------------------------


def test_the_team_name_wins_over_the_account_name():
    # What gets said out loud in the room is the league name, and it is the one
    # they chose knowing eleven others would sit next to it.
    draft = {"draft_order": {"u1": 1, "u2": 2}}
    users = _users(("u1", "yidinghou", "Bagel Boys"), ("u2", "marc", ""))
    assert seat_names(draft, users) == {1: "Bagel Boys", 2: "marc"}


def test_a_seat_nobody_can_be_placed_in_is_left_out():
    # Partial by design: the board falls back to S{slot}, which is strictly
    # what it showed for all twelve before any of this existed.
    draft = {"draft_order": {"u1": 3}}
    users = _users(("u1", "yidinghou", ""), ("u9", "stranger", "Ghost"))
    assert seat_names(draft, users) == {3: "yidinghou"}


def test_a_mock_has_no_league_to_read_and_says_so_with_an_empty_map():
    assert seat_names({}, _users(("u1", "yidinghou", ""))) == {}
    assert slot_for_user_map({}) == {}


def test_rosters_place_people_when_the_draft_order_will_not():
    # A seated draft that publishes no order still maps slots to roster ids, and
    # a roster knows whose it is. Two hops to the same fact.
    draft = {"draft_order": None, "slot_to_roster_id": {"1": 7, "2": 8}}
    rosters = [
        {"roster_id": 7, "owner_id": "u1"},
        {"roster_id": 8, "owner_id": "u2"},
    ]
    users = _users(("u1", "yidinghou", "Bagel Boys"), ("u2", "marc", ""))
    assert seat_names(draft, users, rosters) == {1: "Bagel Boys", 2: "marc"}
    # And without the rosters there is nothing to go on -- not a guess.
    assert seat_names(draft, users) == {}
