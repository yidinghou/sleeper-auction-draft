"""What a player is worth to a particular roster, and what that roster needs.

Not his projection. What he adds to *your* starting lineup over a body you
could have for a dollar — so the same tight end is worth nothing to the seat
who already has three and a great deal to the seat with none.

The method is to solve the lineup twice: once with him on the roster, once with
a freely-available body at his position projected at the bar. It looks like
twice the work and it is what makes the awkward cases answer themselves.

Every roster here is declared on the page with the points written next to the
names, and so is the bar. That is deliberate: proving that a player below the
bar is worth nothing by drafting a league until the bar collapses would be a
test of the fixture rather than of the claim.
"""

import math

import pytest

from draftsim.board import Player
from draftsim.league import LeagueRules
from draftsim.valuation import what_he_adds, what_the_roster_still_needs

def a_player(name, position, points, forecast):
    """One more player — the one being valued — added to a forecast in hand."""
    player = Player(name=name, position=position, team="NFL")
    forecast[player.identity] = points
    return player


# --- What he adds over the dollar guy -------------------------------------


def test_a_player_is_measured_against_the_dollar_guy_not_against_an_empty_slot(a_roster):
    """The founding bug, stated as a test.

    An empty roster's quarterback slot is empty, but nobody fields an empty
    slot — the dollar guy is what he actually has to beat. Measured against the
    empty slot he is worth all 360 of his points; measured against a $1
    quarterback projected at 300, he is worth the 60 he adds.
    """
    roster, forecast = a_roster()
    allen = a_player("Josh Allen", "QB", 360, forecast)
    league = LeagueRules()

    assert what_he_adds(allen, roster, forecast, league, {"QB": 300}) == 60
    assert what_he_adds(allen, roster, forecast, league, {"QB": 0}) == 360


def test_you_cannot_ask_what_a_player_is_worth_without_saying_what_a_dollar_buys(
    a_roster,
):
    """There is no honest default bar, so there is no default at all.

    The zero somebody would reach for is the founding bug: it measures a player
    against an empty slot.
    """
    roster, forecast = a_roster()
    allen = a_player("Josh Allen", "QB", 360, forecast)

    with pytest.raises(TypeError):
        what_he_adds(allen, roster, forecast, LeagueRules())


def test_asking_what_a_man_you_already_own_would_add_is_a_mistake(a_roster):
    """Asked in a loop down the board, this would answer with a plausible
    number rather than an error: he would be counted twice and come out looking
    like an upgrade on himself."""
    roster, forecast = a_roster(("Saquon Barkley", "RB", 280))

    with pytest.raises(ValueError, match="already on this roster"):
        what_he_adds(roster[0], roster, forecast, LeagueRules(), {"RB": 165})


def test_a_position_the_bar_does_not_mention_is_free(a_roster):
    """Nobody has priced it, so there is nothing to beat."""
    roster, forecast = a_roster()
    allen = a_player("Josh Allen", "QB", 360, forecast)

    assert what_he_adds(allen, roster, forecast, LeagueRules(), {}) == 360


def test_nobody_is_ever_worth_less_than_nothing(a_roster):
    """A back below the bar is worth zero, not minus forty."""
    roster, forecast = a_roster()
    a_scrub = a_player("A Fourth Stringer", "RB", 60, forecast)

    assert what_he_adds(a_scrub, roster, forecast, LeagueRules(), {"RB": 100}) == 0


def test_a_player_who_cannot_crack_your_lineup_is_worth_nothing(a_roster):
    """A second defense has nowhere to go: one slot takes one, and no flex
    will have him at any price."""
    roster, forecast = a_roster(("Ravens D/ST", "DEF", 130))
    another = a_player("Bills D/ST", "DEF", 120, forecast)

    assert what_he_adds(another, roster, forecast, LeagueRules(), {"DEF": 60}) == 0


def test_a_kicker_is_worth_nothing_however_many_points_he_scores(a_roster):
    """No slot takes one, so the bar at kicker is one nobody can clear — and
    the two solves come out the same without anybody checking for kickers."""
    roster, forecast = a_roster()
    tucker = a_player("Justin Tucker", "K", 150, forecast)

    assert what_he_adds(tucker, roster, forecast, LeagueRules(), {"K": math.inf}) == 0


