# draftsim — a fantasy football auction draft as a ledger

A draft is a ledger of sales, a cache built from that ledger, and an auction that
appends to it. Everything else is computed.

Stdlib only. Reads the exported projections in `../../data/projections-2026.csv`
and mirrors the roster rules from the TS `domain/` layer.

## Setup

```bash
cd python/draftsim
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

## Use

```python
from draftsim import Draft, DraftRules, Team, load_players, load_projections
from draftsim.evaluation import dollars_per_point, marginal_points, max_sensible_bid

players = {p.id: p for p in load_players()}
proj = load_projections()
rules = DraftRules()                       # $200, 16 slots, $1 minimum, 12 teams

draft = Draft(
    rules=rules,
    players=players,
    teams=[Team(f"t{i}", f"Team {i}") for i in range(12)],
    proj=proj,
)

draft.nominator().id                       # 't0' -- read off the ledger
draft.record_pick("t0", "ja'marr-chase|wr|cin", 61, at=0.0)

ts = draft.team_state("t0")
ts.remaining(rules)                        # 139
ts.max_bid(rules)                          # 125 -- $1 reserved per unfilled slot

rate = dollars_per_point(players.values(), rules, proj)
marginal_points(draft.state, "t0", some_player)          # what he adds to YOUR lineup
max_sensible_bid(draft.state, "t0", some_player, rate)   # and what that's worth

draft.undo()                               # pop the last sale, rebuild the cache
```

## The model

| Entity      | Role                                                    |
| ----------- | ------------------------------------------------------- |
| `Pick`      | The only record that a sale happened. Frozen, append-only. |
| `Draft`     | Owns the ledger. The only writer in the system.         |
| `Player`    | Facts about a football player. Knows nothing about this draft. |
| `Team`      | Identity only — `id` and `name`.                        |
| `TeamState` | The cache: roster, spend, best lineup. Rebuildable from picks. |
| `Lot`/`Bid` | A live auction in progress. Never persisted.            |

Modules layer downward, each importing only from the ones above:
`rules` → `player`/`team`/`ledger` → `lineup` → `state` → `draft` → `evaluation`.

## Design decisions

**The ledger is the only truth.** A pick records one fact — this team bought this
player for this price — and everything else derives from it. There is no
`drafted` flag, no `drafted_to` pointer, no stored `remaining_budget`. Those
would be four copies of one fact, and keeping four copies in agreement is where
draft apps go wrong.

**The cache is disposable.** `DraftState` is a pure function of the ledger,
updated at exactly one call site (`Draft.record_pick`) and rebuildable at any
time. `rebuild()` is written independently of `apply()` — it groups and sorts the
whole ledger from scratch rather than replaying picks — so that
`assert_matches()` proves them equal. If rebuild were a loop over apply, the
property test would be a tautology and the whole scheme decoration. Undo pops and
rebuilds for the same reason: an inverse operation is code that can rot for no
benefit.

**Bids never enter the ledger.** A losing bid is a fact about a moment, not about
the draft. Nothing derives from it and no invariant depends on it. Storing bids
alongside picks would force every calculation to filter for "the winning one".

**Value is marginal, not absolute.** A player is worth what he adds to your
*starting* lineup — a fourth elite tight end scores zero for you (TE, FLEX,
REC_FLEX and SUPER_FLEX are already his) and a lot for the team with none.
Combined with `max_bid()`, that gives a bidder the two numbers they need:

```
max_bid  = remaining - (open_slots - 1) * min_bid
sensible = min(max_bid, min_bid + marginal_points * dollars_per_point)
```

**One lot at a time.** Money is either uncommitted or spent, never in between,
which is what keeps `max_bid()` a one-liner. Parallel lots would mean a team
leading three auctions has committed money the ledger hasn't seen.

**Lineups come from a matching, not a greedy fill.** `lineup.best_lineup` is a
max-weight bipartite matching (Kuhn augmenting paths over the transversal
matroid), so the result is optimal and independent of acquisition order. That is
load-bearing, not tidiness: `apply()` and `rebuild()` see players in different
orders, and a greedy fill would make them disagree by construction.

## Tests

```bash
python -m pytest -q      # 110 tests
```

The centrepiece is `tests/test_state.py`: apply a random legal sequence of picks
across 50 seeds, then assert the cache equals a fresh rebuild field by field.
That single property covers every cached field at once. `test_player.py` reads
the real CSV, so it also catches the export changing shape.

## Not here

No auction loop, no bidding agents, no CLI or batch tooling — `Lot` and `Bid`
exist so an auction layer has somewhere to land, but nothing drives them yet.

`../liveboard` still imports the previous draftsim API and does not currently
import cleanly against this one.
