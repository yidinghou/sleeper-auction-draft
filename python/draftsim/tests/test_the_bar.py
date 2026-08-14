"""What a dollar buys: the bar a player has to beat to be worth anything.

For each position, rank the points still on the board and take the first man
past what the league still wants there. Twelve seats wanting 2.77 backs apiece
want about 33, so the 34th back is what a dollar buys, and everything above his
points is what a manager is actually paying for.

Both halves of that move as a draft runs — supply as players are bought, demand
as starting slots are filled — so where the bar lands is a race between the two.
Bought at exactly the pace the league needs them, the bar holds on the same man
and walks down the line of who is left. Bought faster than they are needed, it
drops. Needed faster than they are bought, it climbs. A position filling up does
not on its own make anything cheaper, and the three tests at the foot of this
file are the three outcomes of that race.

The arithmetic — an index into a ranked line — is proved with four numbers
written on the page. The claims about a draft moving underneath it are proved
against the frozen board, because that is where the shape of a real position
run comes from.
"""

import math

import pytest

from draftsim.valuation import (
    bar_from_a_ranked_line,
    points_still_on_the_board,
    the_bar,
    what_the_league_still_wants,
)


def tight_ends_by_points(board, forecast):
    """Every tight end on the board, best-projected first."""
    return sorted(
        (player for player in board.players if player.position == "TE"),
        key=lambda player: -forecast[player.identity],
    )


def how_many_tight_ends_clear(bar, draft):
    """How many tight ends still going are worth paying above a dollar for."""
    return sum(
        1 for points in points_still_on_the_board(draft)["TE"] if points > bar
    )



# --- The bar is one lookup into a ranked line -----------------------------


def test_the_bar_is_the_first_man_past_what_the_league_still_wants():
    """Four backs, three of them spoken for: the fourth is what a dollar buys."""
    backs = (100.0, 80.0, 60.0, 8.0)

    assert bar_from_a_ranked_line(backs, still_wanted=3) == 8.0


def test_a_fraction_of_a_slot_is_rounded_into_a_player_here_and_nowhere_else():
    """The shares are carried unrounded all the way to this one lookup.

    At twelve seats a fifth of a slot is more than two players of movement in
    the ranking, so rounding earlier would move the bar by more than the
    fraction looks worth.
    """
    backs = (100.0, 80.0, 60.0, 8.0)

    assert bar_from_a_ranked_line(backs, still_wanted=2.4) == 60.0
    assert bar_from_a_ranked_line(backs, still_wanted=2.6) == 8.0


def test_when_nobody_still_wants_a_back_the_bar_is_the_best_one_going():
    """Nobody has a slot for him, so nobody is paying above a dollar."""
    backs = (100.0, 80.0, 60.0, 8.0)

    assert bar_from_a_ranked_line(backs, still_wanted=0) == 100.0


def test_past_the_last_man_at_a_position_the_bar_is_nothing():
    """There is nobody left to be had at any price."""
    backs = (100.0, 80.0, 60.0, 8.0)

    assert bar_from_a_ranked_line(backs, still_wanted=4) == 0.0
    assert bar_from_a_ranked_line((), still_wanted=2) == 0.0


# --- What the league still wants ------------------------------------------


def test_at_the_opening_bell_the_league_wants_its_shares_twelve_times_over(draft):
    """Twelve seats wanting 2.77 backs apiece want about 33 running backs.

    Written out rather than worked out from the share table, because this is
    the number the whole bar is built on: computing the expectation the same
    way the code does would let a mistyped share table agree with itself.
    """
    assert what_the_league_still_wants(draft) == pytest.approx(
        {"QB": 24.0, "RB": 33.24, "WR": 38.76, "TE": 12.0, "K": 0.0, "DEF": 12.0}
    )


def test_a_filled_slot_stops_the_league_wanting_anybody_for_it(
    draft, seats, on_the_board
):
    draft.sell(seats[0], on_the_board("Josh Allen"), 58)

    still_wanted = what_the_league_still_wants(draft)

    assert still_wanted["QB"] == 12 * 2.00 - 1.00


def test_a_seat_that_started_four_backs_has_eaten_the_superflex_its_quarterbacks_wanted(
    draft, seats, board
):
    """Why demand is counted off the open slots rather than subtracted.

    Anna's four backs fill both running back slots, the flex and the superflex.
    Subtracting what she has started from her share would still have her
    wanting two quarterbacks; walking her open slots knows the superflex is
    gone, and asks for one.
    """
    anna = seats[0]
    backs = [player for player in board.players if player.position == "RB"][:4]
    for back in backs:
        draft.sell(anna, back, 1)

    still_wanted = what_the_league_still_wants(draft)

    assert still_wanted["QB"] == 11 * 2.00 + 1.00
    assert still_wanted["RB"] == pytest.approx(11 * 2.77)


