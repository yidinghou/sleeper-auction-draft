"""The highest-scoring legal starting lineup a roster can field.

Seats are scored on their starters, so a lineup that is merely plausible is
wrong. This file pins down three separate things: that the lineup really is the
best one available, that it reads best-first down the template without that
costing a point, and that the answer never depends on the order the players
were bought.

Everything here is declared on the page — a handful of players with their
points written next to them — because every claim is arithmetic a reader
should be able to do in their head. The last section instead throws random
rosters at the solver and checks them against a brute force written
independently below, which is the only honest way to claim "optimal".
"""

import itertools
import random

import pytest

from draftsim.board import Player, Projections
from draftsim.league import LeagueRules, slot_accepts
from draftsim.lineup import best_lineup, lay_out_roster, roster_can_field_a_lineup

TEAM = "NFL"


def a_roster(*described):
    """A roster and the forecast that goes with it, from name/position/points.

    Every player is given the same NFL team: nothing in a lineup reads a team,
    and the identities stay distinct because the names do.
    """
    roster = tuple(
        Player(name=name, position=position, team=TEAM)
        for name, position, _ in described
    )
    forecast = Projections(
        {player.identity: points for player, (_, _, points) in zip(roster, described)}
    )
    return roster, forecast


def reads_as(lineup):
    """The lineup as a reader sees it: slot by slot, who is in it."""
    return tuple(
        (spot.slot, spot.player.name if spot.player else None) for spot in lineup.spots
    )


# --- It is the best lineup, not merely a legal one -------------------------


def test_an_elite_tight_end_starts_at_tight_end_so_the_flex_can_take_a_back():
    """The claim that separates a real solve from a first-fit fill.

    The flex is written ahead of the tight end slot here so the trap is
    visible on the page: a fill that seats the best player in the first slot
    that will take him drops the tight end into the flex, leaves the back with
    nowhere to go, and scores 240 instead of 350.
    """
    league = LeagueRules(roster_template=("FLEX", "TE", "BN"))
    roster, forecast = a_roster(
        ("Travis Kelce", "TE", 200),
        ("Bijan Robinson", "RB", 150),
        ("A Spare Tight End", "TE", 40),
    )

    lineup = best_lineup(roster, forecast, league)

    assert lineup.points == 350
    assert reads_as(lineup) == (
        ("FLEX", "Bijan Robinson"),
        ("TE", "Travis Kelce"),
    )


def test_a_third_receiver_fills_the_flex():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 280),
        ("Puka Nacua", "WR", 260),
    )

    lineup = best_lineup(roster, forecast, league)

    assert lineup.points == 840
    assert reads_as(lineup)[:5] == (
        ("QB", None),
        ("RB", None),
        ("RB", None),
        ("WR", "Ja'Marr Chase"),
        ("WR", "Justin Jefferson"),
    )
    assert ("FLEX", "Puka Nacua") in reads_as(lineup)


def test_a_second_defense_adds_nothing():
    league = LeagueRules()
    roster, forecast = a_roster(("Ravens D/ST", "DEF", 130))
    two_deep, deeper_forecast = a_roster(
        ("Ravens D/ST", "DEF", 130),
        ("Bills D/ST", "DEF", 120),
    )

    assert best_lineup(roster, forecast, league).points == 130
    assert best_lineup(two_deep, deeper_forecast, league).points == 130


def test_a_kicker_never_appears_in_the_lineup():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Justin Tucker", "K", 150),
        ("Josh Allen", "QB", 360),
    )

    lineup = best_lineup(roster, forecast, league)

    assert lineup.points == 360
    assert "Justin Tucker" not in [name for _, name in reads_as(lineup)]


def test_no_way_of_filling_the_template_scores_more_than_the_lineup_it_fields():
    """The optimality proof, and the only one over rosters nobody chose.

    Every legal assignment of these players to slots is tried by the brute
    force at the foot of this file, which shares no reasoning with the solver.
    This is also where relabelling is proved free: the total is summed after
    the lineup has been sorted into reading order, so a trade that cost a point
    would show up here as a lineup scoring less than the brute force found.

    The rosters stop at a dozen bodies because trying every assignment of a
    full sixteen takes seconds apiece.
    """
    league = LeagueRules()

    for seed in range(40):
        roster, forecast = a_random_roster(seed, most_players=12)

        lineup = best_lineup(roster, forecast, league)

        assert lineup.points == pytest.approx(
            the_best_points_by_brute_force(roster, forecast, league)
        )


# --- A half-built roster still shows its shape ----------------------------


def test_a_roster_with_nobody_on_it_scores_nothing_with_every_slot_open():
    league = LeagueRules()

    lineup = best_lineup((), Projections(), league)

    assert lineup.points == 0
    assert reads_as(lineup) == tuple(
        (slot, None) for slot in league.starting_slots
    )


