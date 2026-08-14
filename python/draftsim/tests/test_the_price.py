"""What a point costs, what a player costs, and whether the books balance.

Worth is measured in points above what a dollar buys. Turning that into money
needs an exchange rate: all the money still genuinely biddable, divided by all
the surplus still to be bought. Both ends move as a draft runs, which is the
whole of "prices move" — a league that blows its budget early leaves everyone
else bidding into a cheaper market.

A price is then the minimum bid plus his worth times that rate, capped by what
the seat can legally bid, in whole dollars.

The rate and the price are arithmetic, so they are proved with the numbers on
the page. The last section runs a whole draft over the frozen board and checks
the model's prices against the money that actually exists, which is a claim
only a real board can make.
"""

import random

import pytest

from draftsim.board import Board
from draftsim.draft import Draft
from draftsim.valuation import (
    ExchangeRate,
    a_price_list,
    price_from_worth,
    the_bar,
    the_exchange_rate,
    what_a_seat_should_pay,
)


# --- The exchange rate ----------------------------------------------------


def test_at_the_opening_bell_every_dollar_over_the_reserve_is_biddable(draft):
    """Twelve seats holding $200 against sixteen slots have $2,208 to bid with.

    Each keeps $16 back — a dollar a slot — because every slot has to end up
    with a body in it, so $184 apiece is genuinely in play.
    """
    assert the_exchange_rate(draft).biddable_money == 12 * (200 - 16)


def test_spending_takes_money_out_of_the_market(draft, seats, on_the_board):
    """A dollar spent is a dollar gone, but a slot filled is a dollar of
    reserve released, so a $58 quarterback costs the market $57."""
    at_the_opening_bell = the_exchange_rate(draft).biddable_money

    draft.sell(seats[0], on_the_board("Josh Allen"), 58)

    assert the_exchange_rate(draft).biddable_money == at_the_opening_bell - 57


def test_the_league_has_a_hundred_and_ninety_two_slots_to_fill_at_the_bell(draft):
    """Twelve rosters of sixteen, none of them started, and real surplus on the
    board to fill them with."""
    rate = the_exchange_rate(draft)

    assert rate.slots_left == 12 * 16
    assert rate.surplus_to_be_bought > 0


def test_every_man_worth_paying_for_still_has_a_slot_to_go_to_late_on(
    league, board, seats, forecast
):
    """Why the denominator's cap at the slots left is insurance, not arithmetic.

    Exactly as many men clear the bar as there are open starting slots, because
    the bar stands at the man after the ones the league still wants. The bench
    absorbs the difference — but only while a seat is not carrying more bodies
    that cannot start than it has bench seats for.

    At the opening bell there are 72 bench slots of slack and this says
    nothing. Here, with fifteen bought a seat and twelve slots left in the whole
    league, the margin is all but gone, which is the state every draft ends in
    and the reason the cap stays.
    """
    dice = random.Random(5)
    draft = Draft(league, board, seats, forecast)
    for seat in seats:
        for _ in range(15):
            still_going = [
                player for player in board.players if draft.owner_of(player) is None
            ]
            draft.sell(seat, dice.choice(still_going), 1)

    bar = the_bar(draft)
    worth_paying_for = [
        player
        for player in board.players
        if draft.owner_of(player) is None
        and forecast[player.identity] > bar[player.position]
    ]
    rate = the_exchange_rate(draft)

    assert rate.slots_left == 12
    assert len(worth_paying_for) <= rate.slots_left


def test_with_no_slots_left_a_point_is_worth_nothing():
    """Everybody's roster is full: there is no market here to price."""
    a_full_league = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=900.0, slots_left=0
    )

    assert a_full_league.dollars_per_point == 0


def test_with_nobody_above_the_bar_a_point_is_worth_nothing():
    """Everyone left is a dollar man, so there is nothing to bid over."""
    a_picked_over_board = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=0.0, slots_left=40
    )

    assert a_picked_over_board.dollars_per_point == 0


def test_a_point_costs_the_money_in_the_room_over_the_points_worth_having():
    """Four hundred biddable dollars chasing a thousand points above the bar
    puts a point at forty cents."""
    market = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=1000.0, slots_left=40
    )

    assert market.dollars_per_point == 0.4


