import pytest

from draftsim.config import DraftConfig
from draftsim.valuation import (
    Player,
    load_players,
    market_value,
    replacement_points,
    vorp_value,
)


@pytest.fixture(scope="module")
def players():
    return load_players()


def test_loads_only_rostered_players(players):
    # CSV is 3224 lines incl. header -> 3223 data rows, all with a position.
    # 2185 of them are free agents and are not draftable.
    assert len(players) == 1038
    assert all(p.team for p in players)


def test_free_agents_flag_returns_the_whole_sheet():
    everyone = load_players(free_agents=True)
    assert len(everyone) == 3223
    assert any(not p.team for p in everyone)
    # Nothing draftable is lost by the default filter: no free agent is priced
    # above the $1 floor.
    assert max((p.proj_dollar or 0) for p in everyone if not p.team) == 1


def test_spot_check_josh_allen(players):
    allen = next(p for p in players if p.name == "Josh Allen" and p.pos == "QB")
    assert allen.team == "BUF"
    assert allen.proj_dollar == 59
    assert allen.bye == 7
    assert allen.rank == 1
    assert allen.points == pytest.approx(361.5)
    assert allen.id == "josh-allen|qb|buf"


def test_missing_numeric_cells_are_none(players):
    # Players beyond the auction board have no rank/bye/$; those parse to None,
    # never crash.
    off_board = [p for p in players if p.proj_dollar is None]
    assert off_board, "expected some players with no board dollar"
    assert all(p.bye is None or isinstance(p.bye, int) for p in players)


def test_market_value_uses_dollar_and_defaults_to_zero():
    assert market_value(Player("x", "X", "QB", "BUF", 100.0, proj_dollar=42)) == 42.0
    assert market_value(Player("y", "Y", "WR", "LAR", 50.0, proj_dollar=None)) == 0.0


def test_replacement_points_are_positive_and_ordered(players):
    repl = replacement_points(players, DraftConfig())
    # Every startable position has a finite, positive replacement level.
    for pos in ("QB", "RB", "WR", "TE", "DEF"):
        assert 0 < repl[pos] < float("inf")
    # K has no starter slot in this league -> infinite replacement (undraftable).
    assert repl["K"] == float("inf")


def test_no_slot_positions_have_zero_vorp(players):
    # Kickers must not be valued when there's no K slot, even though they score
    # points -- otherwise they wrongly outrank real starters on VORP.
    repl = replacement_points(players, DraftConfig())
    kickers = [p for p in players if p.pos == "K"]
    assert kickers, "expected kickers in the pool"
    assert all(vorp_value(k, repl) == 0.0 for k in kickers)


def test_vorp_is_points_over_replacement_floored_at_zero(players):
    repl = replacement_points(players, DraftConfig())
    allen = next(p for p in players if p.name == "Josh Allen" and p.pos == "QB")
    assert vorp_value(allen, repl) == pytest.approx(allen.points - repl["QB"])
    # A replacement-level scrub never yields negative value.
    scrub = Player("z", "Z", "WR", "FA", points=0.0)
    assert vorp_value(scrub, repl) == 0.0
