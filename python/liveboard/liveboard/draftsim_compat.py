"""The old draftsim vocabulary, spoken by the rebuilt draftsim.

draftsim was rebuilt from a behavior spec with free rein on names, so almost
nothing it exports is called what it used to be called: `evaluation` became
`valuation`, `rules` became `league`, `state` and `team` folded into `draft`
and `seat`, a player's `id` became an `identity` tuple, and a `Lineup` that was
a flat column of players became a row of `Spot(slot, player)`.

This module is the only place in liveboard that knows any of that. Everything
else keeps importing `Player`, `DraftRules`, `TeamState`, `Market` and the rest
under the names it always used, from here instead of from `draftsim`.

**It is a seam, not a layer.** draftsim is expected to keep moving; while it
does, a reshape costs one edit here rather than an edit in every liveboard file
that touches a roster. Once draftsim's shape settles, the right move is to
delete this module and rewrite liveboard's call sites against the real API --
not to grow this into a permanent second vocabulary. Nothing here should ever
hold logic of its own: it translates shapes and delegates.

Two translations are worth knowing about before reading:

- **Old ids and new identities.** A player used to be keyed by
  `"name|position|team"`; he is now keyed by a case-folded `(name, position,
  team)` tuple. Both are functions of the same three fields, and the old key is
  the coarser of the two, so anything unique under old ids is unique under new
  identities and the two can be held side by side without ambiguity.
- **The same objects come back.** liveboard compares roster and lineup entries
  by object identity, so translating out of the model looks the old `Player`
  back up in a registry rather than building an equal copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from draftsim import board as _board
from draftsim import league as _league
from draftsim import valuation as _valuation
from draftsim.draft import Draft as _Draft
from draftsim.draft import SaleRefused
from draftsim.lineup import roster_can_field_a_lineup
from draftsim.seat import Seat as _Seat

# -- the league's vocabulary -------------------------------------------------

Position = str

BENCH = _league.BENCH
CONCRETE_POSITIONS: Tuple[Position, ...] = _league.POSITIONS
DEFAULT_ROSTER_SLOTS: Tuple[str, ...] = _league.STANDARD_TEMPLATE

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "data" / "projections-2026.csv"


@dataclass(frozen=True)
class DraftRules:
    """Budget, roster template and minimum bid, under the old field names.

    Constructed directly by `sleeper.rules_from_draft` and by tests, so the
    fields have to stay `teams` / `budget` / `min_bid` / `roster_slots` rather
    than delegate to the model's `seats` / `minimum_bid` / `roster_template`.
    Every question about the template is answered by the model.
    """

    teams: int = 12
    budget: int = 200
    min_bid: int = 1
    roster_slots: Tuple[str, ...] = DEFAULT_ROSTER_SLOTS

    def as_league(self) -> _league.LeagueRules:
        return _league.LeagueRules(
            seats=self.teams,
            budget=self.budget,
            minimum_bid=self.min_bid,
            roster_template=tuple(self.roster_slots),
        )

    @property
    def roster_size(self) -> int:
        return self.as_league().roster_size

    @property
    def slots(self) -> Tuple[str, ...]:
        return self.as_league().starting_slots

    def accepts(self, slot: str, position: Position) -> bool:
        return _league.slot_accepts(slot, position)

    def startable_slots(self, position: Position) -> int:
        return self.as_league().slots_a_position_can_fill(position)

    def starter_shares(self) -> Dict[Position, float]:
        return self.as_league().wanted_per_seat()

    def starter_counts(self) -> Dict[Position, int]:
        return self.as_league().wanted_per_seat_rounded()

    def owned_starters(self) -> Dict[Position, int]:
        return self.as_league().wanted_per_seat_floored()


# -- players -----------------------------------------------------------------


@dataclass(frozen=True)
class MarketData:
    """What Sleeper's board says about a player, under the old field names."""

    proj_dollar: Optional[int] = None
    rank: Optional[int] = None
    bye: Optional[int] = None
    sleeper_id: Optional[str] = None


@dataclass(frozen=True)
class WeeklyPoints:
    """Division-round projections. `None`, not zero, where nobody has said."""

    week1: Optional[float] = None
    week2: Optional[float] = None
    week3: Optional[float] = None


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    position: Position
    team: str
    market: MarketData = field(default_factory=MarketData)
    week: Optional[WeeklyPoints] = None


def make_id(name: str, position: Position, team: str) -> str:
    """liveboard's player key, unchanged from the library it came out of."""
    return f"{name}|{position}|{team}".lower().replace(" ", "-")


def by_sleeper_id(players: Iterable[Player]) -> Dict[str, Player]:
    """Index players by Sleeper's own id, for matching live picks."""
    return {p.market.sleeper_id: p for p in players if p.market.sleeper_id}


def _identity_of(player: Player) -> _board.PlayerIdentity:
    return _board.identify(player.name, player.position, player.team)