def test_an_early_spending_spree_makes_the_back_half_cheap(
    draft, seats, on_the_board
):
    """The headline: a rate read off the record is an inflation index.

    The same twelve players go off the board either way, so the surplus left to
    buy is identical — the record is rewound and replayed to hold that still.
    All that differs is the money. Nickel-and-dimed away, the first round
    leaves 54 cents on a point for everybody else; bought at $60 a head it
    leaves 37, and the back half of that draft is cheap.
    """
    first_round = [
        on_the_board(name)
        for name in (
            "Josh Allen", "Bijan Robinson", "Drake Maye", "Jahmyr Gibbs",
            "Ja'Marr Chase", "Puka Nacua", "Jaxon Smith-Njigba",
            "Jayden Daniels", "Christian McCaffrey", "Joe Burrow",
            "Lamar Jackson", "Jonathan Taylor",
        )
    ]

    for seat, player in zip(seats, first_round):
        draft.sell(seat, player, 1)
    a_thrifty_first_round = the_exchange_rate(draft).dollars_per_point

    for _ in first_round:
        draft.undo_last_sale()
    for seat, player in zip(seats, first_round):
        draft.sell(seat, player, 60)
    a_spree = the_exchange_rate(draft).dollars_per_point

    assert a_thrifty_first_round == pytest.approx(0.538, abs=0.001)
    assert a_spree == pytest.approx(0.366, abs=0.001)


def test_a_price_that_moved_while_the_bar_stood_still_moved_through_the_rate(
    draft, seats, board, forecast
):
    """The bar and the rate are two different reasons a price moves.

    Nine seats taking the top nine tight ends removes nine bodies and closes
    nine tight end slots, so the bar does not budge off 129.5 (§10). The rate
    does move — money left the room faster than surplus left the board — and
    every tight end price that changed, changed for that reason. Do not explain
    a moving price with a moving bar without checking that the bar moved.
    """
    tight_ends = sorted(
        (player for player in board.players if player.position == "TE"),
        key=lambda player: -forecast[player.identity],
    )

    before = the_exchange_rate(draft)
    for seat, tight_end in zip(seats[:9], tight_ends):
        draft.sell(seat, tight_end, 5)
    after = the_exchange_rate(draft)

    assert before.bar["TE"] == after.bar["TE"] == 129.5
    assert before.dollars_per_point == pytest.approx(0.405, abs=0.001)
    assert after.dollars_per_point == pytest.approx(0.420, abs=0.001)


# --- What a point costs one seat ------------------------------------------


def test_a_seat_holding_its_share_of_the_money_pays_the_rooms_rate():
    """Forty cents a point in the room, and this seat is holding exactly a
    quarter of the money against a quarter of the slots, so it pays forty
    cents too. Everybody equal is the opening bell, and nothing moves there."""
    market = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=1000.0, slots_left=40
    )

    assert market.what_a_point_costs(biddable_money=100, open_slots=10) == 0.4


def test_a_seat_that_spent_early_pays_less_a_point_than_the_room():
    """Same room, same forty cents. This seat has bought a quarter of the slots
    going but has only an eighth of the money left, so its points are worth half
    what the room's are — it cannot pay the room's rate for what it has left."""
    market = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=1000.0, slots_left=40
    )

    assert market.what_a_point_costs(biddable_money=50, open_slots=10) == 0.2


def test_a_seat_that_sat_on_its_hands_pays_more_a_point_than_the_room():
    market = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=1000.0, slots_left=40
    )

    assert market.what_a_point_costs(biddable_money=200, open_slots=10) == 0.8


def test_a_seat_with_nowhere_to_put_anybody_pays_nothing_a_point():
    """However much it is sitting on: it is out of the market."""
    market = ExchangeRate(
        bar={}, biddable_money=400, surplus_to_be_bought=1000.0, slots_left=40
    )

    assert market.what_a_point_costs(biddable_money=180, open_slots=0) == 0


def test_at_the_opening_bell_every_seat_is_quoted_the_rooms_rate(draft):
    """Nobody has spent, so nobody's share is off the average, so the seat's
    rate and the room's are the same number — and a board nobody has touched
    prices exactly as it did before any of this existed."""
    rate = the_exchange_rate(draft)

    for seat in draft.seats:
        holdings = draft.holdings(seat)
        assert rate.what_a_point_costs(
            holdings.biddable_money, holdings.open_slots
        ) == pytest.approx(rate.dollars_per_point)


def test_the_seats_rates_add_back_to_the_money_in_the_room(draft, seats, on_the_board):
    """The headline claim: this divides the league's money up, it does not
    invent any.

    Each seat's rate times the share of the surplus it is priced against is the
    money that seat has in play, so the twelve of them sum to the money in the
    room — after a lopsided first round as much as before one.
    """
    for seat, name in zip(seats, ("Josh Allen", "Bijan Robinson", "Drake Maye")):
        draft.sell(seat, on_the_board(name), 90)

    rate = the_exchange_rate(draft)
    back_out = sum(
        rate.what_a_point_costs(
            draft.holdings(seat).biddable_money, draft.holdings(seat).open_slots
        )
        * rate.surplus_to_be_bought
        * draft.holdings(seat).open_slots
        / rate.slots_left
        for seat in draft.seats
    )

    assert back_out == pytest.approx(rate.biddable_money)


