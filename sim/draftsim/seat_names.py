"""What each seat is called: what Sleeper says, and what you said instead.

Two maps over one question, and they are kept apart on purpose:

  * **scanned** — read from the league every few minutes. Never written to
    disk. A manager who renames their team mid-season must not find last
    week's name frozen into a file the board trusts over the API.
  * **overrides** — typed by hand into the board, and the only thing persisted.
    They exist because the scan cannot always answer: a mock draft publishes a
    null ``league_id``, so a rehearsal has no names to fetch at all, and a real
    league can seat someone the draft order does not place.

An override always wins. It is the more recent statement of the same fact, made
by the person looking at the room.

The file is per draft (``data/seat-names-<draft_id>.json``) rather than per
league. Slots are a property of the draft, and last year's league draft sat the
same twelve people in different chairs.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional

from .valuation import REPO_ROOT

NAMES_DIR = REPO_ROOT / "data"

# Long enough for "Jefferson Airplane", short enough that a card header stays a
# card header. Names are also echoed into HTML, so the cap is a size limit on
# what one POST can put in every fragment of the page.
MAX_NAME = 24

# Angle brackets and control characters out; everything else through, because
# managers name teams in emoji and every alphabet there is -- and because
# "Marc's Team" is an ordinary name that a stricter filter would quietly
# rewrite. Quotes and ampersands are safe by the time they are drawn: every
# site escapes, and this is the second of the two fences rather than the only
# one.
_STRIP = re.compile(r"[<>\x00-\x1f\x7f]")


def clean_name(raw: str) -> str:
    """A name safe to store and to draw. Empty means "no override"."""
    return _STRIP.sub("", str(raw or "")).strip()[:MAX_NAME].strip()


class SeatNames:
    """The names for one draft, with the hand-typed ones backed by a file.

    Read from the poll thread and written from request handlers, so both maps
    are replaced wholesale rather than mutated in place -- a reader holding the
    old dict sees a consistent older answer instead of a half-written one.
    """

    def __init__(self, draft_id: str, directory: Optional[Path] = None):
        self.draft_id = str(draft_id)
        self.path = (directory or NAMES_DIR) / f"seat-names-{self.draft_id}.json"
        self.scanned: Dict[int, str] = {}
        self.overrides: Dict[int, str] = self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> Dict[int, str]:
        """Overrides from disk, or none. A corrupt or unreadable file is worth
        exactly one lost customisation and must not stop the board from
        starting the night of a draft."""
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        stored = raw.get("overrides") if isinstance(raw, dict) else None
        if not isinstance(stored, dict):
            return {}
        out: Dict[int, str] = {}
        for slot, name in stored.items():
            try:
                out[int(slot)] = clean_name(name)
            except (TypeError, ValueError):
                continue
        return {slot: name for slot, name in out.items() if name}

    def _save(self) -> None:
        """Write via a temp file in the same directory and rename over the top:
        the board is running while this happens, and a truncated file read back
        on restart would lose every name rather than the one being changed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {
                "draft_id": self.draft_id,
                "overrides": {str(k): v for k, v in sorted(self.overrides.items())},
            },
            indent=2,
        )
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(body + "\n")
            os.replace(tmp, self.path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- reading and writing ------------------------------------------------

    def label(self, slot: int) -> str:
        """What to call this seat, or "" if nothing can. The caller draws the
        slot number for the empty case -- this module has no opinion about what
        an unnamed seat looks like."""
        return self.overrides.get(slot) or self.scanned.get(slot, "")

    def labels(self) -> Dict[int, str]:
        slots = set(self.scanned) | set(self.overrides)
        return {slot: self.label(slot) for slot in sorted(slots)}

    def set_scanned(self, names: Dict[int, str]) -> None:
        self.scanned = dict(names)

    def set_override(self, slot: int, name: str) -> str:
        """Name a seat by hand, or clear it back to the scanned name with "".

        Returns the label the seat now carries, which is not always what was
        passed: clearing an override on a scanned league hands the seat back to
        Sleeper's answer rather than to nothing.
        """
        cleaned = clean_name(name)
        overrides = dict(self.overrides)
        if cleaned:
            overrides[int(slot)] = cleaned
        else:
            overrides.pop(int(slot), None)
        self.overrides = overrides
        self._save()
        return self.label(int(slot))