def test_the_slots_a_half_built_roster_cannot_fill_read_as_gaps():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Josh Allen", "QB", 360),
        ("Ravens D/ST", "DEF", 130),
    )

    lineup = best_lineup(roster, forecast, league)

    assert reads_as(lineup) == (
        ("QB", "Josh Allen"),
        ("RB", None),
        ("RB", None),
        ("WR", None),
        ("WR", None),
        ("TE", None),
        ("FLEX", None),
        ("REC_FLEX", None),
        ("SUPER_FLEX", None),
        ("DEF", "Ravens D/ST"),
    )


# --- The lineup reads best-first down the template ------------------------


def test_the_better_back_starts_at_running_back_and_the_spare_spills_to_the_flex():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("A Spare Back", "RB", 90),
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 240),
    )

    lineup = best_lineup(roster, forecast, league)

    assert reads_as(lineup)[1:3] == (
        ("RB", "Saquon Barkley"),
        ("RB", "Jahmyr Gibbs"),
    )
    assert ("FLEX", "A Spare Back") in reads_as(lineup)


def test_a_quarterback_takes_the_superflex_only_once_the_first_slot_is_his():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Jayden Daniels", "QB", 300),
        ("Josh Allen", "QB", 360),
    )

    lineup = best_lineup(roster, forecast, league)

    assert reads_as(lineup)[0] == ("QB", "Josh Allen")
    assert ("SUPER_FLEX", "Jayden Daniels") in reads_as(lineup)


def test_a_back_shuffled_along_to_make_room_still_reads_at_running_back():
    """Making room for the last back walks the others down the template.

    Seating the third back means moving the second into the flex and the
    receiver out to the receiver flex, which leaves the worst back standing at
    RB — right by points, backwards to read. The receiver stays where he is,
    because the back below him could not take a receiver flex.
    """
    league = LeagueRules(roster_template=("RB", "FLEX", "REC_FLEX"))
    roster, forecast = a_roster(
        ("Ja'Marr Chase", "WR", 100),
        ("Saquon Barkley", "RB", 90),
        ("A Spare Back", "RB", 80),
    )

    lineup = best_lineup(roster, forecast, league)

    assert lineup.points == 270
    assert reads_as(lineup) == (
        ("RB", "Saquon Barkley"),
        ("FLEX", "A Spare Back"),
        ("REC_FLEX", "Ja'Marr Chase"),
    )


def test_no_player_sits_in_a_later_slot_than_one_a_worse_player_holds():
    """What "reads best-first" means, stated so it can be checked.

    Where two slots could legally trade their players, the better player is in
    the earlier one; and no slot is left as a gap while somebody who could
    fill it starts further down.
    """
    league = LeagueRules()

    for seed in range(40):
        roster, forecast = a_random_roster(seed)

        lineup = best_lineup(roster, forecast, league)

        for earlier, later in itertools.combinations(lineup.spots, 2):
            if later.player is None:
                continue
            if earlier.player is None:
                assert not slot_accepts(earlier.slot, later.player.position)
            elif forecast[later.player.identity] > forecast[earlier.player.identity]:
                assert not (
                    slot_accepts(earlier.slot, later.player.position)
                    and slot_accepts(later.slot, earlier.player.position)
                )


# --- The order players were bought in changes nothing ---------------------


def test_the_lineup_does_not_depend_on_the_order_the_players_were_bought():
    """Stage after stage rests on this: two paths through the same roster see
    the players in different orders and must still agree, name for name."""
    league = LeagueRules()
    shuffler = random.Random(2026)

    for seed in range(20):
        roster, forecast = a_random_roster(seed)
        as_bought = best_lineup(roster, forecast, league)

        for _ in range(10):
            shuffled = list(roster)
            shuffler.shuffle(shuffled)

            assert reads_as(best_lineup(shuffled, forecast, league)) == reads_as(
                as_bought
            )


def test_two_players_projected_at_the_same_points_always_start_in_the_same_order():
    """The tiebreak is load-bearing, not tidiness: without it the same roster
    could read two ways depending on which player happened to arrive first."""
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Aaron Jones", "RB", 200),
        ("Zack Moss", "RB", 200),
    )

    bought_the_other_way = best_lineup(tuple(reversed(roster)), forecast, league)

    assert reads_as(best_lineup(roster, forecast, league)) == reads_as(
        bought_the_other_way
    )


# --- Whether a roster can field a legal lineup at all ---------------------


