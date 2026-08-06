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
from typing import Any, Dict, Optional

from .live_render import (
    render_ledger,
    render_log,
    render_nomination,
    render_page,
    render_pool,
    render_pressure,
    render_rosters,
)
from .live_state import LeagueState, reconstruct
from .sleeper import (
    SleeperError,
    config_from_draft,
    draft_pulse,
    fetch_draft,
    fetch_picks,
    fetch_user,
    parse_nomination,
    seat_for_user,
)
from .valuation import Player, by_sleeper_id, load_players

DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 3.0


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

        Never fatal. A mock has no `draft_order` at all, and that is the draft
        you rehearse against — so the board has to be willing to run unmarked
        and say why, rather than refusing to start.
        """
        if not self.user:
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
                f"{self.user} is not seated in this draft — a mock never "
                "publishes a draft order, so no seat is marked"
            )
        else:
            self.seat_note = ""
        return seat

    def refresh(self) -> None:
        """Fetch once and replace the snapshot. Errors are recorded, not raised:
        a blip must not kill the poll thread and leave the board frozen with no
        explanation."""
        try:
            draft = fetch_draft(self.draft_id)
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
            else ""
        )
        seat = self.my_seat(draft)
        return {
            "teams": config.teams,
            # Connection state and nothing else. The league's constants never
            # changed mid-draft and did not earn a line; the one live number in
            # the old subtitle -- what the room has spent -- is now drawn.
            "subtitle": status,
            "my_seat": seat,
            "ledger_html": render_ledger(state, seat, self.seat_note),
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
                "my_seat": None,
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

        def log_message(self, *args) -> None:
            """Silence per-request logging: the board polls twice a second and
            would bury the one line that matters."""

    return Handler


def main() -> None:
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
        default=None,
        metavar="USERNAME",
        help="your Sleeper username, to mark which seat is yours. Needs a "
        "draft that has been seated — a mock has no draft order and stays "
        "unmarked",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="poll once, print the snapshot as JSON, and exit (no server)",
    )
    args = parser.parse_args()

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
