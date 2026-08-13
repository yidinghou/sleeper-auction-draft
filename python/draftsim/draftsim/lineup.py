"""The best starting lineup a roster can field, and how it reads.

Seats are scored on their starters, so this has to be correct rather than
plausible. It looks like one question and it is two, asked in order:

- **Who starts?** The most points a roster can put on the field at all. A fill
  that seats each player in the first slot that will take him gets this wrong:
  drop an elite tight end into the flex and the back who could only have played
  there is left on the bench.
- **Where does each starter line up?** Points cannot answer this — a back is
  worth the same in RB2 as in the flex — so the labels are settled by a rule
  instead: the best player takes the earliest slot that can hold him, and the
  overflow spills into the flexes. That rule is not only for the reader:
  valuing a player asks which slots a roster still has open, and reads the
  answer off these labels, so they have to come out the same every time.

Keeping them separate is what stops the second from looking like it is undoing
the first. The seating has no opinion about labels at all; it hands over a set
of starters in arbitrary slots, and the sorting settles where they read. And
because the sorting only ever trades players between slots that could legally
swap, it cannot change the answer to the first question.

The whole thing runs a couple of hundred times a draft, and again for every
player a seat is weighing a bid on, so both steps stay small: one sweep down a
sorted roster, then a handful of trades over ten slots.
"""

from __future__ import annotations

import itertools
from typing import Iterable, List, NamedTuple, Optional, Sequence, Tuple

from draftsim.board import Player, Projections
from draftsim.league import BENCH, LeagueRules, slot_accepts


class Spot(NamedTuple):
    """One row of a roster as it reads: a slot — starting or bench — and who is
    in it. ``None`` is a gap."""

    slot: str
    player: Optional[Player]


class Lineup(NamedTuple):
    """The starting slots in template order, and what they score together."""

    spots: Tuple[Spot, ...]
    points: float


def best_first(players: Iterable[Player], forecast: Projections) -> List[Player]:
    """Most points first, with the player's identity breaking ties.

    The tiebreak is load-bearing rather than tidy: two players projected at the
    same points would otherwise land in either order depending on which was
    bought first, and the same roster would read two different ways.
    """
    return sorted(
        players, key=lambda player: (-forecast[player.identity], player.identity)
    )


def best_lineup(
    roster: Iterable[Player], forecast: Projections, league: LeagueRules
) -> Lineup:
    """The highest-scoring legal starting lineup this roster can field.

    Take the players best-first and seat each one, moving whoever has to move
    to make room; then sort the seated players so the lineup reads down the
    template. Taking the best player first never costs points later: if a
    player can be seated at all, seating him and shuffling people around him
    afterwards is always at least as good as leaving him out — which is why one
    pass in this order is enough, and why no separate search is needed.

    The order players arrive in cannot change the answer, because the sort
    throws that order away before anything is seated. Stage upon stage rests on
    that: a roster maintained sale by sale and the same roster rebuilt from
    scratch see the players in different orders and must still agree.
    """
    slots = league.starting_slots
    who_starts = _fill_the_starting_slots(best_first(roster, forecast), slots)
    seated = _sorted_best_first(slots, who_starts, forecast)

    return Lineup(
        spots=tuple(Spot(slot, player) for slot, player in zip(slots, seated)),
        points=sum(forecast[player.identity] for player in seated if player),
    )


def roster_can_field_a_lineup(
    roster: Iterable[Player], league: LeagueRules
) -> bool:
    """Could every starting slot be filled at once by an eligible player?

    A different question from the best lineup, and it ignores points entirely:
    a roster of bodies projected at nothing is exactly as legal as one full of
    stars. So the players go in in whatever order they were bought — nothing
    here has any reason to rank them, and nothing needs to: because seating a
    player moves the others along to make room, a full lineup is found whenever
    one exists, whichever order the bodies arrive in. A first-fit fill is what
    would make that order matter.
    """
    slots = league.starting_slots
    seated = _fill_the_starting_slots(roster, slots)

    return all(player is not None for player in seated)