def _as_board_player(player: Player) -> _board.Player:
    """The model's view of a player: who he is and what he plays.

    Rank, price, bye and weekly reads are deliberately not carried across. The
    model values nobody on them, and a field translated but never read is a
    field that can quietly fall out of step.
    """
    return _board.Player(
        name=player.name, position=player.position, team=player.team
    )


def load_players(
    path: Optional[Path] = None, *, free_agents: bool = False
) -> List[Player]:
    """The draftable pool, read out of the projections sheet."""
    board, _ = _board.read_board(path or DEFAULT_CSV, free_agents)
    return [_from_board_player(player) for player in board.players]


def load_projections(
    path: Optional[Path] = None, *, free_agents: bool = False
) -> Dict[str, float]:
    """Season points by liveboard's player id.

    A player the sheet leaves blank scores zero, which the model's own forecast
    already answers on a miss.
    """
    board, forecast = _board.read_board(path or DEFAULT_CSV, free_agents)
    return {
        make_id(player.name, player.position, player.team): forecast[player.identity]
        for player in board.players
    }


def _from_board_player(player: _board.Player) -> Player:
    return Player(
        id=make_id(player.name, player.position, player.team),
        name=player.name,
        position=player.position,
        team=player.team,
        market=MarketData(
            proj_dollar=player.market.price,
            rank=player.market.rank,
            bye=player.bye_week,
            sleeper_id=player.provider_id,
        ),
        week=WeeklyPoints(*player.weekly_points),
    )


# -- lineups -----------------------------------------------------------------


@dataclass(frozen=True)
class Lineup:
    points: float
    slots: Tuple[Optional[Player], ...]
    """Aligned to `rules.slots`; None where the slot is unfilled."""


def empty_lineup(rules: DraftRules) -> Lineup:
    """The lineup of a seat that has drafted nobody."""
    return Lineup(points=0.0, slots=(None,) * len(rules.slots))


def is_legal(
    by_position: Mapping[Position, Tuple[Player, ...]], rules: DraftRules
) -> bool:
    """Could every starting slot be filled at once by an eligible player?"""
    roster = [player for bucket in by_position.values() for player in bucket]
    return roster_can_field_a_lineup(
        [_as_board_player(player) for player in roster], rules.as_league()
    )


def display_slots(
    lineup: Lineup, roster: Sequence[Player], rules: DraftRules
) -> List[Tuple[str, Optional[Player]]]:
    """Lay a roster out for display: one row per roster slot, template order.

    Starting rows take the solved lineup, so a player appears in the slot he
    would actually start in. The bench takes the leftovers in the order they
    were bought, gaps show as None, and overflow past the last bench slot is
    appended rather than dropped.

    Spelled out here rather than delegated because the model's own layout
    re-solves the lineup from a forecast, and every caller here is holding a
    solved lineup already.
    """
    started = {player.id for player in lineup.slots if player is not None}
    bench = [player for player in roster if player.id not in started]

    rows: List[Tuple[str, Optional[Player]]] = []
    starting = iter(lineup.slots)
    benched = iter(bench)
    for slot in rules.roster_slots:
        if slot == BENCH:
            rows.append((slot, next(benched, None)))
        else:
            rows.append((slot, next(starting, None)))
    rows.extend((BENCH, player) for player in benched)
    return rows


# -- seats and the draft -----------------------------------------------------


@dataclass(frozen=True)
class Team:
    id: str
    name: str


class InvalidPick(ValueError):
    """Raised when a sale would break the rules."""


@dataclass(frozen=True)
class Pick:
    team_id: str
    player_id: str
    price: int
    at: float


@dataclass
class TeamState:
    """One seat's derived position, under the old field names."""

    team: Team
    rules: DraftRules
    lineup: Lineup
    roster: Tuple[Player, ...] = ()
    by_position: Dict[Position, Tuple[Player, ...]] = field(default_factory=dict)
    spent: int = 0
    open_slots: int = 0
    remaining: int = 0
    max_bid: int = 0


@dataclass
class DraftState:
    """Everything the record of sales implies, under the old field names.

    `proj` is the one field a caller may replace to ask a what-if question, so
    valuation reads points off it rather than off the draft it was built from.
    """

    rules: DraftRules
    players: Mapping[str, Player]
    proj: Mapping[str, float]
    teams: Dict[str, TeamState]
    owner: Dict[str, str]
    draft: _Draft
    """The live model, for the questions that need a whole league rather than
    one roster -- where the bar stands, and what a point costs."""


