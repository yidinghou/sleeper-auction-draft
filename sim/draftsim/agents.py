"""Draft agents: the seat-driving strategies.

Stage 3 ships exactly one, `HeuristicAgent` — a need-aware value bidder with no
archetype personality yet (those arrive in Stage 4, as parameter presets of this
same class). It anchors its valuations on Sleeper's market price ($PROJ) with a
small deterministic per-player jitter so different seeds and seats diverge, and
it stays inside two hard rails:

  * the budget reserve (`max_bid` / `can_bid` from auction.py), and
  * a slot rail: never spend a roster spot on depth while every remaining slot
    is still spoken for by an unmet starter need.

Those two rails are what make the engine's "every roster legal" invariant hold:
the agent always keeps room and money to complete a legal starting lineup.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Dict, Optional, Protocol, runtime_checkable

from .auction import MIN_BID, can_bid, max_bid
from .roster import open_slots, positional_need
from .valuation import Player, market_value

if TYPE_CHECKING:  # pragma: no cover
    from .engine import DraftState

# What an agent returns to sit a nomination out. Any amount below MIN_BID means
# the same thing to the engine; this is the one spelling agents use, so a 0 in
# a bid method is never mistaken for a real $0 offer.
SIT_OUT = 0


@runtime_checkable
class DraftAgent(Protocol):
    """What the engine needs from a seat: a name, a nomination, and a bid."""

    name: str

    def nominate(self, state: "DraftState", my_id: str) -> Optional[Player]:
        """Pick a player from the pool to put up for auction (or None to pass)."""
        ...

    def bid(self, state: "DraftState", player: Player, my_id: str) -> int:
        """Sealed max bid for `player`; anything below MIN_BID means sit out.

        Return `SIT_OUT` to decline rather than a bare 0, so the intent is
        readable at the return site.
        """
        ...


class HeuristicAgent:
    """Need-aware value bidder anchored on market price.

    Parameters (Stage 4 will vary these to make archetypes):
      * `jitter_frac`      — symmetric +/- half-width of the multiplicative price
        jitter, as a fraction of market price. 0.15 means prices land uniformly
        in [0.85x, 1.15x]; 0.0 disables jitter entirely.
      * `depth_value_mult` — multiplier applied to value for non-need depth. It
        is a multiplier, not a discount: 0.4 means "pay 40% of value", not
        "take 40% off".
    """

    def __init__(
        self,
        name: str,
        seed: str = "0",
        jitter_frac: float = 0.15,
        depth_value_mult: float = 0.4,
    ) -> None:
        self.name = name
        self.seed = str(seed)
        self.jitter_frac = jitter_frac
        self.depth_value_mult = depth_value_mult
        self._val_cache: Dict[str, float] = {}

    # -- valuation ---------------------------------------------------------

    def _valuation(self, player: Player) -> float:
        """Market price nudged by a deterministic per-(seed,player) jitter.

        Uses a string-seeded Random so the jitter is stable across processes
        (unlike hash()) and independent of call order — the same player is worth
        the same to this agent no matter when it's evaluated."""
        cached = self._val_cache.get(player.id)
        if cached is not None:
            return cached
        base = market_value(player)
        jitter = random.Random(f"{self.seed}:{player.id}").uniform(
            1.0 - self.jitter_frac, 1.0 + self.jitter_frac
        )
        value = base * jitter
        self._val_cache[player.id] = value
        return value

    # -- policy ------------------------------------------------------------

    def nominate(self, state: "DraftState", my_id: str) -> Optional[Player]:
        pool = state.available
        if not pool:
            return None
        need = positional_need(state.managers[my_id].roster, state.config)
        needed = [p for p in pool if need.get(p.pos, 0) > 0]
        # Nominate the most valuable player at a position we still need; if no
        # need remains (or the pool has none), throw up the best body available.
        candidates = needed if needed else pool
        return max(candidates, key=self._valuation)

    def bid(self, state: "DraftState", player: Player, my_id: str) -> int:
        me = state.managers[my_id]
        slots = me.open_slots(state.config)
        if not can_bid(me.budget, slots):
            return SIT_OUT

        need = positional_need(me.roster, state.config)
        is_need = need.get(player.pos, 0) > 0
        # Slot rail: if every remaining slot is claimed by an unmet need, refuse
        # to burn one on depth. Keeps room to finish a legal lineup.
        if not is_need and slots <= sum(need.values()):
            return SIT_OUT

        value = self._valuation(player)
        if not is_need:
            value *= self.depth_value_mult
        ceiling = max_bid(me.budget, slots)
        return max(SIT_OUT, min(int(round(value)), ceiling))


def build_field(
    n_teams: int,
    seed: int = 0,
    prefix: str = "M",
    **agent_kwargs: object,
) -> Dict[str, HeuristicAgent]:
    """A homogeneous field of `n_teams` HeuristicAgents, one per seat, each with
    a distinct deterministic seed derived from the master `seed`."""
    return {
        f"{prefix}{i:02d}": HeuristicAgent(
            name=f"{prefix}{i:02d}", seed=f"{seed}:{i}", **agent_kwargs  # type: ignore[arg-type]
        )
        for i in range(n_teams)
    }
