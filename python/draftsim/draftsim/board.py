"""The pool of players a draft is run over, read out of a projections sheet.

Two separations are deliberate here, and both are the sort of thing a later
reader will be tempted to collapse:

- **A player knows nothing about any draft.** No owner, no price paid, no
  drafted flag. Who owns whom is a fact the record of sales already holds, and
  a second copy of a fact is only a chance for the two to disagree.
- **Season points live beside the players, not inside them.** They are the
  number every valuation swings on, and re-running a draft against a different
  forecast should mean supplying different numbers, not rebuilding every
  player. So reading a sheet hands back two things: a board, and a mapping
  from player identity to season points. The weekly reads stay on the player,
  because nothing values or starts on them.

There is a third, softer separation: what the *market* thinks — the provider's
rank and its projected auction price — sits behind ``player.market`` rather
than alongside his name and team, so that any valuation leaning on the market
has to say so out loud.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

#: What the export writes for a figure it doesn't have, wherever that figure
#: falls — a rank, a price, a bye week, a projection. Both mean unknown, and
#: neither means zero.
UNKNOWN_CELLS = ("", "-")

#: The weeks the sheet carries an early read on. Sheets exported before these
#: columns existed simply read as unknown for every week.
EARLY_WEEKS = (1, 2, 3)


class PlayerIdentity(NamedTuple):
    """Who a player is: his name, his position, and the team he plays for.

    Identity is deliberately not the provider's id, so that a sheet exported
    before the ``player_id`` column existed still loads and still joins to a
    forecast. It is case- and spacing-insensitive, because the same person
    arrives spelled differently from a spreadsheet and from a live draft board.
    """

    name: str
    position: str
    team: str


def identify(name: str, position: str, team: str) -> PlayerIdentity:
    """Reduce a name, a position and a team to the person they refer to."""
    return PlayerIdentity(*(" ".join(part.split()).casefold()
                            for part in (name, position, team)))


class MarketOpinion(NamedTuple):
    """What the provider's draft board thinks a player is worth.

    A different kind of fact from what the player is: a rank and a dollar price
    are other people's guesses, and either may be unknown.
    """

    rank: Optional[int]
    price: Optional[int]


@dataclass(frozen=True)
class Player:
    """One football player, as the spreadsheet describes him.

    ``team`` is an NFL team abbreviation and never a draft seat. A player with
    no NFL team is a free agent — retired or unsigned — and cannot score for
    anyone this season.
    """

    name: str
    position: str
    team: str
    provider_id: Optional[str] = None
    bye_week: Optional[int] = None
    market: MarketOpinion = MarketOpinion(None, None)
    #: What the sheet reads for each of ``EARLY_WEEKS`` in that order, so
    #: ``weekly_points[0]`` is week one. ``None`` where nobody has forecast
    #: that week yet, which is not the same as a forecast of zero.
    #:
    #: These stay on the player rather than moving beside the board with the
    #: season points, because nothing in this model bids, ranks or starts on
    #: them: they are descriptive, like a bye week. Only the number a
    #: valuation actually swings on has to be swappable, and pulling the weeks
    #: out would mean keeping a second forecast in step with the first for no
    #: behavior that reads it. If a later model ever prices off week one, they
    #: move out then.
    weekly_points: Tuple[Optional[float], ...] = (None,) * len(EARLY_WEEKS)

    @property
    def identity(self) -> PlayerIdentity:
        return identify(self.name, self.position, self.team)


class Projections(dict):
    """Season points, keyed by player identity.

    A player nobody has forecast — because his cell was blank, or because this
    forecast simply does not mention him — scores zero rather than raising. A
    player with no forecast on a roster is worth nothing, which is the right
    answer and not an error.

    **Look points up with** ``forecast[identity]``. The zero lives in
    ``__missing__``, which fires only on subscription: ``forecast.get(who)``
    still returns ``None``, and ``who in forecast`` is still False. That is
    deliberate rather than an oversight — both of those ask whether *anyone
    has forecast him*, which is a different question from what he scores, and
    the honest answer to it is no. What must never happen is ``.get`` being
    fed into arithmetic, because that puts a ``None`` into a points sum.

    Two simpler-looking ways of getting the same zero were rejected:

    - ``defaultdict(float)`` writes the zero back on every miss, so merely
      *asking* what an unforecast player scores grows the forecast, and two
      probes of the same board stop comparing equal.
    - a plain ``dict`` with ``.get(identity, 0.0)`` at each call site hands
      every caller a default to remember, and the one that forgets gets
      ``None`` into a points sum — the failure this class exists to prevent.
    """

    def __missing__(self, identity: PlayerIdentity) -> float:
        return 0.0


@dataclass(frozen=True)
class Board:
    """Everybody who can be drafted, in the order the sheet listed them."""

    players: Tuple[Player, ...]

    def __post_init__(self) -> None:
        """No two draftable players may be the same person.

        Identity exists so that a sale can name exactly one player. Two signed
        players sharing a name and position would leave the record of sales
        ambiguous about who was bought, which is the one thing it may never
        be — so that sheet is a broken export and is refused outright.

        Only players with an NFL team are held to it. The real sheet carries
        several pairs of unsigned men who share a name and a position — two
        running backs called Rodney Smith, neither on a roster — and with no
        team to tell them apart they are one identity. Nothing can ever ask
        which is which, because neither can be nominated, bought, owned or
        started. Refusing them would make the whole-sheet reading unusable
        against the actual export, and silently dropping the second would
        quietly change how many bodies the caller asked for.
        """
        drafted_from = [player for player in self.players if player.team]
        if len({player.identity for player in drafted_from}) != len(drafted_from):
            raise ValueError("the same player appears on the board more than once")

    def by_provider_id(self) -> Dict[str, Player]:
        """An index for matching live picks off a real draft board.

        Players from a sheet without that column are simply absent, and the
        caller falls back to matching on name, position and team.
        """
        return {
            player.provider_id: player
            for player in self.players
            if player.provider_id
        }


def read_board(
    sheet: Union[str, Path, Iterable[str]],
    include_free_agents: bool = False,
) -> Tuple[Board, Projections]:
    """Read a projections spreadsheet into a board and a forecast.

    Accepts a path, or any iterable of lines, so that a test can declare its
    own sheet on the page instead of writing a file first.

    Rows without a name or without a position are not players and are skipped.
    So, by default, are free agents: they are two thirds of the sheet, and
    nothing draftable is lost, because the highest price the market puts on any
    of them is a single dollar. Keeping them makes them the bulk of the pool,
    where they crowd out real players in any tie broken down at the price
    floor. Pass ``include_free_agents`` to read the whole sheet anyway.
    """
    if isinstance(sheet, (str, Path)):
        with open(sheet, newline="", encoding="utf-8") as opened:
            return read_board(list(opened), include_free_agents)

    players: List[Player] = []
    projections = Projections()
    for row in csv.DictReader(sheet):
        name = (row.get("player") or "").strip()
        position = (row.get("position") or "").strip()
        team = (row.get("team") or "").strip()
        if not name or not position:
            continue
        if not team and not include_free_agents:
            continue

        player = Player(
            name=name,
            position=position,
            team=team,
            provider_id=(row.get("player_id") or "").strip() or None,
            bye_week=_whole_number(row.get("bye_week")),
            market=MarketOpinion(
                rank=_whole_number(row.get("sleeper_rank")),
                price=_whole_number(row.get("sleeper_proj_dollar")),
            ),
            weekly_points=tuple(
                _number(row.get(f"week{week}_pts_half_ppr")) for week in EARLY_WEEKS
            ),
        )
        players.append(player)

        season_points = _number(row.get("season_pts_half_ppr"))
        if season_points is not None:
            projections[player.identity] = season_points

    return Board(tuple(players)), projections


def _number(cell: Optional[str]) -> Optional[float]:
    """What a cell reads, or ``None`` where nobody has said."""
    text = (cell or "").strip()
    if text in UNKNOWN_CELLS:
        return None
    return float(text)


def _whole_number(cell: Optional[str]) -> Optional[int]:
    """The same, for the cells that count things: ranks, dollars, bye weeks."""
    reading = _number(cell)
    return None if reading is None else int(reading)
