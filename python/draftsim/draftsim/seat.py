"""A seat at the table: who is bidding, and what the rules let them bid.

A seat is an identity and the league it plays in, and nothing else. It holds no
roster, no spend and no picks — those are answers the record of sales already
has, and a seat that kept its own copy would be a second version of the truth
waiting to disagree with the first. So the questions here take what the record
says as a number and do the arithmetic on it.

The league travels with the seat on purpose: there is then no way to ask a seat
what it could afford under rules it is not playing under.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftsim.league import LeagueRules


@dataclass(frozen=True)
class Seat:
    """One manager at the table, and the rules they agreed to."""

    name: str
    league: LeagueRules

    def open_slots(self, players_bought: int) -> int:
        """How many roster slots this seat still has to fill."""
        return self.league.roster_size - players_bought

    def money_left(self, spent: int) -> int:
        """What this seat still holds."""
        return self.league.budget - spent

    def most_it_can_bid(self, spent: int, players_bought: int) -> int:
        """The most this seat may bid on the player in front of it.

        Everything it holds, less a dollar for every slot this purchase would
        leave unfilled. The slot the player himself would fill is not reserved,
        because this is the bid that fills it — which is why a fresh seat with
        $200 and sixteen slots can bid $185 rather than $184 or $200.

        A seat with a full roster can bid nothing at all. Not "whatever it has
        left": there is nowhere to put the player, so no price is a legal one.

        This is the only place the reserve is worked out. A bidding screen that
        greys out what a seat cannot afford and the gate that refuses the sale
        must be reading the same number, or the screen will lie.
        """
        slots_left_after_this_one = self.open_slots(players_bought) - 1
        if slots_left_after_this_one < 0:
            return 0

        return (
            self.money_left(spent)
            - slots_left_after_this_one * self.league.minimum_bid
        )

    def biddable_money(self, spent: int, players_bought: int) -> int:
        """What this seat holds that is genuinely in play.

        Everything it has, less a dollar for every slot it has yet to fill —
        including the one it is bidding on. That last dollar is the difference
        from `most_it_can_bid`, and the two are asking different questions: the
        most it can bid is what it may put on *one* player, while this is what
        it has to spread over everybody it has still to buy, over and above
        filling those slots with dollar men.

        A seat with a full roster has none of it in play. It may be sitting on a
        fortune, but it has nowhere to put anybody, so counting the money would
        price a market that is not there.
        """
        open_slots = self.open_slots(players_bought)
        if open_slots <= 0:
            return 0

        return self.money_left(spent) - open_slots * self.league.minimum_bid
