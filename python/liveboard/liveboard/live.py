"""Serve a live board for a real Sleeper draft.

    python -m liveboard.live --draft-id 1387809050569240576

Opens a local page showing every seat's money, room and reach, refreshing as
picks land. Point it at a mock first — the valuation is identical, so a mock is
a real rehearsal — then at the league draft on the day.

Two loops, deliberately separated: a background thread owns all network I/O and
keeps the newest snapshot, while request handlers only ever read that snapshot.
So a slow or failing Sleeper response degrades the board's freshness, never its
responsiveness, and N open tabs still cost exactly one poller.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .bid_log import BidLog
from .live_render import (
    render_ledger,
    render_log,
    render_nomination,
    render_page,
    render_pool,
    render_pressure,
    render_rosters,
    render_settled_lot,
    render_spend,
)
from .live_state import LeagueState, reconstruct
from .seat_names import MAX_NAME, SeatNames
from .sleeper import (
    SleeperError,
    rules_from_draft,
    draft_pulse,
    fetch_draft,
    fetch_league_rosters,
    fetch_league_users,
    fetch_picks,
    fetch_user,
    parse_nomination,
    seat_for_user,
    seat_names,
)
from draftsim.player import Player, by_sleeper_id, load_players
from draftsim.player import load_projections

DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 3.0
# Whose board this is. One person runs this tool, and the flag existing did not
# help the night it was left off -- the board came up anonymous and looked
# exactly like a bug. `--user someone-else` overrides it; `--user ""` opts out.
DEFAULT_USER = "yidinghou"
# How long a scanned set of names is trusted. Team names change about twice a
# season, and the draft feed is polled every three seconds -- so this is slow on
# purpose, and the right-click menu has a rescan for the one time it matters.
NAME_TTL = 300.0
# How often to look while a player is actually on the block. Sleeper publishes
# the bid on the clock and no history, so a seat that bids and is outbid between
# two polls is never seen at all -- and the gap between two polls is the whole
# resolution the board has. Cheap: the tick is one small `/draft` request unless
# the pulse moved.
LIVE_INTERVAL = 1.0


class DraftPoller:
    """Keeps one current snapshot of a draft, refreshed on a background thread.

    Only the small `/draft` endpoint is polled every tick. The much larger pick
    feed is refetched only when `draft_pulse` changes — so an idle room between
    nominations costs one tiny request per interval.
    """

    def __init__(
        self,
        draft_id: str,
        csv_path: Optional[Path],
        interval: float,
        replay: Optional[int] = None,
        user: Optional[str] = None,
    ):
        self.draft_id = draft_id
        self.interval = interval
        self.replay = replay
        self.user = user
        # Resolved on the first poll and then kept: the account lookup is one
        # request that never changes its answer, while the seat it maps to has
        # to be read off each draft payload anyway. `_user_id` doubles as the
        # "already tried" flag -- a bad username should not be re-fetched every
        # three seconds for the length of a draft.
        self._user_id: Optional[str] = None
        self._user_tried = False
        self.seat_note = ""
        # Who everyone else is. Scanned names are refreshed on a slow timer of
        # their own -- they answer to the league, not to the pick feed, and
        # nothing about a pick landing makes them any staler.
        self.names = SeatNames(draft_id)
        self._names_at: Optional[float] = None
        self.names_note = ""
        # Who is in the bidding on whatever is on the block, and how far each of
        # them pushed it. Sleeper reports one seat and one figure -- whoever
        # holds the offer this instant, at what -- so anyone who bid and was
        # outbid is only ever known to the poller that watched it happen.
        # A dict rather than a list because insertion order is the order the
        # room bid in, which is worth reading, and the value is the highest
        # offer that seat was *seen* holding. None means seen in the bidding
        # with no figure attached -- the nominator, before anyone raised.
        self._lot: Optional[str] = None
        self._bidders: Dict[int, Optional[int]] = {}
        # `_bidders` answers "who's in, at what ceiling" -- this answers "in
        # what order did the raises happen". A list rather than a dict because
        # the same seat can appear twice (raise, get outbid, raise again), and
        # each entry is worth its own place in the trail.
        self._bid_events: List[Tuple[int, Optional[int]]] = []
        # Once a lot closes, its `_bidders`/`_bid_events` live only in `bids`
        # (`BidLog`) -- on disk, and already loaded into `bids.lots` in memory,
        # so there is nothing to seed here. What the room bid is the one thing
        # on this board that cannot be refetched -- Sleeper publishes the offer
        # on the clock and no history, and the pick feed remembers only the
        # price it closed at -- so a checkpoint on a past pick reads its lot
        # from here; see `_trace_for` and `bid_log.BidLog`.
        self.bids = BidLog(draft_id)
        # A disk that stops taking writes mid-auction is worth a line on the
        # board, not the poll thread. Set by `_archive_lot`, drawn with the
        # other warnings.
        self.bids_note = ""
        self.pool = load_players(csv_path)
        # Projections live outside `Player` in the rebuilt library, so the board
        # carries the mapping alongside the pool and hands it to every fold.
        self.proj = load_projections(csv_path)
        # The wider sheet identifies picks the draftable pool excludes; see
        # live_state.reconstruct.
        self.catalog = load_players(csv_path, free_agents=True)
        self._lock = threading.Lock()
        self._pulse: Optional[str] = None
        self._snapshot: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        self._stop = threading.Event()
        # Checkpoint rewind. `_all_picks` is the sorted (and `--replay`-capped)
        # pick list a live snapshot was last built from -- the same list a
        # checkpoint is just a prefix of, since `reconstruct` is a pure fold
        # over picks. `_view` is None while live; otherwise it is how many of
        # those picks the board is currently showing. `_rules`/`_draft` are
        # kept alongside so a checkpoint can be rebuilt without a network call.
        self._all_picks: List[Dict[str, Any]] = []
        self._rules: Optional[Any] = None
        self._draft: Optional[Dict[str, Any]] = None
        self._view: Optional[int] = None

    # -- polling ------------------------------------------------------------

    def my_seat(self, draft: Dict[str, Any]) -> Optional[int]:
        """Which slot is yours, or None if this draft cannot say.

        Never fatal — but never silent either. There are three separate ways to
        end up without a seat and they used to produce one identical unmarked
        board: no username, a username nobody has, and a draft with no order to
        look you up in. Two of those are mistakes and one is normal, so each
        leaves its own note and the band prints whichever applies.
        """
        if not self.user:
            self.seat_note = "no --user given — no seat marked"
            return None
        if not self._user_tried:
            self._user_tried = True
            try:
                self._user_id = fetch_user(self.user)["user_id"]
            except SleeperError as exc:
                self.seat_note = str(exc)
                return None
        if self._user_id is None:
            return None
        seat = seat_for_user(draft, self._user_id)
        if seat is None:
            self.seat_note = (
                f"{self.user} is not seated in this draft — it publishes no "
                "draft order, so no seat is marked"
            )
        else:
            self.seat_note = ""
        return seat

    def scan_names(self, draft: Dict[str, Any]) -> None:
        """Refresh the scanned seat names, at most once every `NAME_TTL`.

        Never fatal, for the same reason `my_seat` isn't: the names are a
        courtesy on top of a board that worked without them, and a league
        endpoint having a bad minute must not cost you the auction. A failure
        keeps the last good map and leaves a note.

        A mock has no `league_id`, so there is nothing to scan and no error
        either -- that is the normal case for every rehearsal, and the seats
        keep whatever was typed by hand.
        """
        league_id = draft.get("league_id")
        if not league_id:
            # Silent, not a warning. Every mock is in this state, the seats
            # simply keep their numbers, and a red line saying so on every
            # rehearsal would train you to stop reading the warning row.
            return
        # `rescan_names` expires the cache rather than passing a flag down here:
        # the rescan is asked for by a request handler and answered by the poll
        # thread, so it has to be a piece of state and not an argument.
        fresh = (
            self._names_at is not None
            and time.monotonic() - self._names_at < NAME_TTL
        )
        if fresh:
            return
        before = (self.names.labels(), self.names_note)
        try:
            users = fetch_league_users(str(league_id))
            # Only worth a second request when the draft cannot place people on
            # its own; `slot_for_user_map` prefers `draft_order` and would
            # ignore these.
            rosters = (
                None
                if draft.get("draft_order")
                else fetch_league_rosters(str(league_id))
            )
            self.names.set_scanned(seat_names(draft, users, rosters))
            self._names_at = time.monotonic()
            self.names_note = ""
        except SleeperError as exc:
            self.names_note = f"seat names not refreshed: {exc}"
        if (self.names.labels(), self.names_note) != before:
            # A rename on Sleeper -- or a scan that started failing -- has to be
            # able to redraw the board on its own. Between nominations the pulse
            # does not move for minutes at a time, and both the new name and the
            # warning about the old one would wait for somebody to bid.
            with self._lock:
                self._pulse = None

    def _archive_lot(self) -> None:
        """Put the lot on the block into `bids`, on disk.

        Idempotent, and a no-op for a lot nothing was seen on: `BidLog.put`
        ignores an empty witness rather than writing one over a fuller one an
        earlier run left. So calling this twice, or on a board that has been
        watching an empty room, costs nothing.

        A failing disk is worth a line on the board and not the poll thread.
        `bids.lots` is still correct in memory (`BidLog.put` updates it before
        it writes), the session still works, and the only thing lost is the
        next restart's inheritance -- which is not worth interrupting an
        auction over.
        """
        if self._lot is None:
            return
        try:
            self.bids.put(self._lot, self._bidders, self._bid_events)
        except OSError as exc:
            self.bids_note = f"bid history not saved: {exc}"
        else:
            self.bids_note = ""

    def flush_lot(self) -> None:
        """Archive the lot still on the block, without closing it.

        Archiving otherwise happens only when the *next* lot change is
        observed, which means the last lot of a session -- the one you Ctrl-C
        on, or the only one `--once` ever sees -- was dropped on the floor
        having been watched all the way through. This is the exit path for it.
        """
        with self._lock:
            self._archive_lot()

    def watch_bidding(self, nom) -> None:
        """Remember who has been in the bidding on the current lot, and at what.

        Sleeper answers "who holds the offer right now, at what figure", never
        "who has bid", so this is the board's own memory and the only place it
        exists. A seat that raises and is immediately outbid shows up only if a
        poll happened to land while it was in front -- hence `LIVE_INTERVAL`,
        and hence the panel saying "led at" rather than claiming a bid history
        it does not have. What is recorded is the highest offer each seat was
        *seen* holding, which is a floor on how far they were willing to go and
        not a bid anybody told us about.

        `max` and not plain assignment: a seat can take the lead, lose it and
        take it again, and the second visit is the one worth keeping. Only the
        figure moves -- the seat keeps the position it first entered at.

        The nominating seat goes in first: nominating a player *is* opening the
        bidding at a dollar, and a room where the nominator is the only bidder
        is a common and worth-seeing state. It carries no figure of its own
        until it is also the seat holding the offer.

        Safe on the rebuild path alone -- `draft_pulse` carries the nominated
        player, the offer and the offering seat, so bidding cannot move without
        the snapshot being rebuilt.

        The outgoing lot's `_bidders`/`_bid_events` are archived into `bids`
        under its player_id before being cleared -- that is the one moment
        they are still known, and a checkpoint that later rewinds to the pick
        this lot became needs them to still be there; see `_archive_lot`.
        """
        if nom.player_id != self._lot:
            # A new player on the block, or the lot closing. Either way this
            # lot's bidders stop moving -- archive them under the player who
            # was on the block, then clear for whatever is in front of the
            # room now.
            self._archive_lot()
            self._lot = nom.player_id
            self._bidders = {}
            self._bid_events = []
        if nom.nominating_slot is not None:
            self._bidders.setdefault(nom.nominating_slot, None)
            if not self._bid_events:
                self._bid_events.append((nom.nominating_slot, None))
        slot = nom.offering_slot
        if slot is not None:
            if nom.high_bid is None:
                self._bidders.setdefault(slot, None)
            else:
                seen = self._bidders.get(slot)
                self._bidders[slot] = max(seen or 0, nom.high_bid)
                # Only a real change in who's leading (or at what) is a raise
                # worth logging -- a poll landing twice on the same offer must
                # not repeat it in the trail.
                last = self._bid_events[-1] if self._bid_events else None
                if last != (slot, nom.high_bid):
                    self._bid_events.append((slot, nom.high_bid))

    def rename_seat(self, slot: int, name: str) -> str:
        """Name a seat by hand. Returns the label it now carries.

        Clearing `_pulse` is what makes the rename visible: `refresh` skips the
        rebuild when the draft has not moved, and between nominations that is
        every tick for minutes at a time -- so a rename would sit invisible
        until somebody bid.
        """
        label = self.names.set_override(slot, name)
        with self._lock:
            self._pulse = None
        return label

    def rescan_names(self) -> None:
        """Force the next poll to re-read the league. Same trick as above: the
        board has to be allowed to redraw even though no pick has landed."""
        self._names_at = None
        with self._lock:
            self._pulse = None

    def _clamp_view(self, index: int) -> int:
        """Call only while holding `_lock` -- reads `_all_picks`."""
        return max(0, min(index, len(self._all_picks)))

    def view_prev(self) -> None:
        """Step one pick back. From live, this steps to the newest checkpoint
        -- one pick behind whatever is currently on screen."""
        with self._lock:
            current = self._view if self._view is not None else len(self._all_picks)
            self._view = self._clamp_view(current - 1)

    def view_next(self) -> None:
        """Step one pick forward. Stops at the newest checkpoint even if the
        live draft has moved on since -- only `view_live` resumes live
        tracking. That is a deliberate choice: an accidental extra tap of
        "next" must not silently drop you back into live and lose your
        place."""
        with self._lock:
            current = self._view if self._view is not None else len(self._all_picks)
            self._view = self._clamp_view(current + 1)

    def view_goto(self, index: int) -> None:
        with self._lock:
            self._view = self._clamp_view(index)

    def view_live(self) -> None:
        with self._lock:
            self._view = None

    def refresh(self) -> None:
        """Fetch once and replace the snapshot. Errors are recorded, not raised:
        a blip must not kill the poll thread and leave the board frozen with no
        explanation."""
        try:
            draft = fetch_draft(self.draft_id)
            # Before the pulse check, and a no-op until its own TTL expires: the
            # names answer to the league rather than to the pick feed, and an
            # idle room between nominations is exactly where a board that only
            # rescanned on a pick would never rescan at all.
            self.scan_names(draft)
            pulse = draft_pulse(draft)
            with self._lock:
                unchanged = pulse == self._pulse and self._snapshot is not None
            if unchanged:
                with self._lock:
                    self._error = None
                return

            rules = rules_from_draft(draft)
            picks = fetch_picks(self.draft_id)
            # Sorted once, here, rather than left to `reconstruct` alone: this
            # is also the list `_build_checkpoint` slices a prefix of, so it
            # has to be in pick order before it is stored on `self`.
            picks = sorted(picks, key=lambda p: int(p.get("pick_no") or 0))
            if self.replay is not None:
                # Rewind a finished draft to mid-auction. A completed mock
                # shows every seat broke and every need met, which is exactly
                # the state the board is useless in -- this is how you rehearse
                # against one. It also caps how far checkpoint nav can go --
                # a rehearsal has nothing beyond pick `replay` to rewind past.
                picks = picks[: self.replay]
            state = reconstruct(picks, rules, self.pool, self.proj, catalog=self.catalog)
            nom = parse_nomination(draft)
            # Under the lock because a checkpoint built on the request thread
            # now reads the in-flight lot directly, and must never catch it
            # half-archived. The one disk write in here happens once per lot
            # close, not once per poll.
            with self._lock:
                self.watch_bidding(nom)
            for slot, seat in state.seats.items():
                seat.name = self.names.label(slot)
                seat.bidding = (
                    "high"
                    if slot == nom.offering_slot
                    else "in"
                    if slot in self._bidders
                    else ""
                )
            nominee = by_sleeper_id(self.catalog).get(nom.player_id or "")
            snapshot = self._build(draft, state, nom, nominee)
            with self._lock:
                self._pulse = pulse
                self._snapshot = snapshot
                self._all_picks = picks
                self._rules = rules
                self._draft = draft
                self._error = None
        except (SleeperError, ValueError) as exc:
            with self._lock:
                self._error = str(exc)

    def _warning(self, state: LeagueState) -> str:
        """The one line the board has room for, most actionable first.

        A pick the CSV cannot name beats a seat with the wrong label, which
        beats a bid history that will not be there next time -- and all three
        are worth more than nothing, which is what the last of them used to
        get.
        """
        if state.unknown_player_ids:
            return (
                f"{len(state.unknown_player_ids)} picks not in the projections "
                "CSV — re-run npm run export:projections"
            )
        return self.names_note or self.bids_note

    def _snapshot_of(
        self,
        draft: Dict[str, Any],
        state: LeagueState,
        subtitle: str,
        nomination_html: str,
        nom_pos: str,
        nav: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Everything a snapshot says that does not depend on which kind it is.

        A live tick and a rewound checkpoint are the same board at different
        moments, and thirteen of their fifteen keys were identical -- built
        twice, so every new fragment had to be added in both and a checkpoint
        that quietly lacked one was the failure mode. They differ in the
        subtitle, the block panel, the position the pressure band lights, and
        the nav; those arrive as arguments.
        """
        # Which draft this actually is. A finished mock and tonight's league
        # draft render identically, so pointing the board at last week's id is
        # a mistake it used to keep to itself -- the name and the tail of the
        # id are cheap and make it a glance.
        name = (draft.get("metadata") or {}).get("name") or "draft"
        seat = self.my_seat(draft)
        return {
            "teams": state.rules.teams,
            "subtitle": subtitle,
            "draft_label": f"{name} · …{str(self.draft_id)[-6:]}",
            "my_seat": seat,
            # Not for drawing -- every fragment already carries its own names.
            # This is what the rename box is prefilled from, so editing a name
            # starts from the one on screen rather than from an empty field.
            "seat_names": {
                str(slot): seat_.name for slot, seat_ in state.seats.items()
            },
            # In the band's header, not in the chart: one line summarising the
            # whole draft is what a header is for, and the chart under it needs
            # every pixel now that the band is a fixed share of the column.
            "spend_html": render_spend(state),
            "ledger_html": render_ledger(
                state, seat, self.seat_note, self.user or ""
            ),
            "nomination_html": nomination_html,
            "rosters_html": render_rosters(state, seat),
            # Only the card for whatever is on the block lights its bidders:
            # the bidding is on one player at one position, and repeating it
            # across all four said nothing about which run is actually on.
            "pressure_html": render_pressure(state, nom_pos),
            "pool_html": render_pool(state, seat),
            "log_html": render_log(state, seat),
            "polled_at": time.strftime("%H:%M:%S"),
            "warning": self._warning(state),
            "nav": nav,
        }

    def _build(self, draft, state: LeagueState, nom, nominee: Optional[Player]):
        status = draft.get("status") or "unknown"
        if self.replay is not None:
            # The feed's own status still reads "complete"; say what is
            # actually on screen so a rehearsal is never mistaken for live.
            status = f"REPLAY at pick {len(state.picks)}"
        snapshot = self._snapshot_of(
            draft,
            state,
            # Connection state and nothing else. The league's constants never
            # changed mid-draft and did not earn a line; the one live number in
            # the old subtitle -- what the room has spent -- is now drawn.
            status,
            render_nomination(state, nom, nominee, self._bidders, self._bid_events),
            nominee.position if nominee is not None else "",
            # A live snapshot is always "caught up to itself" -- index and
            # total are the same number. `snapshot()` only ever substitutes a
            # checkpoint in place of this dict; it never edits this one.
            {"index": len(state.picks), "total": len(state.picks), "live": True},
        )
        # Live only: there is no lot in progress on a checkpoint to have a
        # nominating seat.
        snapshot["nominating_seat"] = nom.nominating_slot
        return snapshot

    def _trace_for(
        self, player_id: Optional[str]
    ) -> Tuple[Dict[int, Optional[int]], List[Tuple[int, Optional[int]]]]:
        """How this lot got to its price, or two empties if nobody saw.

        Two sources in one order. The lot still on the block is the live
        `_bidders`/`_bid_events`, which have not been archived yet and would
        otherwise be missed by exactly the checkpoint most likely to be asked
        for -- the one on the pick that just landed, tapped the moment it did.
        Everything before it comes from `bids`, which a previous run may have
        filled via `BidLog`.

        Takes the lock: this runs on a request thread while the poll thread is
        mutating the in-flight pair.
        """
        if player_id is None:
            return {}, []
        with self._lock:
            if player_id == self._lot and (self._bidders or self._bid_events):
                return dict(self._bidders), list(self._bid_events)
            return self.bids.get(player_id)

    def _build_checkpoint(
        self,
        index: int,
        picks: List[Dict[str, Any]],
        rules,
        draft: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """The board as it stood after exactly `index` picks. Always after a
        bid cleared and before the next one opened -- a checkpoint is a pick
        boundary by construction -- so there is never a lot *in progress* to
        show on the block. There is, however, always the pick that just
        landed: the seat that won it is decorated the same `"high"` bidding
        state a live leader gets, so it lights up the same amber everywhere
        that state already drives -- the ledger chart, the pressure tile --
        rather than the checkpoint inventing a visual language of its own."""
        draft = draft or {}
        state = reconstruct(picks[:index], rules, self.pool, self.proj, catalog=self.catalog)
        winner = max(state.picks, key=lambda p: p.pick_no) if state.picks else None
        for slot, seat in state.seats.items():
            seat.name = self.names.label(slot)
            seat.bidding = "high" if winner is not None and slot == winner.slot else ""
        bidders, events = self._trace_for(
            winner.player.market.sleeper_id if winner is not None else None
        )
        total = len(picks)
        return self._snapshot_of(
            draft,
            state,
            f"Pick {index} of {total} — rewound",
            render_settled_lot(state, bidders, events),
            winner.player.position if winner is not None else "",
            {"index": index, "total": total, "live": False},
        )

    def wait_for(self) -> float:
        """How long to sleep before looking again.

        Faster while a player is on the block, because that is the only window
        in which anything the board cannot reconstruct later goes past: the
        picks are still there in an hour, a bid that was outbid is not. Between
        lots there is nothing to miss and no reason to ask twice as often.

        Never slower than `--interval` asked for -- someone who set it to half a
        second wants half a second everywhere.
        """
        return min(self.interval, LIVE_INTERVAL) if self._lot else self.interval

    def run(self) -> None:
        while not self._stop.wait(0):
            self.refresh()
            if self._stop.wait(self.wait_for()):
                break

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="draft-poller", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Stop polling, keeping the lot that was on the block when you did.

        Ctrl-C during the last nomination of the night is the single most
        likely moment to press it, and that lot's bidding had been watched all
        the way through -- it was only ever waiting on the next lot change to
        be written down.
        """
        self._stop.set()
        self.flush_lot()

    # -- reading ------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """The newest good snapshot, annotated with any current error.

        A stale board plus a visible warning beats a blank one: mid-draft you
        would rather see 20-second-old budgets than nothing.

        When `_view` is set, a checkpoint is substituted in place of the live
        snapshot -- the background poller keeps polling and keeps its own
        `_snapshot` current underneath, it just isn't what gets served until
        `view_live()` is called.
        """
        with self._lock:
            view = self._view
            picks = list(self._all_picks)
            rules = self._rules
            draft = self._draft
            snap = dict(self._snapshot) if self._snapshot else None
            error = self._error
        if view is not None and rules is not None:
            checkpoint = self._build_checkpoint(view, picks, rules, draft)
            if error:
                checkpoint["warning"] = f"{error} (showing last good data)"
            return checkpoint
        if snap is None:
            return {
                "teams": 0,
                "subtitle": error or "waiting for the first poll…",
                "draft_label": f"…{str(self.draft_id)[-6:]}",
                "my_seat": None,
                "seat_names": {},
                "spend_html": "",
                "ledger_html": "",
                "nomination_html": '<div class="block idle">connecting…</div>',
                "rosters_html": "",
                "pressure_html": "",
                "pool_html": "",
                "log_html": "",
                "polled_at": "—",
                "warning": error or "",
                "nav": {"index": 0, "total": 0, "live": True},
            }
        if error:
            snap["warning"] = f"{error} (showing last good data)"
        return snap


def _handler(poller: DraftPoller):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            path = self.path.split("?", 1)[0]
            if path == "/":
                page = render_page(poller.draft_id).encode("utf-8")
                self._send(page, "text/html; charset=utf-8")
            elif path == "/api/state":
                body = json.dumps(poller.snapshot()).encode("utf-8")
                self._send(body, "application/json")
            else:
                self.send_error(404)

        def _read_json(self) -> Optional[Dict[str, Any]]:
            """The request body, or None having already answered the client.

            The JSON content type is required rather than assumed. This server
            binds 127.0.0.1 and has no other defence, and a page in another tab
            can POST a form here without being asked -- but it cannot set this
            header without a preflight it will not get. So the one endpoint
            that writes a file is closed to a drive-by form.
            """
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self.send_error(415, "send application/json")
                return None
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            # A name is capped at MAX_NAME; anything near a kilobyte is not one.
            if length <= 0 or length > 1024:
                self.send_error(400, "expected a small JSON body")
                return None
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.send_error(400, "malformed JSON")
                return None
            if not isinstance(body, dict):
                self.send_error(400, "expected a JSON object")
                return None
            return body

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            path = self.path.split("?", 1)[0]
            if path == "/api/rescan-names":
                poller.rescan_names()
                self._send(b'{"ok":true}', "application/json")
                return
            if path == "/api/nav":
                body = self._read_json()
                if body is None:
                    return
                action = body.get("action")
                if action == "prev":
                    poller.view_prev()
                elif action == "next":
                    poller.view_next()
                elif action == "live":
                    poller.view_live()
                elif action == "goto":
                    try:
                        index = int(body.get("index"))
                    except (TypeError, ValueError):
                        self.send_error(400, "index must be a number")
                        return
                    poller.view_goto(index)
                else:
                    self.send_error(400, "action must be prev/next/live/goto")
                    return
                # The moved-to state, not just an ack -- so the click can
                # redraw the board immediately instead of waiting for the
                # next 2s poll.
                payload = json.dumps(poller.snapshot()).encode("utf-8")
                self._send(payload, "application/json")
                return
            if path != "/api/seat-name":
                self.send_error(404)
                return

            body = self._read_json()
            if body is None:
                return
            teams = poller.snapshot().get("teams") or 0
            try:
                slot = int(body.get("slot"))
            except (TypeError, ValueError):
                self.send_error(400, "slot must be a number")
                return
            # Named against the board that is actually up. Without the bound a
            # typo'd slot would write an override into the file for a seat that
            # does not exist, where nothing would ever show it again.
            if not teams:
                self.send_error(409, "the board has not polled yet")
                return
            if not 1 <= slot <= teams:
                self.send_error(400, f"slot must be 1..{teams}")
                return
            name = body.get("name", "")
            if not isinstance(name, str) or len(name) > MAX_NAME * 4:
                self.send_error(400, f"name must be a string of at most {MAX_NAME}")
                return
            label = poller.rename_seat(slot, name)
            payload = json.dumps({"slot": slot, "label": label}).encode("utf-8")
            self._send(payload, "application/json")

        def log_message(self, *args) -> None:
            """Silence per-request logging: the board polls twice a second and
            would bury the one line that matters."""

    return Handler


def build_parser() -> argparse.ArgumentParser:
    """Split out from `main` so the defaults can be asserted without starting a
    server -- `--user` defaulting is the whole point of the flag now."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--draft-id", required=True, help="Sleeper draft id (from the draft URL)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="seconds between polls of Sleeper (default 3)",
    )
    parser.add_argument(
        "--csv", type=Path, default=None, help="projections CSV (defaults to 2026)"
    )
    parser.add_argument(
        "--replay",
        type=int,
        default=None,
        metavar="N",
        help="only use the first N picks — rehearse mid-draft against a mock "
        "that has already finished",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_USER,
        metavar="USERNAME",
        help=f"Sleeper username, to mark which seat is yours (default: "
        f"{DEFAULT_USER}). Needs a draft that has been seated; pass an empty "
        f"string to run anonymous",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="poll once, print the snapshot as JSON, and exit (no server)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    poller = DraftPoller(
        args.draft_id, args.csv, args.interval, replay=args.replay, user=args.user
    )
    if args.once:
        poller.refresh()
        print(json.dumps(poller.snapshot(), indent=2))
        # The only lot this run will ever see, and it never gets a lot change
        # to be archived by. A `--once` fired during a nomination is a witness
        # worth keeping.
        poller.flush_lot()
        return

    poller.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(poller))
    print(f"Live draft board for {args.draft_id}")
    print(f"  http://127.0.0.1:{args.port}  (polling every {args.interval}s)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        poller.stop()
        server.server_close()


if __name__ == "__main__":
    main()
