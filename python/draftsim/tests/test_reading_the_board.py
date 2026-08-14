"""Turning a projections spreadsheet into a pool of draftable players.

The sheet is messier than the model wants to be: it carries retired and
unsigned bodies who cannot score for anyone, cells that read "-" where nobody
has an opinion, and a column layout that has changed between exports. This
file pins down what survives that trip — who counts as a player, what a blank
cell means, and what makes two rows the same person.

The parsing rules are all arithmetic-free bookkeeping, so most of the page
declares its own tiny spreadsheet inline where the reader can see every cell.
The last two sections read real files, because "the export still has the shape
the model expects" is a claim only reality can make.
"""

import collections
import dataclasses
from pathlib import Path

import pytest

from draftsim.board import EARLY_WEEKS, Projections, identify, read_board

FROZEN_BOARD = Path(__file__).parent / "data" / "board-2026.csv"
#: tests/ -> draftsim/ -> python/ -> the repository root, where the live export
#: produced by `npm run export:projections` lands.
REAL_PROJECTIONS = (
    Path(__file__).resolve().parents[3] / "data" / "projections-2026.csv"
)

COLUMNS = (
    "player_id,player,position,team,sleeper_rank,bye_week,"
    "sleeper_proj_dollar,season_pts_half_ppr,"
    "week1_pts_half_ppr,week2_pts_half_ppr,week3_pts_half_ppr"
)


def sheet(*rows):
    """A spreadsheet written out on the page, one row per argument."""
    return [COLUMNS, *rows]


def row(
    name,
    position,
    team,
    player_id="",
    rank="1",
    bye="7",
    price="10",
    season_points="100.0",
    weeks=("",) * len(EARLY_WEEKS),
):
    """One spreadsheet row, written a column at a time.

    Use this where a single cell is the whole claim, so the reader can see
    which column is the rank without counting commas up to the header. Where
    the shape of the row is itself the point — a sheet exported before the
    weekly columns existed — write the commas out instead.

    The provider's id is left blank unless a test asks for one, so that seeing
    one on the page always means something reads it.
    """
    return ",".join(
        [player_id, name, position, team, rank, bye, price, season_points, *weeks]
    )


# --- Who counts as a player -----------------------------------------------


def test_a_row_with_no_name_is_not_a_player():
    board, _ = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF"),
            row(name="", position="QB", team="BUF"),
        )
    )

    assert [player.name for player in board.players] == ["Josh Allen"]


def test_a_row_with_no_position_is_not_a_player():
    board, _ = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF"),
            row(name="Somebody Untagged", position="", team="BUF"),
        )
    )

    assert [player.name for player in board.players] == ["Josh Allen"]


def test_a_player_with_no_nfl_team_cannot_be_drafted():
    """Two thirds of the sheet is retired and unsigned bodies.

    Left in, they would be the bulk of the pool, and they would crowd out real
    players in any tie broken down at the price floor.
    """
    board, _ = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF"),
            row(name="Peyton Manning", position="QB", team=""),
        )
    )

    assert [player.name for player in board.players] == ["Josh Allen"]


def test_the_unsigned_can_be_asked_for_when_you_want_the_whole_sheet():
    board, _ = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF"),
            row(name="Peyton Manning", position="QB", team=""),
        ),
        include_free_agents=True,
    )

    assert [player.name for player in board.players] == ["Josh Allen", "Peyton Manning"]


def test_two_signed_players_of_the_same_name_are_a_broken_export():
    """The sheet could not say which of them a seat had bought."""
    with pytest.raises(ValueError):
        read_board(
            sheet(
                row(name="Josh Allen", position="QB", team="BUF"),
                row(name="josh   allen", position="QB", team="buf"),
            )
        )


def test_two_unsigned_men_of_the_same_name_do_not_spoil_the_sheet():
    """Nobody can draft either of them, so nothing needs to tell them apart."""
    board, _ = read_board(
        sheet(
            row(name="Rodney Smith", position="RB", team=""),
            row(name="Rodney Smith", position="RB", team=""),
        ),
        include_free_agents=True,
    )

    assert len(board.players) == 2


# --- What a blank cell means ----------------------------------------------


def test_a_player_with_no_season_forecast_is_worth_zero_points():
    """The right answer, not an error: he does nothing for the roster."""
    board, projections = read_board(
        sheet(row(name="Deep Sleeper", position="WR", team="NYJ", season_points=""))
    )
    sleeper = board.players[0]

    assert projections[sleeper.identity] == 0


def test_a_player_nobody_forecast_at_all_scores_zero():
    _, projections = read_board(
        sheet(row(name="Josh Allen", position="QB", team="BUF"))
    )
    somebody_else = identify("Bijan Robinson", "RB", "ATL")

    assert projections[somebody_else] == 0


def test_a_week_nobody_has_read_yet_is_unknown_rather_than_zero():
    """A body with no early-week read is not a body projected to score nothing."""
    board, _ = read_board(
        sheet(
            row(name="Rookie Back", position="RB", team="LAC", weeks=("9.5", "", ""))
        )
    )
    rookie = board.players[0]

    assert rookie.weekly_points == (9.5, None, None)


def test_a_sheet_exported_before_the_weekly_columns_existed_still_loads():
    board, projections = read_board(
        [
            "player_id,player,position,team,sleeper_rank,bye_week,"
            "sleeper_proj_dollar,season_pts_half_ppr",
            "1,Josh Allen,QB,BUF,1,7,58,361.5",
        ]
    )
    allen = board.players[0]

    assert allen.weekly_points == (None, None, None)
    assert projections[allen.identity] == pytest.approx(361.5)