def test_a_roster_can_field_a_lineup_when_every_starting_slot_has_somebody():
    league = LeagueRules()
    roster, _ = a_roster(
        ("Josh Allen", "QB", 360),
        ("Jayden Daniels", "QB", 300),
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 240),
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 280),
        ("Puka Nacua", "WR", 260),
        ("Travis Kelce", "TE", 200),
        ("A Spare Back", "RB", 90),
        ("Ravens D/ST", "DEF", 130),
    )

    assert roster_can_field_a_lineup(roster, league)


def test_a_roster_of_nothing_but_receivers_cannot_field_a_lineup():
    league = LeagueRules()
    roster, _ = a_roster(
        *[(f"Receiver {number}", "WR", 100) for number in range(1, 11)]
    )

    assert not roster_can_field_a_lineup(roster, league)


def test_whether_a_roster_is_legal_has_nothing_to_do_with_points():
    """Two rosters of the same shape, one projected at nothing: both legal."""
    league = LeagueRules(roster_template=("QB", "FLEX", "BN"))
    scorers, _ = a_roster(("Josh Allen", "QB", 360), ("Bijan Robinson", "RB", 150))
    duds, _ = a_roster(("A Backup", "QB", 0), ("A Third Stringer", "RB", 0))

    assert roster_can_field_a_lineup(scorers, league)
    assert roster_can_field_a_lineup(duds, league)


# --- Laying a roster out for display --------------------------------------


def test_a_starter_is_shown_in_the_slot_he_would_actually_start_in():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Puka Nacua", "WR", 260),
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 280),
    )

    laid_out = lay_out_roster(roster, forecast, league)

    assert [(spot.slot, spot.player.name if spot.player else None)
            for spot in laid_out][3:7] == [
        ("WR", "Ja'Marr Chase"),
        ("WR", "Justin Jefferson"),
        ("TE", None),
        ("FLEX", "Puka Nacua"),
    ]


def test_the_bench_reads_in_the_order_the_players_were_bought():
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Ravens D/ST", "DEF", 130),
        ("Bills D/ST", "DEF", 120),
        ("Justin Tucker", "K", 150),
        ("49ers D/ST", "DEF", 110),
    )

    laid_out = lay_out_roster(roster, forecast, league)
    bench = [spot.player.name for spot in laid_out if spot.player and spot.slot == "BN"]

    assert bench == ["Bills D/ST", "Justin Tucker", "49ers D/ST"]


def test_a_roster_with_nobody_on_it_lays_out_as_sixteen_gaps():
    league = LeagueRules()

    laid_out = lay_out_roster((), Projections(), league)

    assert len(laid_out) == 16
    assert all(spot.player is None for spot in laid_out)
    assert [spot.slot for spot in laid_out] == list(league.roster_template)


def test_a_seventeenth_player_is_shown_anyway_rather_than_dropped():
    """An ugly display beats one that has quietly lost a player."""
    league = LeagueRules()
    roster, forecast = a_roster(
        ("Josh Allen", "QB", 360),
        ("Jayden Daniels", "QB", 300),
        ("Saquon Barkley", "RB", 280),
        ("Jahmyr Gibbs", "RB", 240),
        ("Ja'Marr Chase", "WR", 300),
        ("Justin Jefferson", "WR", 280),
        ("Puka Nacua", "WR", 260),
        ("Travis Kelce", "TE", 200),
        ("A Spare Back", "RB", 90),
        ("Ravens D/ST", "DEF", 130),
        *[(f"A Benchwarmer {number}", "K", 10) for number in range(1, 7)],
        ("One Body Too Many", "K", 5),
    )

    laid_out = lay_out_roster(roster, forecast, league)

    assert len(laid_out) == 17
    assert (laid_out[-1].slot, laid_out[-1].player.name) == ("BN", "One Body Too Many")


# --- The brute force, and the random rosters it checks --------------------


def a_random_roster(seed, most_players=16):
    """Random bodies, weighted towards the positions that compete for the
    flexes, since a contested flex is where a solve goes wrong."""
    dice = random.Random(seed)
    positions = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DEF"]
    return a_roster(
        *[
            (f"Player {number}", dice.choice(positions), dice.randrange(0, 300))
            for number in range(1, dice.randrange(2, most_players + 1))
        ]
    )


def the_best_points_by_brute_force(roster, forecast, league):
    """Try every legal way to fill the template and keep the best total.

    Written the slow, obvious way on purpose: it is the independent check on
    the solver, so it must share no reasoning with it.
    """

    def fill(slots, still_available):
        if not slots:
            return 0.0
        this_slot, rest = slots[0], slots[1:]
        best = fill(rest, still_available)
        for index, player in enumerate(still_available):
            if slot_accepts(this_slot, player.position):
                others = still_available[:index] + still_available[index + 1:]
                best = max(best, forecast[player.identity] + fill(rest, others))
        return best

    return fill(league.starting_slots, tuple(roster))