def test_a_seat_that_cannot_bid_puts_no_money_into_the_market(
    draft, seats, board
):
    """Anna fills all sixteen slots for a dollar apiece and sits on $184.

    None of it is biddable, because she has nowhere to put anybody: counting it
    would price a market that is not there. What is left in the room is the
    other eleven seats' $184 apiece.
    """
    anna = seats[0]
    for player in board.players[:16]:
        draft.sell(anna, player, 1)

    assert draft.holdings(anna).money_left == 184
    assert draft.holdings(anna).most_it_can_bid == 0
    assert the_exchange_rate(draft).biddable_money == 11 * (200 - 16)


def test_kickers_add_nothing_to_the_value_still_to_be_bought(
    league, board, seats, forecast
):
    """A position with no bar contributes no surplus.

    Nobody can start one, so the bar at kicker is one nobody clears, and the
    twenty of them on the board weigh exactly nothing in what the league has
    left to buy.
    """
    without_kickers = Board(
        tuple(player for player in board.players if player.position != "K")
    )

    with_them = the_exchange_rate(Draft(league, board, seats, forecast))
    without_them = the_exchange_rate(Draft(league, without_kickers, seats, forecast))

    assert with_them.surplus_to_be_bought == without_them.surplus_to_be_bought


def test_the_rate_carries_the_bar_it_was_measured_against(
    draft, seats, on_the_board
):
    """A rate handed over on its own would be priced against whatever bar the
    caller fetched next, which is a different moment in the same draft."""
    at_the_opening_bell = the_exchange_rate(draft)
    for name in ("Brock Bowers", "Trey McBride", "Colston Loveland", "Tyler Warren"):
        draft.sell(seats[0], on_the_board(name), 5)

    assert at_the_opening_bell.bar["TE"] == 129.5
    assert the_bar(draft)["TE"] == 124.5


# --- What a player costs ---------------------------------------------------


def test_a_price_is_the_minimum_bid_plus_his_worth_at_the_going_rate():
    """A hundred points above the bar, at forty cents a point, is $41."""
    assert price_from_worth(
        worth=100, dollars_per_point=0.4, ceiling=185, minimum_bid=1
    ) == 41


def test_a_player_worth_nothing_still_costs_a_dollar():
    """A body in an empty slot beats an empty slot."""
    assert price_from_worth(
        worth=0, dollars_per_point=0.4, ceiling=185, minimum_bid=1
    ) == 1


def test_a_seat_with_nowhere_to_put_him_prices_him_at_nothing():
    """Not a dollar — there is no slot to put a body in, so no price is legal
    however much money the seat is sitting on."""
    assert price_from_worth(
        worth=100, dollars_per_point=0.4, ceiling=0, minimum_bid=1
    ) == 0


def test_a_price_is_whole_dollars_rounded_down():
    """Bids are made in dollars, and rounding up would advise a bid the model
    has just called too dear."""
    assert price_from_worth(
        worth=100, dollars_per_point=0.405, ceiling=185, minimum_bid=1
    ) == 41


def test_nobody_is_priced_above_what_the_seat_could_legally_bid():
    """Worth a thousand points is worth nothing you cannot pay."""
    assert price_from_worth(
        worth=1000, dollars_per_point=0.4, ceiling=185, minimum_bid=1
    ) == 185


def test_pricing_a_whole_board_agrees_with_pricing_one_man(
    draft, seats, on_the_board
):
    """The board is priced in one pass only to save rescanning it per player;
    the answer must not depend on which way it was asked.

    One man at each position, because the saving is per position: the whole
    board shares one dollar quarterback, one dollar back and so on, where
    asking about a single player solves his own. A kicker rides along as the
    position no slot will take.
    """
    priced_together = a_price_list(seats[0], draft)

    for name in ("Josh Allen", "Bijan Robinson", "Puka Nacua", "Brock Bowers",
                 "Brandon Aubrey"):
        player = on_the_board(name)
        assert priced_together[player.identity] == what_a_seat_should_pay(
            player, seats[0], draft
        ), f"{name} is priced two different ways"


def test_pricing_a_shortlist_quotes_the_same_prices_as_the_whole_board(
    draft, seats, on_the_board
):
    """A board can be priced a few men at a time, for a caller who is only
    drawing the top of it. Narrowing who is asked about must not change the
    answer for anybody — the rate and the bar are still read off the whole
    league, and only the pricing is skipped."""
    shortlist = [on_the_board(name) for name in ("Josh Allen", "Bijan Robinson")]

    priced_alone = a_price_list(seats[0], draft, shortlist)
    priced_among_everyone = a_price_list(seats[0], draft)

    assert priced_alone == {
        player.identity: priced_among_everyone[player.identity]
        for player in shortlist
    }


