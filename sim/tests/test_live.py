"""The poller and its snapshot, with Sleeper stubbed out.

These are about behaviour under a flaky network as much as about parsing: the
board is read mid-auction, where a frozen or blank screen is worse than a
slightly stale one.
"""

import json
import re
from pathlib import Path

import pytest

from draftsim import live as live_mod
from draftsim import seat_names as names_mod
from draftsim.live import DraftPoller
from draftsim.sleeper import SleeperError

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def poller(monkeypatch, tmp_path):
    """A poller wired to the recorded mock draft instead of the network.

    Seat-name overrides are pointed at a temp directory: they are the one thing
    the board writes to disk, and a test suite that named seats in the repo's
    own `data/` would leave them there for the next real draft.
    """
    monkeypatch.setattr(names_mod, "NAMES_DIR", tmp_path)
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
    # How far in the draft is rides in the band header now -- one line about the
    # whole draft, where the chart under it needs the height.
    assert "192</b> of 192" in snap["spend_html"]
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
    assert "60</b> of 192" in snap["spend_html"]
    # A rehearsal rewinds past picks this run never watched, so their lots have
    # nothing but the price the feed vouches for. Asserted rather than assumed:
    # this test used to say nothing at all about the bidding, which is how the
    # trail going missing on a replay stayed invisible.
    poller.view_goto(60)
    assert poller.snapshot()["nomination_html"].count('<span class="rung') == 1


# -- checkpoint nav -----------------------------------------------------------


def test_nav_defaults_to_live(poller):
    poller.refresh()
    assert poller.snapshot()["nav"] == {"index": 192, "total": 192, "live": True}


def test_goto_rewinds_the_board_to_a_pick_boundary(poller):
    poller.refresh()
    poller.view_goto(30)
    snap = poller.snapshot()
    assert snap["nav"] == {"index": 30, "total": 192, "live": False}
    assert "30</b> of 192" in snap["spend_html"]
    assert snap["subtitle"] == "Pick 30 of 192 — rewound"
    # A checkpoint is always after a bid cleared -- there is never a lot *in
    # progress* to show, but the pick that just landed (#30: Bo Nix to S6 for
    # $28) takes its place rather than the panel going blank.
    assert "Bo Nix" in snap["nomination_html"]
    assert "won by S6" in snap["nomination_html"]
    assert 'class="chip bid-high"' in snap["nomination_html"]
    assert "$28" in snap["nomination_html"]
    # The winning seat lights up the same amber a live leader would, in every
    # place that colour already lives: the chart bar and the tag under it.
    assert 'class="col bid-high"' in snap["ledger_html"]
    assert 'class="bid-high"' in snap["ledger_html"]
    assert re.search(r'class="tile[^"]*\bbid-high"', snap["pressure_html"])


def test_a_checkpoint_at_pick_zero_has_nothing_to_show(poller):
    poller.refresh()
    poller.view_goto(0)
    snap = poller.snapshot()
    assert snap["nav"] == {"index": 0, "total": 192, "live": False}
    assert "Nothing nominated — 0 picks in." in snap["nomination_html"]
    assert "bid-high" not in snap["ledger_html"]


def test_a_checkpoint_shows_the_full_bidding_trail_when_the_poller_saw_it(poller):
    # Unlike `test_goto_rewinds_the_board_to_a_pick_boundary`, which rewinds a
    # poller that only ever saw the finished feed and so falls back to a
    # single winner chip, this one watches pick #30 (Bo Nix, S6, $28) close in
    # real time first -- so the checkpoint should show everyone who bid on it.
    poller.replay = 29
    poller.refresh()
    _bid(poller, player="11563", nominating=6, offering=6, amount="20")
    poller.refresh()                                    # S6 opens at $20
    _bid(poller, player="11563", nominating=6, offering=9, amount="25")
    poller.refresh()                                    # S9 raises to $25
    _bid(poller, player="11563", nominating=6, offering=6, amount="28")
    poller.refresh()                                    # S6 retakes at $28
    poller.replay = 30
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()                                    # the lot closes
    assert poller._bid_history["11563"] == {6: 28, 9: 25}
    poller.view_goto(30)
    snap = poller.snapshot()
    assert "Bo Nix" in snap["nomination_html"]
    assert snap["nomination_html"].count('class="chip') == 2
    assert 'class="chip bid-high"' in snap["nomination_html"]
    assert "$28" in snap["nomination_html"]
    assert "$25" in snap["nomination_html"]


