"""Who is sitting in each seat: scanned from the league, or typed by hand.

Two halves, and the seam between them is what most of these are about. The scan
is the API's answer and can be wrong or absent; the override is yours and always
wins; and neither may leave the board worse off than the twelve numbered seats
it drew before either existed.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from draftsim import live as live_mod
from draftsim import seat_names as names_mod
from draftsim.live import DraftPoller
from draftsim.seat_names import MAX_NAME, SeatNames, clean_name
from draftsim.sleeper import SleeperError, seat_names, slot_for_user_map

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def poller(monkeypatch, tmp_path):
    """The same poller `test_live` builds, on the recorded mock feed.

    Overrides are pointed at a temp directory: they are the one thing the board
    writes to disk, and a suite that named seats in the repo's own `data/` would
    leave them there for the next real draft.
    """
    monkeypatch.setattr(names_mod, "NAMES_DIR", tmp_path)
    state = {"draft": _load("draft-mock"), "picks": _load("picks-mock")}
    monkeypatch.setattr(live_mod, "fetch_draft", lambda _: state["draft"])
    monkeypatch.setattr(live_mod, "fetch_picks", lambda _: state["picks"])
    p = DraftPoller("mock-id", None, interval=0.01)
    p.feed = state
    return p


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


# -- the board, named and unnamed ---------------------------------------------


def test_the_mock_board_still_reads_in_seat_numbers(poller):
    # No league on a mock, so nothing is named -- and every seat has to keep the
    # label it had before any of this existed. This is the rehearsal path.
    poller.refresh()
    snap = poller.snapshot()
    assert snap["rosters_html"].count(">S1<") >= 1
    assert ">S12<" in snap["rosters_html"]
    assert ">S12<" in snap["ledger_html"]
    assert snap["seat_names"] == {str(n): "" for n in range(1, 13)}


def test_a_named_seat_keeps_its_number_on_the_card(poller):
    # The number is what this card and Sleeper's own board have in common, and
    # this one can be dragged out of seat order. Losing it costs the card the
    # only thing it can be checked against.
    poller.names.set_scanned({5: "Bagel Boys"})
    poller.refresh()
    cards = poller.snapshot()["rosters_html"]
    assert ">Bagel Boys<" in cards
    assert '<span class="slot">S5</span>' in cards


def test_a_name_reaches_every_place_a_seat_is_drawn(poller):
    poller.names.set_scanned({5: "Bagel Boys"})
    poller.replay = 60
    poller.refresh()
    snap = poller.snapshot()
    # The card, the ledger's tags, the run-pressure tiles and the log's buyer
    # column: one seat, one name, wherever it is read.
    assert ">Bagel Boys<" in snap["rosters_html"]
    assert ">Bagel<" in snap["ledger_html"]
    assert ">Bagel<" in snap["pressure_html"]
    assert "S5 · Bagel" in snap["log_html"]
    # And the tooltip carries both halves, everywhere.
    assert "Bagel Boys · S5" in snap["ledger_html"]
    assert "Bagel Boys · S5" in snap["pressure_html"]


def test_a_name_is_escaped_into_every_fragment(poller):
    poller.names.set_override(5, "Marc & <b>Co</b>")
    poller.replay = 60
    poller.refresh()
    snap = poller.snapshot()
    for html in (snap["rosters_html"], snap["ledger_html"], snap["log_html"]):
        assert "<b>Co</b>" not in html
    # Cleaned on the way in, then escaped on the way out -- both, because either
    # alone is one missed path away from injecting markup into every fragment.
    assert poller.names.label(5) == "Marc & bCo/b"
    assert "Marc &amp; bCo/b" in snap["rosters_html"]


def test_the_ledger_names_your_seat_as_well_as_your_account(poller):
    poller.feed["draft"] = dict(poller.feed["draft"], draft_order={"u-123": 7})
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = "u-123"
    poller.names.set_scanned({7: "Bagel Boys"})
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    # Both halves of the lookup are checkable: the username says the seat is
    # yours, the team name says the scan put the right name on it.
    assert "<b>yidinghou</b> · <b>S7</b>" in ledger
    assert "Bagel Boys" in ledger


# -- scanning, and when -------------------------------------------------------


@pytest.fixture
def league(poller, monkeypatch):
    """The mock feed, promoted to a real league with three named seats."""
    calls = {"users": 0, "rosters": 0}
    roll = _users(
        ("u-1", "yidinghou", "Bagel Boys"), ("u-2", "marc", ""), ("u-3", "dan", "Dan")
    )

    def fake_users(league_id):
        calls["users"] += 1
        return roll

    def fake_rosters(league_id):
        calls["rosters"] += 1
        return []

    monkeypatch.setattr(live_mod, "fetch_league_users", fake_users)
    monkeypatch.setattr(live_mod, "fetch_league_rosters", fake_rosters)
    poller.feed["draft"] = dict(
        poller.feed["draft"],
        league_id="L1",
        draft_order={"u-1": 1, "u-2": 2, "u-3": 3},
    )
    poller.roll = roll
    poller.name_calls = calls
    return poller


def test_a_real_league_names_its_seats_without_being_asked(league):
    league.refresh()
    assert league.names.scanned == {1: "Bagel Boys", 2: "marc", 3: "Dan"}
    assert ">Bagel Boys<" in league.snapshot()["rosters_html"]


def test_the_league_is_read_once_per_ttl_not_once_per_poll(league):
    # The draft feed is polled every three seconds; team names change about
    # twice a season. Scanning on every tick would double the request rate for
    # an answer that does not move.
    for _ in range(5):
        league.refresh()
    assert league.name_calls["users"] == 1
    assert league.name_calls["rosters"] == 0  # draft_order answered it alone

    league.rescan_names()
    league.refresh()
    assert league.name_calls["users"] == 2


def test_a_rename_on_sleeper_redraws_a_board_nobody_has_bid_on(league):
    # The trap this exists for: `refresh` skips the rebuild when the draft has
    # not moved, and between nominations that is every tick for minutes. A name
    # that changed would sit invisible until somebody bid.
    league.refresh()
    league.roll[1]["metadata"] = {"team_name": "Marc's Team"}
    league._names_at = None  # the TTL has expired
    league.refresh()
    # Escaped on the way out: a name from the API gets the same treatment as one
    # typed into the box, and half the teams in a league have an apostrophe.
    assert ">Marc&#x27;s Team<" in league.snapshot()["rosters_html"]


def test_a_failed_scan_keeps_the_last_good_names_and_says_so(league, monkeypatch):
    league.refresh()
    monkeypatch.setattr(
        live_mod,
        "fetch_league_users",
        lambda _: (_ for _ in ()).throw(SleeperError("league unreachable")),
    )
    league._names_at = None
    league.refresh()
    snap = league.snapshot()
    # Never fatal: the names are a courtesy on top of a board that worked
    # without them, and a bad minute at the league endpoint must not cost you
    # the auction.
    assert ">Bagel Boys<" in snap["rosters_html"]
    assert "league unreachable" in snap["warning"]


# -- the endpoint the box posts to --------------------------------------------


@pytest.fixture
def board(poller):
    """The board's own HTTP server, on a port the OS picks."""
    poller.refresh()
    server = ThreadingHTTPServer(("127.0.0.1", 0), live_mod._handler(poller))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", poller
    server.shutdown()
    server.server_close()


