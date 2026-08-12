"""A team is identity only.

Every number about a team -- roster, spend, budget remaining, best lineup --
lives in its `TeamState`, which is derived from the ledger. Putting any of it
here would mean maintaining it on every pick, and a field that must be
maintained is a field that can be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Team:
    id: str
    name: str