def test_live_returns_to_the_pulse_driven_snapshot(poller):
    poller.refresh()
    live_snap = poller.snapshot()
    poller.view_goto(30)
    assert poller.snapshot()["nav"]["live"] is False
    poller.view_live()
    assert poller.snapshot() == live_snap


def test_prev_steps_back_one_pick_at_a_time_and_clamps_at_zero(poller):
    poller.refresh()
    poller.view_prev()
    assert poller.snapshot()["nav"] == {"index": 191, "total": 192, "live": False}
    poller.view_goto(0)
    poller.view_prev()
    assert poller.snapshot()["nav"]["index"] == 0


def test_next_stops_at_the_newest_checkpoint_and_never_auto_goes_live(poller):
    poller.refresh()
    poller.view_goto(190)
    poller.view_next()
    poller.view_next()
    poller.view_next()
    snap = poller.snapshot()
    # Even tapping past the end does not flip back to live -- only
    # `view_live` does.
    assert snap["nav"] == {"index": 192, "total": 192, "live": False}
    assert "rewound" in snap["subtitle"]


def test_a_rewound_view_does_not_chase_the_live_draft_forward(poller):
    poller.replay = 150
    poller.refresh()
    poller.view_goto(150)
    poller.view_next()
    assert poller.snapshot()["nav"] == {"index": 150, "total": 150, "live": False}
    # The draft "continues" -- the replay cap lifts and more picks land.
    poller.replay = 160
    poller.feed["draft"] = json.loads(json.dumps(poller.feed["draft"]))
    poller.feed["draft"]["metadata"]["highest_offer"] = "5"
    poller.refresh()
    snap = poller.snapshot()
    assert snap["nav"]["total"] == 160
    # Still parked where it was left; the view does not follow live forward.
    assert snap["nav"]["index"] == 150
    poller.view_next()
    assert poller.snapshot()["nav"]["index"] == 151


def test_nav_endpoint_dispatches_and_validates(poller):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib import request as urlreq

    poller.refresh()
    server = ThreadingHTTPServer(("127.0.0.1", 0), live_mod._handler(poller))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path, body):
        req = urlreq.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlreq.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())

    try:
        status, body = post("/api/nav", {"action": "goto", "index": 30})
        assert status == 200
        assert body["nav"] == {"index": 30, "total": 192, "live": False}

        status, body = post("/api/nav", {"action": "live"})
        assert body["nav"]["live"] is True

        with pytest.raises(Exception):
            post("/api/nav", {"action": "bogus"})
    finally:
        server.shutdown()
        thread.join()


# -- the money band ----------------------------------------------------------


def test_before_the_first_pick_nobody_is_a_leader(poller):
    # Twelve seats holding the same $200: the ranking is entirely the
    # tie-break, so naming three of them is worse than naming none. This is the
    # state the board sits in for the half hour before a draft opens.
    poller.feed["picks"] = []
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = poller.feed["draft"]["creators"][0]
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    assert 'class="amt"' not in ledger
    assert "level with the room" in ledger
    assert "of 12)" not in ledger


def test_the_ledger_draws_every_seat_and_names_only_three(poller):
    poller.replay = 60
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    # One column and one seat tag per seat.
    assert ledger.count('class="col ') == 12
    # Three figures, because twelve would be a table.
    assert ledger.count('class="amt"') == 3


def test_the_band_header_reports_what_the_room_has_spent(poller):
    poller.refresh()
    snap = poller.snapshot()
    # The completed mock spends $2,344 of the $2,400 on the table.
    assert "$2,344" in snap["spend_html"]
    assert "of $2,400" in snap["spend_html"]
    # And it is out of the chart's way: the band is a fixed share of the column
    # now, so every line in it is competing with the plot for height.
    assert "$2,344" not in snap["ledger_html"]


def test_the_chart_is_drawn_in_shares_not_pixels(poller):
    # The plot takes whatever the band has left after the labels and the tags,
    # which is a height the server cannot know. A bar is a share of the budget,
    # and a share is the same fact at any height the browser hands it.
    poller.replay = 60
    poller.refresh()
    ledger = poller.snapshot()["ledger_html"]
    assert "px" not in ledger
    assert ledger.count("%") >= 12


def test_an_unseated_board_draws_no_line_and_marks_no_seat(poller):
    # A user the draft's order does not list. The band has to run anyway: this
    # is the state every pre-draft rehearsal is in.
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = "u-123"
    poller.refresh()
    snap = poller.snapshot()
    assert snap["my_seat"] is None
    assert 'class="gl"' not in snap["ledger_html"]
    assert "not seated in this draft" in snap["ledger_html"]


