"""Draft agents: the seat-driving strategies.

Stage 3 ships exactly one, `HeuristicAgent` — a need-aware value bidder with no
archetype personality yet (those arrive in Stage 4, as parameter presets of this
same class). It anchors its valuations on Sleeper's market price ($PROJ) with a
small deterministic per-player jitter so different seeds and seats diverge, bids
that value outright (see `bid` on why it does not shade), and stays inside two
hard rails:

  * the budget reserve (`max_bid` / `can_bid` from auction.py), and
  * a slot rail: never spend a roster spot on depth while every remaining slot
    is still spoken for by an unmet starter need.

What the rails actually guarantee: the agent always keeps enough *room* and
*money* to complete a legal starting lineup. They do NOT guarantee it wins the
positions it needs — outbid on every TE, a seat still ends up without one. Roster
legality is therefore checked after the fact by `engine.invariant_violations`,
not proven by these rails. It holds in practice because the pool is thousands of
players deep, so a $1 body at any position is always available.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Dict, Optional, Protocol, Tuple, runtime_checkable

from .auction import MIN_BID, can_bid, max_bid
from .roster import (
    marginal_points,
    marginal_thresholds,
    open_slots,
    positional_need,
    startable_slots,
)
from .valuation import Player, market_value

if TYPE_CHECKING:  # pragma: no cover
    from .engine import DraftState

# What an agent returns to sit a nomination out. Any amount below MIN_BID means
# the same thing to the engine; this is the one spelling agents use, so a 0 in
# a bid method is never mistaken for a real $0 offer.
SIT_OUT = 0

# Sort key for a player Sleeper never ranked. Larger than any real rank, so an
# unranked player falls behind every ranked one instead of ahead of them.
_WORST_RANK = 10**9


@runtime_checkable
class DraftAgent(Protocol):
    """What the engine needs from a seat: a name, a nomination, and a bid."""

    name: str

    def nominate(self, state: "DraftState", my_id: str) -> Optional[Player]:
        """Pick a player from the pool to put up for auction.

        Return None to pass: the nomination moves to the next seat, and this
        seat keeps its open slots. The draft ends only once every seat has
        passed in a row (or every roster is full).
        """
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
      * `max_pick_share`   — cap on how much of a seat's legal ceiling one buy
        may consume. 0.5 means no single player takes more than half of what
        this seat could commit; 1.0 disables the cap. This is the knob that
        keeps the draft from being decided by the RNG: uncapped, starter points
        spread ~764 across seeds; capped, ~251.
      * `vorp_weight`      — how much to blend marginal VORP into the market
        anchor, 0.0 (market only) to 1.0 (VORP only). Defaults to 0.5: prices
        stay recognisable against Sleeper's board while roster context decides
        what a seat actually wants.
    """

    def __init__(
        self,
        name: str,
        seed: str = "0",
        jitter_frac: float = 0.15,
        depth_value_mult: float = 0.4,
        max_pick_share: float = 0.5,
        vorp_weight: float = 0.5,
    ) -> None:
        self.name = name
        self.seed = str(seed)
        self.jitter_frac = jitter_frac
        self.depth_value_mult = depth_value_mult
        self.max_pick_share = max_pick_share
        self.vorp_weight = vorp_weight
        # Thresholds are a function of this seat's roster, which only changes
        # when it wins a player -- but bid() is called for every seat on every
        # nomination. Cached against the roster length, which is exactly the
        # thing that changes (a roster is only ever appended to).
        self._threshold_cache: Optional[Tuple[int, Dict[str, float]]] = None
        # Jitter is per (seat, player) and never moves; only the anchor it
        # multiplies is roster-dependent. Cached because seeding a Random from a
        # string is expensive and nomination scores the whole pool every round.
        self._jitter_cache: Dict[str, float] = {}

    # -- valuation ---------------------------------------------------------

    def _thresholds(self, state: "DraftState", my_id: str) -> Dict[str, float]:
        """This seat's per-position marginal thresholds, cached per roster."""
        roster = state.managers[my_id].roster
        if self._threshold_cache is not None and self._threshold_cache[0] == len(roster):
            return self._threshold_cache[1]
        thresholds = marginal_thresholds(roster, state.config, state.replacement)
        self._threshold_cache = (len(roster), thresholds)
        return thresholds

    def _valuation(
        self, state: "DraftState", player: Player, thresholds: Dict[str, float]
    ) -> float:
        """What this agent privately thinks `player` is worth, in dollars.

        Two anchors, blended by `vorp_weight`:

          * the market — Sleeper's $PROJ, what the crowd will pay;
          * marginal VORP — points this player would actually add to *this*
            seat's optimal lineup, over a replacement-level body in the same
            slot. Roster-relative, so the same player is worth different amounts
            to different seats, and worth nothing to a seat whose lineup he
            couldn't crack.

        The second is what makes positional sanity fall out of the valuation
        rather than being enforced beside it: a seat holding a defense gets 0
        from the VORP half for another one, because there is no second DEF slot
        for it to occupy. Nothing here names a position.

        Jitter is a string-seeded Random so it's stable across processes (unlike
        hash()) and independent of call order. Not cached: unlike the old
        market-only value, this changes as the roster fills.
        """
        anchor = market_value(player)
        # Marginal VORP is in points and the anchor is in dollars, so convert
        # before blending. A 0.0 rate means the pool offers no conversion; stay
        # on the market.
        if self.vorp_weight > 0.0 and state.points_per_dollar > 0.0:
            vorp_dollars = marginal_points(player, thresholds) / state.points_per_dollar
            anchor = (1.0 - self.vorp_weight) * anchor + self.vorp_weight * vorp_dollars

        jitter = self._jitter_cache.get(player.id)
        if jitter is None:
            jitter = random.Random(f"{self.seed}:{player.id}").uniform(
                1.0 - self.jitter_frac, 1.0 + self.jitter_frac
            )
            self._jitter_cache[player.id] = jitter
        return anchor * jitter

    def _insurance(self, state: "DraftState", player: Player) -> float:
        """Backup value, for ordering candidates the lineup gains nothing from.

        Past a certain point every remaining player has zero marginal value —
        the bench genuinely doesn't score under season-total projections — and
        something still has to choose. This ranks by how much use a body could
        ever be: the number of lineup slots the position can fill, times the
        player's points as a share of what replacement there costs.

        The slot count is what carries it. A spare receiver has five routes into
        the lineup, a spare defense one, a kicker none, so defenses and kickers
        sink to the bottom of the bench on their own merits rather than being
        filtered out. Note this deliberately uses points *relative to*
        replacement, not VORP: VORP floors at zero, which is precisely where
        every bench candidate sits, so it would tie them all at 0.0.
        """
        floor = state.replacement.get(player.pos, 0.0)
        if floor <= 0.0 or floor == float("inf"):
            return 0.0
        return startable_slots(player.pos, state.config) * (player.points / floor)

    # -- policy ------------------------------------------------------------

    def _nomination_key(
        self, state: "DraftState", player: Player, thresholds: Dict[str, float]
    ) -> tuple:
        """Rank a nomination candidate: dollars, insurance, points, then rank.

        Price leads, but it is quantized to whole dollars — partly because an
        auction cannot resolve finer, and mainly because below the $1 floor it
        carries no information at all. Only 126 of the ~1000 draftable players
        have a $PROJ over $1; the rest all anchor at exactly $1, so a raw
        valuation would order them purely by their jitter draw and hand the
        entire back half of the draft to the RNG. Rounding collapses that tail
        to a single tie, which points then breaks.

        Insurance breaks the tie the dollars leave behind. Late on, every
        candidate is worth $1 and adds nothing to the lineup, so without it the
        points tiebreak alone would take a spare defense (86 projected) over a
        spare receiver (60) -- and since the engine forces a nominator to open
        at MIN_BID, nominating it means buying it.

        Points before Sleeper rank because points is what the sim scores, and
        rank is missing for most of the sheet. Rank negated: lower is better.
        """
        rank = player.rank if player.rank is not None else _WORST_RANK
        return (
            round(self._valuation(state, player, thresholds)),
            self._insurance(state, player),
            player.points,
            -rank,
        )

    def nominate(self, state: "DraftState", my_id: str) -> Optional[Player]:
        pool = state.available
        if not pool:
            return None
        need = positional_need(state.managers[my_id].roster, state.config)
        needed = [p for p in pool if need.get(p.pos, 0) > 0]
        # Nominate the most valuable player at a position we still need; if no
        # need remains (or the pool has none), throw up the best body available.
        candidates = needed if needed else pool
        thresholds = self._thresholds(state, my_id)
        return max(
            candidates, key=lambda p: self._nomination_key(state, p, thresholds)
        )

    def bid(self, state: "DraftState", player: Player, my_id: str) -> int:
        """Sealed offer for `player`, in dollars. `SIT_OUT` to decline.

        The agent offers its full private value. Value passes through three
        filters, narrowest last:
          1. depth       — non-need players are worth `depth_value_mult` of value
          2. pick cap    — no single buy eats more than `max_pick_share` of the
                           ceiling
          3. reserve     — the hard `max_bid` ceiling, which keeps $1 per slot

        Why no bid shading: a first-price sealed auction normally invites a seat
        to offer under its value, since it pays its own bid. It was tried here
        (a flat 15% holdback) and removed. Two reasons. It did not do what it
        was credited with — `max_pick_share`, added in the same commit, is what
        collapsed the RNG-driven spread (764 -> 251); shading on top of the cap
        made the spread *worse* (251 -> 306). And it stranded money: a uniform
        discount means the field as a whole under-bids the board, so seats ran
        out of roster slots with $40-$92 unspent instead of the $3-$4 they end
        with now.

        The winner's curse it was meant to fix is small in this model. With 12
        seats drawing +/-15% jitter, the top two valuations are almost always
        within a dollar, so paying your own bid costs ~$1 over paying the
        runner-up's — league-wide spend under a second-price rule is 2380 vs
        2381. Which is also why bidding full value in a first-price auction is
        an honest stand-in for the ascending open auction Sleeper actually runs,
        rather than a simplification that distorts prices.
        """
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

        value = self._valuation(state, player, self._thresholds(state, my_id))
        if not is_need:
            value *= self.depth_value_mult

        # `ceiling` is every dollar this seat may legally commit to one player.
        # The cap takes a fraction of it, floored at MIN_BID so a seat down to
        # its reserve can still buy its remaining slots at $1 rather than sit
        # out and finish short. max_pick_share=1.0 makes the cap a no-op.
        ceiling = max_bid(me.budget, slots)
        pick_cap = max(float(MIN_BID), ceiling * self.max_pick_share)

        offer = int(round(min(value, pick_cap)))
        return max(SIT_OUT, min(offer, ceiling))


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
