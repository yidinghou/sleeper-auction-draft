"""The record of who bought whom, and everything read back off it.

One sale records one fact: this seat bought this player, for this price. There
is no owned flag, no owner written on a player and no running total of what a
seat has left — whether a player is gone, what a seat holds and whose turn it
is are all answered from the record, so there is only ever one version of them
to be wrong.

This file also pins down the single gate every sale has to pass. A bidding
screen greying out what a seat cannot afford and the hammer falling on a sale
must be asking the same question, or the screen will lie about the sale it is
about to allow.

The table and the board come from the frozen sheet, because these claims are
about how a draft behaves rather than about arithmetic anyone should check in
their head.
"""

import dataclasses

import pytest

from draftsim.board import Player
from draftsim.draft import Bid, Draft, Lot, SaleRefused
from draftsim.league import LeagueRules
from draftsim.seat import Seat


# --- What a sale is, and what it isn't ------------------------------------


def test_a_sale_records_the_seat_the_player_and_the_price_and_nothing_else(
    draft, seats, on_the_board
):
    anna = seats[0]
    allen = on_the_board("Josh Allen")

    sale = draft.sell(anna, allen, 58)

    assert (sale.seat, sale.player, sale.price) == (anna, allen, 58)
    assert draft.sales == (sale,)


def test_the_moment_a_sale_happened_is_where_it_sits_in_the_record(
    draft, seats, on_the_board
):
    """A pick number stored on the sale would be a second copy of something the
    order of the record already says."""
    anna, ben = seats[0], seats[1]

    first = draft.sell(anna, on_the_board("Josh Allen"), 58)
    second = draft.sell(ben, on_the_board("Bijan Robinson"), 57)

    assert draft.sales.index(first) == 0
    assert draft.sales.index(second) == 1


def test_a_sale_once_recorded_is_never_edited(draft, seats, on_the_board):
    """Correcting a price means taking the sale back off and selling again."""
    sale = draft.sell(seats[0], on_the_board("Josh Allen"), 58)

    with pytest.raises(dataclasses.FrozenInstanceError):
        sale.price = 40


def test_who_owns_a_player_is_answered_from_the_record(draft, seats, on_the_board):
    anna = seats[0]
    allen = on_the_board("Josh Allen")

    assert draft.owner_of(allen) is None

    draft.sell(anna, allen, 58)

    assert draft.owner_of(allen) == anna
    assert not hasattr(allen, "owner")


def test_what_a_seat_has_spent_is_added_up_from_its_sales(
    draft, seats, on_the_board
):
    anna = seats[0]

    draft.sell(anna, on_the_board("Josh Allen"), 58)
    draft.sell(anna, on_the_board("Bijan Robinson"), 57)

    assert draft.holdings(anna).spent == 115
    assert anna.money_left(draft.holdings(anna).spent) == 85


def test_a_losing_bid_never_enters_the_record(draft, seats, on_the_board):
    """A losing bid is a fact about a moment, not about the draft. Keeping them
    alongside sales would make every later count filter for the winning one."""
    anna, ben = seats[0], seats[1]
    allen = on_the_board("Josh Allen")

    lot = Lot(player=allen, nominated_by=anna, deadline=10.0)
    lot = lot.with_bid(Bid(seat=ben, amount=40))
    lot = lot.with_bid(Bid(seat=anna, amount=58))
    draft.sell(anna, allen, lot.standing_high_bid.amount)

    assert [(sale.seat, sale.price) for sale in draft.sales] == [(anna, 58)]


# --- The lot on the block, which nothing else reads -----------------------


def test_nobody_is_the_high_bidder_until_somebody_opens(seats, on_the_board):
    lot = Lot(player=on_the_board("Josh Allen"), nominated_by=seats[0], deadline=10.0)

    assert lot.standing_high_bid is None


def test_the_standing_high_bid_is_the_best_anybody_has_offered_so_far(
    seats, on_the_board
):
    anna, ben = seats[0], seats[1]
    lot = Lot(player=on_the_board("Josh Allen"), nominated_by=anna, deadline=10.0)

    lot = lot.with_bid(Bid(seat=ben, amount=40)).with_bid(Bid(seat=anna, amount=58))

    assert lot.standing_high_bid == Bid(seat=anna, amount=58)