def _post(url, body=b"{}", ctype="application/json"):
    req = urllib.request.Request(url, data=body, method="POST")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_naming_a_seat_over_http_writes_it_and_answers_with_the_label(board):
    url, poller = board
    status, body = _post(
        url + "/api/seat-name", json.dumps({"slot": 5, "name": "Marc"}).encode()
    )
    assert (status, body) == (200, {"slot": 5, "label": "Marc"})
    assert poller.names.label(5) == "Marc"
    assert json.loads(poller.names.path.read_text())["overrides"] == {"5": "Marc"}


def test_the_endpoint_refuses_what_it_cannot_draw(board):
    url, _ = board
    seat = url + "/api/seat-name"
    # A slot outside the board would write an override for a seat nothing will
    # ever show again.
    assert _post(seat, json.dumps({"slot": 13, "name": "x"}).encode())[0] == 400
    assert _post(seat, json.dumps({"slot": 0, "name": "x"}).encode())[0] == 400
    assert _post(seat, json.dumps({"slot": "five"}).encode())[0] == 400
    assert _post(seat, b"{not json")[0] == 400
    assert _post(seat, json.dumps([1, 2]).encode())[0] == 400
    assert _post(seat, json.dumps({"slot": 5, "name": "x" * 500}).encode())[0] == 400


def test_a_form_from_another_tab_cannot_name_a_seat(board):
    # This server binds 127.0.0.1 and has no other defence. A cross-origin form
    # post can reach it, but it cannot set a JSON content type without a
    # preflight it will not get -- so the one endpoint that writes a file is
    # closed to a drive-by.
    url, poller = board
    body = json.dumps({"slot": 5, "name": "Marc"}).encode()
    status, _ = _post(
        url + "/api/seat-name", body, ctype="application/x-www-form-urlencoded"
    )
    assert status == 415
    assert poller.names.label(5) == ""


def test_a_rescan_is_asked_for_over_http_too(board):
    url, poller = board
    poller._names_at = 123.0
    assert _post(url + "/api/rescan-names")[0] == 200
    assert poller._names_at is None


def test_a_hand_typed_name_redraws_the_board_and_outranks_the_scan(league):
    league.refresh()
    assert league.rename_seat(1, "Marc") == "Marc"
    league.refresh()
    cards = league.snapshot()["rosters_html"]
    assert ">Marc<" in cards
    assert "Bagel Boys" not in cards
    # And the scan still knows what Sleeper said, so clearing gives it back.
    assert league.rename_seat(1, "") == "Bagel Boys"
