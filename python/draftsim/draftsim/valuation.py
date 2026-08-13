"""What a player is worth, which is not what he scores.

Two ideas, in order:

- **The bar.** For each position, rank the points still on the board and take
  the first man past what the league still wants there. He is what a dollar
  buys, and everything above his points is what a manager is actually paying
  for. Both halves move as the draft runs — supply as players are bought,
  demand as starting slots are filled — so where the bar lands is the race
  between the two rather than a matter of how far along the draft is. A
  position bought at exactly the pace the league needs it holds the bar on the
  same man while walking it down the line of who is left: at the opening bell
  that is the thirteenth tight end going, once nine seats have theirs it is the
  fourth, and the queue of tight ends worth paying for has collapsed from
  twelve to three. The bar only falls when bodies leave faster than slots
  close, and it climbs when slots close faster than bodies leave.
- **Worth.** What a player adds to *your* starting lineup over that
  freely-available body. Not his projection, and not what he adds to an empty
  slot.

This module reads the record and everything counted off it. Nothing down there
reads anything up here: the draft does not know how a player is valued, which
is what lets a second opinion be bolted on beside this one without touching a
line of the record.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from draftsim.board import Player, PlayerIdentity, Projections
from draftsim.draft import Draft, Holdings
from draftsim.league import POSITIONS, LeagueRules, who_fills
from draftsim.lineup import best_lineup
from draftsim.seat import Seat


def bar_from_a_ranked_line(
    points_still_going: Sequence[float], still_wanted: float
) -> float:
    """The first man past what is still wanted, out of a line ranked best-first.

    Four backs at 100, 80, 60 and 8 with three still wanted: three of them are
    spoken for, so a dollar buys the fourth, and the bar is 8.

    This is the one place a fractional need becomes a whole player. The shares
    are carried unrounded all the way here because at twelve seats a fifth of a
    slot is more than two players of movement in the ranking.

    Past the last man there is nobody left to be had at any price, so the bar is
    nothing — which is the honest answer rather than an error, and it makes a
    position that has run dry free to anybody who still wants one.
    """
    spoken_for = math.floor(still_wanted + 0.5)
    if spoken_for >= len(points_still_going):
        return 0.0

    return points_still_going[spoken_for]


def points_still_on_the_board(draft: Draft) -> Dict[str, List[float]]:
    """What the players nobody has bought are projected for, best-first."""
    still_going: Dict[str, List[float]] = {position: [] for position in POSITIONS}
    for player in draft.board.players:
        if draft.owner_of(player) is None:
            still_going[player.position].append(draft.forecast[player.identity])

    for line in still_going.values():
        line.sort(reverse=True)
    return still_going


def what_the_league_still_wants(draft: Draft) -> Dict[str, float]:
    """How many more players at each position the table's open slots call for.

    Counted by walking the starting slots nobody has filled and asking each one
    what it wants — one player's worth, divided among the positions competing
    for it.

    Subtracting what each seat has already started from its share would
    double-count. A seat that started four running backs has eaten the
    superflex its quarterback share was counting on; walking the open slots
    knows that for free, because the slot is simply gone.
    """
    still_wanted = {position: 0.0 for position in POSITIONS}
    for seat in draft.seats:
        for spot in draft.holdings(seat).lineup.spots:
            if spot.player is None:
                for position, share in who_fills(spot.slot).items():
                    still_wanted[position] += share
    return still_wanted


def the_bar(draft: Draft) -> Dict[str, float]:
    """What a dollar buys at each position, as of this moment in the record.

    A position the lineup can never start carries a bar nobody can clear. That
    is structural rather than a market condition — no slot takes a kicker, and
    no amount of buying will change it — and carrying it as an unreachable
    number instead of a special case means the callers need no branch for it: a
    freely-available body projected out of reach is one nobody can beat, so
    everybody there is worth nothing and stays worth nothing.
    """
    still_going = points_still_on_the_board(draft)
    still_wanted = what_the_league_still_wants(draft)

    return {
        position: (
            math.inf
            if draft.league.slots_a_position_can_fill(position) == 0
            else bar_from_a_ranked_line(still_going[position], still_wanted[position])
        )
        for position in POSITIONS
    }


def what_he_adds(
    player: Player,
    roster: Sequence[Player],
    forecast: Projections,
    league: LeagueRules,
    bar: Mapping[str, float],
) -> float:
    """What this player adds to that roster's starting lineup over a dollar body.

    Solve the lineup with him on the roster; solve it again with a
    freely-available body at his position, projected at the bar; the difference
    is what he is worth. Nobody is ever worth less than nothing.

    **The baseline is the freely-available body, never an empty slot**, and this
    is the one thing in the module most likely to be "simplified" back. You are
    never going to field an empty slot, so an empty slot is not what a player
    has to beat — the dollar guy is. Measured against an empty slot a player is
    worth his whole projection against an empty roster, and pricing that way put
    this league's 192 sold players at $13,472 against the $2,400 of money that
    actually exists.

    For the same reason there is no default bar. There is no honest default,
    and the zero that used to be assumed was the bug, so the caller has to say
    what a dollar buys. A position the bar does not mention at all is free:
    nobody has priced it, so there is nothing to beat.

    Solving both sides through the lineup looks like twice the work, and it is
    what makes the awkward cases answer themselves instead of each needing a
    branch. A third elite tight end finds every slot that would take him
    already his and comes out at nothing. A player at a position you have
    filled is measured against whoever he actually displaces. A kicker cannot
    be seated on either side, so both solves come out the same.
    """
    if any(already.identity == player.identity for already in roster):
        raise ValueError(
            f"{player.name} is already on this roster; what he adds to it is a "
            "question about a player who might be bought"
        )

    a_dollar_body = Player(
        name=f"A freely available {player.position}",
        position=player.position,
        team="FA",
    )
    # He has to be a real enough player to be solved into a lineup — that is
    # the whole point of measuring against him — so he needs a forecast that
    # mentions him. It is copied rather than written into: asking what somebody
    # might be worth has no business changing the numbers of the draft itself.
    with_a_dollar_body_forecast = Projections(
        {**forecast, a_dollar_body.identity: bar.get(player.position, 0.0)}
    )

    with_him = best_lineup(tuple(roster) + (player,), forecast, league).points
    with_a_dollar_body = best_lineup(
        tuple(roster) + (a_dollar_body,), with_a_dollar_body_forecast, league
    ).points

    return max(0.0, with_him - with_a_dollar_body)


def what_the_roster_still_needs(
    roster: Sequence[Player], league: LeagueRules
) -> Dict[str, int]:
    """How many more bodies of each position this roster needs to field a lineup.

    Against the rounded shopping list — 2 quarterbacks, 3 backs, 3 receivers, 1
    tight end, 1 defense — so it answers "what should I still be shopping for?"
    rather than "what does the template owe me?".

    Bodies past that count are depth, never a need: a seat with five receivers
    needs no more receivers, not minus one.
    """
    already_has = {position: 0 for position in POSITIONS}
    for player in roster:
        already_has[player.position] += 1

    return {
        position: max(0, wanted - already_has[position])
        for position, wanted in league.wanted_per_seat_rounded().items()
    }


@dataclass(frozen=True)
class ExchangeRate:
    """What a point above the bar costs, as of one moment in the record.

    The bar comes with it on purpose. The surplus below was measured against
    *this* bar, so a caller handed the rate and left to fetch a bar of its own
    could price a player against one that no longer stands. Two numbers from
    two moments is a mistake worth designing out rather than documenting.
    """

    bar: Dict[str, float]
    biddable_money: int
    surplus_to_be_bought: float
    slots_left: int

    @property
    def dollars_per_point(self) -> float:
        """Biddable money over the surplus still to be bought.

        With no slots left, or with nobody above the bar, this is nothing: both
        would otherwise be a division by nothing, and in both the honest answer
        is that there is no market here to price.
        """
        if not self.slots_left or not self.surplus_to_be_bought:
            return 0.0

        return self.biddable_money / self.surplus_to_be_bought


def the_exchange_rate(draft: Draft) -> ExchangeRate:
    """Biddable money, divided by the value still to be bought.

    Read off the sales record this is an inflation index as much as an exchange
    rate. A league that blows its budget in the first hour leaves everyone
    bidding into a cheaper market; a disciplined early market makes the back
    half dear. Recomputing it after every sale is the whole of the behaviour
    people describe as prices moving.

    It is not the same reason a price moves as the bar is. The bar says what a
    player has to beat, and it only shifts when bodies and slots leave at
    different rates. The rate says what a point above it costs. A price that
    has moved while the bar stood still moved for this reason, and it is worth
    checking which before explaining either.

    The numerator is only what is genuinely in play: what a seat holds, less a
    dollar for every slot it has yet to fill. A seat whose roster is full is
    left out of it altogether — it may be sitting on a fortune, but it cannot
    bid a penny of it, so counting the money would price a market that is not
    there.

    The denominator is the surplus of the players who will *actually be
    bought*, which is why it is capped at the slots left rather than run over
    the whole board: most of the board goes undrafted.

    That cap does not bite today, and it is insurance rather than dead weight.
    Exactly as many men clear the bar as there are open starting slots, because
    the bar stands at the man after the ones the league still wants. What keeps
    the slots left above that count is the bench — and only while no seat is
    carrying more bodies that cannot start than it has bench seats to put them
    on. The slack is 72 slots at the opening bell and nil by the end of a
    draft, where the two run exactly level.
    """
    bar = the_bar(draft)

    biddable_money = 0
    slots_left = 0
    for seat in draft.seats:
        holdings = draft.holdings(seat)
        slots_left += holdings.open_slots
        if holdings.open_slots:
            biddable_money += (
                holdings.money_left - holdings.open_slots * draft.league.minimum_bid
            )

    surplus = sorted(
        (
            max(0.0, draft.forecast[player.identity] - bar[player.position])
            for player in draft.board.players
            if draft.owner_of(player) is None
        ),
        reverse=True,
    )

    return ExchangeRate(
        bar=bar,
        biddable_money=biddable_money,
        surplus_to_be_bought=sum(surplus[:slots_left]),
        slots_left=slots_left,
    )


def price_from_worth(
    worth: float, dollars_per_point: float, ceiling: int, minimum_bid: int
) -> int:
    """The minimum bid plus what he is worth at the going rate, under the cap.

    Whole dollars, rounded down: an auction is bid in whole dollars, and
    rounding up would have the model advise a bid it has just called too dear.

    A player worth nothing still costs the minimum, because a body in an empty
    slot beats an empty slot. Unless there is no slot — a ceiling of nothing
    prices everybody at nothing, however much money the seat is sitting on.
    """
    return min(math.floor(minimum_bid + worth * dollars_per_point), ceiling)


def what_a_seat_should_pay(player: Player, seat: Seat, draft: Draft) -> int:
    """What this seat should be willing to pay for this player, right now.

    Both halves are read off one moment: the rate carries the bar his worth is
    measured against, so the points being priced and the price of a point can
    never come from two different draughts of the same draft.

    Working the rate out costs a scan of the board, which is nothing for the
    one player on the block. Pricing a whole board is what ``a_price_list`` is
    for.
    """
    return _priced_against(
        player, draft.holdings(seat), the_exchange_rate(draft), draft
    )


def a_price_list(seat: Seat, draft: Draft) -> Dict[PlayerIdentity, int]:
    """What this seat should pay for every player still on the board.

    The rate is worked out once for the lot, which is the only difference from
    asking player by player — a board of three hundred would otherwise rescan
    itself three hundred times to arrive at the same number.
    """
    rate = the_exchange_rate(draft)
    holdings = draft.holdings(seat)

    return {
        player.identity: _priced_against(player, holdings, rate, draft)
        for player in draft.board.players
        if draft.owner_of(player) is None
    }


def _priced_against(
    player: Player, holdings: Holdings, rate: ExchangeRate, draft: Draft
) -> int:
    """One price, against a rate and a roster already in hand."""
    return price_from_worth(
        worth=what_he_adds(
            player, holdings.roster, draft.forecast, holdings.seat.league, rate.bar
        ),
        dollars_per_point=rate.dollars_per_point,
        ceiling=holdings.most_it_can_bid,
        minimum_bid=draft.league.minimum_bid,
    )
