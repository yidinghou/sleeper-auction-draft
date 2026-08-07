"""Serve a live board for a real Sleeper draft.

    python -m draftsim.live --draft-id 1387809050569240576

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
from typing import Any, Dict, List, Optional

from .live_render import (
    render_ledger,
    render_log,
    render_nomination,
    render_page,
    render_pool,
    render_pressure,
    render_rosters,
    render_spend,
)
from .live_state import LeagueState, reconstruct
from .seat_names import MAX_NAME, SeatNames
from .sleeper import (
    SleeperError,
    config_from_draft,
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
from .valuation import Player, by_sleeper_id, load_players

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
        self.pool = load_players(csv_path)
        # The wider sheet identifies picks the draftable pool excludes; see
        # live_state.reconstruct.
        self.catalog = load_players(csv_path, free_agents=True)
        self._lock = threading.Lock()
        self._pulse: Optional[str] = None
        self._snapshot: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        self._stop = threading.Event()

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

            config = config_from_draft(draft)
            picks = fetch_picks(self.draft_id)
            if self.replay is not None:
                # Rewind a finished draft to mid-auction. A completed mock
                # shows every seat broke and every need met, which is exactly
                # the state the board is useless in -- this is how you rehearse
                # against one.
                picks = sorted(picks, key=lambda p: int(p.get("pick_no") or 0))
                picks = picks[: self.replay]
            state = reconstruct(picks, config, self.pool, catalog=self.catalog)
            nom = parse_nomination(draft)
            for slot, seat in state.seats.items():
                seat.name = self.names.label(slot)
            nominee = by_sleeper_id(self.catalog).get(nom.player_id or "")
            snapshot = self._build(draft, state, nom, nominee)
            with self._lock:
                self._pulse = pulse
                self._snapshot = snapshot
                self._error = None
        except (SleeperError, ValueError) as exc:
            with self._lock:
                self._error = str(exc)

    def _build(self, draft, state: LeagueState, nom, nominee: Optional[Player]):
        config = state.config
        status = draft.get("status") or "unknown"
        if self.replay is not None:
            # The feed's own status still reads "complete"; say what is
            # actually on screen so a rehearsal is never mistaken for live.
            status = f"REPLAY at pick {len(state.picks)}"
        stale = (
            f"{len(state.unknown_player_ids)} picks not in the projections CSV — "
            "re-run npm run export:projections"
            if state.unknown_player_ids
            else self.names_note
        )
        seat = self.my_seat(draft)
        # Which draft this actually is. A finished mock and tonight's league
        # draft render identically, so pointing the board at last week's id is
        # a mistake it used to keep to itself -- the name and the tail of the
        # id are cheap and make it a glance.
        name = (draft.get("metadata") or {}).get("name") or "draft"
        return {
            "teams": config.teams,
            # Connection state and nothing else. The league's constants never
            # changed mid-draft and did not earn a line; the one live number in
            # the old subtitle -- what the room has spent -- is now drawn.
            "subtitle": status,
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
            "nomination_html": render_nomination(state, nom, nominee),
            "rosters_html": render_rosters(state),
            "pressure_html": render_pressure(state),
            "pool_html": render_pool(state),
            "log_html": render_log(state),
            "polled_at": time.strftime("%H:%M:%S"),
            "warning": stale,
        }

    def run(self) -> None:
        while not self._stop.wait(0):
            self.refresh()
            if self._stop.wait(self.interval):
                break

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="draft-poller", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    # -- reading ------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """The newest good snapshot, annotated with any current error.

        A stale board plus a visible warning beats a blank one: mid-draft you
        would rather see 20-second-old budgets than nothing.
        """
        with self._lock:
            snap = dict(self._snapshot) if self._snapshot else None
            error = self._error
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
