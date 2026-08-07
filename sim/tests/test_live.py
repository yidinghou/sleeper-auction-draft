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


def test_a_new_player_on_the_block_empties_the_list(poller):
    poller.refresh()
    _bid(poller, offering=4)
    poller.refresh()
    _bid(poller, player="4034", nominating=7, offering=7)
    poller.refresh()
    assert poller._bidders == {7: 1}


def test_a_lot_closing_empties_the_list(poller):
    poller.refresh()
    _bid(poller, player="", nominating=None, offering=None)
    poller.refresh()
    assert poller._lot is None
    assert poller._bidders == {}
    assert "Nothing nominated" in poller.snapshot()["nomination_html"]


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
