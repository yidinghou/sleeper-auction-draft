from __future__ import annotations

import random

from conftest import make_pool

from draftsim import DraftRules, best_lineup, display_slots, is_legal
from draftsim.lineup import empty_lineup, group_by_position, insort, sort_key

RULES = DraftRules()


def _lineup(players, proj):
    return best_lineup(group_by_position(players, proj), RULES, proj)


def test_an_empty_roster_scores_zero_with_every_slot_open():
    lineup = empty_lineup(RULES)
    assert lineup.points == 0.0
    assert lineup.slots == (None,) * 10


def test_the_lineup_is_the_same_however_the_roster_is_shuffled():
    players, proj = make_pool(
        ("Allen", "QB", 360.0),
        ("Mahomes", "QB", 340.0),
        ("Chase", "WR", 300.0),
        ("Jefferson", "WR", 290.0),
        ("Lamb", "WR", 280.0),
        ("Gibbs", "RB", 270.0),
        ("Barkley", "RB", 260.0),
        ("Robinson", "RB", 250.0),
        ("Bowers", "TE", 240.0),
        ("Ravens", "DEF", 120.0),
    )
    roster = list(players.values())
    baseline = _lineup(roster, proj)

    rng = random.Random(7)
    for _ in range(25):
        rng.shuffle(roster)
        assert _lineup(roster, proj).points == baseline.points


def test_the_lineup_is_optimal_not_first_fit():
    # A greedy fill walking slots in template order would put the better RB in
    # the first RB slot and leave the FLEX to the worse one -- same total. The
    # real trap is the superflex: it must take the second QB, not a spare WR.
    players, proj = make_pool(
        ("QB1", "QB", 300.0),
        ("QB2", "QB", 280.0),
        ("WR1", "WR", 200.0),
        ("WR2", "WR", 190.0),
        ("WR3", "WR", 180.0),
        ("WR4", "WR", 170.0),
    )
    lineup = _lineup(list(players.values()), proj)
    starters = [p.name for p in lineup.slots if p is not None]
    assert "QB2" in starters
    assert lineup.points == 300 + 280 + 200 + 190 + 180 + 170


def test_a_second_defense_adds_nothing():
    players, proj = make_pool(("D1", "DEF", 120.0), ("D2", "DEF", 110.0))
    roster = list(players.values())
    assert _lineup(roster[:1], proj).points == 120.0
    assert _lineup(roster, proj).points == 120.0  # no flex accepts a DEF


def test_a_kicker_can_never_start():
    players, proj = make_pool(("K1", "K", 150.0))
    assert _lineup(list(players.values()), proj).points == 0.0


def test_a_full_starting_lineup_is_legal_and_a_partial_one_is_not():
    players, proj = make_pool(
        ("QB1", "QB", 300.0),
        ("QB2", "QB", 280.0),
        ("RB1", "RB", 270.0),
        ("RB2", "RB", 260.0),
        ("RB3", "RB", 250.0),
        ("WR1", "WR", 240.0),
        ("WR2", "WR", 230.0),
        ("WR3", "WR", 220.0),
        ("TE1", "TE", 210.0),
        ("DEF1", "DEF", 120.0),
    )
    roster = list(players.values())
    assert is_legal(group_by_position(roster, proj), RULES)
    assert not is_legal(group_by_position(roster[:-1], proj), RULES)


def test_insort_keeps_a_bucket_in_the_same_order_a_fresh_sort_would():
    players, proj = make_pool(
        ("A", "WR", 100.0), ("B", "WR", 300.0), ("C", "WR", 200.0)
    )
    bucket = ()
    for player in players.values():
        bucket = insort(bucket, player, proj)
    assert bucket == tuple(sorted(players.values(), key=lambda p: sort_key(p, proj)))
    assert [p.name for p in bucket] == ["B", "C", "A"]


def test_insort_breaks_ties_on_id_so_equal_projections_still_have_one_order():
    players, proj = make_pool(("Z", "WR", 0.0), ("A", "WR", 0.0))
    bucket = ()
    for player in players.values():  # insertion order Z then A
        bucket = insort(bucket, player, proj)
    assert [p.name for p in bucket] == ["A", "Z"]


def test_display_slots_shows_every_roster_slot_in_template_order():
    players, proj = make_pool(("QB1", "QB", 300.0), ("WR1", "WR", 200.0))
    roster = list(players.values())
    rows = display_slots(_lineup(roster, proj), roster, RULES)
    assert [slot for slot, _ in rows] == list(RULES.roster_slots)
    assert rows[0] == ("QB", players["QB1|QB"])
    assert rows[-1] == ("BN", None)  # a half-built roster still shows its shape