def test_the_three_ways_to_have_no_seat_do_not_look_alike(poller):
    """The bug this whole change exists for.

    An anonymous board, a mistyped username and a draft with no order used to
    render one identical unmarked chart, so a mistake was indistinguishable
    from the normal case -- and from a bug in the board.
    """
    said = {}

    poller.user = ""
    poller.refresh()
    said["anonymous"] = poller.snapshot()["ledger_html"]

    poller.user = "nobody"
    poller._user_tried = True
    poller._user_id = None
    poller.seat_note = "no Sleeper account named 'nobody'"
    poller._pulse = None  # force a rebuild; the feed itself has not moved
    poller.refresh()
    said["bad name"] = poller.snapshot()["ledger_html"]

    poller.user = "yidinghou"
    poller._user_id = "u-123"
    poller._pulse = None
    poller.refresh()
    said["unseated"] = poller.snapshot()["ledger_html"]

    assert "no --user given" in said["anonymous"]
    assert "no Sleeper account named" in said["bad name"]
    assert "not seated in this draft" in said["unseated"]
    assert len(set(said.values())) == 3


def test_the_board_names_the_draft_it_is_reading(poller):
    # Pointing at last week's finished mock is the mistake this catches, and it
    # is invisible without a label: every draft renders the same picture.
    poller.refresh()
    snap = poller.snapshot()
    assert snap["draft_label"].endswith("…ock-id")  # "mock-id" tail
    # Present before the first poll too, or the wrong id hides until it lands.
    fresh = DraftPoller("1391215167026511872", None, interval=0.01)
    assert "511872" in fresh.snapshot()["draft_label"]


def test_a_bare_command_line_still_knows_who_you_are():
    # The command that failed was exactly this one, with no --user at all.
    args = live_mod.build_parser().parse_args(["--draft-id", "123"])
    assert args.user == live_mod.DEFAULT_USER
    assert args.user  # and it is a real name, not None or ""


def test_the_user_flag_still_overrides_and_can_opt_out():
    parse = live_mod.build_parser().parse_args
    assert parse(["--draft-id", "1", "--user", "someone"]).user == "someone"
    assert parse(["--draft-id", "1", "--user", ""]).user == ""


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
    # Named, not just numbered: a username resolves through two lookups, and a
    # bare "S7" gives you no way to notice it landed on the wrong account.
    assert "<b>yidinghou</b> · <b>S7</b>" in snap["ledger_html"]


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


# -- who is in the bidding ----------------------------------------------------
#
# Sleeper publishes the offer on the clock and no history, so every one of these
# is about the board's own memory: what it saw, what it kept, and what it threw
# away when the room moved on.


def _bid(poller, *, player="KC", nominating=12, offering=12, amount="1"):
    """Rewrite the in-flight auction state on the recorded feed."""
    draft = json.loads(json.dumps(poller.feed["draft"]))
    draft["metadata"].update(
        nominated_player_id=player,
        nominating_slot=str(nominating) if nominating else "",
        offering_slot=str(offering) if offering else "",
        highest_offer=amount,
    )
    poller.feed["draft"] = draft


def test_the_nominator_is_in_the_bidding_from_the_start(poller):
    # Nominating a player is opening the bidding at a dollar, and a lot where
    # the nominator is still the only bidder is a common and worth-seeing state.
    poller.refresh()
    assert poller._bidders == {12: 1}
    assert 'class="chip bid-high"' in poller.snapshot()["nomination_html"]


def test_a_seat_that_is_outbid_stays_in_the_bidding(poller):
    # The whole reason this is accumulated rather than read: the API forgets
    # seat 12 the instant seat 4 raises, and "who wants this player" does not.
    poller.refresh()
    _bid(poller, offering=4, amount="9")
    poller.refresh()
    assert poller._bidders == {12: 1, 4: 9}
    html = poller.snapshot()["nomination_html"]
    assert html.count('class="chip') == 2
    assert html.count('class="chip bid-high"') == 1  # only one holds it now


