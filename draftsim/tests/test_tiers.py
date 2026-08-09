from pathlib import Path

from draftsim.tiers import (
    rank_by_position,
    render_html,
    tier_breaks,
    tier_count,
)
from draftsim.valuation import Player

SOURCE = Path("projections-2026.csv")


def _player(name, pos, points, dollar=None):
    return Player(
        id=name.lower(), name=name, pos=pos, team="BUF", points=points,
        proj_dollar=dollar,
    )


# -- the rule ----------------------------------------------------------------


def test_an_even_ramp_is_one_tier():
    # Every gap is the median gap, so nothing clears the threshold: a list with
    # no cliff in it is not made of tiers.
    assert tier_breaks([100.0, 90.0, 80.0, 70.0, 60.0], 2.5) == []


def test_a_single_cliff_splits_the_list_in_two():
    points = [100.0, 99.0, 98.0, 60.0, 59.0, 58.0]
    assert tier_breaks(points, 2.5) == [2]
    assert tier_count(points, 2.5) == 2


def test_a_higher_gap_factor_never_finds_more_breaks():
    points = [100.0, 96.0, 95.0, 80.0, 79.0, 70.0, 69.0]
    coarse = tier_breaks(points, 5.0)
    fine = tier_breaks(points, 2.0)
    assert set(coarse) <= set(fine)
    assert tier_count(points, 5.0) <= tier_count(points, 2.0)


def test_the_last_player_never_starts_a_tier():
    # The gap at the final index is off the end of the list; a break there would
    # render a `Tier N` heading with nothing under it.
    points = [100.0, 99.0, 98.0, 97.0, 10.0]
    assert max(tier_breaks(points, 2.5)) < len(points) - 1


def test_degenerate_lists_have_no_breaks():
    assert tier_breaks([], 2.5) == []
    assert tier_breaks([100.0], 2.5) == []
    assert tier_breaks([100.0, 100.0, 100.0], 2.5) == []  # median gap 0
    assert tier_count([], 2.5) == 0
    assert tier_count([100.0], 2.5) == 1


# -- ranking -----------------------------------------------------------------


def test_rank_by_position_takes_the_best_n_of_each():
    players = [
        _player("QB1", "QB", 300.0),
        _player("QB2", "QB", 200.0),
        _player("QB3", "QB", 100.0),
        _player("WR1", "WR", 250.0),
    ]
    ranked = rank_by_position(players, {"QB": 2, "WR": 5})
    assert [p.name for p in ranked["QB"]] == ["QB1", "QB2"]
    assert [p.name for p in ranked["WR"]] == ["WR1"]  # short position, not padded


# -- the page ----------------------------------------------------------------


def _board():
    return {
        "QB": [
            _player("Josh Allen", "QB", 361.5, 58),
            _player("Lamar Jackson", "QB", 326.0, 45),
            _player("Drake Maye", "QB", 325.0, 40),
            _player("Ja'Marr Chase", "QB", 324.0),  # no $PROJ, and needs escaping
        ]
    }


def test_render_html_is_a_nonempty_full_page():
    page = render_html(_board(), gap_factor=2.5, source=SOURCE)
    assert page.startswith("<!doctype html>")
    assert len(page) > 500


def test_render_html_shows_every_player_with_its_price():
    page = render_html(_board(), gap_factor=2.5, source=SOURCE)
    assert "Josh Allen" in page
    assert "Ja&#x27;Marr Chase" in page  # name is HTML-escaped
    assert "$58" in page
    assert "—" in page  # a player with no $PROJ still gets a cell


def test_render_html_records_its_source_and_settings():
    # The failure this prevents: a board saved to disk that cannot say which
    # projections export it was built from.
    page = render_html(_board(), gap_factor=2.5, source=SOURCE)
    assert "projections-2026.csv" in page
    assert "2.5" in page
    assert "4 QB" in page


def test_render_html_marks_the_tier_break():
    # Allen is alone above a 35.5-point cliff; the other three are 1.0 apart.
    page = render_html(_board(), gap_factor=2.5, source=SOURCE)
    assert "Tier 2" in page
    assert "Tier 3" not in page


def test_render_html_skips_empty_positions():
    page = render_html({"QB": [], "DEF": []}, gap_factor=2.5, source=SOURCE)
    assert "<section" not in page
