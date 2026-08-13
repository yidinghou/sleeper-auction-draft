"""The record of sales, the one gate a sale has to pass, and what it adds up to.

**The record is the only truth here.** It is an append-only list of sales, and
one sale says one thing: this seat bought this player, for this price. Who owns
whom, what a seat has spent, how many slots it has left, whose turn it is to
nominate and what its best lineup would be are all read back off that list.
None of them is stored, because four copies of one fact is three chances for
them to disagree, and that is where draft applications go wrong.

Everything counted off the record is a **cache**: a pure function of the rules,
the board, the table, the sales and the forecast. It can be thrown away and
rebuilt at any moment, and it is arrived at two ways on purpose — see
``Holdings`` and ``count_the_whole_record`` below.

What this module does *not* do is value anybody. Valuation reads the record and
the cache; nothing here reaches up into it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from draftsim.board import Board, Player, Projections
from draftsim.league import LeagueRules
from draftsim.lineup import Lineup, best_first, best_lineup, standing_of
from draftsim.seat import Seat


#: Whether every sale is checked against a fresh count of the whole record.
#:
#: On, a disagreement between the two ways of counting a draft surfaces on the
#: sale that caused it, rather than at the end of a draft by which time nobody
#: can say which sale was to blame. It costs about a fifth of a second over a
#: full 192-sale draft: cheap while a draft is being played, and not worth
#: paying by anything simulating a hundred drafts, which should turn it off
#: here rather than thread a flag through every draft it builds.
CHECK_EVERY_SALE = __debug__


class SaleRefused(Exception):
    """A sale that broke one of the rules in ``Draft.reason_to_refuse``."""


@dataclass(frozen=True)
class Sale:
    """One fact: this seat bought this player, for this price.

    When it happened is not written down here. A sale's moment is where it sits
    in the record, and the record is only ever appended to — a pick number on
    the sale itself would be a second copy of something the order already says,
    and the two could come apart the first time anything is undone.
    """

    seat: Seat
    player: Player
    price: int


@dataclass(frozen=True)
class Bid:
    """Somebody offering a price. Only the winning one becomes a sale."""

    seat: Seat
    amount: int


@dataclass(frozen=True)
class Lot:
    """The player on the block: who put him up, when it closes, and the bids.

    A lot exists between the nomination and the hammer and is then thrown away.
    Nothing in the record or in anything counted off it reads a lot — losing
    bids are facts about a moment, not about the draft — so this lives with
    whoever is running the auction rather than inside the draft.

    Only one lot is ever open at a time, and that is a real constraint rather
    than an incidental one: it is what makes "what can this seat bid" a single
    subtraction. Were three lots open at once, a seat leading all three would
    have money committed that the record has never seen, and every question
    about its budget would have to go looking for it.
    """

    player: Player
    nominated_by: Seat
    deadline: float
    bids: Tuple[Bid, ...] = ()

    def with_bid(self, bid: Bid) -> "Lot":
        """The same lot with one more bid standing against it."""
        return replace(self, bids=self.bids + (bid,))

    @property
    def standing_high_bid(self) -> Optional[Bid]:
        """The best offer so far, or nothing at all before anybody opens."""
        return max(self.bids, key=lambda bid: bid.amount, default=None)


@dataclass(frozen=True)
class Holdings:
    """What one seat has bought, and everything that follows from it.

    Part of the cache, so nothing here is a source of truth. Three shapes of
    the same players are kept because three different questions are asked of
    them, all the time:

    - ``roster`` in the order they were bought, so a bench reads like a draft
      history;
    - ``by_position`` sorted best-first, so a lineup solve or a value probe
      never has to re-sort;
    - ``lineup``, the best starting eleven those players can field.
    """

    seat: Seat
    roster: Tuple[Player, ...]
    by_position: Dict[str, Tuple[Player, ...]]
    spent: int
    lineup: Lineup

    @property
    def open_slots(self) -> int:
        return self.seat.open_slots(len(self.roster))

    @property
    def money_left(self) -> int:
        return self.seat.money_left(self.spent)

    @property
    def most_it_can_bid(self) -> int:
        return self.seat.most_it_can_bid(self.spent, len(self.roster))


def count_the_whole_record(
    seats: Sequence[Seat],
    sales: Sequence[Sale],
    forecast: Projections,
) -> Dict[str, Holdings]:
    """Add the entire record up from nothing: the slow, obvious way.

    Gather each seat's sales, take the players in the order they were bought,
    sort each position best-first, total the prices, solve the lineup. Nothing
    is carried forward from sale to sale.

    **This must never be written as a loop over the incremental fold.** The two
    ways of arriving at the same numbers are checked against each other after
    every sale, and that check is only worth running while they are genuinely
    different computations — one grouping and sorting from scratch, the other
    appending and inserting. Collapse either into the other and the check
    becomes a tautology that would agree just as cheerfully with a bug.
    (Sharing the lineup solver is fine and not the same thing: that is a
    primitive both are counting *with*, not the counting itself.)
    """
    counted = {}
    for seat in seats:
        his_sales = [sale for sale in sales if sale.seat == seat]
        roster = tuple(sale.player for sale in his_sales)
        positions = {player.position for player in roster}
        counted[seat.name] = Holdings(
            seat=seat,
            roster=roster,
            by_position={
                position: tuple(
                    best_first(
                        [player for player in roster if player.position == position],
                        forecast,
                    )
                )
                for position in positions
            },
            spent=sum(sale.price for sale in his_sales),
            lineup=best_lineup(roster, forecast, seat.league),
        )
    return counted


class Draft:
    """A draft in progress: the record of sales, and what it all adds up to."""

    def __init__(
        self,
        league: LeagueRules,
        board: Board,
        seats: Sequence[Seat],
        forecast: Projections,
    ) -> None:
        if len(seats) != league.seats:
            # The table has to be the size the league says, because the league's
            # count of seats is what its per-seat demand gets multiplied by when
            # the replacement bar is worked out. A table of ten playing under a
            # twelve-seat league would be priced for a league that isn't there.
            raise ValueError(
                f"this league seats {league.seats}; {len(seats)} sat down"
            )
        seen = set()
        for seat in seats:
            if seat.name in seen:
                raise ValueError(f"two managers cannot both be {seat.name}")
            if seat.league != league:
                raise ValueError(
                    f"{seat.name} is playing under different rules from this table"
                )
            seen.add(seat.name)

        self.league = league
        self.board = board
        self.seats = tuple(seats)
        self.forecast = forecast
        self._sales: List[Sale] = []
        self._on_the_board = {player.identity for player in board.players}
        self._recount()

    # -- Reading the record -------------------------------------------------

    @property
    def sales(self) -> Tuple[Sale, ...]:
        """Every sale so far, oldest first. Append-only; never edited."""
        return tuple(self._sales)

    def holdings(self, seat: Seat) -> Holdings:
        """What this seat has bought and everything that follows from it."""
        return self._holdings[seat.name]

    def owner_of(self, player: Player) -> Optional[Seat]:
        """Whoever bought this player, or nobody."""
        return self._owners.get(player.identity)

    def whose_nomination(self) -> Seat:
        """Whose turn it is to put a player up.

        Read off the count of sales rather than tracked. A stored pointer would
        be a second copy of something the record already says, and it would
        drift the first time a sale was undone.
        """
        return self.seats[len(self._sales) % len(self.seats)]

    # -- Selling ------------------------------------------------------------

    def reason_to_refuse(
        self, seat: Seat, player: Player, price: int
    ) -> Optional[str]:
        """Why this sale cannot happen, or ``None`` if it can.

        The one gate. A bidding screen asks it to grey out what a seat cannot
        afford, and the hammer asks it again before the sale lands, so the two
        can never come apart into slightly different copies of the same rules.
        """
        if seat not in self.seats:
            return f"{seat.name} is not at this table"
        if player.identity not in self._on_the_board:
            return f"{player.name} is not on this board"

        owner = self.owner_of(player)
        if owner is not None:
            return f"{owner.name} already bought {player.name}"

        holdings = self.holdings(seat)
        if holdings.open_slots == 0:
            return f"{seat.name}'s roster is full"
        if price < self.league.minimum_bid:
            return f"${price} is under the ${self.league.minimum_bid} minimum bid"
        if price > holdings.most_it_can_bid:
            return (
                f"{seat.name} can bid at most ${holdings.most_it_can_bid}: "
                f"it holds ${holdings.money_left} and must still fill "
                f"{holdings.open_slots} slots"
            )
        return None

    def sell(self, seat: Seat, player: Player, price: int) -> Sale:
        """Record a sale, or refuse it and leave the record untouched.

        Nothing is written until the gate has passed, so a refused sale is
        never half-applied and never rolled back.

        Unless ``CHECK_EVERY_SALE`` has been turned off, the sale is then
        counted a second way and the two are compared.
        """
        refused_because = self.reason_to_refuse(seat, player, price)
        if refused_because is not None:
            raise SaleRefused(refused_because)

        sale = Sale(seat=seat, player=player, price=price)
        self._sales.append(sale)
        self._fold_in(sale)

        if CHECK_EVERY_SALE:
            assert self._holdings == count_the_whole_record(
                self.seats, self._sales, self.forecast
            ), f"counting {player.name} to {seat.name} two ways gave two answers"

        return sale

    def undo_last_sale(self) -> Optional[Sale]:
        """Take the last sale back off the record, or do nothing if there is none.

        Everything is then counted again from what remains rather than the sale
        being run backwards. The count is disposable, so an inverse operation
        would be a second description of every rule, free to rot — and
        recounting a whole draft costs about what one draft's worth of
        incremental updates costs, once.
        """
        if not self._sales:
            return None

        undone = self._sales.pop()
        self._recount()
        return undone

    # -- The count, kept two ways ------------------------------------------

    def _fold_in(self, sale: Sale) -> None:
        """Take one sale into the count: the fast way, run once per sale.

        Append the player to the roster, slide him into his position bucket
        where his points put him, add the price, and re-solve that one seat's
        lineup. Nobody else at the table is touched.
        """
        held = self._holdings[sale.seat.name]
        position = sale.player.position
        roster = held.roster + (sale.player,)

        self._holdings[sale.seat.name] = Holdings(
            seat=held.seat,
            roster=roster,
            by_position={
                **held.by_position,
                position: _slide_into_place(
                    held.by_position.get(position, ()), sale.player, self.forecast
                ),
            },
            spent=held.spent + sale.price,
            lineup=best_lineup(roster, self.forecast, held.seat.league),
        )
        self._owners[sale.player.identity] = sale.seat

    def _recount(self) -> None:
        """Throw the count away and add the record up again from nothing."""
        self._holdings = count_the_whole_record(
            self.seats, self._sales, self.forecast
        )
        self._owners = {
            sale.player.identity: sale.seat for sale in self._sales
        }


def _slide_into_place(
    bucket: Tuple[Player, ...], arrival: Player, forecast: Projections
) -> Tuple[Player, ...]:
    """Put one player into an already best-first bucket, keeping it best-first.

    Where he stands is asked of the same rule the from-scratch sort asks, ties
    and all. Two answers to that question would be two answers to "is this the
    same roster?", which is precisely what the two ways of counting are meant
    to be unable to disagree about.
    """
    arrival_stands = standing_of(arrival, forecast)
    ahead = [
        player for player in bucket if standing_of(player, forecast) < arrival_stands
    ]

    return tuple(ahead) + (arrival,) + bucket[len(ahead):]
