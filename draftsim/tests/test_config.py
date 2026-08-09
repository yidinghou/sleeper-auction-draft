import pytest

from draftsim.config import DEFAULT_ROSTER_SLOTS, DraftConfig


def test_defaults_match_the_league():
    c = DraftConfig()
    assert c.teams == 12
    assert c.budget == 200
    assert c.roster_size == 16


def test_starter_slots_exclude_bench():
    c = DraftConfig()
    assert len(c.starter_slots) == 10
    assert "BN" not in c.starter_slots


def test_starter_shares_split_the_contested_flex():
    # FLEX is the only genuinely contested slot: measured 77% RB / 23% WR.
    # SUPER_FLEX goes to a QB 97.9% of the time and REC_FLEX to a WR 100%, so
    # those stay whole.
    shares = DraftConfig().starter_shares()
    assert shares["QB"] == pytest.approx(2.0)   # QB + SUPER_FLEX
    assert shares["RB"] == pytest.approx(2.77)  # RB + RB + 0.77 of FLEX
    assert shares["WR"] == pytest.approx(3.23)  # WR + WR + REC_FLEX + 0.23 FLEX
    assert shares["TE"] == pytest.approx(1.0)
    assert shares["DEF"] == pytest.approx(1.0)
    assert shares["K"] == 0.0


def test_starter_shares_always_sum_to_the_starting_lineup():
    # However the flex splits, no spot may be invented or lost.
    for config in (
        DraftConfig(),
        DraftConfig(roster_slots=("QB", "WRRB_FLEX", "FLEX", "BN")),
        DraftConfig(roster_slots=("QB", "RB", "WR", "TE", "BN")),
    ):
        assert sum(config.starter_shares().values()) == pytest.approx(
            len(config.starter_slots)
        )


def test_starter_counts_round_shares_to_whole_players():
    # positional_need targets a countable roster, so it gets whole numbers --
    # and they must match what the old all-or-nothing attribution produced.
    counts = DraftConfig().starter_counts()
    assert counts == {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DEF": 1}
    assert sum(counts.values()) == 10


def test_custom_roster_slots():
    c = DraftConfig(teams=10, budget=300, roster_slots=("QB", "RB", "WR", "BN"))
    assert c.roster_size == 4
    assert c.starter_slots == ("QB", "RB", "WR")


def test_budget_below_roster_size_is_rejected():
    # $10 across 16 slots would freeze every manager out under the reserve rule;
    # that must be a construction error, not a silent dead draft.
    with pytest.raises(ValueError, match="budget"):
        DraftConfig(budget=10)  # default 16 slots


def test_other_invalid_configs_rejected():
    with pytest.raises(ValueError, match="teams"):
        DraftConfig(teams=0)
    with pytest.raises(ValueError, match="roster_slots"):
        DraftConfig(roster_slots=())
