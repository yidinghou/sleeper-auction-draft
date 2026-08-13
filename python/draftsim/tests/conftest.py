"""A table, a board, and a draft to run over them.

The frozen board is read once for the whole session, because it is the same
292 players every time and parsing it per test is the slowest thing in the
suite. Alongside it is ``a_roster``, for the tests whose claim is arithmetic
and whose players are better written out on the page than looked up.
"""

from pathlib import Path

import pytest

from draftsim.board import Player, Projections, read_board
from draftsim.draft import Draft
from draftsim.league import LeagueRules
from draftsim.seat import Seat

MANAGERS = (
    "Anna",
    "Ben",
    "Chloe",
    "Dev",
    "Elena",
    "Femi",
    "Gus",
    "Hana",
    "Ivan",
    "Jo",
    "Kip",
    "Lena",
)


@pytest.fixture(scope="session")
def frozen_sheet():
    return read_board(Path(__file__).parent / "data" / "board-2026.csv")


@pytest.fixture
def board(frozen_sheet):
    return frozen_sheet[0]


@pytest.fixture
def forecast(frozen_sheet):
    return frozen_sheet[1]


@pytest.fixture
def league():
    return LeagueRules()


@pytest.fixture
def seats(league):
    return tuple(Seat(manager, league) for manager in MANAGERS)


@pytest.fixture
def draft(league, board, seats, forecast):
    return Draft(league, board, seats, forecast)


@pytest.fixture
def on_the_board(board):
    """Look a player up by name, the way a person at the table would."""

    def find(name):
        for player in board.players:
            if player.name == name:
                return player
        raise LookupError(f"nobody called {name} is on this board")

    return find


@pytest.fixture
def a_roster():
    """Build a roster and the forecast that goes with it, from the page.

    Each player is given as a name, a position and his projected points, so a
    reader can do the lineup arithmetic without leaving the test.

    Everybody is given the same NFL team. Nothing in a lineup, a count or a
    valuation reads a team — it is there because identity is name, position and
    team — and the names keep the identities apart on their own.
    """

    def build(*described):
        roster = tuple(
            Player(name=name, position=position, team="NFL")
            for name, position, _ in described
        )
        forecast = Projections(
            {
                player.identity: points
                for player, (_, _, points) in zip(roster, described)
            }
        )
        return roster, forecast

    return build
