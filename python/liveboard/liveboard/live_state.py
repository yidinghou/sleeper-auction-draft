"""Rebuild a league's standing from a live Sleeper pick feed.

The board answers one question: *who can still outbid me, and on what?* That
needs three things per seat, all derived from the picks alone —

  * **money**: what they have left after what they've spent,
  * **room**: how many slots are still open and which positions are unfilled,
  * **reach**: the highest bid that still leaves $1 for every other open slot.

The third is the one that actually settles an auction. A seat with $80 and one
open slot can outbid you; a seat with $80 and eight open slots tops out at $73
and is not the threat it looks like.

All three come from `draftsim`, which models exactly this: a ledger of settled
sales and a cache derived from it. This module used to fold the feed itself and
do its own budget arithmetic; now it records the picks into a `Draft` and reads
the answers back. `seat_from` is the one place the two models meet -- draftsim's
`TeamState` in, the board's `Seat` out -- so when the library moves again there
is a single function to follow rather than a diffuse fold.

Nothing here re-implements a value model, and now nothing here re-implements
roster arithmetic either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from draftsim.draft import Draft
from draftsim.evaluation import Market, marginal_points_of, positional_need_of
from draftsim.lineup import Lineup, empty_lineup
from draftsim.player import MarketData, Player, by_sleeper_id, make_id
from draftsim.rules import CONCRETE_POSITIONS, DraftRules, Position
from draftsim.state import DraftState, TeamState
from draftsim.team import Team


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

    lineup: Lineup = field(default_factory=lambda: Lineup(0.0, ()))
    """The seat's best legal starting lineup, solved by draftsim. Replaces the
    board's old `starters()` call, and is slot-aligned to `rules.slots`."""
    by_position: Mapping[Position, Tuple[Player, ...]] = field(default_factory=dict)
    """Each position's players, best first -- what `is_legal` wants."""

    @property
    def filled(self) -> int:
        return len(self.roster)


def seat_from(state: DraftState, slot: int) -> Seat:
    """Project draftsim's `TeamState` into the board's view of a seat.

    The one place the two models meet. Everything on the left of this function
    is a fact the ledger implies; everything the board adds afterwards -- who is
    sitting here, whether they are in the bidding -- is decorated by the poller,
    because neither is derivable from settled picks.

    A flat snapshot rather than a live wrapper, so `seat.max_bid` stays an
    attribute and the renderer reads the same as it always did. Duplicating
    derived state is safe for the same reason draftsim's own cache is: it is
    rebuilt from the ledger every poll and is never a source of truth.
    """
    ts: TeamState = state.teams[str(slot)]
    return Seat(
        slot=slot,
        roster=list(ts.roster),
        picks=[],  # filled by `reconstruct`, which alone knows the pick numbers
        spent=ts.spent,
        budget_left=ts.remaining,
        open_slots=ts.open_slots,
        needs=positional_need_of(state, str(slot)),
        max_bid=ts.max_bid,
        lineup=ts.lineup,
        by_position=ts.by_position,
    )


@dataclass
class LeagueState:
    """Every seat plus the pool that's left, as of the last pick seen."""

    rules: DraftRules
    seats: Dict[int, Seat]
    available: List[Player]
    replacement: Dict[str, float]
    picks: List[SeatPick]
    unknown_player_ids: List[str]
    """Picks whose player_id wasn't anywhere in the projections CSV. Their
    money and roster slot still count; they just carry no points. A non-empty
    list means the sheet is stale -- re-run `npm run export:projections`."""
    proj: Mapping[str, float] = field(default_factory=dict)
    """Season points by player id. Projections live outside `Player` in the
    rebuilt library, so anything drawing a points figure reads it from here."""
    ledger: Optional[DraftState] = None
    """draftsim's cache, for the valuation calls that need a whole draft rather
    than one roster. Named for what it is so nothing ends up as `state.state`."""
    market: Optional[Market] = None
    """Replacement level and the exchange rate, as of this poll."""

    def points(self, player: Optional[Player]) -> float:
        """This player's projection, or zero -- an unprojected body scores
        nothing, which is the right answer and not an error."""
        return self.proj.get(player.id, 0.0) if player is not None else 0.0


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
        id=make_id(name or str(pick.get("player_id")), pos, team),
        name=name or f"player {pick.get('player_id')}",
        position=pos,
        team=team,
        market=MarketData(sleeper_id=str(pick.get("player_id") or "") or None),
    )


def _price(pick: Dict[str, Any], rules: DraftRules) -> int:
    """The auction price, from `metadata.amount`.

    A missing `amount` reads as `rules.min_bid`, not as free. Nothing sells for
    nothing in an auction, so a blank cell is missing data rather than a $0
    sale, and the floor is the least-wrong thing to say about it -- which is
    also the only thing the ledger will accept, since `Draft.record_pick`
    refuses a price under the minimum.

    Snake picks have no amount either, but a snake draft is refused upstream in
    `rules_from_draft`, so it is never this function's problem.
    """
    raw = str((pick.get("metadata") or {}).get("amount") or "").strip()
    try:
        return max(int(float(raw)), rules.min_bid)
    except ValueError:
        return rules.min_bid


