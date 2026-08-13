"""The shape of the league.

Twelve seats, $200 apiece, sixteen roster slots of which ten start. This file
pins down which positions each slot will take, how many places on the field a
position could ever occupy, and what makes a proposed league not a league at
all.

Every claim here is arithmetic over a written-down template, so everything is
declared on the page — no spreadsheet is read.
"""

import dataclasses

import pytest

from draftsim.league import LeagueRules, slot_accepts


# --- What the sixteen slots are ------------------------------------------


def test_the_league_starts_ten_players_and_benches_six():
    league = LeagueRules()

    assert league.roster_size == 16
    assert league.starting_slots == (
        "QB",
        "RB",
        "RB",
        "WR",
        "WR",
        "TE",
        "FLEX",
        "REC_FLEX",
        "SUPER_FLEX",
        "DEF",
    )


def test_a_concrete_slot_takes_only_its_own_position():
    assert slot_accepts("QB", "QB")
    assert not slot_accepts("QB", "RB")
    assert not slot_accepts("DEF", "K")


def test_the_flex_takes_a_back_a_receiver_or_a_tight_end():
    assert slot_accepts("FLEX", "RB")
    assert slot_accepts("FLEX", "WR")
    assert slot_accepts("FLEX", "TE")
    assert not slot_accepts("FLEX", "QB")


def test_the_superflex_takes_a_quarterback_but_the_receiver_flex_does_not():
    assert slot_accepts("SUPER_FLEX", "QB")
    assert not slot_accepts("REC_FLEX", "QB")
    assert not slot_accepts("REC_FLEX", "RB")


def test_no_flex_will_take_a_kicker_or_a_defense():
    for flex in ("FLEX", "REC_FLEX", "SUPER_FLEX", "WRRB_FLEX"):
        assert not slot_accepts(flex, "K")
        assert not slot_accepts(flex, "DEF")


def test_a_back_receiver_flex_is_understood_even_though_this_league_has_none():
    assert slot_accepts("WRRB_FLEX", "WR")
    assert slot_accepts("WRRB_FLEX", "RB")
    assert not slot_accepts("WRRB_FLEX", "TE")

    assert "WRRB_FLEX" not in LeagueRules().starting_slots


# --- How many places on the field a position could ever occupy ------------


def test_a_kicker_can_never_start():
    assert LeagueRules().slots_a_position_can_fill("K") == 0


def test_a_second_defense_can_never_start():
    assert LeagueRules().slots_a_position_can_fill("DEF") == 1


def test_the_flexes_give_receivers_five_places_to_start_and_backs_four():
    league = LeagueRules()

    assert league.slots_a_position_can_fill("WR") == 5
    assert league.slots_a_position_can_fill("RB") == 4
    assert league.slots_a_position_can_fill("TE") == 4
    assert league.slots_a_position_can_fill("QB") == 2


def test_the_bench_is_not_a_place_to_start():
    league = LeagueRules(roster_template=("QB", "BN", "BN"))

    assert league.slots_a_position_can_fill("QB") == 1


# --- What is not a league -------------------------------------------------


def test_a_league_with_nobody_at_the_table_is_not_a_league():
    with pytest.raises(ValueError):
        LeagueRules(seats=0)


def test_a_league_with_no_roster_slots_is_not_a_league():
    with pytest.raises(ValueError):
        LeagueRules(roster_template=())


def test_a_league_where_players_are_free_is_not_a_league():
    with pytest.raises(ValueError):
        LeagueRules(minimum_bid=0)


def test_a_budget_that_cannot_afford_a_dollar_a_slot_is_not_a_league():
    with pytest.raises(ValueError):
        LeagueRules(budget=15)


def test_a_budget_of_exactly_a_dollar_a_slot_is_a_league():
    league = LeagueRules(budget=16)

    assert league.budget == 16


# --- The rules are a fixed thing ------------------------------------------


def test_the_rules_cannot_be_changed_once_the_draft_is_under_way():
    league = LeagueRules()

    with pytest.raises(dataclasses.FrozenInstanceError):
        league.budget = 500


def test_the_league_carries_no_picks_no_clock_and_nobody_on_the_block():
    """The league is only what everyone agreed to before the draft started.

    How far along a draft is, whose turn it is and who is on the block are all
    read off the record of sales; none of them is a rule of the league. And
    because the rules hold nothing that moves, one seat's copy of them is the
    same league every other seat is playing.
    """
    league = LeagueRules()

    for the_draft_so_far in (
        "picks",
        "sales",
        "spent",
        "clock",
        "deadline",
        "on_the_block",
        "nominating_seat",
        "turn",
    ):
        assert not hasattr(league, the_draft_so_far)

    assert LeagueRules() == league