def test_each_seat_keeps_the_highest_figure_it_was_seen_holding(poller):
    """Sleeper reports one seat at one figure, so the amount on a chip is the
    furthest that seat was *watched* getting -- a floor on what they would pay,
    not a bid anybody published. A seat that leads, loses it and leads again
    keeps the later figure and its original place in the row."""
    poller.refresh()                                    # S12 opens at $1
    _bid(poller, offering=4, amount="9")
    poller.refresh()                                    # S4 takes it at $9
    _bid(poller, offering=12, amount="14")
    poller.refresh()                                    # S12 back in front
    _bid(poller, offering=4, amount="20")
    poller.refresh()                                    # S4 again, higher
    assert poller._bidders == {12: 14, 4: 20}
    assert list(poller._bidders) == [12, 4]  # entry order, not bid order
    html = poller.snapshot()["nomination_html"]
    chips = re.search(r'<div class="bidders">.*?</div>', html).group()
    assert "$14" in chips and "$20" in chips
    assert "$9" not in chips  # superseded by the same seat's later figure
    # The trail is the one place $9 is expected to survive: it is a real raise
    # that happened, even though the chip above it moved on.
    trail = re.search(r'<div class="trail">.*?</div>', html).group()
    assert "$9" in trail and "$14" in trail and "$20" in trail


def test_a_new_player_on_the_block_empties_the_list(poller):
    poller.refresh()
    _bid(poller, offering=4)
    poller.refresh()
    _bid(poller, player="4034", nominating=7, offering=7)
    poller.refresh()
    assert poller._bidders == {7: 1}


def test_a_closing_lot_is_archived_into_bid_history(poller):
    # The whole point of the archive: the field of bidders a closed lot had is
    # gone from `_bidders` the instant the room moves on, so a checkpoint that
    # rewinds to the pick it became needs it kept somewhere that outlives that.
    poller.refresh()               # S12 opens KC at $1
    _bid(poller, offering=4, amount="9")
    poller.refresh()               # S4 takes it at $9
    _bid(poller, player="4034", nominating=7, offering=7)
    poller.refresh()               # a new lot opens; KC's is done
    assert poller._bid_history == {"KC": {12: 1, 4: 9}}
    assert poller._bidders == {7: 1}


def test_a_lot_closing_empties_the_list(poller):
    poller.refresh()
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()
    assert poller._lot is None
    assert poller._bidders == {}
    assert "Nothing nominated" in poller.snapshot()["nomination_html"]
    # Nothing on the block is a lot closing too, and its bidders are worth
    # keeping the same as any other -- the KC lot from the fixture's default
    # nomination, archived under the player rather than under "nothing".
    assert poller._bid_history == {"KC": {12: 1}}


# -- and what outlives the process --------------------------------------------


def _watch_bo_nix_close(poller):
    """Watch pick #30 (Bo Nix, 11563, S6, $28) get bid up and close.

    Three raises and a close, which is the shape every test down here needs:
    `_bid_history` ends up `{6: 28, 9: 25}` and the trail ends up four rungs
    deep. `replay` walks 29 -> 30 so the pick lands as the lot closes, the way
    it would live.
    """
    poller.replay = 29
    poller.refresh()
    _bid(poller, player="11563", nominating=6, offering=6, amount="20")
    poller.refresh()                                    # S6 opens at $20
    _bid(poller, player="11563", nominating=6, offering=9, amount="25")
    poller.refresh()                                    # S9 raises to $25
    _bid(poller, player="11563", nominating=6, offering=6, amount="28")
    poller.refresh()                                    # S6 retakes at $28
    # The pick lands. `last_picked` moves so the board rebuilds, but the
    # nomination has not cleared on the payload yet -- which is the window a
    # checkpoint tapped the instant a pick lands falls into.
    poller.replay = 30
    poller.feed["draft"] = dict(poller.feed["draft"], last_picked=1)
    poller.refresh()


def test_a_watched_lot_still_has_its_trail_in_the_next_process(poller):
    """The reported bug. Rewinding after the draft showed one sold rung for
    every lot, because the bidding lived only in the process that watched it and
    Sleeper will not tell a second one. Now it is on disk."""
    _watch_bo_nix_close(poller)
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()                                    # the lot closes

    later = DraftPoller("mock-id", None, interval=0.01)
    assert later._bid_history["11563"] == {6: 28, 9: 25}
    later.refresh()
    later.view_goto(30)
    html = later.snapshot()["nomination_html"]
    assert "Bo Nix" in html
    # The same four rungs the watching process drew, not the lone sold one.
    assert html.count('<span class="rung') == 4
    assert "$25" in html


def test_a_lot_nobody_ever_watched_still_shows_only_its_price(poller):
    """The other half of the same guarantee: the board must not invent a trail
    for a lot no run of it saw. An honest single rung is the answer there."""
    poller.refresh()
    poller.view_goto(30)
    html = poller.snapshot()["nomination_html"]
    assert html.count('<span class="rung') == 1
    assert 'class="rung sold"' in html
