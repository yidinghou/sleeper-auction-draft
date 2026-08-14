"""Two ways of totting up the same draft, and the claim that they agree.

Everything a draft knows beyond its record of sales — each seat's roster, its
position buckets, what it has spent, its best lineup, who owns whom — is
counted, not stored, and it is counted two ways. As the draft runs, each sale
is folded into what was already there, which happens a couple of hundred times
and has to be quick. At any moment the whole thing can instead be added up from
the complete record, grouping and sorting from nothing, which is slow and
obviously right.

**They must produce identical answers**, seat by seat and field by field. That
is the claim this file exists for, and it is only worth anything while the two
are genuinely different computations — so there is a test here about that too.

The random drafts run over the frozen board, because the claim is about how a
real draft behaves. The two claims about *order* — purchase order, and
best-first with the tiebreak — are declared on the page instead, since they are
about which of two players comes first and nothing else.
"""

import inspect
import random

import pytest

from draftsim.board import Board, Player, Projections
from draftsim.draft import Draft, count_the_whole_record
from draftsim.league import LeagueRules
from draftsim.seat import Seat


def sell_at_random(draft, board, dice, how_many):
    """Play out a few legal sales: any seat with room, anybody still going."""
    for _ in range(how_many):
        buyers = [seat for seat in draft.seats if draft.holdings(seat).open_slots]
        still_going = [
            player for player in board.players if draft.owner_of(player) is None
        ]
        if not buyers or not still_going:
            return

        buyer = dice.choice(buyers)
        ceiling = min(draft.holdings(buyer).most_it_can_bid, 30)
        draft.sell(buyer, dice.choice(still_going), dice.randint(1, ceiling))


# --- The claim the whole design rests on ----------------------------------


def test_a_draft_folded_in_sale_by_sale_matches_one_counted_from_scratch(
    league, board, seats, forecast
):
    """Run a draft, then add the same record up again from nothing."""
    for seed in range(12):
        dice = random.Random(seed)
        draft = Draft(league, board, seats, forecast)

        sell_at_random(draft, board, dice, how_many=40)

        counted_afresh = count_the_whole_record(draft.seats, draft.sales, forecast)
        for seat in draft.seats:
            running = draft.holdings(seat)
            fresh = counted_afresh[seat.name]

            assert running.roster == fresh.roster
            assert running.by_position == fresh.by_position
            assert running.spent == fresh.spent
            assert running.lineup == fresh.lineup


def test_who_owns_whom_agrees_with_the_record_after_every_sale(
    league, board, seats, forecast
):
    dice = random.Random(2026)
    draft = Draft(league, board, seats, forecast)

    sell_at_random(draft, board, dice, how_many=30)

    for sale in draft.sales:
        assert draft.owner_of(sale.player) == sale.seat
    assert all(
        draft.owner_of(player) is None
        for player in board.players
        if player.identity not in {sale.player.identity for sale in draft.sales}
    )


def test_the_slow_count_is_not_written_as_a_loop_over_the_fast_one():
    """The check above is worth nothing if the two are the same computation.

    Written as a loop over the fold, they would agree by construction — they
    would agree just as happily about a wrong answer — and the whole
    arrangement would be decoration. So: adding up the whole record must not go
    anywhere near folding one sale in.
    """
    the_slow_way = inspect.getsource(count_the_whole_record)

    assert "_fold_in" not in the_slow_way, (
        "counting the whole record has started folding sales in one at a time, "
        "so the two counts can no longer disagree about anything"
    )
    assert "sell" not in the_slow_way, (
        "counting the whole record has started replaying sales through a draft, "
        "which is the same collapse by a longer road"
    )


def test_the_same_record_replayed_into_a_fresh_draft_gives_the_same_standings(
    league, board, seats, forecast
):
    """The count is disposable: throw it away, play the record again, and the
    same numbers come back."""
    dice = random.Random(7)
    played = Draft(league, board, seats, forecast)
    sell_at_random(played, board, dice, how_many=25)

    replayed = Draft(league, board, seats, forecast)
    for sale in played.sales:
        replayed.sell(sale.seat, sale.player, sale.price)

    for seat in seats:
        assert replayed.holdings(seat) == played.holdings(seat)


