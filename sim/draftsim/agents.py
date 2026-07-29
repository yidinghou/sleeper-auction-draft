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
from typing import TYPE_CHECKING, Dict, Optional, Protocol, Sequence, runtime_checkable

from .auction import MIN_BID, can_bid, max_bid
from .config import DraftConfig
from .roster import open_slots, positional_need, startable_slots
from .valuation import Player, market_value, vorp_value

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
      * `vorp_weight`      — how much to blend points-above-replacement into the
        market anchor, 0.0 (market only, the default) to 1.0 (VORP only).
      * `bench_insurance`  — residual worth of a body at a position already
        covered to its slot ceiling, as a fraction of depth value. Stands in for
        bye and injury coverage, which season-total scoring doesn't model. Only
        granted where the lineup starts a position more than once, so it never
        resurrects a second defense. 0.0 makes the slot ceiling a hard cap.
    """

    def __init__(
        self,
        name: str,
        seed: str = "0",
        jitter_frac: float = 0.15,
        depth_value_mult: float = 0.4,
        max_pick_share: float = 0.5,
        vorp_weight: float = 0.0,
        bench_insurance: float = 0.10,
    ) -> None:
        self.name = name
        self.seed = str(seed)
        self.jitter_frac = jitter_frac
        self.depth_value_mult = depth_value_mult
        self.max_pick_share = max_pick_share
        self.vorp_weight = vorp_weight
        self.bench_insurance = bench_insurance
        self._val_cache: Dict[str, float] = {}

    # -- valuation ---------------------------------------------------------

    def _valuation(self, state: "DraftState", player: Player) -> float:
        """What this agent privately thinks `player` is worth, in dollars.

        Market price (optionally blended with VORP) nudged by a deterministic
        per-(seed, player) jitter. Uses a string-seeded Random so the jitter is
        stable across processes (unlike hash()) and independent of call order —
        the same player is worth the same to this agent no matter when it's
        evaluated. Cached per player, which is safe because every input
        (market price, replacement level, exchange rate) is fixed for a draft.
        """
        cached = self._val_cache.get(player.id)
        if cached is not None:
            return cached

        anchor = market_value(player)
        # VORP is in points and the anchor is in dollars, so convert before
        # blending. A 0.0 rate means the pool offers no conversion; stay on market.
        if self.vorp_weight > 0.0 and state.points_per_dollar > 0.0:
            vorp_dollars = vorp_value(player, state.replacement) / state.points_per_dollar
            anchor = (1.0 - self.vorp_weight) * anchor + self.vorp_weight * vorp_dollars

        jitter = random.Random(f"{self.seed}:{player.id}").uniform(
            1.0 - self.jitter_frac, 1.0 + self.jitter_frac
        )
        value = anchor * jitter
        self._val_cache[player.id] = value
        return value

    # -- policy ------------------------------------------------------------

    def _depth_exposure(
        self, roster: Sequence[Player], pos: str, config: DraftConfig
    ) -> float:
        """Share of this position's startable slots this roster hasn't covered.

        1.0 when it owns none, falling to `bench_insurance` once it owns as many
        as the lineup could ever start. This is what makes "don't roster two
        defenses" fall out of the league structure rather than being written
        down as a rule: DEF has exactly one slot and no flex accepts it, so a
        seat that owns a defense drops to zero exposure and a second one is
        worth nothing — the position is never named anywhere in the code.

        Kickers are zero from the start, for the same structural reason: the
        default lineup has no K slot at all.

        Positions the lineup starts several of keep a residual instead of
        dropping to zero, because a spare body there has several routes back
        into the lineup. Without it every seat drafts the identical positional
        shape — the startable slots sum to exactly the roster size (2 QB, 4 RB,
        5 WR, 4 TE, 1 DEF = 16), so a hard ceiling leaves no room to build a
        roster differently, and Stage 4 archetypes would have nothing to vary.
        """
        slots = startable_slots(pos, config)
        if slots == 0:
            return 0.0
        owned = sum(1 for p in roster if p.pos == pos)
        if owned < slots:
            return (slots - owned) / slots
        return self.bench_insurance if slots > 1 else 0.0

    def _nomination_key(self, state: "DraftState", player: Player) -> tuple:
        """Rank a nomination candidate: whole dollars, then points, then rank.

        Price leads, but it is quantized to whole dollars — partly because an
        auction cannot resolve finer, and mainly because below the $1 floor it
        carries no information at all. Only 126 of the ~1000 draftable players
        have a $PROJ over $1; the rest all anchor at exactly $1, so a raw
        valuation would order them purely by their jitter draw and hand the
        entire back half of the draft to the RNG. Rounding collapses that tail
        to a single tie, which points then breaks.

        Points before Sleeper rank because points is what the sim scores, and
        rank is missing for most of the sheet. Rank negated: lower is better.
        """
        rank = player.rank if player.rank is not None else _WORST_RANK
        return (round(self._valuation(state, player)), player.points, -rank)

    def nominate(self, state: "DraftState", my_id: str) -> Optional[Player]:
        pool = state.available
        if not pool:
            return None
        roster = state.managers[my_id].roster
        need = positional_need(roster, state.config)
        needed = [p for p in pool if need.get(p.pos, 0) > 0]
        if not needed:
            # No starter need left, so this is a depth pick. Only put up players
            # this seat could still start -- the engine forces a nominator to
            # open at MIN_BID, so nominating a body you'd never bid on is how
            # you end up buying it. Falls back to the pool if nothing has
            # exposure left, which keeps rosters fillable under a lineup whose
            # bench outnumbers its startable slots.
            live = [
                p
                for p in pool
                if self._depth_exposure(roster, p.pos, state.config) > 0
            ]
            needed = live
        candidates = needed if needed else pool
        return max(candidates, key=lambda p: self._nomination_key(state, p))

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

        value = self._valuation(state, player)
        if not is_need:
            # Depth is discounted, then scaled by how much of this position the
            # lineup could still start. A body at a position already covered to
            # its slot ceiling scores 0 and this seat sits out.
            value *= self.depth_value_mult * self._depth_exposure(
                me.roster, player.pos, state.config
            )

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
