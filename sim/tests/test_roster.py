import pytest

from draftsim.config import DraftConfig
from draftsim.roster import (
    display_slots,
    is_lineup_legal,
    marginal_points,
    marginal_thresholds,
    open_slots,
    positional_need,
    starters,
)
from draftsim.valuation import Player, replacement_points, vorp_value


def P(name: str, pos: str, points: float = 0.0) -> Player:
    """A real Player with only the fields roster logic reads spelled out."""
    return Player(id=name, name=name, pos=pos, team="FA", points=points)


def test_full_legal_roster_fills_every_starter():
    config = DraftConfig()
    # One eligible body for each of the 10 starter slots.
    roster = [
        P("qb1", "QB"),
        P("rb1", "RB"),
        P("rb2", "RB"),
        P("wr1", "WR"),
        P("wr2", "WR"),
        P("te1", "TE"),
        P("rb3", "RB"),   # FLEX
        P("wr3", "WR"),   # REC_FLEX
        P("qb2", "QB"),   # SUPER_FLEX
        P("def1", "DEF"),
    ]
    assert is_lineup_legal(roster, config)
    assert all(s is not None for s in starters(roster, config))


def test_missing_defense_makes_lineup_illegal():
    config = DraftConfig()
    roster = [P(f"wr{i}", "WR") for i in range(10)]  # no DEF, no QB, etc.
    assert not is_lineup_legal(roster, config)


def test_open_slots_counts_remaining():
    config = DraftConfig()  # roster_size 16
    assert open_slots(0, config) == 16
    assert open_slots(16, config) == 0
    assert open_slots(20, config) == 0


# --- matching-based legality/starters (order-invariant, optimal) -----------


def _fillable_roster():
    # Fills QB,RB,RB,WR,WR,TE,SUPER_FLEX,DEF directly; leftovers [WR, RB] must
    # cover FLEX (RB/WR/TE) and REC_FLEX (WR/TE) — only possible if RB->FLEX and
    # WR->REC_FLEX. A first-fit greedy that puts WR in FLEX would fail.
    base = [
        P("qb1", "QB"), P("rb1", "RB"), P("rb2", "RB"), P("wr1", "WR"),
        P("wr2", "WR"), P("te1", "TE"), P("qb2", "QB"), P("def1", "DEF"),
    ]
    return base + [P("wrX", "WR"), P("rbX", "RB")]


def test_legality_is_order_invariant():
    config = DraftConfig()
    roster = _fillable_roster()
    assert is_lineup_legal(roster, config)
    assert is_lineup_legal(list(reversed(roster)), config)
    # Every permutation-ish reordering agrees (the old greedy did not).
    shuffled = roster[5:] + roster[:5]
    assert is_lineup_legal(shuffled, config)


def test_starters_pick_the_max_points_lineup():
    # Two QBs compete for the single SUPER_FLEX-ish overflow; the optimal lineup
    # must start the higher scorer regardless of input order.
    config = DraftConfig(teams=1, budget=200, roster_slots=("QB", "SUPER_FLEX", "BN"))
    low, high = P("low", "QB", points=100.0), P("high", "QB", points=300.0)
    lineup_a = starters([low, high], config)
    lineup_b = starters([high, low], config)
    started_names_a = {s.name for s in lineup_a if s}
    assert started_names_a == {"low", "high"}  # both start (2 QB slots)
    # QB slot + SUPER_FLEX both filled; total points identical either input order.
    assert sum(s.points for s in lineup_a if s) == sum(s.points for s in lineup_b if s)


def test_bench_drops_the_lowest_scorer():
    config = DraftConfig(teams=1, budget=200, roster_slots=("QB", "BN"))
    lineup = starters([P("a", "QB", 50.0), P("b", "QB", 200.0)], config)
    # Only one QB starter slot -> the 200-pt QB starts, the 50 sits.
    assert [s.name if s else None for s in lineup] == ["b"]


def test_matching_handles_nonlaminar_flex_eligibility():
    # WRRB_FLEX={WR,RB} and REC_FLEX={WR,TE} overlap without nesting, so greedy
    # order can misassign. One WR + one RB must fill both: RB->WRRB, WR->REC.
    config = DraftConfig(teams=1, budget=200, roster_slots=("REC_FLEX", "WRRB_FLEX"))
    assert is_lineup_legal([P("wr", "WR"), P("rb", "RB")], config)
    assert is_lineup_legal([P("rb", "RB"), P("wr", "WR")], config)


def test_legality_agrees_with_starters_fill():
    # The invariant Stage 5 scoring relies on: a roster is legal iff starters()
    # fills every slot. The two must never disagree.
    config = DraftConfig()
    legal = _fillable_roster()
    illegal = [P(f"wr{i}", "WR") for i in range(10)]  # no DEF/QB/RB/TE
    for roster in (legal, illegal, list(reversed(legal))):
        filled = all(s is not None for s in starters(roster, config))
        assert is_lineup_legal(roster, config) == filled


# --- positional need -------------------------------------------------------