def test_a_table_of_seats_priced_against_one_rate_agrees_with_pricing_each(
    draft, seats
):
    """What a point costs is a fact about the league, not about who is asking,
    so a whole table can be priced against one rate. Every seat must be quoted
    exactly what it would have been quoted on its own."""
    rate = the_exchange_rate(draft)

    for seat in seats:
        assert a_price_list(seat, draft, rate=rate) == a_price_list(seat, draft)


def test_a_player_already_sold_is_left_off_the_price_list(
    draft, seats, on_the_board
):
    """A price is advice on a bid, and there is no bidding on a player who has
    gone — so he is dropped even when a caller asks about him by name."""
    allen = on_the_board("Josh Allen")
    draft.sell(seats[3], allen, 58)

    assert a_price_list(seats[0], draft, [allen]) == {}


def test_a_kicker_costs_a_dollar_and_never_a_penny_more(draft, seats, on_the_board):
    """Straight through the whole model: no slot takes him, so his worth is
    nothing, and a body in an empty slot is still worth the minimum."""
    assert what_a_seat_should_pay(on_the_board("Brandon Aubrey"), seats[0], draft) == 1


def test_the_best_quarterback_going_costs_more_than_the_twentieth(
    draft, seats, on_the_board
):
    """Prices fall down the ranking, which is the whole point of a price."""
    assert what_a_seat_should_pay(on_the_board("Josh Allen"), seats[0], draft) > (
        what_a_seat_should_pay(on_the_board("Malik Willis"), seats[0], draft)
    )

def test_a_seat_that_blew_its_budget_is_quoted_below_what_it_could_still_bid(
    draft, seats, on_the_board
):
    """The seat's own rate, and it bites long before the ceiling does.

    Anna blows $183 on Lamar Jackson and has $17 left against fifteen empty
    slots. She may legally bid $3, but $2 is all she has in play against a
    fifteenth of the surplus left, so the best quarterback going is quoted to
    her at a dollar — the price of a body in an empty slot and no more.

    Ben, who has watched, is quoted $55 for the same man — and he is the reason
    to read the rate rather than the price. The room got cheaper on that sale
    (41 cents a point down to 38), and Ben got dearer, because the money that
    left was somebody else's and his share of what remains is richer.
    """
    anna, ben = seats[0], seats[1]
    allen = on_the_board("Josh Allen")

    draft.sell(anna, on_the_board("Lamar Jackson"), 183)
    rate = the_exchange_rate(draft)
    bens = draft.holdings(ben)

    assert draft.holdings(anna).most_it_can_bid == 3
    assert what_a_seat_should_pay(allen, anna, draft) == 1

    assert rate.dollars_per_point == pytest.approx(0.378, abs=0.001)
    assert rate.what_a_point_costs(
        bens.biddable_money, bens.open_slots
    ) == pytest.approx(0.410, abs=0.001)
    assert what_a_seat_should_pay(allen, ben, draft) == 55


def test_a_seat_down_to_one_slot_is_quoted_no_more_than_it_can_bid(
    draft, seats, on_the_board
):
    """The ceiling out of §5, still wired straight through.

    Anna fills fifteen of her sixteen slots for a dollar apiece and comes to the
    last one holding $185. A seat with one slot left is priced against a
    sixteenth of nothing, so her rate runs away and the best quarterback going is
    worth more to her than the table has money — the ceiling is what stops it.
    """
    anna = seats[0]
    for player in [
        player for player in draft.board.players if player.name != "Josh Allen"
    ][:15]:
        draft.sell(anna, player, 1)

    assert draft.holdings(anna).most_it_can_bid == 185
    assert what_a_seat_should_pay(on_the_board("Josh Allen"), anna, draft) == 185


# --- The books balance ----------------------------------------------------


def test_the_league_prices_its_players_at_about_the_money_that_exists(draft, seats):
    """The accountant's check, and the one that catches an error anywhere.

    Twelve seats with $200 is $2,400 of real money chasing the 192 players who
    will be bought. Add up what the model says those 192 are worth at the
    opening bell and the total comes to $2,340 — near enough the money in the
    room, and nowhere near the $13,472 that measuring against an empty slot
    produced.
    """
    priced = sorted(a_price_list(seats[0], draft).values(), reverse=True)
    the_ones_who_will_go = priced[: 12 * 16]

    # A fact about this board rather than a tolerance: the frozen sheet never
    # moves, so this figure only changes if the model does.
    assert sum(the_ones_who_will_go) == 2340

    money_in_the_league = 12 * 200
    assert sum(the_ones_who_will_go) == pytest.approx(money_in_the_league, rel=0.1)
