"""Rebuild a league's standing from a live Sleeper pick feed.

The board answers one question: *who can still outbid me, and on what?* That
needs three things per seat, all derived from the picks alone —

  * **money**: what they have left after what they've spent,
  * **room**: how many slots are still open and which positions are unfilled,
  * **reach**: the highest bid that still leaves $1 for every other open slot.

The third is the one that actually settles an auction. A seat with $80 and one
open slot can outbid you; a seat with $80 and eight open slots tops out at $73
and is not the threat it looks like.

Valuation is reused wholesale from the simulator (`roster`, `auction`,
`valuation`) so the live board and the sim agree on what a roster is worth.
Nothing here re-implements a value model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .auction import max_bid
from .config import CONCRETE_POSITIONS, DraftConfig
from .roster import marginal_points, marginal_thresholds, open_slots, positional_need
from .valuation import Player, _make_id, by_sleeper_id, replacement_points


@dataclass(frozen=True)
class SeatPick:
    """One settled purchase, tied to the seat that made it."""

    pick_no: int
    slot: int
    player: Player
    price: int


@dataclass
class Seat:
    """A single team's standing, keyed by its draft slot."""

    slot: int
    roster: List[Player]
    picks: List[SeatPick]
    spent: int
    budget_left: int
    open_slots: int
    needs: Dict[str, int]
    max_bid: int

    @property
    def filled(self) -> int:
        return len(self.roster)


@dataclass
class LeagueState:
    """Every seat plus the pool that's left, as of the last pick seen."""

    config: DraftConfig
    seats: Dict[int, Seat]
    available: List[Player]
    replacement: Dict[str, float]
    picks: List[SeatPick]
    unknown_player_ids: List[str]
    """Picks whose player_id wasn't anywhere in the projections CSV. Their
    money and roster slot still count; they just carry no points. A non-empty
    list means the sheet is stale -- re-run `npm run export:projections`."""


def _player_from_pick(pick: Dict[str, Any]) -> Player:
    """A stand-in for a drafted player who isn't in the projections.

    Kickers, and the ~2/3 of the sheet `load_players` drops as free agents, can
    still be drafted. Inventing a 0-point body keeps the seat's budget and slot
    accounting exact -- which is what the board is for -- while making it
    obvious the pick carries no projection.
    """
    meta = pick.get("metadata") or {}
    name = " ".join(
        part for part in (meta.get("first_name"), meta.get("last_name")) if part
    ).strip()
    pos = (meta.get("position") or "").strip()
    team = (meta.get("team") or "").strip()
    return Player(
        id=_make_id(name or str(pick.get("player_id")), pos, team),
        name=name or f"player {pick.get('player_id')}",
        pos=pos,
        team=team,
        points=0.0,
        sleeper_id=str(pick.get("player_id") or "") or None,
    )


def _price(pick: Dict[str, Any]) -> int:
    """The auction price, from `metadata.amount`. Snake picks have none."""
    raw = str((pick.get("metadata") or {}).get("amount") or "").strip()
    try:
        return int(float(raw))
    except ValueError:
        return 0


def reconstruct(
    picks: Sequence[Dict[str, Any]],
    config: DraftConfig,
    players: Sequence[Player],
    catalog: Optional[Sequence[Player]] = None,
) -> LeagueState:
    """Fold the raw pick feed into per-seat standings and a remaining pool.

    `players` is the draftable pool -- what's still on the board and what
    replacement level is measured against. `catalog` is the wider sheet used
    only to *identify* picks, and defaults to `players`.

    They differ because a seat can draft someone who isn't in the pool:
    `load_players` drops unsigned free agents, but a $1 dart at an unsigned
    receiver is a real pick that really spent a dollar. Resolving against the
    full sheet keeps that player's name and projection instead of reducing
    them to an anonymous 0-point body.
    """
    lookup = list(catalog) if catalog is not None else list(players)
    index = by_sleeper_id(lookup)
    # Fall back to name+pos+team for a CSV exported before the player_id
    # column existed, so a stale sheet degrades to matching rather than to
    # every pick reading as unknown.
    by_key = {p.id: p for p in lookup}

    seats: Dict[int, Seat] = {
        slot: Seat(
            slot=slot,
            roster=[],
            picks=[],
            spent=0,
            budget_left=config.budget,
            open_slots=config.roster_size,
            needs={},
            max_bid=0,
        )
        for slot in range(1, config.teams + 1)
    }

    taken: set = set()
    all_picks: List[SeatPick] = []
    unknown: List[str] = []

    for pick in sorted(picks, key=lambda p: int(p.get("pick_no") or 0)):
        slot = int(pick.get("draft_slot") or 0)
        if slot not in seats:
            # A pick from a seat outside 1..teams means the config and the feed
            # disagree; skipping it would hide that, so surface it loudly.
            raise ValueError(
                f"pick {pick.get('pick_no')} has draft_slot {slot}, "
                f"outside 1..{config.teams}"
            )
        pid = str(pick.get("player_id") or "")
        player = index.get(pid)
        if player is None:
            fallback = _player_from_pick(pick)
            player = by_key.get(fallback.id)
            if player is None:
                player = fallback
                unknown.append(pid)

        entry = SeatPick(
            pick_no=int(pick.get("pick_no") or 0),
            slot=slot,
            player=player,
            price=_price(pick),
        )
        all_picks.append(entry)
        seat = seats[slot]
        seat.roster.append(player)
        seat.picks.append(entry)
        seat.spent += entry.price
        taken.add(player.id)

    available = [p for p in players if p.id not in taken]
    # Replacement level is measured against who is actually left, so it rises
    # as the room drains -- the same reason a late-draft WR2 is worth less than
    # the identical player was in round one.
    replacement = replacement_points(available, config)

    for seat in seats.values():
        seat.budget_left = config.budget - seat.spent
        seat.open_slots = open_slots(seat.filled, config)
        seat.needs = positional_need(seat.roster, config)
        seat.max_bid = max_bid(seat.budget_left, seat.open_slots)

    return LeagueState(
        config=config,
        seats=seats,
        available=available,
        replacement=replacement,
        picks=all_picks,
        unknown_player_ids=unknown,
    )


def seat_value_of(
    state: LeagueState, seat: Seat, player: Optional[Player]
) -> float:
    """Points `player` would add to this seat's starting lineup.

    Zero means the seat cannot use them -- a full lineup at that position, or a
    position it can never start (a second defense). That, crossed with the
    seat's max bid, is what separates a rival who *will* push the price from
    one who merely *could*.
    """
    if player is None:
        return 0.0
    thresholds = marginal_thresholds(seat.roster, state.config, state.replacement)
    return marginal_points(player, thresholds)


def contenders(state: LeagueState, player: Optional[Player]) -> List[Seat]:
    """Seats that can both afford the player and actually start them, richest
    reach first — the shortlist of who you're really bidding against."""
    if player is None:
        return []
    live = [
        seat
        for seat in state.seats.values()
        if seat.max_bid > 0 and seat_value_of(state, seat, player) > 0.0
    ]
    return sorted(live, key=lambda s: (-s.max_bid, s.slot))


def spend_by_position(state: LeagueState) -> Dict[str, int]:
    """League-wide dollars sunk into each position so far."""
    totals = {pos: 0 for pos in CONCRETE_POSITIONS}
    for pick in state.picks:
        if pick.player.pos in totals:
            totals[pick.player.pos] += pick.price
    return totals
