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
from .roster import (
    marginal_points,
    marginal_thresholds,
    open_slots,
    positional_need,
    starters,
)
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
    name: str = ""
    """Who is sitting here, if anyone can say. Defaulted and filled in by the
    poller after the fact: a name is not derivable from the pick feed, and
    reconstruction must stay a pure function of the picks."""
    bidding: str = ""
    """Whether this seat is in the bidding on whatever is on the block right
    now: "high" if it holds the current offer, "in" if it has held it at some
    point since the lot opened, "" otherwise.

    Decorated by the poller like `name`, and for a stronger version of the same
    reason: it is about a bid that has not become a pick and may never become
    one, which is the one thing the settled feed this module folds cannot say.
    One field rather than two flags -- the three states are exclusive, and each
    maps to exactly one class on the mark that draws it."""

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


# How many bodies a seat wants at each position, as starter-equivalents.
#
# Deliberately *not* `config.starter_shares()` (QB 2.00, RB 2.77, WR 3.23,
# TE 1.00). Those are structural -- how the lineup template divides, and the
# right input to replacement level. These are a draft plan: the extra QB is
# insurance in a superflex league where the position empties early, and the
# lighter RB / heavier WR reflects buying the flex with receivers. Fractional
# because a flex slot is genuinely shared -- "2.5 running backs" is the honest
# target, and rounding it to 3 is what made a filled seat look short.
#
# Positions absent here fall back to the structural count, so DEF and K still
# report a target rather than silently reading as zero.
DRAFT_TARGETS: Dict[str, float] = {"QB": 3.0, "RB": 2.5, "WR": 3.5, "TE": 1.0}


@dataclass(frozen=True)
class PositionLine:
    """One position's worth of a seat, summarized."""

    pos: str
    have: int
    """Bodies on the roster at this position, bench included."""
    want: float
    """Target bodies at this position, from `DRAFT_TARGETS`. Fractional."""
    starter_points: float
    """What this position contributes to the seat's starting lineup."""

    @property
    def need(self) -> float:
        """More bodies wanted before the target is met. Never negative -- a
        fourth running back is depth, not a need."""
        return max(0.0, self.want - self.have)


def position_summary(seat: Seat, config: DraftConfig) -> List[PositionLine]:
    """A seat at a glance: how full each position is and what it's scoring.

    The coarse read the roster card can't give you -- *does seat 7 still need a
    receiver?* -- without reading sixteen rows to find out.

    `want` is the draft target (`DRAFT_TARGETS`), not the slot count, so this
    deliberately differs from `seat.needs`: the seat's structural need says what
    makes a legal lineup, this says what makes a good one.

    `starter_points` is grouped off the same `starters()` matching the simulator
    scores on, so the summary and the roster behind it cannot disagree. A
    position nobody starts and nobody owns is dropped, so K stays out of a
    league that doesn't play one but appears the moment someone drafts a kicker.
    """
    want: Dict[str, float] = dict(config.starter_counts())
    want.update(DRAFT_TARGETS)
    have: Dict[str, int] = {}
    for player in seat.roster:
        have[player.pos] = have.get(player.pos, 0) + 1

    points: Dict[str, float] = {}
    for player in starters(seat.roster, config):
        if player is not None:
            points[player.pos] = points.get(player.pos, 0.0) + player.points

    positions = list(CONCRETE_POSITIONS)
    # Anything the roster holds that the position list doesn't know about (an
    # odd metadata position on an unmatched pick) still gets a line: a body that
    # spent money must be visible somewhere.
    positions += [pos for pos in have if pos not in positions]
    return [
        PositionLine(
            pos=pos,
            have=have.get(pos, 0),
            want=float(want.get(pos, 0)),
            starter_points=points.get(pos, 0.0),
        )
        for pos in positions
        if want.get(pos, 0) or have.get(pos, 0)
    ]


def spend_by_position(state: LeagueState) -> Dict[str, int]:
    """League-wide dollars sunk into each position so far."""
    totals = {pos: 0 for pos in CONCRETE_POSITIONS}
    for pick in state.picks:
        if pick.player.pos in totals:
            totals[pick.player.pos] += pick.price
    return totals
