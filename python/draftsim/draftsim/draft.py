"""The draft: owns the ledger, and is the only thing in the system that writes.

Everything a caller wants to know -- whose turn it is, who owns whom, what a team
can afford -- is answered from the picks or from the cache the picks produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from .ledger import InvalidPick, Pick
from .player import Player
from .rules import DraftRules
from .state import DraftState, TeamState
from .team import Team


@dataclass
class Draft:
    rules: DraftRules
    players: Mapping[str, Player]
    """Every draftable player, by id. Not a pool that shrinks -- who is gone is a
    question the ledger answers."""
    teams: Sequence[Team]
    proj: Mapping[str, float]
    picks: List[Pick] = field(default_factory=list)
    state: DraftState = field(init=False)

    def __post_init__(self) -> None:
        if not self.teams:
            raise ValueError("a draft needs at least one team")
        ids = [t.id for t in self.teams]
        if len(set(ids)) != len(ids):
            raise ValueError(f"team ids must be unique, got {ids}")
        self.state = DraftState.rebuild(
            self.rules, self.players, self.teams, self.picks, self.proj
        )

    # -- reading the ledger ------------------------------------------------

    def nominator(self) -> Team:
        """Whose turn it is to nominate -- read straight off the ledger, so it
        cannot drift out of step with what has actually been sold."""
        return self.teams[len(self.picks) % len(self.teams)]

    def owner_of(self, player_id: str) -> Optional[str]:
        """The team that bought this player, or None if he's still available."""
        return self.state.owner.get(player_id)

    def team_state(self, team_id: str) -> TeamState:
        return self.state.teams[team_id]

    # -- writing to it -----------------------------------------------------

    def check_pick(
        self, team_id: str, player_id: str, price: int
    ) -> Optional[str]:
        """Why this sale would be illegal, or None if it's fine.

        The same check a live bid runs and the hammer runs, so a UI can gray out
        what a team can't afford using exactly the rules the sale will face --
        rather than a second, slightly different copy of them.
        """
        ts = self.state.teams.get(team_id)
        if ts is None:
            return f"no such team: {team_id}"
        if player_id not in self.players:
            return f"no such player: {player_id}"

        owner = self.owner_of(player_id)
        if owner is not None:
            return f"{player_id} already drafted by {owner}"

        if ts.open_slots(self.rules) == 0:
            return f"{team_id} has a full roster"
        if price < self.rules.min_bid:
            return f"price {price} is below the ${self.rules.min_bid} minimum"

        max_bid = ts.max_bid(self.rules)
        if price > max_bid:
            return (
                f"price {price} exceeds {team_id}'s max bid of {max_bid} "
                f"(${ts.remaining(self.rules)} left, "
                f"{ts.open_slots(self.rules)} slots to fill)"
            )
        return None

    def record_pick(
        self, team_id: str, player_id: str, price: int, at: float
    ) -> Pick:
        """Append a sale to the ledger and fold it into the cache."""
        reason = self.check_pick(team_id, player_id, price)
        if reason:
            raise InvalidPick(reason)

        pick = Pick(team_id, player_id, price, at)
        self.picks.append(pick)  # 1. the truth
        self.state.apply(pick)  # 2. the cache -- called nowhere else

        if __debug__:
            self.state.assert_matches(self.picks)
        return pick

    def undo(self) -> Optional[Pick]:
        """Pop the last sale, or return None if nothing has been sold.

        Rebuilds the cache rather than un-applying the pick. The cache is
        disposable, so an inverse operation would be extra code that can rot for
        no benefit -- and a rebuild here is the same work as one draft's worth of
        applies, once.
        """
        if not self.picks:
            return None
        pick = self.picks.pop()
        self.state = DraftState.rebuild(
            self.rules, self.players, self.teams, self.picks, self.proj
        )
        return pick

    # -- convenience -------------------------------------------------------

    def available(self) -> List[Player]:
        """Every player nobody has bought yet."""
        return [p for pid, p in self.players.items() if pid not in self.state.owner]

    def is_complete(self) -> bool:
        return all(
            ts.open_slots(self.rules) == 0 for ts in self.state.teams.values()
        )

    def rosters(self) -> Dict[str, Sequence[Player]]:
        return {tid: ts.roster for tid, ts in self.state.teams.items()}