def reconstruct(
    picks: Sequence[Dict[str, Any]],
    rules: DraftRules,
    players: Sequence[Player],
    proj: Mapping[str, float],
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
    them to an anonymous 0-point body. On a real 192-pick auction that is worth
    two picks, so pass `load_players(free_agents=True)` and mean it.

    The fold itself is draftsim's: every pick is recorded into a `Draft`, and
    money, room and reach are read back off the cache rather than counted here.
    """
    lookup = list(catalog) if catalog is not None else list(players)
    index = by_sleeper_id(lookup)
    # Fall back to name+pos+team for a CSV exported before the player_id
    # column existed, so a stale sheet degrades to matching rather than to
    # every pick reading as unknown.
    by_key = {p.id: p for p in lookup}

    # draftsim keys teams by string; the board thinks in Sleeper's 1..N draft
    # slots. `str(slot)` is the whole of the translation, both ways.
    teams = [Team(id=str(n), name=f"Seat {n}") for n in range(1, rules.teams + 1)]
    known: Dict[str, Player] = {p.id: p for p in lookup}
    scores: Dict[str, float] = dict(proj)

    all_picks: List[SeatPick] = []
    unknown: List[str] = []
    resolved: List[Tuple[int, SeatPick]] = []

    for pick in sorted(picks, key=lambda p: int(p.get("pick_no") or 0)):
        slot = int(pick.get("draft_slot") or 0)
        if not 1 <= slot <= rules.teams:
            # A pick from a seat outside 1..teams means the rules and the feed
            # disagree; skipping it would hide that, so surface it loudly.
            raise ValueError(
                f"pick {pick.get('pick_no')} has draft_slot {slot}, "
                f"outside 1..{rules.teams}"
            )
        pid = str(pick.get("player_id") or "")
        player = index.get(pid)
        if player is None:
            fallback = _player_from_pick(pick)
            player = by_key.get(fallback.id)
            if player is None:
                player = fallback
                unknown.append(pid)
        # An invented body has to exist for draftsim to record a pick against
        # it, and scores nothing -- points live outside `Player` now, so absence
        # from `scores` is how "no projection" is spelled.
        known.setdefault(player.id, player)
        scores.setdefault(player.id, 0.0)

        entry = SeatPick(
            pick_no=int(pick.get("pick_no") or 0),
            slot=slot,
            player=player,
            price=_price(pick, rules),
        )
        all_picks.append(entry)
        resolved.append((slot, entry))

    draft = Draft(rules=rules, players=known, teams=teams, proj=scores)
    for slot, entry in resolved:
        draft.record_pick(str(slot), entry.player.id, entry.price, float(entry.pick_no))

    state = draft.state
    market = Market.of(state)
    available = [p for p in players if p.id not in state.owner]

    seats: Dict[int, Seat] = {}
    for n in range(1, rules.teams + 1):
        seat = seat_from(state, n)
        seat.picks = [e for s, e in resolved if s == n]
        seats[n] = seat

    return LeagueState(
        rules=rules,
        seats=seats,
        available=available,
        replacement=dict(market.replacement),
        picks=all_picks,
        unknown_player_ids=unknown,
        proj=scores,
        ledger=state,
        market=market,
    )


def seat_value_of(
    state: LeagueState, seat: Seat, player: Optional[Player]
) -> float:
    """Points `player` would add to this seat's starting lineup, over a $1 body.

    Zero now means two things, and both are reasons not to fear this seat: it
    cannot use them at all -- a full lineup at that position, or one it can
    never start, like a second defense -- or it can, but gains nothing a
    freely-available body would not also give it. The board wants the second
    reading as much as the first, because a seat that would not improve is a
    seat that will not push the price.
    """
    if player is None or state.ledger is None or state.market is None:
        return 0.0
    return marginal_points_of(
        state.ledger, str(seat.slot), player, state.market.replacement
    )


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
# Deliberately *not* `rules.starter_shares()` (QB 2.00, RB 2.77, WR 3.23,
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


def position_summary(
    seat: Seat, rules: DraftRules, proj: Mapping[str, float]
) -> List[PositionLine]:
    """A seat at a glance: how full each position is and what it's scoring.

    The coarse read the roster card can't give you -- *does seat 7 still need a
    receiver?* -- without reading sixteen rows to find out.

    `want` is the draft target (`DRAFT_TARGETS`), not the slot count, so this
    deliberately differs from `seat.needs`: the seat's structural need says what
    makes a legal lineup, this says what makes a good one.

    `starter_points` is grouped off the seat's own solved lineup, the same one
    draftsim scores, so the summary and the roster behind it cannot disagree. A
    position nobody starts and nobody owns is dropped, so K stays out of a
    league that doesn't play one but appears the moment someone drafts a kicker.
    """
    want: Dict[str, float] = dict(rules.starter_counts())
    want.update(DRAFT_TARGETS)
    have: Dict[str, int] = {}
    for player in seat.roster:
        have[player.position] = have.get(player.position, 0) + 1

    points: Dict[str, float] = {}
    for player in seat.lineup.slots:
        if player is not None:
            points[player.position] = (
                points.get(player.position, 0.0) + proj.get(player.id, 0.0)
            )

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
        if pick.player.position in totals:
            totals[pick.player.position] += pick.price
    return totals
