"""A table, a board, and a draft to run over them.

The frozen board is read once for the whole session, because it is the same
292 players every time and parsing it per test is the slowest thing in the
suite.
"""

from pathlib import Path

import pytest

from draftsim.board import read_board
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
