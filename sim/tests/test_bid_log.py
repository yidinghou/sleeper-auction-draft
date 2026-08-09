"""The bid sidecar: what survives a restart, and what a bad file costs.

Every test writes to `tmp_path`. The board's real `data/` holds the log for
drafts that actually happened, and a suite that scribbled in it would corrupt
the one thing this module exists to protect.
"""

import json

from draftsim.bid_log import BidLog


def test_a_closed_lot_survives_a_new_instance(tmp_path):
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22, 7: 31}, [(3, None), (3, 18), (7, 24), (3, 28), (7, 31)])

    bidders, events = BidLog("d1", tmp_path).get("4034")
    assert bidders == {3: 22, 7: 31}
    assert events == [(3, None), (3, 18), (7, 24), (3, 28), (7, 31)]


def test_events_come_back_as_tuples(tmp_path):
    """`_bid_timeline` compares `events[-1]` against the settled `(slot, price)`
    to keep from drawing the closing rung twice. Lists never match."""
    BidLog("d1", tmp_path).put("4034", {7: 31}, [(7, 31)])
    _, events = BidLog("d1", tmp_path).get("4034")
    assert events[-1] == (7, 31)


def test_the_nominators_missing_figure_round_trips(tmp_path):
    """`None` is not zero: it means seen in the bidding with no figure attached,
    and the panel draws it differently."""
    BidLog("d1", tmp_path).put("99", {5: None}, [(5, None)])
    bidders, events = BidLog("d1", tmp_path).get("99")
    assert bidders == {5: None}
    assert events == [(5, None)]


def test_a_second_lot_does_not_displace_the_first(tmp_path):
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22}, [(3, 22)])
    log.put("5555", {9: 4}, [(9, 4)])

    reread = BidLog("d1", tmp_path)
    assert reread.get("4034")[0] == {3: 22}
    assert reread.get("5555")[0] == {9: 4}


def test_a_lot_nobody_watched_is_not_written(tmp_path):
    """A run that saw no bidding knows less than the file does. Its silence
    must not overwrite an earlier run's witness."""
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22}, [(3, 22)])
    log.put("4034", {}, [])

    assert BidLog("d1", tmp_path).get("4034")[0] == {3: 22}


def test_a_shorter_trail_does_not_truncate_a_longer_one(tmp_path):
    """Restart the board mid-lot and it sees only whoever is in front now -- a
    suffix of the lot. That must not replace the run that watched the whole
    thing."""
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22, 7: 31}, [(3, None), (3, 18), (7, 24), (7, 31)])
    log.put("4034", {7: 31}, [(7, 31)])

    bidders, events = BidLog("d1", tmp_path).get("4034")
    assert bidders == {3: 22, 7: 31}
    assert len(events) == 4


def test_a_longer_trail_replaces_a_shorter_one(tmp_path):
    """The other direction, or the rule would freeze the first witness in
    place: a run that saw more is the better record."""
    log = BidLog("d1", tmp_path)
    log.put("4034", {7: 31}, [(7, 31)])
    log.put("4034", {3: 22, 7: 31}, [(3, None), (3, 18), (7, 24), (7, 31)])

    bidders, events = BidLog("d1", tmp_path).get("4034")
    assert bidders == {3: 22, 7: 31}
    assert len(events) == 4


def test_an_empty_put_on_a_fresh_log_writes_no_file(tmp_path):
    log = BidLog("d1", tmp_path)
    log.put("4034", {}, [])
    assert not log.path.exists()


def test_an_unwatched_lot_reads_back_empty(tmp_path):
    """Not an error — the honest answer for a lot no run ever saw, and the one
    the renderer already draws as a lone sold rung."""
    assert BidLog("d1", tmp_path).get("nobody") == ({}, [])
    assert BidLog("d1", tmp_path).get(None) == ({}, [])


def test_a_corrupt_file_costs_the_history_and_nothing_else(tmp_path):
    (tmp_path / "bid-log-d1.json").write_text("{not json at all")
    log = BidLog("d1", tmp_path)
    assert log.lots == {}
    # And it is still writable: the bad file gets replaced, not worked around.
    log.put("4034", {3: 22}, [(3, 22)])
    assert BidLog("d1", tmp_path).get("4034")[0] == {3: 22}


def test_one_malformed_lot_does_not_take_the_others_with_it(tmp_path):
    (tmp_path / "bid-log-d1.json").write_text(
        json.dumps(
            {
                "draft_id": "d1",
                "lots": {
                    "good": {"bidders": {"3": 22}, "events": [[3, 22]]},
                    "bad": "not a lot at all",
                    "half": {"bidders": {"x": "y"}, "events": [[1, 2, 3], "nope"]},
                },
            }
        )
    )
    log = BidLog("d1", tmp_path)
    assert log.get("good")[0] == {3: 22}
    assert log.get("bad") == ({}, [])
    assert log.get("half") == ({}, [])


def test_the_file_is_per_draft(tmp_path):
    """Last year's league draft sat the same twelve people in different chairs,
    and player_ids repeat across drafts."""
    BidLog("d1", tmp_path).put("4034", {3: 22}, [(3, 22)])
    assert BidLog("d2", tmp_path).get("4034") == ({}, [])


def test_the_mutation_is_wholesale(tmp_path):
    """A reader holding the old map must see a consistent older answer, not a
    half-written one — the poll thread writes while handlers read."""
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22}, [(3, 22)])
    held = log.lots
    log.put("5555", {9: 4}, [(9, 4)])
    assert "5555" not in held


def test_a_returned_trace_is_a_copy(tmp_path):
    log = BidLog("d1", tmp_path)
    log.put("4034", {3: 22}, [(3, 22)])
    bidders, events = log.get("4034")
    bidders[3] = 999
    events.append((1, 1))
    assert log.get("4034") == ({3: 22}, [(3, 22)])
