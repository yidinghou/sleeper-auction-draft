"""Read a live Sleeper draft over the public v1 API.

Everything the live board needs is unauthenticated and available from two
endpoints — no scraping, no websocket, no session token:

  * ``GET /draft/{id}``        — league settings plus, in ``metadata``, the
    *in-flight* auction state: which player is nominated, by which seat, and
    what the current high offer is. Polling this is how the board stays live.
  * ``GET /draft/{id}/picks``  — every completed pick, each carrying its
    ``metadata.amount`` (the price paid), ``player_id`` and ``draft_slot``.

Note the two are different shapes of truth: ``/picks`` is settled history,
``metadata`` is the bid currently on the clock and may never become a pick.

Teams are keyed by ``draft_slot`` (1..teams), not by user. Mock drafts return
an empty ``picked_by`` and a null ``draft_order`` until they start, so the slot
number is the only identifier that works both before and during a real draft.

``seat_for_user`` is the one bridge from a name back to a slot, for the single
purpose of marking which seat is yours. It uses a third endpoint,
``GET /user/{username}``, and it is allowed to fail: on a mock there is no
``draft_order`` to look a user up in, and the board simply goes unmarked.

``seat_names`` runs that bridge the other way and for all twelve seats, over
``GET /league/{id}/users`` (and ``/rosters`` when the draft order alone can't
place them). Same permission to fail, and it is used more often than not: a mock
publishes a null ``league_id``, so a rehearsal has no names to fetch at all and
falls back to the slot numbers.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from draftsim.config import BENCH, DraftConfig

API_BASE = "https://api.sleeper.app/v1"

_TIMEOUT = 10  # seconds; the poll loop must not wedge on a slow response

# Sleeper's `settings.slots_*` keys -> our slot tokens, in the order slots
# should appear on a roster. Mirrors DEFAULT_ROSTER_SLOTS: starters grouped
# concrete-then-flex, defense next, bench last.
_SLOT_KEYS = (
    ("slots_qb", "QB"),
    ("slots_rb", "RB"),
    ("slots_wr", "WR"),
    ("slots_te", "TE"),
    ("slots_flex", "FLEX"),
    ("slots_rec_flex", "REC_FLEX"),
    ("slots_super_flex", "SUPER_FLEX"),
    ("slots_wrrb_flex", "WRRB_FLEX"),
    ("slots_k", "K"),
    ("slots_def", "DEF"),
    ("slots_bn", BENCH),
)


class SleeperError(RuntimeError):
    """A draft could not be read, or is a shape this simulator can't model."""


