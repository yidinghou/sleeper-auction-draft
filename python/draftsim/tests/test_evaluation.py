from __future__ import annotations

import pytest
from conftest import deep_pool, make_draft, make_pool

from draftsim import DraftRules
from draftsim.evaluation import (
    Market,
    dollars_per_point,
    marginal_points,
    max_sensible_bid,
    positional_need,
    remaining_starter_shares,
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


def test_at_the_open_remaining_need_is_the_whole_template():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    shares = remaining_starter_shares(draft.state)
    per_team = DraftRules().starter_shares()
    for position, share in per_team.items():
        assert shares[position] == pytest.approx(share * 12)


def test_a_spent_superflex_stops_counting_as_quarterback_demand():
    # The case that makes this count slots instead of subtracting starters from
    # shares. Four running backs fill RB, RB, FLEX and SUPER_FLEX -- so this team
    # needs one more QB, not two, and subtracting RBs from the RB share would
    # never have noticed.
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    assert remaining_starter_shares(draft.state)["QB"] == pytest.approx(24.0)

    for i in range(4):
        draft.record_pick("t0", f"RB{i:02d}|RB", 5, float(i))

    shares = remaining_starter_shares(draft.state)
    assert shares["QB"] == pytest.approx(23.0)  # one seat's superflex is gone
    assert shares["RB"] == pytest.approx(33.24 - 2.77)
    assert shares["WR"] == pytest.approx(38.76 - 0.23)  # the FLEX was 23% WR


def test_demand_falls_to_zero_once_every_team_is_covered():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    for i in range(12):
        draft.record_pick(f"t{i}", f"DEF{i:02d}|DEF", 2, float(i))

    assert remaining_starter_shares(draft.state)["DEF"] == 0.0
    # Nobody has a slot left, so the best defense available *is* the bar -- there
    # is no surplus to pay for at a position the league is done buying.
    best_left = max(
        proj[p.id] for p in players.values()
        if p.position == "DEF" and p.id not in draft.state.owner
    )
    assert replacement_points(draft.state)["DEF"] == best_left


def test_replacement_is_the_first_player_past_what_the_league_starts():
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    replacement = replacement_points(draft.state)
    # Nothing drafted, so remaining need is the whole template: 12 teams x 2.00
    # QB = QB #24 (0-indexed), whose points are 60 - 24 = 36.
    assert replacement["QB"] == 36.0
    # 12 x 2.77 = 33.24 -> round to 33; 12 x 3.23 = 38.76 -> 39.
    assert replacement["RB"] == 60 - 33
    assert replacement["WR"] == 60 - 39


def test_an_unstartable_position_has_no_replacement_level():
    players, proj = deep_pool(positions=("QB", "RB", "WR", "TE", "DEF", "K"))
    draft = make_draft(players, proj)
    # Structural, so it never moves with the draft: no slot accepts a kicker.
    assert replacement_points(draft.state)["K"] == float("inf")


def test_a_shallow_position_falls_back_to_zero():
    players, proj = make_pool(("QB1", "QB", 300.0))
    draft = make_draft(players, proj)
    replacement = replacement_points(draft.state)
    assert replacement["QB"] == 0.0  # fewer players than the league starts


def test_dollars_per_point_prices_the_leagues_discretionary_money():
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    rate = dollars_per_point(draft.state)
    # 12 teams x ($200 - $16 reserved) = $2208 of biddable money, spread over
    # the value-above-replacement of the players who will actually be drafted.
    assert 0 < rate < 10

    # Double the money and every point costs twice as much.
    richer = make_draft(players, proj, rules=DraftRules(budget=400))
    assert dollars_per_point(richer.state) > rate


def test_max_sensible_bid_never_exceeds_what_is_legal():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    market = Market.of(draft.state)
    for player in list(players.values())[:40]:
        bid = max_sensible_bid(draft.state, "t0", player, market)
        assert 0 <= bid <= draft.team_state("t0").max_bid(draft.rules)


def test_a_worthless_player_is_still_worth_the_minimum():
    players, proj = make_pool(("D1", "DEF", 120.0), ("D2", "DEF", 110.0))
    draft = make_draft(players, proj, teams=2)
    draft.record_pick("t0", "D1|DEF", 5, 0.0)
    market = Market.of(draft.state)
    assert max_sensible_bid(draft.state, "t0", players["D2|DEF"], market) == 1


def _by_projection(draft):
    return sorted(draft.players.values(), key=lambda p: -draft.proj[p.id])


def _sell(draft, players, overpay=1.0):
    """Sell each player to a seat in turn at (a multiple of) the sensible price."""
    for i, player in enumerate(players):
        seat = f"t{i % draft.rules.teams}"
        price = int(max_sensible_bid(draft.state, seat, player, Market.of(draft.state)))
        price = min(max(1, int(price * overpay)), draft.team_state(seat).max_bid(draft.rules))
        draft.record_pick(seat, player.id, price, float(i))


def test_the_books_balance():
    # THE assertion. Both halves of the bid formula are measured over replacement,
    # so pricing every player the league will buy has to come out near the money
    # the league actually has. Feeding absolute lineup improvement to a
    # per-surplus rate -- the bug this replaced -- lands 5.6x over instead.
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    market = Market.of(draft.state)
    rules = draft.rules

    drafted = _by_projection(draft)[: rules.teams * rules.roster_size]
    total = sum(max_sensible_bid(draft.state, "t0", p, market) for p in drafted)

    assert total == pytest.approx(rules.teams * rules.budget, rel=0.05)


def test_the_books_stay_balanced_once_the_draft_is_running():
    # If the league pays exactly what the model says, recomputing mid-draft has
    # to reproduce the same rate -- otherwise the "prices move" machinery is
    # drifting rather than measuring.
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    opening = Market.of(draft.state).dollars_per_point

    _sell(draft, _by_projection(draft)[:40])

    assert Market.of(draft.state).dollars_per_point == pytest.approx(opening, rel=0.05)


def test_an_early_spending_spree_makes_the_back_half_cheap():
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    opening = Market.of(draft.state).dollars_per_point

    _sell(draft, _by_projection(draft)[:40], overpay=1.6)

    # The money is gone but the players are not: everyone left bids into a
    # cheaper market. This is the whole point of recomputing.
    assert Market.of(draft.state).dollars_per_point < opening * 0.9


def test_a_baseline_is_what_separates_worth_from_improvement():
    players, proj = deep_pool()
    draft = make_draft(players, proj)
    replacement = replacement_points(draft.state)
    rb = players["RB00|RB"]

    improvement = marginal_points(draft.state, "t0", rb)
    worth = marginal_points(draft.state, "t0", rb, replacement)

    assert improvement == proj[rb.id]  # against an empty slot, his whole score
    assert worth == improvement - replacement["RB"]
    assert 0 < worth < improvement


def test_a_player_below_the_bar_costs_the_minimum():
    players, proj = deep_pool(per_position=60)
    draft = make_draft(players, proj)
    market = Market.of(draft.state)
    # RB #50 of 60 is far below a replacement bar set at RB #33.
    assert proj["RB50|RB"] < market.replacement["RB"]
    assert max_sensible_bid(draft.state, "t0", players["RB50|RB"], market) == 1


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
