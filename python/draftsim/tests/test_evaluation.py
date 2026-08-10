from __future__ import annotations

from conftest import deep_pool, make_draft, make_pool

from draftsim import DraftRules
from draftsim.evaluation import (
    dollars_per_point,
    marginal_points,
    max_sensible_bid,
    positional_need,
    replacement_points,
)


def test_a_player_is_worth_what_he_adds_to_an_empty_lineup():
    players, proj = make_pool(("Chase", "WR", 300.0))
    draft = make_draft(players, proj, teams=2)
    assert marginal_points(draft.state, "t0", players["Chase|WR"]) == 300.0


def test_an_extra_elite_tight_end_is_worth_nothing_to_you_and_a_lot_to_them():
    players, proj = make_pool(
        ("TE1", "TE", 250.0),
        ("TE2", "TE", 240.0),
        ("TE3", "TE", 230.0),
        ("TE4", "TE", 220.0),
        ("TE5", "TE", 210.0),
    )
    draft = make_draft(players, proj, teams=2)
    # A TE can start in four slots: TE, FLEX, REC_FLEX, SUPER_FLEX. Fill all
    # four and the next one has nowhere to go.
    for i, name in enumerate(["TE1", "TE2", "TE3", "TE4"]):
        draft.record_pick("t0", f"{name}|TE", 20, float(i))

    te5 = players["TE5|TE"]
    assert marginal_points(draft.state, "t0", te5) == 0.0
    assert marginal_points(draft.state, "t1", te5) == 210.0


def test_a_player_who_cannot_crack_the_lineup_is_worth_zero():
    players, proj = make_pool(("D1", "DEF", 120.0), ("D2", "DEF", 110.0))
    draft = make_draft(players, proj, teams=2)
    draft.record_pick("t0", "D1|DEF", 5, 0.0)
    # One DEF slot, no flex accepts a DEF: the second one can never start.
    assert marginal_points(draft.state, "t0", players["D2|DEF"]) == 0.0


def test_marginal_value_falls_as_you_fill_a_position():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    wr = players["WR10|WR"]
    before = marginal_points(draft.state, "t0", wr)
    for i in range(5):
        draft.record_pick("t0", f"WR0{i}|WR", 10, float(i))
    after = marginal_points(draft.state, "t0", wr)
    assert after < before


def test_replacement_is_the_first_player_past_what_the_league_starts():
    players, proj = deep_pool(per_position=60)
    replacement = replacement_points(players.values(), DraftRules(), proj)
    # 12 teams x 2.00 QB = QB #24 (0-indexed), whose points are 60 - 24 = 36.
    assert replacement["QB"] == 36.0
    # 12 x 2.77 = 33.24 -> round to 33; 12 x 3.23 = 38.76 -> 39.
    assert replacement["RB"] == 60 - 33
    assert replacement["WR"] == 60 - 39


def test_an_unstartable_position_has_no_replacement_level():
    players, proj = deep_pool(positions=("QB", "RB", "WR", "TE", "DEF", "K"))
    replacement = replacement_points(players.values(), DraftRules(), proj)
    assert replacement["K"] == float("inf")


def test_a_shallow_position_falls_back_to_zero():
    players, proj = make_pool(("QB1", "QB", 300.0))
    replacement = replacement_points(players.values(), DraftRules(), proj)
    assert replacement["QB"] == 0.0  # fewer players than the league starts


def test_dollars_per_point_prices_the_leagues_discretionary_money():
    players, proj = deep_pool(per_position=60)
    rules = DraftRules()
    rate = dollars_per_point(players.values(), rules, proj)
    # 12 teams x ($200 - $16 reserved) = $2208 of biddable money, spread over
    # the value-above-replacement of the players who will actually be drafted.
    assert 0 < rate < 10

    # Double the money and every point costs twice as much.
    richer = dollars_per_point(
        players.values(), DraftRules(budget=400), proj
    )
    assert richer > rate


def test_max_sensible_bid_never_exceeds_what_is_legal():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    rate = dollars_per_point(players.values(), draft.rules, proj)
    for player in list(players.values())[:40]:
        bid = max_sensible_bid(draft.state, "t0", player, rate)
        assert 0 <= bid <= draft.team_state("t0").max_bid(draft.rules)


def test_a_worthless_player_is_still_worth_the_minimum():
    players, proj = make_pool(("D1", "DEF", 120.0), ("D2", "DEF", 110.0))
    draft = make_draft(players, proj, teams=2)
    draft.record_pick("t0", "D1|DEF", 5, 0.0)
    assert max_sensible_bid(draft.state, "t0", players["D2|DEF"], 1.0) == 1


def test_positional_need_counts_down_as_a_roster_fills():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    assert positional_need(draft.state, "t0")["QB"] == 2
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    assert positional_need(draft.state, "t0")["QB"] == 1
    draft.record_pick("t0", "QB01|QB", 30, 1.0)
    assert positional_need(draft.state, "t0")["QB"] == 0
    draft.record_pick("t0", "QB02|QB", 5, 2.0)
    assert positional_need(draft.state, "t0")["QB"] == 0  # depth, never a need