def test_undoing_a_sale_leaves_the_count_exactly_where_it_would_have_been(
    league, board, seats, forecast
):
    dice = random.Random(11)
    draft = Draft(league, board, seats, forecast)
    sell_at_random(draft, board, dice, how_many=20)
    before = {seat.name: draft.holdings(seat) for seat in seats}

    draft.sell(seats[3], next(
        player for player in board.players if draft.owner_of(player) is None
    ), 12)
    draft.undo_last_sale()

    assert {seat.name: draft.holdings(seat) for seat in seats} == before


# --- How a seat's players are kept ----------------------------------------


def a_two_seat_draft(*described):
    """A two-seat league and a board of exactly these players.

    Everyone is a Buffalo Bill; nothing in a count reads an NFL team, and the
    names keep the identities apart.
    """
    league = LeagueRules(seats=2)
    board = Board(
        tuple(
            Player(name=name, position=position, team="BUF")
            for name, position, _ in described
        )
    )
    forecast = Projections(
        {
            player.identity: points
            for player, (_, _, points) in zip(board.players, described)
        }
    )
    seats = (Seat("Anna", league), Seat("Ben", league))
    return Draft(league, board, seats, forecast), board, seats


def test_a_seats_roster_reads_in_the_order_he_bought_them():
    """So that a bench reads like a draft history rather than a ranking."""
    draft, board, seats = a_two_seat_draft(
        ("A Spare Back", "RB", 90),
        ("Saquon Barkley", "RB", 280),
        ("Travis Kelce", "TE", 200),
    )
    anna = seats[0]

    for player in board.players:
        draft.sell(anna, player, 5)

    assert [player.name for player in draft.holdings(anna).roster] == [
        "A Spare Back",
        "Saquon Barkley",
        "Travis Kelce",
    ]


def test_each_position_is_kept_best_first_so_no_solve_has_to_re_sort():
    draft, board, seats = a_two_seat_draft(
        ("A Spare Back", "RB", 90),
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 240),
        ("Travis Kelce", "TE", 200),
    )
    anna = seats[0]

    for player in board.players:
        draft.sell(anna, player, 5)

    assert [
        player.name for player in draft.holdings(anna).by_position["RB"]
    ] == ["Saquon Barkley", "Jahmyr Gibbs", "A Spare Back"]
    assert [
        player.name for player in draft.holdings(anna).by_position["TE"]
    ] == ["Travis Kelce"]


def test_two_backs_projected_alike_are_filed_in_the_same_order_either_way():
    """The tiebreak is load-bearing, not tidiness.

    Without it, two players projected at the same points would sit in whichever
    order they happened to be bought — and a draft counted as it ran and the
    same draft counted from scratch would describe identical rosters
    differently, which is exactly the disagreement this design exists to make
    impossible.

    Take the tiebreak away and this test does not fail on the assertion below:
    it fails inside the second ``sell``, on the check that counts the record
    both ways. The running count settles order by comparing one arrival against
    the bucket; the fresh count sorts the lot. The two only ever agree while
    "who comes first" is a total order, and that is what the identity supplies.
    """
    bought_one_way, board, seats = a_two_seat_draft(
        ("Aaron Jones", "RB", 200),
        ("Zack Moss", "RB", 200),
    )
    bought_the_other_way, other_board, other_seats = a_two_seat_draft(
        ("Aaron Jones", "RB", 200),
        ("Zack Moss", "RB", 200),
    )

    for player in board.players:
        bought_one_way.sell(seats[0], player, 5)
    for player in reversed(other_board.players):
        bought_the_other_way.sell(other_seats[0], player, 5)

    assert [
        player.name for player in bought_one_way.holdings(seats[0]).by_position["RB"]
    ] == [
        player.name
        for player in bought_the_other_way.holdings(other_seats[0]).by_position["RB"]
    ]


def test_a_seat_who_has_bought_nobody_holds_nothing_and_starts_nobody(draft, seats):
    empty = draft.holdings(seats[0])

    assert empty.roster == ()
    assert empty.by_position == {}
    assert empty.spent == 0
    assert empty.lineup.points == 0
