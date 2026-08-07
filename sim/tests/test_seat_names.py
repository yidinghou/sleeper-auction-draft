"""Who is sitting in each seat: scanned from the league, or typed by hand.

Two halves, and the seam between them is what most of these are about. The scan
is the API's answer and can be wrong or absent; the override is yours and always
wins; and neither may leave the board worse off than the twelve numbered seats
it drew before either existed.
"""

import json

import pytest

from draftsim.seat_names import MAX_NAME, SeatNames, clean_name
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


# -- the override file --------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return SeatNames("draft-1", directory=tmp_path)


def test_an_override_beats_the_scan_and_survives_a_restart(store, tmp_path):
    store.set_scanned({5: "Bagel Boys"})
    assert store.label(5) == "Bagel Boys"
    store.set_override(5, "Marc")
    assert store.label(5) == "Marc"

    # A new process, the same file. The scan has not run yet in this one, which
    # is the state the board starts every session in.
    again = SeatNames("draft-1", directory=tmp_path)
    assert again.label(5) == "Marc"


def test_clearing_an_override_hands_the_seat_back_to_the_scan(store):
    store.set_scanned({5: "Bagel Boys"})
    store.set_override(5, "Marc")
    # Returns the label the seat now carries, which is not what was passed: an
    # emptied box on a scanned league is "use Sleeper's answer", not "no name".
    assert store.set_override(5, "") == "Bagel Boys"
    assert store.overrides == {}
    assert json.loads(store.path.read_text())["overrides"] == {}


def test_a_scanned_name_is_never_written_to_the_file(store):
    # The scan is refreshed every few minutes and the file outlives the process.
    # A team renamed mid-season must not find last week's name frozen into a
    # file the board then trusts over the API.
    store.set_scanned({1: "Bagel Boys", 2: "marc"})
    assert not store.path.exists()


def test_a_corrupt_file_costs_one_name_not_the_board(tmp_path):
    (tmp_path / "seat-names-draft-1.json").write_text("{not json")
    store = SeatNames("draft-1", directory=tmp_path)
    assert store.overrides == {}
    # And it is still writable: the bad file is replaced, not appended to.
    store.set_override(3, "Marc")
    assert SeatNames("draft-1", directory=tmp_path).label(3) == "Marc"


def test_a_name_is_cleaned_before_it_is_stored_or_drawn():
    # It is typed into a box and echoed into every fragment of the page.
    assert clean_name("  Marc  ") == "Marc"
    assert clean_name("<b>x</b>") == "bx/b"
    # An apostrophe is not an attack, and half the teams in a league have one.
    assert clean_name("Marc's Team & Co") == "Marc's Team & Co"
    assert clean_name("a" * 80) == "a" * MAX_NAME
    assert clean_name("") == ""
    assert clean_name(None) == ""
