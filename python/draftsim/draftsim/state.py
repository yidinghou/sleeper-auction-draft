"""The cache: everything the ledger implies, kept warm.

`DraftState` is a pure function of `(rules, players, teams, picks, proj)`. It is
updated at exactly one call site -- `Draft.record_pick` -- and can be thrown away
and rebuilt at any moment. Nothing here is a source of truth.

`rebuild()` is deliberately written *independently* of `apply()`: it groups the
whole ledger and sorts each bucket from scratch rather than replaying picks one
at a time. That is what gives `assert_matches()` teeth. If rebuild were a loop
over apply, the two would agree by construction, the property test would be a
tautology, and the whole scheme would be decoration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from .ledger import Pick
from .lineup import Lineup, best_lineup, group_by_position, insort
from .player import Player
from .rules import DraftRules, Position
from .team import Team


@dataclass
class TeamState:
    """One team's derived position. Every field is rebuildable from the picks."""

    team: Team
    lineup: Lineup
    roster: Tuple[Player, ...] = ()
    """In acquisition order -- the ledger's order, so the bench reads like a
    draft history."""
    by_position: Dict[Position, Tuple[Player, ...]] = field(default_factory=dict)
    """Each bucket sorted best-first, so lineup solves and marginal-value probes
    never re-sort."""
    spent: int = 0

    def open_slots(self, rules: DraftRules) -> int:
        return max(0, rules.roster_size - len(self.roster))

    def remaining(self, rules: DraftRules) -> int:
        return rules.budget - self.spent

    def max_bid(self, rules: DraftRules) -> int:
        """The most this team can legally bid: everything left, minus a reserve
        of `min_bid` for every slot this bid would *not* fill.

        Zero when the roster is full. A one-liner only because a single lot is
        open at a time -- see `Lot`.
        """
        open_slots = self.open_slots(rules)
        if open_slots == 0:
            return 0
        return self.remaining(rules) - (open_slots - 1) * rules.min_bid


@dataclass
class DraftState:
    rules: DraftRules
    players: Mapping[str, Player]
    proj: Mapping[str, float]
    teams: Dict[str, TeamState]
    owner: Dict[str, str] = field(default_factory=dict)
    """player_id -> team_id, so "is he gone?" is O(1) instead of a ledger scan
    per candidate. Derived like everything else here."""

    @classmethod
    def rebuild(
        cls,
        rules: DraftRules,
        players: Mapping[str, Player],
        teams: Sequence[Team],
        picks: Iterable[Pick],
        proj: Mapping[str, float],
    ) -> "DraftState":
        """Build the whole cache from the ledger, from scratch.

        The slow, obviously-correct version -- this is the specification that
        `apply()` has to match.
        """
        rosters: Dict[str, list] = {t.id: [] for t in teams}
        spent: Dict[str, int] = {t.id: 0 for t in teams}
        owner: Dict[str, str] = {}

        for pick in picks:
            rosters[pick.team_id].append(players[pick.player_id])
            spent[pick.team_id] += pick.price
            owner[pick.player_id] = pick.team_id

        team_states: Dict[str, TeamState] = {}
        for team in teams:
            roster = tuple(rosters[team.id])
            by_position = group_by_position(roster, proj)
            team_states[team.id] = TeamState(
                team=team,
                roster=roster,
                by_position=by_position,
                spent=spent[team.id],
                lineup=best_lineup(by_position, rules, proj),
            )

        return cls(
            rules=rules,
            players=players,
            proj=proj,
            teams=team_states,
            owner=owner,
        )

    def apply(self, pick: Pick) -> None:
        """Fold one pick into the cache, in place.

        The incremental path: append to the roster, slot the player into his one
        position bucket, add the price, re-solve that one team's lineup.
        Microseconds, roughly 200 times per draft.

        Called from `Draft.record_pick` and nowhere else.
        """
        player = self.players[pick.player_id]
        ts = self.teams[pick.team_id]

        ts.roster = ts.roster + (player,)
        ts.by_position[player.position] = insort(
            ts.by_position.get(player.position, ()), player, self.proj
        )
        ts.spent += pick.price
        ts.lineup = best_lineup(ts.by_position, self.rules, self.proj)
        self.owner[pick.player_id] = pick.team_id

    def assert_matches(self, picks: Sequence[Pick]) -> None:
        """Assert the cache equals a fresh rebuild from `picks`.

        Runs under `__debug__` after every recorded pick, so a divergence
        surfaces on the pick that caused it rather than at the end of a draft.
        """
        fresh = DraftState.rebuild(
            self.rules,
            self.players,
            [ts.team for ts in self.teams.values()],
            picks,
            self.proj,
        )
        assert self.owner == fresh.owner, "owner index diverged from the ledger"
        assert set(self.teams) == set(fresh.teams), "team set diverged"
        for team_id, ts in self.teams.items():
            other = fresh.teams[team_id]
            assert ts.roster == other.roster, f"{team_id}: roster diverged"
            assert ts.by_position == other.by_position, (
                f"{team_id}: by_position diverged"
            )
            assert ts.spent == other.spent, (
                f"{team_id}: spent {ts.spent} != rebuilt {other.spent}"
            )
            assert ts.lineup == other.lineup, (
                f"{team_id}: lineup diverged "
                f"({ts.lineup.points} != {other.lineup.points})"
            )
