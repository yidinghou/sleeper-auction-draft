"""What the room bid, kept past the process that watched it.

Sleeper publishes the offer on the clock and no history — so who bid, in what
order, and how far each of them pushed it is not a fact the board can look up.
It is a fact the board *witnessed*, once, and only if a poll happened to land
while that seat was in front. Held in memory, that witness died with the
process: rewind a finished draft in a fresh run and every lot collapsed to the
one rung the pick feed can vouch for, its price.

This is that memory written down. One file per draft
(``data/bid-log-<draft_id>.json``), a lot appended as it closes, read back on
start. The board still only ever knows what some run of it saw — this just
stops each run from being the last one to know.

The failure posture is `seat_names.py`'s, and for the same reason: a corrupt or
unreadable file is worth exactly the history and must never be worth the board
on draft night.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .valuation import REPO_ROOT

BIDS_DIR = REPO_ROOT / "data"

# One lot's witness, in the two shapes the renderer wants: who was in at what
# ceiling, and in what order the raises landed. `None` for an amount means seen
# in the bidding with no figure attached — the nominator, before anyone raised.
Bidders = Dict[int, Optional[int]]
Events = List[Tuple[int, Optional[int]]]


class BidLog:
    """Every lot this draft has closed, and how it got there.

    Written from the poll thread as lots close and read from request handlers
    building a checkpoint, so the map is replaced wholesale rather than mutated:
    a reader holding the old dict sees a consistent older answer instead of a
    half-written one. Same discipline as `SeatNames`.
    """

    def __init__(self, draft_id: str, directory: Optional[Path] = None):
        self.draft_id = str(draft_id)
        self.path = (directory or BIDS_DIR) / f"bid-log-{self.draft_id}.json"
        self.lots: Dict[str, Tuple[Bidders, Events]] = self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> Dict[str, Tuple[Bidders, Events]]:
        """Every lot on disk, skipping any that does not parse.

        A lot at a time rather than all-or-nothing: one malformed entry is a
        bug in one lot's witness, and throwing away the other hundred with it
        would turn a small defect into the whole reason this file exists.
        """
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        stored = raw.get("lots") if isinstance(raw, dict) else None
        if not isinstance(stored, dict):
            return {}
        out: Dict[str, Tuple[Bidders, Events]] = {}
        for player_id, lot in stored.items():
            if not isinstance(lot, dict):
                continue
            bidders = _read_bidders(lot.get("bidders"))
            events = _read_events(lot.get("events"))
            if bidders or events:
                out[str(player_id)] = (bidders, events)
        return out

    def _save(self) -> None:
        """Write via a temp file in the same directory and rename over the top:
        this happens mid-auction with the board being read, and a truncated file
        picked up on restart would lose every lot rather than the one closing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {
                "draft_id": self.draft_id,
                "lots": {
                    player_id: {
                        "bidders": {
                            str(slot): amount
                            for slot, amount in sorted(bidders.items())
                        },
                        "events": [list(event) for event in events],
                    }
                    for player_id, (bidders, events) in sorted(self.lots.items())
                },
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

    def get(self, player_id: Optional[str]) -> Tuple[Bidders, Events]:
        """This lot's witness, or two empties. Empty is the honest answer for a
        lot no run of the board ever watched, and the renderer already draws it
        as the single sold rung."""
        if player_id is None:
            return {}, []
        bidders, events = self.lots.get(str(player_id), ({}, []))
        return dict(bidders), list(events)

    def put(
        self,
        player_id: str,
        bidders: Mapping[int, Optional[int]],
        events: Sequence[Tuple[int, Optional[int]]],
    ) -> None:
        """Record a closed lot, if this is the best account of it there is.

        Two ways a write can know less than the file already does, and neither
        may erase it. A run that watched no bidding at all — it started after
        the lot closed, or the pulse never moved while the lot was up — writes
        nothing. A run that joined the lot late saw a *suffix* of it: restart
        the board mid-lot and it sees only whoever is in front now, which must
        not truncate the trail a longer-running witness already recorded.

        Longer wins, on both counts, rather than the two accounts being merged.
        Merging bidders would be easy and merging two orderings of raises would
        not, and half a merge — a full field of bidders under a partial trail —
        is a record that contradicts itself. One witness, the one that saw
        most.
        """
        if not bidders and not events:
            return
        held = self.lots.get(str(player_id))
        if held is not None:
            kept_bidders, kept_events = held
            if len(kept_events) >= len(events) and len(kept_bidders) >= len(bidders):
                return
        lots = dict(self.lots)
        lots[str(player_id)] = (
            {int(slot): amount for slot, amount in bidders.items()},
            [(int(slot), amount) for slot, amount in events],
        )
        self.lots = lots
        self._save()


def _read_bidders(raw: object) -> Bidders:
    """JSON object keys are strings; slots are ints everywhere else."""
    if not isinstance(raw, dict):
        return {}
    out: Bidders = {}
    for slot, amount in raw.items():
        try:
            out[int(slot)] = None if amount is None else int(amount)
        except (TypeError, ValueError):
            continue
    return out


def _read_events(raw: object) -> Events:
    """Tuples, not lists — `_bid_timeline` compares the last event against the
    settled `(slot, price)` to avoid drawing the closing rung twice, and a list
    would never match."""
    if not isinstance(raw, list):
        return []
    out: Events = []
    for event in raw:
        if not isinstance(event, (list, tuple)) or len(event) != 2:
            continue
        slot, amount = event
        try:
            out.append((int(slot), None if amount is None else int(amount)))
        except (TypeError, ValueError):
            continue
    return out
