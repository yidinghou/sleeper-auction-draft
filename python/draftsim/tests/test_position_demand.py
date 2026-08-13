"""How much of each position the league wants, per seat.

A concrete slot wants one player at its own position. A flex wants one player
too, but split across the positions that compete for it — and they do not
compete equally, so the shares come out fractional. Two whole-number readings
are taken from those fractions and they are not the same number: rounding
answers "how many should I buy?", flooring answers "how many bodies does the
template guarantee a home?".

Everything here is arithmetic over a written-down template, so nothing on this
page reads a spreadsheet.
"""

import pytest

from draftsim.league import LeagueRules


# --- The fractional shares ------------------------------------------------


def test_a_seat_wants_two_quarterbacks_and_two_and_three_quarter_backs():
    wanted = LeagueRules().wanted_per_seat()

    assert wanted["QB"] == pytest.approx(2.00)
    assert wanted["RB"] == pytest.approx(2.77)
    assert wanted["WR"] == pytest.approx(3.23)
    assert wanted["TE"] == pytest.approx(1.00)
    assert wanted["DEF"] == pytest.approx(1.00)


def test_a_seat_wants_no_kickers_at_all():
    assert LeagueRules().wanted_per_seat()["K"] == 0


def test_the_ordinary_flex_goes_to_a_back_three_times_in_four():
    """The only genuinely contested slot: 77% back, 23% receiver, measured.

    The other two flexes are landslides — the superflex went to a quarterback
    97.9% of the time and the receiver flex to a receiver every single time —
    so they are carried as whole numbers.
    """
    with_flex = LeagueRules(roster_template=("FLEX", "BN")).wanted_per_seat()

    assert with_flex["RB"] == pytest.approx(0.77)
    assert with_flex["WR"] == pytest.approx(0.23)
    assert with_flex["TE"] == 0


def test_a_back_receiver_flex_splits_evenly_because_nobody_measured_it():
    even = LeagueRules(roster_template=("WRRB_FLEX", "BN")).wanted_per_seat()

    assert even["RB"] == pytest.approx(0.50)
    assert even["WR"] == pytest.approx(0.50)


def test_what_the_league_wants_always_adds_up_to_its_starting_lineup():
    """A mis-typed share table shows up here first, whatever the template."""
    for template in (
        LeagueRules().roster_template,
        ("QB", "RB", "WR", "FLEX", "WRRB_FLEX", "BN", "BN"),
        ("SUPER_FLEX", "REC_FLEX", "BN"),
    ):
        league = LeagueRules(roster_template=template)

        assert sum(league.wanted_per_seat().values()) == pytest.approx(
            len(league.starting_slots)
        )


# --- The two whole-number readings ----------------------------------------


def test_rounded_a_seat_shops_for_three_backs_and_three_receivers():
    shopping_list = LeagueRules().wanted_per_seat_rounded()

    assert shopping_list == {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DEF": 1}


def test_a_rounded_shopping_list_fills_every_starting_slot():
    league = LeagueRules()

    assert sum(league.wanted_per_seat_rounded().values()) == len(
        league.starting_slots
    )


def test_floored_the_template_seats_nobody_in_the_ordinary_flex():
    """Neither the backs nor the receivers can claim the contested flex.

    Nine bodies have a slot the roster owes them; the tenth starter is whoever
    wins the flex on the day.
    """
    guaranteed = LeagueRules().wanted_per_seat_floored()

    assert guaranteed == {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 0, "DEF": 1}
    assert sum(guaranteed.values()) == 9


def test_half_a_share_rounds_up_to_a_body_rather_than_down_to_none():
    """An even flex split leaves each side owed half a player; buy the player.

    Python's own round() sends halves to the nearest even number, which would
    quietly leave this slot unshopped-for.
    """
    league = LeagueRules(roster_template=("WRRB_FLEX", "BN"))

    assert league.wanted_per_seat_rounded()["RB"] == 1
    assert league.wanted_per_seat_rounded()["WR"] == 1