def _get(path: str) -> Any:
    """GET a JSON endpoint, busting Sleeper's CDN cache.

    The `_cb` cache-buster is the same trick `lib/sleeper.ts` uses: without it
    a poll can be served a stale copy and the board silently stops updating.
    """
    url = f"{API_BASE}{path}?_cb={int(time.time() * 1000)}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SleeperError(f"{path} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SleeperError(f"{path} unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SleeperError(f"{path} returned malformed JSON") from exc


def fetch_draft(draft_id: str) -> Dict[str, Any]:
    """The draft's settings and current in-flight auction state."""
    draft = _get(f"/draft/{draft_id}")
    if not isinstance(draft, dict) or not draft.get("draft_id"):
        raise SleeperError(f"no draft found with id {draft_id}")
    return draft


def fetch_picks(draft_id: str) -> List[Dict[str, Any]]:
    """Completed picks, oldest first. Empty before the draft starts."""
    picks = _get(f"/draft/{draft_id}/picks")
    if not isinstance(picks, list):
        raise SleeperError(f"draft {draft_id} returned a non-list pick feed")
    return picks


def fetch_user(username: str) -> Dict[str, Any]:
    """A Sleeper account, by username or by user_id — both resolve here.

    Only the `user_id` is wanted, and only to look the account up in a draft's
    `draft_order`. Sleeper answers 200 with a null body for a name nobody has,
    which is not an HTTP error and has to be caught here.
    """
    user = _get(f"/user/{username}")
    if not isinstance(user, dict) or not user.get("user_id"):
        raise SleeperError(f"no Sleeper account named {username!r}")
    return user


def seat_for_user(draft: Dict[str, Any], user_id: str) -> Optional[int]:
    """Which draft slot an account is sitting in, if the draft says.

    `draft_order` maps user_id -> draft_slot, and is null on a mock and on a
    real draft that has not had its order set yet. None means "unknown", never
    "not playing" — the caller leaves the board unmarked rather than guessing.
    """
    order = draft.get("draft_order")
    if not isinstance(order, dict):
        return None
    return _int_or_none(order.get(user_id))


def fetch_league_users(league_id: str) -> List[Dict[str, Any]]:
    """Everyone in the league, with the name each of them chose.

    The only place the other eleven managers have names. Sleeper answers 200
    with a null body for a league nobody has, the same way `/user` does.
    """
    users = _get(f"/league/{league_id}/users")
    if not isinstance(users, list):
        raise SleeperError(f"league {league_id} returned no user list")
    return users


def fetch_league_rosters(league_id: str) -> List[Dict[str, Any]]:
    """Rosters, read only for their `owner_id`.

    The fallback route from a seat to an account: a draft that has been seated
    but publishes no `draft_order` still maps slots to roster ids, and a roster
    knows whose it is.
    """
    rosters = _get(f"/league/{league_id}/rosters")
    if not isinstance(rosters, list):
        raise SleeperError(f"league {league_id} returned no roster list")
    return rosters


def _user_label(user: Dict[str, Any]) -> str:
    """What to call a manager: the team name they picked for this league, and
    only failing that their account name.

    A league name is what gets said out loud in the room -- and it is the one
    they chose knowing it would sit next to these eleven others.
    """
    meta = user.get("metadata") or {}
    team = (meta.get("team_name") or "").strip()
    return team or (user.get("display_name") or "").strip()


def slot_for_user_map(
    draft: Dict[str, Any], rosters: Optional[Sequence[Dict[str, Any]]] = None
) -> Dict[str, int]:
    """user_id -> draft slot, by whichever route this draft supports.

    `draft_order` is the direct answer and is usually there. When it is not, a
    seated draft still has `slot_to_roster_id`, and rosters carry `owner_id` --
    two hops to the same fact. Both are absent on a mock, which is why the
    return is a dict that is allowed to be empty.
    """
    order = draft.get("draft_order")
    if isinstance(order, dict) and order:
        placed = {
            str(user_id): slot
            for user_id, raw in order.items()
            if (slot := _int_or_none(raw)) is not None
        }
        if placed:
            return placed

    slot_to_roster = draft.get("slot_to_roster_id")
    if not isinstance(slot_to_roster, dict) or not rosters:
        return {}
    owner_of = {
        int(roster["roster_id"]): str(roster.get("owner_id") or "")
        for roster in rosters
        if roster.get("roster_id") is not None
    }
    placed = {}
    for raw_slot, roster_id in slot_to_roster.items():
        slot = _int_or_none(raw_slot)
        owner = owner_of.get(_int_or_none(roster_id) or -1, "")
        if slot is not None and owner:
            placed[owner] = slot
    return placed


def seat_names(
    draft: Dict[str, Any],
    users: Sequence[Dict[str, Any]],
    rosters: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[int, str]:
    """draft slot -> manager's name, for as many seats as can be placed.

    Partial by design. A league where one seat is co-owned or unclaimed still
    names the eleven it can, and the board falls back to `S{slot}` for the rest
    -- which is strictly what it showed for all twelve before.
    """
    by_user = slot_for_user_map(draft, rosters)
    if not by_user:
        return {}
    named: Dict[int, str] = {}
    for user in users:
        slot = by_user.get(str(user.get("user_id") or ""))
        label = _user_label(user)
        if slot is not None and label:
            named[slot] = label
    return named


def config_from_draft(draft: Dict[str, Any]) -> DraftConfig:
    """Build a DraftConfig from the draft's own settings.

    Reading the league shape off the API rather than hardcoding it means the
    board values a mock exactly as it values the real draft, and notices when
    the commissioner changes the budget or a slot.

    Raises on a non-auction draft, or on a roster slot this simulator has no
    valuation rules for — silently dropping an unknown slot would quietly
    understate every roster's size and therefore everyone's max bid.
    """
    if draft.get("type") not in (None, "auction"):
        raise SleeperError(
            f"draft {draft.get('draft_id')} is a {draft['type']} draft; "
            "the live board only models auctions"
        )
    settings = draft.get("settings") or {}

    known = {key for key, _ in _SLOT_KEYS}
    unknown = {
        key
        for key, count in settings.items()
        if key.startswith("slots_") and key not in known and count
    }
    if unknown:
        raise SleeperError(
            f"unsupported roster slots: {', '.join(sorted(unknown))}. "
            "Add them to _SLOT_KEYS and to FLEX_ELIGIBILITY/FLEX_SHARES first."
        )

    slots: List[str] = []
    for key, token in _SLOT_KEYS:
        slots.extend([token] * int(settings.get(key) or 0))
    if not slots:
        raise SleeperError("draft declares no roster slots")

    return DraftConfig(
        teams=int(settings.get("teams") or 0),
        budget=int(settings.get("budget") or 0),
        roster_slots=tuple(slots),
    )


@dataclass(frozen=True)
class Nomination:
    """The player currently on the block, if any.

    Sleeper reports this in the draft's `metadata` as flat strings, and clears
    the fields between lots — so `player_id` being None means the room is
    between nominations, not that the draft ended.
    """

    player_id: Optional[str]
    nominating_slot: Optional[int]
    high_bid: Optional[int]
    offering_slot: Optional[int]

    @property
    def is_live(self) -> bool:
        return self.player_id is not None


def _int_or_none(raw: Any) -> Optional[int]:
    raw = (str(raw) if raw is not None else "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def parse_nomination(draft: Dict[str, Any]) -> Nomination:
    meta = draft.get("metadata") or {}
    player_id = (meta.get("nominated_player_id") or "").strip() or None
    return Nomination(
        player_id=player_id,
        nominating_slot=_int_or_none(meta.get("nominating_slot")),
        high_bid=_int_or_none(meta.get("highest_offer")),
        offering_slot=_int_or_none(meta.get("offering_slot")),
    )


def draft_pulse(draft: Dict[str, Any]) -> str:
    """An opaque token that changes whenever the board should be redrawn.

    Lets the poller hit the cheap `/draft` endpoint every tick and refetch the
    much larger pick feed only when something actually moved — either a pick
    settled (`last_picked`) or the bidding on the current lot changed.
    """
    meta = draft.get("metadata") or {}
    return "|".join(
        str(part)
        for part in (
            draft.get("last_picked"),
            draft.get("status"),
            meta.get("nominated_player_id"),
            meta.get("highest_offer"),
            meta.get("offering_slot"),
            meta.get("last_action_at"),
        )
    )
