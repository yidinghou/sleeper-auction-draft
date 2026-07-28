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


def test_starter_counts_attribute_flex_to_a_position():
    # QB(1)+SUPER_FLEX, RB(2)+FLEX, WR(2)+REC_FLEX, TE(1), DEF(1) => 10 starters.
    counts = DraftConfig().starter_counts()
    assert counts == {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DEF": 1}
    assert sum(counts.values()) == 10


def test_custom_roster_slots():
    c = DraftConfig(teams=10, budget=300, roster_slots=("QB", "RB", "WR", "BN"))
    assert c.roster_size == 4
    assert c.starter_slots == ("QB", "RB", "WR")
