"""The poller and its snapshot, with Sleeper stubbed out.

These are about behaviour under a flaky network as much as about parsing: the
board is read mid-auction, where a frozen or blank screen is worse than a
slightly stale one.
"""

import json
from pathlib import Path

import pytest

from draftsim import live as live_mod
from draftsim.live import DraftPoller
from draftsim.sleeper import SleeperError

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def poller(monkeypatch):
    """A poller wired to the recorded mock draft instead of the network."""
    calls = {"draft": 0, "picks": 0}
    state = {"draft": _load("draft-mock"), "picks": _load("picks-mock")}

    def fake_draft(draft_id):
        calls["draft"] += 1
        return state["draft"]

    def fake_picks(draft_id):
        calls["picks"] += 1
        return state["picks"]

    monkeypatch.setattr(live_mod, "fetch_draft", fake_draft)
    monkeypatch.setattr(live_mod, "fetch_picks", fake_picks)
    p = DraftPoller("mock-id", None, interval=0.01)
    p.calls = calls
    p.feed = state
    return p


def test_snapshot_before_the_first_poll_says_so(poller):
    snap = poller.snapshot()
    assert "waiting" in snap["subtitle"]
    assert snap["rosters_html"] == ""
    # Every fragment the page swaps in has to be present even here, or the first
    # tick throws on an undefined and the board never starts.
    assert snap["pressure_html"] == ""
    assert snap["pool_html"] == ""
    assert snap["log_html"] == ""
    assert snap["ledger_html"] == ""
    assert snap["my_seat"] is None


def test_a_poll_produces_a_renderable_snapshot(poller):
    poller.refresh()
    snap = poller.snapshot()
    assert snap["teams"] == 12
    # The subtitle is connection state now and nothing else; what the room has
    # spent moved into the ledger, where it is drawn.
    assert snap["subtitle"] == "complete"
    assert "192</b> of 192" in snap["ledger_html"]
    # One card per seat, all from the same snapshot, so no two parts of the
    # page can show different moments of the draft.
    assert snap["rosters_html"].count("data-seat=") == 12
    # Four position cards, each holding a tile for all twelve seats -- the same
    # twelve, from the same snapshot, so the tiles and the cards cannot disagree.
    assert snap["pressure_html"].count("data-seat=") == 48
    # A finished draft empties the rosters, not the sheet -- 192 picks leave
    # plenty of undrafted bodies, so the pool still has rows.
    assert snap["pool_html"].count('class="prow"') > 0
    assert snap["log_html"].count('class="lrow"') > 0
    assert "Kansas City Chiefs" in snap["nomination_html"]
    assert snap["warning"] == ""
    # The whole snapshot crosses the wire as JSON on every tick.
    json.dumps(snap)


def test_an_unchanged_draft_does_not_refetch_the_pick_feed(poller):
    poller.refresh()
    poller.refresh()
    poller.refresh()
    assert poller.calls["draft"] == 3
    # The pick feed is ~80x larger than the draft endpoint; polling it every
    # tick when nothing moved is the cost this avoids.
    assert poller.calls["picks"] == 1


def test_a_moved_bid_refetches(poller):
    poller.refresh()
    poller.feed["draft"] = json.loads(json.dumps(poller.feed["draft"]))
    poller.feed["draft"]["metadata"]["highest_offer"] = "99"
    poller.refresh()
    assert poller.calls["picks"] == 2
    assert "$99" in poller.snapshot()["nomination_html"]


def test_a_failed_poll_keeps_the_last_good_board(poller, monkeypatch):
    poller.refresh()
    good = poller.snapshot()["rosters_html"]

    def boom(draft_id):
        raise SleeperError("connection reset")

    monkeypatch.setattr(live_mod, "fetch_draft", boom)
    poller.refresh()
    snap = poller.snapshot()
    assert snap["rosters_html"] == good  # still readable
    assert "connection reset" in snap["warning"]
    assert "last good data" in snap["warning"]


def test_a_failure_before_any_success_reports_the_reason(poller, monkeypatch):
    monkeypatch.setattr(
        live_mod, "fetch_draft", lambda _: (_ for _ in ()).throw(SleeperError("nope"))
    )
    poller.refresh()
    snap = poller.snapshot()
    assert "nope" in snap["subtitle"]
    assert snap["rosters_html"] == ""


def test_recovery_clears_the_warning(poller, monkeypatch):
    poller.refresh()
    monkeypatch.setattr(
        live_mod, "fetch_draft", lambda _: (_ for _ in ()).throw(SleeperError("blip"))
    )
    poller.refresh()
    assert poller.snapshot()["warning"]
    monkeypatch.setattr(live_mod, "fetch_draft", lambda _: poller.feed["draft"])
    poller.refresh()
    assert poller.snapshot()["warning"] == ""


def test_replay_rewinds_a_finished_draft_to_mid_auction(poller):
    poller.replay = 60
    poller.refresh()
    snap = poller.snapshot()
    # A rehearsal must never read as the live draft.
    assert snap["subtitle"] == "REPLAY at pick 60"
    assert "60</b> of 192" in snap["ledger_html"]


# -- the money band ----------------------------------------------------------


def test_the_ledger_draws_every_seat_and_names_only_three(poller):
    poller.replay = 60
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    # One column and one seat tag per seat.
    assert ledger.count('class="col ') == 12
    # Three figures, because twelve would be a table.
    assert ledger.count('class="amt"') == 3


def test_the_ledger_reports_what_the_room_has_spent(poller):
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    # The completed mock spends $2,344 of the $2,400 on the table.
    assert "$2,344" in ledger
    assert "of $2,400 spent" in ledger


def test_an_unseated_board_draws_no_line_and_marks_no_seat(poller):
    # The recorded mock has no draft_order, so --user cannot resolve. The band
    # has to run anyway: this is the draft every rehearsal happens against.
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = "u-123"
    poller.refresh()
    snap = poller.snapshot()
    assert snap["my_seat"] is None
    assert 'class="gl"' not in snap["ledger_html"]
    assert "not seated in this draft" in snap["ledger_html"]


def test_a_seated_user_is_marked_and_sets_the_line(poller):
    poller.feed["draft"] = dict(poller.feed["draft"], draft_order={"u-123": 7})
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = "u-123"
    poller.replay = 60
    poller.refresh()
    snap = poller.snapshot()
    assert snap["my_seat"] == 7
    assert 'class="col me"' in snap["ledger_html"]
    # The dashed line is your own ceiling, so it only exists once you have one.
    assert 'class="gl"' in snap["ledger_html"]
    assert "You are <b>S7</b>" in snap["ledger_html"]


def test_stale_projections_are_surfaced_not_swallowed(poller):
    poller.feed["picks"] = [
        {
            "pick_no": 1,
            "draft_slot": 1,
            "player_id": "no-such-player",
            "metadata": {"amount": "5", "position": "WR"},
        }
    ]
    poller.refresh()
    assert "export:projections" in poller.snapshot()["warning"]
