# draftsim

A model of a fantasy football **auction** draft: twelve managers, $200 each,
sixteen roster slots, and one player on the block at a time. It answers the
question a manager actually has at the table — *what is this player worth to
me, right now?* — and it answers it in dollars.

It is a library. There is no auction loop, no bidding robot, no command line
and no web layer; a draft is driven by whatever is running the auction, and
this tells it what things are worth.

## The two ideas it is built on

**The record of sales is the only truth.** A draft is an append-only list of
sales, and one sale says one thing: this seat bought this player, for this
price. There is no owned flag on a player, no owner, no running total of what a
seat has left. Who owns whom, what a seat has spent, how many slots it still
has, whose turn it is to nominate and what its best lineup would be are all
*counted* from that list. Four copies of one fact is three chances for them to
disagree, and that is where draft applications go wrong. Undo takes the last
sale off and counts everything again from what remains.

**A player is worth what he adds to your starting lineup over a body you could
have for a dollar.** Not what he scores. It is worth being blunt about this,
because the obvious simplification — measure him against the empty slot he
would fill — is wrong in a way that looks right: you are never going to field
an empty slot, so an empty slot is not what a player has to beat. Priced that
way, this league's 192 sold players come to $13,472 against the $2,400 that
actually exists. Priced against the dollar man, they come to $2,340.

## How a price is built

Four steps, each of which you can ask about on its own.

1. **The bar** — for each position, rank the points still on the board and take
   the first man past what the league still wants there. Twelve seats wanting
   2.77 running backs apiece want about 33, so the 34th back is what a dollar
   buys. Everything above his points is what you are actually paying for.
2. **Worth** — solve your lineup with the player on your roster; solve it again
   with a freely-available body at his position projected at the bar; the
   difference is what he adds. Doing it through the lineup twice is what makes
   the awkward cases answer themselves: a fourth tight end finds every slot that
   would take him already taken and comes out at nothing, and a fifth running
   back is measured against the back he displaces rather than against a bar he
   never has to beat.
3. **The exchange rate** — all the money still genuinely biddable, divided by
   all the surplus over the bar still to be bought. About 40 cents a point at
   the opening bell of this league.
4. **The price** — the minimum bid, plus his worth at that rate, capped by what
   the seat can legally bid. Whole dollars, rounded down.

## Why prices move

Two different reasons, and it is worth knowing which one you are looking at.

**The bar moves** when bodies and slots leave the board at different rates.
This is less intuitive than it sounds. Nine seats buying the top nine tight ends
does *not* make tight ends cheaper: it removes nine bodies and closes nine tight
end slots, so the bar lands on the same man scoring the same points. What
changes is where he sits in what is left — from the thirteenth tight end going
to the fourth — so the number of tight ends worth paying for collapses from
twelve to three, and the three seats still short have to pay for one of them.
The bar only falls when supply shrinks faster than demand, as when one seat
hoards four tight ends nobody else can start; it rises when demand shrinks
faster than supply.

**The rate moves** with the money in the room. Read off the sales record it is
an inflation index: a league that blows its budget in the first hour leaves
everyone else bidding into a cheaper market, and a disciplined early market
makes the back half dear.

## Using it

```python
from pathlib import Path

from draftsim.board import read_board
from draftsim.draft import Draft
from draftsim.league import LeagueRules
from draftsim.seat import Seat
from draftsim.valuation import (
    a_price_list,
    the_exchange_rate,
    what_a_seat_should_pay,
)

board, forecast = read_board(Path("data/projections-2026.csv"))
league = LeagueRules()
seats = tuple(
    Seat(name, league)
    for name in ("Anna", "Ben", "Chloe", "Dev", "Elena", "Femi",
                 "Gus", "Hana", "Ivan", "Jo", "Kip", "Lena")
)
draft = Draft(league, board, seats, forecast)

# Live picks arrive with the provider's player id on them; 4984 is Josh Allen.
allen = board.by_provider_id()["4984"]
what_a_seat_should_pay(allen, seats[0], draft)       # 55

# The market's own board says $58 for him, and $58 is what the room pays.
draft.sell(seats[0], allen, 58)
draft.holdings(seats[0]).most_it_can_bid             # 128
draft.whose_nomination()                             # Seat('Ben', ...)
the_exchange_rate(draft).dollars_per_point           # 0.404

# And to put a price against every name on the board at once:
a_price_list(seats[0], draft)                        # {identity: dollars, ...}
```

(Those are the real numbers off the 2026 export, not illustrations.)

Every sale runs one gate, and asking whether a sale is allowed calls exactly
the code the hammer does, so a board that greys out what a seat cannot afford
can never disagree with the sale it is about to refuse. Refusals are written for
a person: *"Anna can bid at most $185: it holds $200 and must still fill 16
slots."*

## Where things live

| Module | What it knows |
| --- | --- |
| `league.py` | Seats, budget, the sixteen-slot template, which slots take which positions, and how much of each position a lineup wants |
| `board.py` | Reading the projections spreadsheet into players, with the forecast kept beside them rather than inside them |
| `lineup.py` | The best legal starting lineup a roster can field, and how it reads down the template |
| `seat.py` | One manager, and what the rules let them bid |
| `draft.py` | The record of sales, the gate, and everything counted off the record |
| `valuation.py` | The bar, worth, the exchange rate, the price |

Valuation reads the record; the record knows nothing about valuation, so a
second opinion about what players are worth can be written beside this one
without touching a line of the draft.

## Running the tests

```sh
cd python/draftsim
python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest -q
```

Standard library only, pytest for the tests.

The suite is written to be read: every test name is a sentence about football,
and every file opens with a docstring saying what it is for. Tests come in two
registers, and which one a claim gets is a decision made each time. Claims about
arithmetic declare their numbers on the page — four backs at 100, 80, 60 and 8
with three of them wanted, and the bar is 8. Claims that reality supplies a
shape run against the frozen 292-player board in `tests/data/`, which is
committed so that re-exporting the real spreadsheet can never move a test. One
test reads the live export at `data/projections-2026.csv`, so the suite doubles
as a schema check on it.

Two tests carry more weight than the rest. `test_a_draft_folded_in_sale_by_sale_matches_one_counted_from_scratch`
plays random legal drafts and checks the running count against a count built
from scratch, seat by seat and field by field — the two are written as
genuinely different computations, and there is a test to stop anyone collapsing
them into one. And `test_the_league_prices_its_players_at_about_the_money_that_exists`
is the accountant's check: price the board at the opening bell, add up the 192
who will be bought, and the total should look like the $2,400 in the room.

While a draft is being played, every sale is counted both ways and the two
compared, so a disagreement surfaces on the sale that caused it. Anything
simulating drafts in bulk should set `draftsim.draft.CHECK_EVERY_SALE = False`.