def test_the_draft_carries_no_lot_because_nothing_it_computes_reads_one(draft):
    """The auction on the block lives with whoever is running the auction. The
    record and everything counted off it never look at it, so keeping it here
    would only invite something to start."""
    for the_auction_in_progress in ("lot", "lots", "on_the_block", "bids", "deadline"):
        assert not hasattr(draft, the_auction_in_progress)


def test_leading_an_auction_commits_none_of_a_seats_money(draft, seats, on_the_board):
    """Why only one player is ever on the block at a time.

    A bid commits nothing until the hammer falls, so what a seat can bid stays
    a single subtraction against the record. Were three lots open at once, a
    seat leading all three would have money promised that the record has never
    seen, and every question about its budget would have to go hunting for it.
    """
    anna = seats[0]

    Lot(
        player=on_the_board("Josh Allen"), nominated_by=anna, deadline=10.0
    ).with_bid(Bid(seat=anna, amount=150))

    assert draft.holdings(anna).most_it_can_bid == 185


# --- Whose turn it is to nominate -----------------------------------------


def test_nomination_goes_round_the_table_in_seat_order(draft, seats, on_the_board):
    assert draft.whose_nomination() == seats[0]

    draft.sell(seats[0], on_the_board("Josh Allen"), 58)

    assert draft.whose_nomination() == seats[1]


def test_undoing_a_sale_hands_the_nomination_back(draft, seats, on_the_board):
    """The claim that a turn is worked out rather than kept.

    A pointer moved along at each sale would still be pointing at the second
    seat here, because nothing moved it back when the sale it was counting came
    off the record. Reading the turn off the sales themselves cannot drift,
    because there is nothing to drift from.
    """
    draft.sell(seats[0], on_the_board("Josh Allen"), 58)
    assert draft.whose_nomination() == seats[1]

    draft.undo_last_sale()

    assert draft.whose_nomination() == seats[0]


def test_seven_players_sold_means_the_eighth_seat_nominates(
    draft, seats, on_the_board
):
    """Whose turn it is is read off the count of sales rather than tracked: a
    stored pointer is a second copy of a fact, and it can drift."""
    for player, buyer in zip(
        ("Josh Allen", "Bijan Robinson", "Ja'Marr Chase"), seats
    ):
        draft.sell(buyer, on_the_board(player), 5)
    for player in ("Justin Jefferson", "Saquon Barkley", "Jahmyr Gibbs", "Malik Nabers"):
        draft.sell(seats[0], on_the_board(player), 5)

    assert len(draft.sales) == 7
    assert draft.whose_nomination() == seats[7]


def test_the_nomination_comes_back_round_to_the_first_seat(
    draft, seats, on_the_board
):
    for player, buyer in zip(
        (
            "Josh Allen",
            "Bijan Robinson",
            "Ja'Marr Chase",
            "Justin Jefferson",
            "Saquon Barkley",
            "Jahmyr Gibbs",
            "Malik Nabers",
            "Amon-Ra St. Brown",
            "Nico Collins",
            "Brock Bowers",
            "Trey McBride",
            "Lamar Jackson",
        ),
        seats,
    ):
        draft.sell(buyer, on_the_board(player), 5)

    assert draft.whose_nomination() == seats[0]


# --- The one gate every sale has to pass ----------------------------------


def test_a_seat_from_another_league_cannot_buy_anybody(draft, on_the_board):
    stranger = Seat("A Stranger", LeagueRules())

    with pytest.raises(SaleRefused, match="not at this table"):
        draft.sell(stranger, on_the_board("Josh Allen"), 5)


def test_a_player_who_is_not_on_the_board_cannot_be_bought(draft, seats):
    made_up = Player(name="A Made Up Man", position="RB", team="BUF")

    with pytest.raises(SaleRefused, match="not on this board"):
        draft.sell(seats[0], made_up, 5)


def test_a_player_somebody_already_bought_is_refused_by_name(
    draft, seats, on_the_board
):
    """The refusal says who has him, because that is the first thing anyone
    asks."""
    anna, ben = seats[0], seats[1]
    allen = on_the_board("Josh Allen")
    draft.sell(anna, allen, 58)

    with pytest.raises(SaleRefused, match="Anna"):
        draft.sell(ben, allen, 60)


