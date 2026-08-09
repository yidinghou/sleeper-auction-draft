"""Parsing a real Sleeper draft feed, against recorded responses.

Fixtures are verbatim captures of the two drafts this project uses: a finished
12-team mock and the league's real draft before it started. Recording them
keeps these tests offline and deterministic — and pins the shapes the live
board depends on, so a change on Sleeper's side fails here rather than at the
table on draft night.
"""

import json
from pathlib import Path

import pytest

from draftsim.config import DEFAULT_ROSTER_SLOTS, BENCH
from liveboard.sleeper import (
    SleeperError,
    config_from_draft,
    draft_pulse,
    parse_nomination,
    seat_for_user,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def mock_draft():
    return _load("draft-mock")


@pytest.fixture(scope="module")
def real_draft():
    return _load("draft-real")


@pytest.fixture(scope="module")
def mock_picks():
    return _load("picks-mock")


# -- config ------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["draft-mock", "draft-real"])
def test_config_matches_the_leagues_default_roster(fixture):
    # Both drafts are the same league, and its settings are what
    # DEFAULT_ROSTER_SLOTS was written from -- so deriving the config from the
    # API must reproduce it exactly, slot for slot and in order.
    config = config_from_draft(_load(fixture))
    assert config.teams == 12
    assert config.budget == 200
    assert config.roster_slots == DEFAULT_ROSTER_SLOTS


def test_slots_are_ordered_starters_then_bench(mock_draft):
    slots = config_from_draft(mock_draft).roster_slots
    bench_at = [i for i, s in enumerate(slots) if s == BENCH]
    assert bench_at == list(range(min(bench_at), len(slots)))


def test_unmodelled_slot_is_rejected_not_dropped(mock_draft):
    # Silently ignoring a slot would shrink every roster and so inflate every
    # max bid -- a wrong number is worse than a refusal here.
    draft = json.loads(json.dumps(mock_draft))
    draft["settings"]["slots_idp_flex"] = 2
    with pytest.raises(SleeperError, match="slots_idp_flex"):
        config_from_draft(draft)


def test_an_empty_unmodelled_slot_is_harmless(mock_draft):
    draft = json.loads(json.dumps(mock_draft))
    draft["settings"]["slots_idp_flex"] = 0
    assert config_from_draft(draft).roster_slots == DEFAULT_ROSTER_SLOTS


def test_non_auction_drafts_are_refused(mock_draft):
    draft = json.loads(json.dumps(mock_draft))
    draft["type"] = "snake"
    with pytest.raises(SleeperError, match="snake"):
        config_from_draft(draft)


# -- nomination --------------------------------------------------------------


def test_nomination_reads_the_in_flight_auction(mock_draft):
    nom = parse_nomination(mock_draft)
    assert nom.is_live
    # Team defenses use the team abbreviation as their player_id.
    assert nom.player_id == "KC"
    assert nom.nominating_slot == 12
    assert nom.high_bid == 1
    assert nom.offering_slot == 12


def test_no_nomination_before_the_draft_starts(real_draft):
    nom = parse_nomination(real_draft)
    assert not nom.is_live
    assert nom.player_id is None
    assert nom.high_bid is None


def test_blank_metadata_strings_read_as_absent(mock_draft):
    # Sleeper clears these to "" between lots rather than removing the keys.
    draft = json.loads(json.dumps(mock_draft))
    draft["metadata"].update(
        {"nominated_player_id": "", "highest_offer": "", "offering_slot": ""}
    )
    nom = parse_nomination(draft)
    assert not nom.is_live
    assert nom.high_bid is None
    assert nom.offering_slot is None


# -- pulse -------------------------------------------------------------------


def test_pulse_is_stable_for_an_unchanged_draft(mock_draft):
    assert draft_pulse(mock_draft) == draft_pulse(mock_draft)


def test_pulse_moves_when_the_bidding_moves(mock_draft):
    draft = json.loads(json.dumps(mock_draft))
    draft["metadata"]["highest_offer"] = "42"
    assert draft_pulse(draft) != draft_pulse(mock_draft)


def test_pulse_moves_when_a_pick_settles(mock_draft):
    draft = json.loads(json.dumps(mock_draft))
    draft["last_picked"] = (draft.get("last_picked") or 0) + 1
    assert draft_pulse(draft) != draft_pulse(mock_draft)


# -- the recorded feeds themselves -------------------------------------------


def test_mock_feed_is_a_complete_auction(mock_draft, mock_picks):
    config = config_from_draft(mock_draft)
    assert mock_draft["status"] == "complete"
    assert len(mock_picks) == config.teams * config.roster_size
    assert all(p["metadata"]["amount"] for p in mock_picks)


def test_picks_identify_seats_by_slot_not_user(mock_picks):
    # Mock drafts leave picked_by empty and roster_id null, which is why the
    # board keys on draft_slot.
    assert all(p["picked_by"] == "" for p in mock_picks)
    assert {p["draft_slot"] for p in mock_picks} == set(range(1, 13))


# -- finding your own seat ---------------------------------------------------


def test_a_seated_user_resolves_to_their_slot():
    draft = {"draft_order": {"abc123": 7, "def456": 3}}
    assert seat_for_user(draft, "abc123") == 7


def test_the_recorded_mock_seats_its_creator(mock_draft):
    # A mock does publish an order, for the one human in it — which is why
    # --user resolves against a rehearsal and not only on draft night. The
    # empty `picked_by` on its picks is a separate hole and stays one.
    creator = mock_draft["creators"][0]
    assert seat_for_user(mock_draft, creator) == mock_draft["draft_order"][creator]


def test_a_draft_with_no_order_yields_no_seat(real_draft):
    # The real draft had not been seated when it was captured. Unknown, not
    # absent — the board goes unmarked rather than guessing a slot.
    assert real_draft.get("draft_order") is None
    assert seat_for_user(real_draft, "abc123") is None


def test_a_user_not_in_the_order_yields_no_seat():
    assert seat_for_user({"draft_order": {"abc123": 7}}, "nobody") is None
