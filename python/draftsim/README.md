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
from draftsim.evaluation import Market, marginal_points, max_sensible_bid

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

market = Market.of(draft.state)             # rebuild after each pick; prices move
marginal_points(draft.state, "t0", some_player)            # adds to YOUR lineup
max_sensible_bid(draft.state, "t0", some_player, market)   # and what that's worth

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
sensible = min(max_bid, min_bid + value_over_replacement * dollars_per_point)
```

**Marginal to a replacement body, not to an empty slot.** You are never going to
field an empty slot, so comparing a player to one overstates him — against an
empty roster it makes him worth his entire projection. The baseline is the
freely-available body you would otherwise start, which is also the only baseline
that squares with `dollars_per_point`: that rate is dollars per point *above
replacement*, so the points figure has to be too. Feeding it absolute lineup
improvement priced this league's 192 drafted players at $13,472 against $2,400 of
real money. `marginal_points` takes the baseline as an argument and defaults to
zero, because "how much better is my lineup with him" is still worth asking on a
roster screen — it just isn't a price.

**Prices move, because supply and demand do.** `Market.of(state)` recomputes
replacement level and the exchange rate from the ledger, so both track the draft:
only available players are ranked, and the bar sits at the starting need *still
unfilled* league-wide. Once nine of twelve teams have their tight end, the bar
drops to the third-best one left — which is exactly why a mediocre tight end is
worth something to the three teams still short. A league that blows its budget
early leaves everyone bidding into a cheaper market; a disciplined early market
makes the back half dear. Remaining need is counted from unfilled *slots* rather
than by subtracting starters from shares, because a team that started four
running backs has spent the superflex its quarterback share was counting on.

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
python -m pytest -q      # 123 tests
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
