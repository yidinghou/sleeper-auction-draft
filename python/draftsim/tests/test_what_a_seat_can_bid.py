"""What a seat has left, and the most it is allowed to bid.

A seat holds a budget and a roster it has to fill, and the two constrain each
other: every slot it has yet to fill has a dollar spoken for, because a body in
every slot is not optional. What is left over after that reserve is the most it
can bid on the player in front of it.

Every number here is declared on the page — a budget, a few dollars spent, a
count of players bought — because the whole subject is one subtraction and a
reader should be able to check it in their head.
"""

import pytest

from draftsim.league import LeagueRules
from draftsim.seat import Seat


def a_seat(name="Anna", **house_rules):
    return Seat(name, LeagueRules(**house_rules))


# --- What a seat has ------------------------------------------------------


def test_a_seat_counts_its_open_slots_from_the_players_it_has_bought():
    anna = a_seat()

    assert anna.open_slots(players_bought=0) == 16
    assert anna.open_slots(players_bought=3) == 13
    assert anna.open_slots(players_bought=16) == 0


def test_a_seat_has_what_it_started_with_less_what_it_has_spent():
    anna = a_seat()

    assert anna.money_left(spent=0) == 200
    assert anna.money_left(spent=61) == 139


# --- What it can bid ------------------------------------------------------


def test_a_seat_reserves_a_dollar_for_every_slot_the_bid_will_not_fill():
    """$200 and sixteen empty slots is a $185 bid, not a $200 one.

    Fifteen dollars are spoken for by the fifteen slots this purchase leaves
    unfilled. The sixteenth is not reserved, because this is the bid that fills
    it.
    """
    anna = a_seat()

    assert anna.most_it_can_bid(spent=0, players_bought=0) == 185


def test_a_sixty_one_dollar_running_back_leaves_a_hundred_and_twenty_five_to_bid():
    anna = a_seat()

    assert anna.most_it_can_bid(spent=61, players_bought=1) == 125


def test_a_seat_with_a_full_roster_can_bid_nothing_at_all():
    """Not "whatever it has left" — nothing, because there is nowhere to put
    the player, however much money is sitting there."""
    anna = a_seat()

    assert anna.money_left(spent=20) == 180
    assert anna.most_it_can_bid(spent=20, players_bought=16) == 0


def test_a_seat_down_to_its_last_slot_can_spend_everything_on_it():
    anna = a_seat()

    assert anna.most_it_can_bid(spent=150, players_bought=15) == 50


def test_a_seat_that_has_spent_everything_but_the_reserve_can_still_bid_a_dollar():
    """Which is the whole reason a league is refused a budget under a dollar a
    slot: no seat may ever be priced out of bidding the minimum."""
    anna = a_seat()

    assert anna.most_it_can_bid(spent=185, players_bought=1) == 1


# --- What it has to spread over everybody it still has to buy -------------


def test_a_fresh_seat_has_a_hundred_and_eighty_four_dollars_in_play():
    """A dollar apiece for all sixteen slots is spoken for; the rest is what it
    has to bid *with* across the whole draft.

    One dollar below the $185 it may put on the man in front of it, and the two
    are different questions: that is the most it can pay for one player, this is
    what it has over and above filling every slot with a dollar man.
    """
    anna = a_seat()

    assert anna.biddable_money(spent=0, players_bought=0) == 184
    assert anna.most_it_can_bid(spent=0, players_bought=0) == 185


def test_a_sixty_one_dollar_running_back_leaves_a_hundred_and_twenty_four_in_play():
    anna = a_seat()

    assert anna.biddable_money(spent=61, players_bought=1) == 124


def test_a_seat_with_a_full_roster_has_none_of_its_money_in_play():
    """It is sitting on $180 and cannot bid a penny of it, because there is
    nowhere to put anybody."""
    anna = a_seat()

    assert anna.money_left(spent=20) == 180
    assert anna.biddable_money(spent=20, players_bought=16) == 0


def test_a_seat_down_to_the_reserve_has_nothing_left_in_play():
    """It can still bid the minimum on every slot it has left — that is what
    the reserve is for — but it has nothing to bid *over* the minimum with."""
    anna = a_seat()

    assert anna.biddable_money(spent=185, players_bought=1) == 0
    assert anna.most_it_can_bid(spent=185, players_bought=1) == 1


# --- Under its own league's rules and nobody else's -----------------------


def test_a_seat_in_a_poorer_league_is_priced_by_the_league_it_plays_in():
    """A seat carries its own rules, so there is no way to ask it what it could
    afford in somebody else's league."""
    anna = a_seat(budget=50)

    assert anna.most_it_can_bid(spent=0, players_bought=0) == 35


def test_a_seat_knows_nothing_about_a_draft_in_progress():
    """It answers questions about money and slots; what has actually been sold
    is the record's business, and it is handed in as a number."""
    anna = a_seat()

    for the_draft_so_far in ("sales", "roster", "spent", "players", "lineup"):
        assert not hasattr(anna, the_draft_so_far)


def test_two_seats_of_the_same_name_at_the_same_table_are_the_same_seat():
    """Which is why a table refuses to seat two people under one name."""
    assert a_seat("Anna") == a_seat("Anna")
    assert a_seat("Anna") != a_seat("Ben")
