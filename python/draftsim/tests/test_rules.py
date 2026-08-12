from __future__ import annotations

import pytest

from draftsim import BENCH, DraftRules
from draftsim.rules import slot_shares


def test_default_rules_are_the_real_league():
    rules = DraftRules()
    assert rules.teams == 12
    assert rules.budget == 200
    assert rules.min_bid == 1
    assert rules.roster_size == 16
    assert len(rules.slots) == 10  # 16 slots, 6 of them bench


def test_slots_excludes_the_bench():
    assert BENCH not in DraftRules().slots


def test_a_budget_that_cannot_fill_the_roster_is_rejected():
    with pytest.raises(ValueError, match="must be >="):
        DraftRules(budget=10)


def test_zero_teams_is_rejected():
    with pytest.raises(ValueError, match="teams must be"):
        DraftRules(teams=0)


def test_flex_slots_accept_their_positions_and_nothing_else():
    rules = DraftRules()
    assert rules.accepts("FLEX", "RB")
    assert rules.accepts("FLEX", "TE")
    assert not rules.accepts("FLEX", "QB")
    assert rules.accepts("SUPER_FLEX", "QB")
    assert rules.accepts("REC_FLEX", "WR")
    assert not rules.accepts("REC_FLEX", "RB")
    assert rules.accepts("QB", "QB")
    assert not rules.accepts("QB", "RB")


def test_startable_slots_counts_the_flexes_a_position_can_reach():
    rules = DraftRules()
    assert rules.startable_slots("WR") == 5  # 2 WR + FLEX + REC_FLEX + SUPER_FLEX
    assert rules.startable_slots("RB") == 4
    assert rules.startable_slots("TE") == 4
    assert rules.startable_slots("QB") == 2
    assert rules.startable_slots("DEF") == 1
    assert rules.startable_slots("K") == 0  # the default lineup never starts one


def test_slot_shares_splits_one_slot_across_the_positions_that_want_it():
    assert slot_shares("QB") == {"QB": 1.0}  # a concrete slot is one whole player
    assert slot_shares("FLEX") == {"RB": 0.77, "WR": 0.23}
    assert slot_shares("SUPER_FLEX") == {"QB": 1.0}  # measured, not eligibility
    assert sum(slot_shares("FLEX").values()) == pytest.approx(1.0)


def test_slot_shares_is_what_starter_shares_is_built_from():
    # Not an implementation detail -- replacement level counts the slots still
    # open the same way the template counts all of them, so they must agree.
    rules = DraftRules()
    summed: dict = {}
    for slot in rules.slots:
        for pos, share in slot_shares(slot).items():
            summed[pos] = summed.get(pos, 0.0) + share
    shares = rules.starter_shares()
    for pos, share in summed.items():
        assert shares[pos] == pytest.approx(share)


def test_starter_shares_sum_to_the_starting_lineup():
    shares = DraftRules().starter_shares()
    assert sum(shares.values()) == pytest.approx(10.0)
    assert shares["QB"] == pytest.approx(2.0)
    assert shares["RB"] == pytest.approx(2.77)
    assert shares["WR"] == pytest.approx(3.23)


def test_starter_counts_round_the_shares_to_whole_players():
    counts = DraftRules().starter_counts()
    assert counts == {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DEF": 1}
    assert sum(counts.values()) == 10
