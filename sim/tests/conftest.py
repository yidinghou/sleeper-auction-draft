"""Suite-wide guard: nothing here writes to the repo's real `data/`.

The board persists two things — hand-typed seat names and the bid history of
every lot it watched — and both are keyed by draft id, not by "real" or "test".
A suite that used the default directories would leave `mock-id`'s leavings in
`data/` for the next real draft to inherit, and a poller seeded from a stale
sidecar fails in the least obvious way there is: with plausible extra data.

Autouse rather than per-fixture, because the failure mode is a test that
*forgets*, and every such test passes locally on the first run and then poisons
the next one.
"""

import pytest

from draftsim import bid_log as bid_log_mod
from draftsim import seat_names as names_mod


@pytest.fixture(autouse=True)
def _isolate_board_state(monkeypatch, tmp_path):
    monkeypatch.setattr(names_mod, "NAMES_DIR", tmp_path)
    monkeypatch.setattr(bid_log_mod, "BIDS_DIR", tmp_path)