class Draft:
    """The record of sales, written and read under the old names."""

    def __init__(
        self,
        rules: DraftRules,
        players: Mapping[str, Player],
        teams: Sequence[Team],
        proj: Mapping[str, float],
    ) -> None:
        self.rules = rules
        self.players = players
        self.teams = teams
        self.proj = proj
        self.picks: List[Pick] = []

        league = rules.as_league()
        self._known = {_identity_of(p): p for p in players.values()}
        self._seats = {
            team.id: _Seat(name=team.id, league=league) for team in teams
        }
        self._draft = _Draft(
            league=league,
            board=_board.Board(
                tuple(_as_board_player(p) for p in players.values())
            ),
            seats=[self._seats[team.id] for team in teams],
            forecast=_forecast_over(players, proj),
        )

    def record_pick(
        self, team_id: str, player_id: str, price: int, at: float
    ) -> Pick:
        """Append a sale, or refuse it and leave the record untouched."""
        try:
            self._draft.sell(
                self._seats[team_id],
                _as_board_player(self.players[player_id]),
                price,
            )
        except SaleRefused as refused:
            raise InvalidPick(str(refused)) from refused

        pick = Pick(team_id, player_id, price, at)
        self.picks.append(pick)
        return pick

    @property
    def state(self) -> DraftState:
        """What the record adds up to, as of now."""
        return DraftState(
            rules=self.rules,
            players=self.players,
            proj=self.proj,
            teams={
                team.id: self._team_state(team) for team in self.teams
            },
            owner={
                self._known[sale.player.identity].id: sale.seat.name
                for sale in self._draft.sales
            },
            draft=self._draft,
        )

    def _team_state(self, team: Team) -> TeamState:
        held = self._draft.holdings(self._seats[team.id])
        return TeamState(
            team=team,
            rules=self.rules,
            lineup=Lineup(
                points=held.lineup.points,
                slots=tuple(
                    self._known[spot.player.identity] if spot.player else None
                    for spot in held.lineup.spots
                ),
            ),
            roster=tuple(self._known[p.identity] for p in held.roster),
            by_position={
                position: tuple(self._known[p.identity] for p in bucket)
                for position, bucket in held.by_position.items()
            },
            spent=held.spent,
            open_slots=held.open_slots,
            remaining=held.money_left,
            max_bid=held.most_it_can_bid,
        )


def _forecast_over(
    players: Mapping[str, Player], proj: Mapping[str, float]
) -> _board.Projections:
    """Season points, re-keyed from liveboard's ids to the model's identities."""
    forecast = _board.Projections()
    for player_id, player in players.items():
        forecast[_identity_of(player)] = float(proj.get(player_id, 0.0))
    return forecast


# -- valuation ---------------------------------------------------------------


@dataclass(frozen=True)
class Market:
    """The bar and the exchange rate, as of one moment in the record."""

    replacement: Mapping[Position, float]
    dollars_per_point: float

    @classmethod
    def of(cls, state: DraftState) -> "Market":
        rate = _valuation.the_exchange_rate(state.draft)
        return cls(rate.bar, rate.dollars_per_point)


def marginal_points_of(
    state: DraftState,
    team_id: str,
    player: Player,
    replacement: Mapping[Position, float],
) -> float:
    """What this player would add to that seat's starting lineup over a $1 body.

    The candidate's own points come off `state.proj` and are put into the
    forecast here, because a seat can be asked about a player the board it was
    built from has never heard of.
    """
    roster = state.teams[team_id].roster
    forecast = _forecast_over(state.players, state.proj)
    forecast[_identity_of(player)] = float(state.proj.get(player.id, 0.0))

    return _valuation.what_he_adds(
        _as_board_player(player),
        [_as_board_player(held) for held in roster],
        forecast,
        state.rules.as_league(),
        replacement,
    )


def price_lists_of(
    state: DraftState, players: Sequence[Player]
) -> Dict[str, Dict[str, int]]:
    """What every seat should pay for each of these players, by team and id.

    Every seat in one call rather than one call per seat, because the exchange
    rate and the board the players are read off are the same for all of them.
    Asked seat by seat, twelve seats rescan the board twelve times to arrive at
    the same rate twelve times, which is a third of the work of pricing a pane.

    Handed the players to price rather than pricing the whole board: the pool
    draws a few hundred names out of a sheet of three thousand, and pricing the
    ones nobody will draw is the bulk of the rest.

    Anyone already sold comes back missing rather than at zero, which is the
    model's own answer and the one the caller wants — a sold player has no
    price because there is no bidding on him.
    """
    rate = _valuation.the_exchange_rate(state.draft)
    on_the_board = [_as_board_player(player) for player in players]

    priced_by_seat = {
        seat.name: _valuation.a_price_list(seat, state.draft, on_the_board, rate)
        for seat in state.draft.seats
    }

    return {
        team_id: {
            player.id: priced[_identity_of(player)]
            for player in players
            if _identity_of(player) in priced
        }
        for team_id, priced in priced_by_seat.items()
    }


def positional_need_of(state: DraftState, team_id: str) -> Dict[Position, int]:
    """How many more bodies of each position a seat needs to start a lineup."""
    roster = [
        _as_board_player(player)
        for player in state.teams[team_id].roster
        if player.position in CONCRETE_POSITIONS
    ]
    return _valuation.what_the_roster_still_needs(roster, state.rules.as_league())