def test_empty_roster_needs_the_full_starter_target():
    config = DraftConfig()
    # Target = starter_counts(): 2 QB, 3 RB, 3 WR, 1 TE, 1 DEF (K unused).
    assert positional_need([], config) == {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DEF": 1}


def test_need_drops_as_positions_are_filled_and_never_goes_negative():
    config = DraftConfig()
    roster = [P("qb1", "QB"), P("qb2", "QB"), P("qb3", "QB")]  # 3 QBs, target 2
    need = positional_need(roster, config)
    assert need["QB"] == 0            # over-filled clamps to 0, not -1
    assert need["RB"] == 3            # untouched positions unchanged


def test_meeting_every_need_yields_a_legal_lineup():
    # Acquire exactly the target counts -> lineup must be legal.
    config = DraftConfig()
    target = positional_need([], config)
    roster = []
    for pos, count in target.items():
        roster += [P(f"{pos}{i}", pos) for i in range(count)]
    assert is_lineup_legal(roster, config)


# --- marginal lineup value -------------------------------------------------


def _pool():
    """A pool deep enough that every position has a real replacement level."""
    pool = []
    for pos, n, top in (("QB", 30, 300.0), ("RB", 40, 280.0), ("WR", 50, 260.0),
                        ("TE", 30, 190.0), ("DEF", 16, 105.0), ("K", 14, 120.0)):
        pool += [
            Player(id=f"{pos}{i}", name=f"{pos}{i}", pos=pos, team="NE",
                   points=top - i * 4.0)
            for i in range(n)
        ]
    return pool


def test_marginal_thresholds_match_a_direct_lineup_recomputation():
    # The whole point of thresholds is to avoid recomputing the lineup per
    # candidate. That shortcut is only valid if it agrees exactly with doing it
    # the slow way, across arbitrary rosters -- so check it does.
    import random

    from draftsim.roster import _lineup_points, _replacement_bench

    config = DraftConfig()
    pool = _pool()
    replacement = replacement_points(pool, config)
    bench = _replacement_bench(config, replacement)
    rng = random.Random(0)

    for _ in range(12):
        roster = rng.sample(pool, rng.randint(0, 14))
        thresholds = marginal_thresholds(roster, config, replacement)
        base = _lineup_points(roster + bench, config)
        for cand in rng.sample(pool, 20):
            direct = _lineup_points(roster + bench + [cand], config) - base
            assert marginal_points(cand, thresholds) == pytest.approx(direct)


def test_marginal_on_an_empty_roster_is_classic_vorp():
    # Marginal value is a generalization of VORP, not a replacement for it: with
    # nothing rostered, every slot is empty and the two must agree exactly.
    config = DraftConfig()
    pool = _pool()
    replacement = replacement_points(pool, config)
    thresholds = marginal_thresholds([], config, replacement)
    for pos in ("QB", "RB", "WR", "TE", "DEF"):
        best = max((p for p in pool if p.pos == pos), key=lambda p: p.points)
        assert marginal_points(best, thresholds) == pytest.approx(
            vorp_value(best, replacement)
        )


def test_an_unstartable_position_is_always_worth_zero():
    # No K slot in the default lineup, so no kicker can ever add anything.
    config = DraftConfig()
    pool = _pool()
    thresholds = marginal_thresholds([], config, replacement_points(pool, config))
    best_k = max((p for p in pool if p.pos == "K"), key=lambda p: p.points)
    assert marginal_points(best_k, thresholds) == 0.0


# -- display_slots: the shared board layout ----------------------------------


def test_display_slots_gives_one_row_per_roster_slot():
    config = DraftConfig()
    rows = display_slots([], config)
    assert [slot for slot, _ in rows] == list(config.roster_slots)
    assert all(player is None for _, player in rows)


def test_display_slots_seats_players_where_they_start_not_where_bought():
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "RB", "FLEX", "BN"))
    rows = display_slots([P("rb2", "RB", 100.0), P("qb", "QB", 90.0),
                          P("rb1", "RB", 200.0)], config)
    # A second RB starts only by taking the flex. Which of the two lands in RB
    # and which in FLEX is not asserted: both start either way and the lineup
    # scores the same, so pinning it would test the matching's tie-break rather
    # than anything a reader of the card cares about.
    seated = {slot: player.pos for slot, player in rows if player}
    assert seated == {"QB": "QB", "RB": "RB", "FLEX": "RB"}
    assert rows[-1] == ("BN", None)


def test_display_slots_is_independent_of_acquisition_order():
    config = DraftConfig()
    roster = [P("qb", "QB", 300.0), P("rb", "RB", 200.0), P("wr", "WR", 100.0)]
    assert display_slots(roster, config) == display_slots(roster[::-1], config)


def test_display_slots_benches_the_leftovers():
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "BN", "BN"))
    rows = display_slots([P("qb1", "QB", 200.0), P("qb2", "QB", 100.0)], config)
    assert [(slot, p.name if p else None) for slot, p in rows] == [
        ("QB", "qb1"), ("BN", "qb2"), ("BN", None),
    ]


def test_display_slots_never_silently_drops_a_player():
    # More bodies than seats: the extras spill past the bench rather than
    # vanishing, so a card can never under-report what a team owns.
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "BN"))
    roster = [P(f"qb{i}", "QB", float(i)) for i in range(5)]
    rows = display_slots(roster, config)
    assert len(rows) == len(roster)
    assert {p.name for _, p in rows if p} == {p.name for p in roster}