def _cards(pressure_html):
    """The pressure band split into {pos: card html}.

    Split on the card element, not on `data-pos` -- the tier rows inside a card
    carry that attribute too, so keying off it lands on the last match in the
    card rather than the card.
    """
    out = {}
    for chunk in pressure_html.split('<section class="pcard')[1:]:
        pos = chunk.split('data-pos="')[1].split('"')[0]
        out[pos] = chunk
    return out


def test_the_lights_reach_the_chart_and_the_tiles(poller):
    # One fact, marked in both places it is asked about: the chart answers "who
    # can outbid me" and the tiles answer "who else needs this position", and
    # while a lot is live both questions are about the seat that is bidding.
    poller.replay = 60  # mid-auction, where a seat is neither broke nor done
    _bid(poller, player="4034", nominating=12, offering=12)  # McCaffrey, an RB
    poller.refresh()
    _bid(poller, player="4034", offering=4, amount="9")
    poller.refresh()
    snap = poller.snapshot()
    assert 'class="col bid-high"' in snap["ledger_html"]
    assert 'class="col bid-in"' in snap["ledger_html"]
    assert 'class="bid-high"' in snap["ledger_html"]  # and the tag under it
    # `bid-*` rides on top of `done`, so match the class list rather than a
    # literal: a seat that has filled its RBs can still be bidding on one.
    assert re.search(r'class="tile[^"]*\bbid-high"', snap["pressure_html"])
    assert re.search(r'class="tile[^"]*\bbid-in"', snap["pressure_html"])


def test_only_the_nominee_s_own_card_lights_its_bidders(poller):
    """The bidding is on one player, and that player has one position. The same
    amber on all four cards said "these seats are bidding" four times over and
    answered the question the grid is for -- who is in on *this* run -- in none
    of them."""
    poller.replay = 60
    _bid(poller, player="4034", nominating=12, offering=12)  # McCaffrey, an RB
    poller.refresh()
    _bid(poller, player="4034", offering=4, amount="9")
    poller.refresh()
    cards = _cards(poller.snapshot()["pressure_html"])
    assert set(cards) == {"QB", "RB", "WR", "TE"}
    assert re.search(r'class="tile[^"]*\bbid-high"', cards["RB"])
    assert re.search(r'class="tile[^"]*\bbid-in"', cards["RB"])
    for pos in ("QB", "WR", "TE"):
        assert "bid-high" not in cards[pos]
        assert "bid-in" not in cards[pos]


def test_a_position_with_no_card_lights_nothing(poller):
    """A defense on the block belongs to none of the four runs, so none of them
    light -- rather than all of them, which is what a bare `seat.bidding` did."""
    poller.replay = 60
    poller.refresh()  # the feed nominates "KC", the Chiefs defense
    snap = poller.snapshot()
    assert "DEF" in snap["nomination_html"] or "Chiefs" in snap["nomination_html"]
    assert "bid-high" not in snap["pressure_html"]
    assert "bid-in" not in snap["pressure_html"]
    # ...but the money chart still marks them: "who can outbid me" is a question
    # about budgets, and budgets do not have a position.
    assert 'class="col bid-high"' in snap["ledger_html"]


def test_nothing_lights_between_lots(poller):
    poller.replay = 60
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()
    assert "bid-high" not in poller.snapshot()["pressure_html"]


def test_bidding_is_marked_on_top_of_being_yours_or_broke(poller):
    # A third fact, not a fourth state. Your own seat can be bidding and so can
    # a seat that is nearly broke -- and those are the two you most want to see
    # at once, so neither may be overwritten by the other.
    poller.feed["draft"] = dict(poller.feed["draft"], draft_order={"u-123": 12})
    poller.user = "yidinghou"
    poller._user_tried = True
    poller._user_id = "u-123"
    poller.replay = 60
    poller.refresh()
    assert 'class="col me bid-high"' in poller.snapshot()["ledger_html"]


def test_the_board_looks_faster_while_a_player_is_on_the_block(poller):
    # The only window in which something the board cannot reconstruct later goes
    # past: the picks are still there in an hour, a bid that was outbid is not.
    poller.interval = 3.0
    poller.refresh()
    assert poller.wait_for() == live_mod.LIVE_INTERVAL
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()
    assert poller.wait_for() == 3.0


def test_a_faster_interval_than_the_lot_rate_is_left_alone(poller):
    # `--interval 0.5` asked for half a second everywhere; this must never be
    # the thing that slows a board down.
    poller.interval = 0.5
    poller.refresh()
    assert poller.wait_for() == 0.5
