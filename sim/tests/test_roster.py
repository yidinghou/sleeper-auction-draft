from dataclasses import dataclass

from draftsim.config import DraftConfig
from draftsim.roster import is_lineup_legal, open_slots, starters


@dataclass(frozen=True)
class P:
    """Minimal player stub: .pos for slotting, .points for lineup weighting."""

    name: str
    pos: str
    points: float = 0.0


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
