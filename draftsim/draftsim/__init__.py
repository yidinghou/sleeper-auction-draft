"""Deterministic auction fantasy-draft simulator (research harness).

Built in stages; see sim/README.md. The core (config, valuation, auction,
roster, engine, agents) is stdlib-only so it stays fast and trivially testable.
Only the Stage-5 batch tooling reaches for pandas.
"""