# --- Supply falls as the board empties ------------------------------------


def test_a_player_already_bought_no_longer_holds_the_bar_up(
    draft, seats, board, forecast
):
    best_tight_end = tight_ends_by_points(board, forecast)[0]

    before = points_still_on_the_board(draft)["TE"]
    draft.sell(seats[0], best_tight_end, 40)
    after = points_still_on_the_board(draft)["TE"]

    assert before[0] == forecast[best_tight_end.identity]
    assert forecast[best_tight_end.identity] not in after
    assert len(after) == len(before) - 1


# --- The bar over a draft in progress -------------------------------------


def test_the_bar_holds_when_tight_ends_go_as_fast_as_they_are_needed(
    draft, seats, board, forecast
):
    """The claim the whole section exists for.

    Twelve seats want a tight end each, so at the opening bell a dollar buys
    the thirteenth man and twelve tight ends are worth paying for. Nine seats
    taking the top nine removes nine bodies and closes nine tight end slots, so
    the index walks down the line by exactly as much as the line shortens: the
    bar lands on the same man, scoring the same 129.5.

    What has changed is where he sits in what is left — from the thirteenth
    tight end going to the fourth — and the queue worth paying for has
    collapsed from twelve to three. That is why a middling tight end is
    suddenly worth real money to the three seats still short: not because he
    got cheaper to beat, but because the men who were worth more than him have
    gone.
    """
    tight_ends = tight_ends_by_points(board, forecast)

    at_the_opening_bell = the_bar(draft)["TE"]
    assert at_the_opening_bell == 129.5
    assert how_many_tight_ends_clear(at_the_opening_bell, draft) == 12

    for seat, tight_end in zip(seats[:9], tight_ends):
        draft.sell(seat, tight_end, 5)

    once_nine_seats_have_theirs = the_bar(draft)["TE"]
    assert once_nine_seats_have_theirs == 129.5
    assert how_many_tight_ends_clear(once_nine_seats_have_theirs, draft) == 3


def test_a_seat_hoarding_tight_ends_drops_the_bar_for_everyone(
    draft, seats, board, forecast
):
    """Supply shrinking faster than demand.

    Anna's four tight ends take four bodies off the line but close only one
    tight end slot — there is only one to close, and the other three land in
    flexes the tight end share was never counting on. Demand falls by one,
    supply by four, and what a dollar buys drops three rungs for everybody.
    """
    tight_ends = tight_ends_by_points(board, forecast)

    assert what_the_league_still_wants(draft)["TE"] == 12.0
    assert len(points_still_on_the_board(draft)["TE"]) == 60
    assert the_bar(draft)["TE"] == 129.5

    for tight_end in tight_ends[:4]:
        draft.sell(seats[0], tight_end, 5)

    assert what_the_league_still_wants(draft)["TE"] == 11.0
    assert len(points_still_on_the_board(draft)["TE"]) == 56
    assert the_bar(draft)["TE"] == 124.5


def test_taking_a_tight_end_from_far_down_the_line_lifts_the_bar(
    draft, seats, on_the_board
):
    """Demand shrinking faster than supply, which is the other way round.

    A seat that fills its tight end slot from deep in the line closes a slot
    without removing anybody the bar was resting on. The line above the bar is
    untouched, the need is one smaller, and the bar climbs a rung. Scarcity is
    the race between the two, not the passage of the draft.
    """
    assert the_bar(draft)["TE"] == 129.5

    draft.sell(seats[0], on_the_board("Cade Otton"), 1)

    assert the_bar(draft)["TE"] == 130.6


def test_a_kicker_can_never_be_worth_anything_and_that_never_moves(
    draft, seats, on_the_board
):
    """Structural, not a market condition: no slot in the lineup takes one.

    Carried as a bar nobody can clear, rather than as a special case every
    caller has to remember.
    """
    assert the_bar(draft)["K"] == math.inf

    for seat, name in zip(
        seats, ("Brandon Aubrey", "Cameron Dicker", "Chris Boswell")
    ):
        draft.sell(seat, on_the_board(name), 3)

    assert the_bar(draft)["K"] == math.inf