def test_a_seat_with_a_full_roster_cannot_buy_anybody_else(
    draft, seats, board, on_the_board
):
    anna = seats[0]
    for player in board.players[:16]:
        draft.sell(anna, player, 1)

    with pytest.raises(SaleRefused, match="full"):
        draft.sell(anna, board.players[16], 1)


def test_nobody_can_be_bought_for_less_than_the_minimum_bid(
    draft, seats, on_the_board
):
    with pytest.raises(SaleRefused, match=r"\$1"):
        draft.sell(seats[0], on_the_board("Josh Allen"), 0)


def test_a_bid_over_what_a_seat_can_afford_is_refused_and_says_what_it_holds(
    draft, seats, on_the_board
):
    """A person reading "$186 is too much" needs the three numbers behind it:
    the ceiling, what the seat is holding, and how many slots it still owes."""
    anna = seats[0]

    with pytest.raises(SaleRefused) as refusal:
        draft.sell(anna, on_the_board("Josh Allen"), 186)

    said = str(refusal.value)
    assert "$185" in said and "$200" in said and "16" in said


def test_a_refused_sale_leaves_the_record_untouched(draft, seats, on_the_board):
    anna = seats[0]
    draft.sell(anna, on_the_board("Josh Allen"), 58)

    with pytest.raises(SaleRefused):
        draft.sell(anna, on_the_board("Bijan Robinson"), 500)

    assert len(draft.sales) == 1
    assert draft.holdings(anna).spent == 58
    assert draft.owner_of(on_the_board("Bijan Robinson")) is None


def test_asking_whether_a_sale_is_allowed_and_trying_it_are_the_same_question(
    draft, seats, on_the_board
):
    """One implementation, so a board can grey out exactly what the sale will
    refuse rather than a second, slightly different copy of the rules."""
    anna = seats[0]
    allen = on_the_board("Josh Allen")

    assert draft.reason_to_refuse(anna, allen, 58) is None

    draft.sell(anna, allen, 58)
    refused_because = draft.reason_to_refuse(seats[1], allen, 60)

    assert refused_because is not None
    with pytest.raises(SaleRefused) as refusal:
        draft.sell(seats[1], allen, 60)
    assert str(refusal.value) == refused_because


# --- Undoing the last sale ------------------------------------------------


def test_undoing_the_last_sale_puts_the_player_back_on_the_board(
    draft, seats, on_the_board
):
    anna = seats[0]
    allen = on_the_board("Josh Allen")
    draft.sell(anna, allen, 58)

    draft.undo_last_sale()

    assert draft.sales == ()
    assert draft.owner_of(allen) is None
    assert draft.holdings(anna).spent == 0
    assert draft.holdings(anna).roster == ()


def test_undoing_leaves_every_earlier_sale_standing(draft, seats, on_the_board):
    anna, ben = seats[0], seats[1]
    draft.sell(anna, on_the_board("Josh Allen"), 58)
    draft.sell(ben, on_the_board("Bijan Robinson"), 57)

    draft.undo_last_sale()

    assert [sale.seat for sale in draft.sales] == [anna]
    assert draft.holdings(ben).roster == ()
    assert draft.holdings(anna).spent == 58


def test_undoing_when_nothing_has_been_sold_is_not_an_error(draft):
    assert draft.undo_last_sale() is None
    assert draft.sales == ()


# --- Who is allowed at the table ------------------------------------------


def test_two_managers_cannot_sit_down_under_one_name(board, forecast):
    small_league = LeagueRules(seats=3)
    twice = tuple(Seat(name, small_league) for name in ("Anna", "Ben", "Anna"))

    with pytest.raises(ValueError, match="Anna"):
        Draft(small_league, board, twice, forecast)


def test_a_draft_needs_a_seat_for_everybody_the_league_says_is_playing(
    league, board, forecast
):
    too_few = tuple(Seat(name, league) for name in ("Anna", "Ben"))

    with pytest.raises(ValueError):
        Draft(league, board, too_few, forecast)


def test_nobody_may_sit_down_under_rules_they_did_not_agree_to(
    league, board, forecast, seats
):
    poorer = LeagueRules(budget=100)
    interloper = (Seat("Interloper", poorer),) + seats[1:]

    with pytest.raises(ValueError, match="Interloper"):
        Draft(league, board, interloper, forecast)