# --- The same player, two different rosters -------------------------------


def test_a_third_elite_tight_end_is_worth_nothing_to_you(a_roster):
    """Every slot that would take a tight end is already somebody's.

    The tight end slot, the flex, the receiver flex and the superflex can all
    hold one — but two tight ends and four receivers have them between them,
    and every one of those six is projected above the man being offered.
    """
    roster, forecast = a_roster(
        ("Brock Bowers", "TE", 220),
        ("Trey McBride", "TE", 210),
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 290),
        ("Puka Nacua", "WR", 280),
        ("Nico Collins", "WR", 270),
    )
    kelce = a_player("Travis Kelce", "TE", 190, forecast)

    assert what_he_adds(kelce, roster, forecast, LeagueRules(), {"TE": 100}) == 0


def test_the_same_tight_end_is_worth_a_great_deal_to_the_seat_with_none(a_roster):
    roster, forecast = a_roster(
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 290),
        ("Puka Nacua", "WR", 280),
    )
    kelce = a_player("Travis Kelce", "TE", 190, forecast)

    assert what_he_adds(kelce, roster, forecast, LeagueRules(), {"TE": 100}) == 90


def test_a_back_at_a_position_you_have_filled_is_measured_against_the_man_he_displaces(a_roster):
    """Not against a bar he never has to beat.

    Both running back slots, the flex and the superflex are taken by backs, so
    a fifth back only plays by pushing the worst of them out of the lineup: he
    is worth the twenty points between them, not the hundred and five he
    clears the bar by.
    """
    roster, forecast = a_roster(
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 270),
        ("Bijan Robinson", "RB", 260),
        ("James Cook", "RB", 250),
    )
    one_more = a_player("Kenneth Walker", "RB", 270, forecast)

    assert what_he_adds(one_more, roster, forecast, LeagueRules(), {"RB": 165}) == 20


def test_worth_is_read_against_the_bar_the_caller_hands_in(a_roster):
    """There is no honest default, so there is no default: the same player is
    worth less into a deep position than a thin one."""
    roster, forecast = a_roster()
    chase = a_player("Ja'Marr Chase", "WR", 300, forecast)
    league = LeagueRules()

    into_a_deep_market = what_he_adds(chase, roster, forecast, league, {"WR": 200})
    into_a_thin_one = what_he_adds(chase, roster, forecast, league, {"WR": 120})

    assert into_a_deep_market == 100
    assert into_a_thin_one == 180


# --- What a roster still needs --------------------------------------------


def test_an_empty_roster_needs_the_whole_shopping_list(a_roster):
    """Two quarterbacks, three backs, three receivers, a tight end, a defense —
    and no kickers, ever."""
    assert what_the_roster_still_needs((), LeagueRules()) == {
        "QB": 2,
        "RB": 3,
        "WR": 3,
        "TE": 1,
        "K": 0,
        "DEF": 1,
    }


def test_a_roster_stops_needing_backs_once_it_has_three(a_roster):
    two_deep, _ = a_roster(
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 270),
    )
    three_deep, _ = a_roster(
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 270),
        ("Bijan Robinson", "RB", 260),
    )

    assert what_the_roster_still_needs(two_deep, LeagueRules())["RB"] == 1
    assert what_the_roster_still_needs(three_deep, LeagueRules())["RB"] == 0


def test_five_receivers_is_depth_and_never_a_need(a_roster):
    """A seat with five receivers needs no more receivers, not minus one."""
    roster, _ = a_roster(
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 290),
        ("Puka Nacua", "WR", 280),
        ("Nico Collins", "WR", 270),
        ("Malik Nabers", "WR", 260),
    )

    assert what_the_roster_still_needs(roster, LeagueRules())["WR"] == 0


def test_a_kicker_on_the_roster_was_never_needed_and_still_is_not(a_roster):
    roster, _ = a_roster(("Justin Tucker", "K", 150))

    assert what_the_roster_still_needs(roster, LeagueRules())["K"] == 0