def lay_out_roster(
    roster: Sequence[Player], forecast: Projections, league: LeagueRules
) -> Tuple[Spot, ...]:
    """The whole roster as a person would read it down the page.

    One row per roster slot, in template order. A starting row shows whoever
    would actually start there, which is not necessarily the slot he was bought
    for. The bench takes the leftovers in the order they were bought, so it
    reads like a draft history, and empty rows show as gaps.

    A player past the last bench slot is appended rather than dropped: an ugly
    display beats one that has quietly lost somebody.
    """
    lineup = best_lineup(roster, forecast, league)
    starting_rows = iter(lineup.spots)
    who_starts = {spot.player.identity for spot in lineup.spots if spot.player}
    benched = iter(
        [player for player in roster if player.identity not in who_starts]
    )

    laid_out = [
        Spot(BENCH, next(benched, None)) if slot == BENCH else next(starting_rows)
        for slot in league.roster_template
    ]
    laid_out.extend(Spot(BENCH, player) for player in benched)

    return tuple(laid_out)


def _fill_the_starting_slots(
    players: Iterable[Player], slots: Sequence[str]
) -> List[Optional[Player]]:
    """Seat as many of these players as the slots will take, in the order given.

    This answers *who starts* and nothing else. Which of two interchangeable
    slots a player ends up in here is an accident of the order people had to be
    moved around in, and no meaning should be read into it — a lineup meant for
    a person to read is sorted afterwards.
    """
    seated: List[Optional[Player]] = [None] * len(slots)
    for player in players:
        _find_room(player, slots, seated, slots_already_tried=set())
    return seated


def _find_room(
    player: Player,
    slots: Sequence[str],
    seated: List[Optional[Player]],
    slots_already_tried: set,
) -> bool:
    """Seat this player, moving whoever has to move, and say whether it worked.

    Moving people along is the whole difference between this and a first-fit
    fill: an elite tight end who has taken the flex will shift into the tight
    end slot so that a back who can play nowhere else gets on the field. A slot
    already tried on this player's behalf is never tried again, which is what
    stops two players passing each other back and forth forever.
    """
    for index, slot in enumerate(slots):
        if index in slots_already_tried or not slot_accepts(slot, player.position):
            continue
        slots_already_tried.add(index)
        if seated[index] is None or _find_room(
            seated[index], slots, seated, slots_already_tried
        ):
            seated[index] = player
            return True

    return False


def _sorted_best_first(
    slots: Sequence[str],
    who_starts: Sequence[Optional[Player]],
    forecast: Projections,
) -> List[Optional[Player]]:
    """Trade players between slots until the lineup reads best-first.

    The seating has no opinion about labels — a back is worth the same in RB2
    as in the flex — so it leaves them in whatever order people had to be
    shuffled in, which tends to come out exactly backwards: the best back in
    the flex and the worst in RB1. This settles them: the best player takes the
    earliest slot that can hold him, the overflow spills into the flexes, and
    nobody starts below a slot left empty that he could have filled.

    **A trade can never cost a point.** Two players only ever change places
    when each is eligible for the other's slot, so the same names are on the
    field in the same numbers and only the labels have moved.

    **And this is not decoration.** A later stage asks which slots a roster
    still has open and reads the answer off these labels, so where a player
    lines up has to be a fact about the roster — the same every time — rather
    than one of the several renderings the seating could equally have produced.
    Skipping this where only the total is wanted would leave two callers
    describing the same roster differently.

    The pass repeats until nothing moves, and it always gets there: every trade
    raises what stands in the earlier of the two slots — a gap counting as
    lower than anybody — and changes nothing before it, so the lineup read as a
    list of points strictly rises in dictionary order, and there are only so
    many ways to arrange the same players.
    """
    def points(player):
        return forecast[player.identity]

    seated = list(who_starts)
    somebody_moved = True
    while somebody_moved:
        somebody_moved = False
        for earlier, later in itertools.combinations(range(len(slots)), 2):
            moving_up, sitting_there = seated[later], seated[earlier]
            if moving_up is None or not slot_accepts(
                slots[earlier], moving_up.position
            ):
                continue

            fills_a_gap = sitting_there is None
            beats_the_man_there = not fills_a_gap and (
                points(moving_up) > points(sitting_there)
                and slot_accepts(slots[later], sitting_there.position)
            )
            if fills_a_gap or beats_the_man_there:
                seated[earlier], seated[later] = moving_up, sitting_there
                somebody_moved = True

    return seated
