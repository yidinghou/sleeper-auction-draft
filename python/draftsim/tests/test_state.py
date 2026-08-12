"""The property test the whole design rests on.

`apply()` and `rebuild()` compute the same cache by different routes -- one
incremental, one from scratch. If they agree over arbitrary legal draft
sequences, every cached field is correct, because there is no field they could
both get wrong in the same way.
"""

from __future__ import annotations

import random

import pytest
from conftest import deep_pool, make_draft, make_pool, make_teams

from draftsim import DraftRules, DraftState, Team
from draftsim.lineup import empty_lineup


def random_picks(draft, rng, count):
    """Drive a draft with legal-but-arbitrary sales."""
    for _ in range(count):
        seats = [
            ts
            for ts in draft.state.teams.values()
            if ts.open_slots(draft.rules) > 0
        ]
        available = draft.available()
        if not seats or not available:
            return
        ts = rng.choice(seats)
        player = rng.choice(available)
        price = rng.randint(draft.rules.min_bid, ts.max_bid(draft.rules))
        draft.record_pick(ts.team.id, player.id, price, 0.0)


def assert_cache_equals_rebuild(draft):
    """Compare field by field, independently of `DraftState.assert_matches` --
    so a bug in that method cannot hide a bug in the cache."""
    fresh = DraftState.rebuild(
        draft.rules, draft.players, draft.teams, draft.picks, draft.proj
    )
    assert draft.state.owner == fresh.owner
    for team_id, ts in draft.state.teams.items():
        other = fresh.teams[team_id]
        assert ts.roster == other.roster, team_id
        assert ts.by_position == other.by_position, team_id
        assert ts.spent == other.spent, team_id
        assert ts.lineup.points == other.lineup.points, team_id
        assert ts.lineup.slots == other.lineup.slots, team_id


@pytest.mark.parametrize("seed", range(50))
def test_applying_a_random_legal_sequence_matches_a_fresh_rebuild(seed):
    draft = make_draft()
    random_picks(draft, random.Random(seed), count=60)
    assert_cache_equals_rebuild(draft)


def test_a_full_draft_matches_a_fresh_rebuild():
    draft = make_draft()
    random_picks(draft, random.Random(99), count=12 * 16)
    assert draft.is_complete()
    assert len(draft.picks) == 12 * 16
    assert_cache_equals_rebuild(draft)


def test_rebuild_from_an_empty_ledger_is_an_empty_cache():
    draft = make_draft()
    for ts in draft.state.teams.values():
        assert ts.roster == ()
        assert ts.spent == 0
        assert ts.lineup == empty_lineup(draft.rules)
        assert ts.remaining(draft.rules) == 200
        assert ts.open_slots(draft.rules) == 16
    assert draft.state.owner == {}


def test_max_bid_reserves_a_dollar_for_every_slot_the_bid_will_not_fill():
    draft = make_draft()
    ts = draft.team_state("t0")
    # $200, 16 slots: spend everything but $1 per remaining slot.
    assert ts.max_bid(draft.rules) == 200 - 15

    draft.record_pick("t0", "WR00|WR", 100, 0.0)
    ts = draft.team_state("t0")
    assert ts.spent == 100
    assert ts.remaining(draft.rules) == 100
    assert ts.open_slots(draft.rules) == 15
    assert ts.max_bid(draft.rules) == 100 - 14


def test_max_bid_is_zero_once_the_roster_is_full():
    rules = DraftRules(teams=2, roster_slots=("QB", "BN"), budget=10)
    players, proj = make_pool(("A", "QB", 10.0), ("B", "QB", 5.0))
    draft = make_draft(players, proj, teams=2, rules=rules)
    draft.record_pick("t0", "A|QB", 5, 0.0)
    draft.record_pick("t0", "B|QB", 4, 0.0)
    assert draft.team_state("t0").max_bid(rules) == 0


def test_the_cache_holds_positions_sorted_best_first():
    draft = make_draft()
    draft.record_pick("t0", "WR05|WR", 5, 0.0)  # the worse one first
    draft.record_pick("t0", "WR00|WR", 50, 1.0)
    ts = draft.team_state("t0")
    assert [p.name for p in ts.roster] == ["WR05", "WR00"]  # acquisition order
    assert [p.name for p in ts.by_position["WR"]] == ["WR00", "WR05"]  # best first


def test_assert_matches_catches_a_cache_that_drifted():
    draft = make_draft()
    draft.record_pick("t0", "WR00|WR", 10, 0.0)
    draft.state.teams["t0"].spent = 999  # corrupt the cache by hand
    with pytest.raises(AssertionError, match="spent"):
        draft.state.assert_matches(draft.picks)


def test_rebuild_is_not_a_replay_of_apply():
    """Guards the design, not the behaviour: if `rebuild` ever starts calling
    `apply`, the property tests above become tautologies."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(DraftState.rebuild)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "apply" not in called


def test_states_for_teams_with_no_picks_are_still_present():
    draft = make_draft(*deep_pool(), teams=12)
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    assert set(draft.state.teams) == {t.id for t in make_teams(12)}
    assert draft.team_state("t7").roster == ()
    assert isinstance(draft.team_state("t7").team, Team)