def test_a_figure_the_sheet_does_not_have_may_read_blank_or_a_dash():
    """The dash is the export's way of writing "no figure", wherever it falls."""
    board, projections = read_board(
        sheet(
            row(name="Blank Cells", position="TE", team="SEA", rank="", price=""),
            row(
                name="Dashed Cells",
                position="TE",
                team="GB",
                rank="-",
                price="-",
                bye="-",
                season_points="-",
            ),
        )
    )
    blank, dashed = board.players

    assert blank.market.rank is None and blank.market.price is None
    assert dashed.market.rank is None and dashed.market.price is None
    assert dashed.bye_week is None
    assert projections[dashed.identity] == 0


def test_what_the_market_thinks_is_kept_apart_from_what_the_player_is():
    """A valuation leaning on the market's price has to say so out loud."""
    board, _ = read_board(
        sheet(row(name="Josh Allen", position="QB", team="BUF", rank="1", price="58"))
    )
    allen = board.players[0]

    assert allen.market.rank == 1
    assert allen.market.price == 58
    assert not hasattr(allen, "rank")
    assert not hasattr(allen, "price")


# --- What makes two rows the same person ----------------------------------


def test_a_player_is_the_same_player_however_his_name_is_typed():
    assert identify("  Josh   Allen ", "qb", " buf ") == identify(
        "josh allen", "QB", "BUF"
    )


def test_two_players_who_share_a_name_are_told_apart_by_position_and_team():
    board, _ = read_board(
        sheet(
            row(name="Michael Thomas", position="WR", team="NO"),
            row(name="Michael Thomas", position="TE", team="HOU"),
        )
    )
    receiver, tight_end = board.players

    assert receiver.identity != tight_end.identity


def test_a_sheet_exported_before_player_ids_existed_still_joins_up():
    board, projections = read_board(
        [
            "player,position,team,sleeper_rank,bye_week,"
            "sleeper_proj_dollar,season_pts_half_ppr",
            "Josh Allen,QB,BUF,1,7,58,361.5",
        ]
    )
    allen = board.players[0]

    assert allen.provider_id is None
    assert projections[allen.identity] == pytest.approx(361.5)


def test_a_live_pick_off_the_real_draft_board_is_matched_by_the_providers_id():
    board, _ = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF", player_id="4984")
        )
    )

    assert board.by_provider_id()["4984"].name == "Josh Allen"


def test_a_player_the_provider_never_numbered_is_absent_from_that_index():
    board, _ = read_board(
        [
            "player,position,team,sleeper_rank,bye_week,"
            "sleeper_proj_dollar,season_pts_half_ppr",
            "Josh Allen,QB,BUF,1,7,58,361.5",
        ]
    )

    assert board.by_provider_id() == {}


def test_a_player_knows_nothing_about_who_bought_him():
    """Ownership is a fact the record of sales already holds; a second copy of
    it is only a chance for the two to disagree."""
    board, _ = read_board(sheet(row(name="Josh Allen", position="QB", team="BUF")))
    allen = board.players[0]

    assert not any(
        hasattr(allen, forbidden)
        for forbidden in ("owner", "owned", "is_drafted", "price_paid")
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        allen.name = "Somebody Else"


def test_the_same_board_can_be_run_against_a_gloomier_forecast():
    """Re-running a draft against different numbers means supplying different
    numbers, not rebuilding every player."""
    board, sleeper_says = read_board(
        sheet(
            row(name="Josh Allen", position="QB", team="BUF", season_points="361.5")
        )
    )
    allen = board.players[0]

    gloomier = Projections({allen.identity: 250.0})

    assert not hasattr(allen, "points")
    assert sleeper_says[allen.identity] == pytest.approx(361.5)
    assert gloomier[allen.identity] == 250.0


# --- The frozen board the rest of the suite drafts from -------------------


def test_the_frozen_board_is_the_top_sixty_at_each_position_plus_every_defense():
    board, _ = read_board(FROZEN_BOARD)

    assert collections.Counter(player.position for player in board.players) == {
        "QB": 60,
        "RB": 60,
        "WR": 60,
        "TE": 60,
        "DEF": 32,
        "K": 20,
    }


def test_every_player_on_the_frozen_board_has_a_forecast():
    board, projections = read_board(FROZEN_BOARD)

    assert all(projections[player.identity] > 0 for player in board.players)


# --- The real export ------------------------------------------------------


def test_the_real_spreadsheet_still_reads_the_way_the_model_expects():
    """A schema check on the export, run as a test: if the columns move, this
    is where it shows."""
    board, projections = read_board(REAL_PROJECTIONS)

    assert len(board.players) > 500
    best = max(board.players, key=lambda player: projections[player.identity])
    assert projections[best.identity] > 200
    assert best.team and best.position in ("QB", "RB", "WR", "TE")
    assert board.by_provider_id()


def test_the_retired_and_the_unsigned_are_two_thirds_of_the_real_spreadsheet():
    """And nothing draftable is lost by dropping them: the most the market will
    pay for any of them is the minimum bid."""
    draftable, _ = read_board(REAL_PROJECTIONS)
    everybody, _ = read_board(REAL_PROJECTIONS, include_free_agents=True)

    unsigned = [player for player in everybody.players if not player.team]

    assert len(unsigned) > 2 * len(draftable.players)
    assert max((player.market.price or 0) for player in unsigned) == 1
