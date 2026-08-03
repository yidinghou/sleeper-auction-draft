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
    assert snap["table_html"] == ""


def test_a_poll_produces_a_renderable_snapshot(poller):
    poller.refresh()
    snap = poller.snapshot()
    assert snap["teams"] == 12
    assert "192 picks" in snap["subtitle"]
    assert "complete" in snap["subtitle"]
    assert 'data-slot="1"' in snap["table_html"]
    # Rosters ride along in the same snapshot, so the board and the cards can
    # never show two different moments of the draft.
    assert snap["rosters_html"].count("data-roster=") == 12
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
    good = poller.snapshot()["table_html"]

    def boom(draft_id):
        raise SleeperError("connection reset")

    monkeypatch.setattr(live_mod, "fetch_draft", boom)
    poller.refresh()
    snap = poller.snapshot()
    assert snap["table_html"] == good  # still readable
    assert "connection reset" in snap["warning"]
    assert "last good data" in snap["warning"]


def test_a_failure_before_any_success_reports_the_reason(poller, monkeypatch):
    monkeypatch.setattr(
        live_mod, "fetch_draft", lambda _: (_ for _ in ()).throw(SleeperError("nope"))
    )
    poller.refresh()
    snap = poller.snapshot()
    assert "nope" in snap["subtitle"]
    assert snap["table_html"] == ""


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
    assert "60 picks" in snap["subtitle"]
    # A rehearsal must never read as the live draft.
    assert "REPLAY at pick 60" in snap["subtitle"]
    assert "complete" not in snap["subtitle"]


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
