# draftsim — auction draft simulator (research harness)

Deterministic, headless auction-draft simulator for comparing strategy
archetypes. Programmed agents only (no LLM). Reuses the exported projections in
`../data/projections-2026.csv` and mirrors the auction rules from the TS
`domain/` layer.

Built in stages (each with its own tests + a review gate):

1. **Scaffold + valuation** — load players, value models (market $PROJ / VORP).
2. **Auction + roster rules** (ported from `domain/`).
3. **Deterministic engine + a basic agent.** ← current
4. Archetypes + behavior tests.
5. Metrics + batch analysis.

## Setup

```bash
cd sim
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
python -m pytest                    # tests
python -m draftsim.inspect          # Stage 1 sanity: top players by each value model
python scripts/simulate.py --seed 1 # Stage 3: run one draft; rerun -> byte-identical
```

## How a draft runs (Stage 3)

`engine.run_draft(agents, config, players)` plays a nomination-style auction:
each round one manager (fixed seat rotation, skipping full rosters) nominates a
player, every manager with room submits a **sealed** bid, highest wins and pays
it (first-price, per `domain/auction.ts`). The nominator opens at `MIN_BID`, so
every sale removes one player and the draft always terminates — when every roster
is full, or when every seat passes in a row. An agent returning `None` from
`nominate` passes to the next seat; it does not end the draft. It's a pure
function of its inputs — same agents + seed ⇒ identical `DraftResult`.

The one agent so far, `HeuristicAgent`, is a need-aware value bidder anchored on
market price with deterministic per-seat jitter. Its knobs, which Stage 4 turns
into archetypes:

| knob | default | effect |
| --- | --- | --- |
| `jitter_frac` | 0.15 | ± band on market price, so seats disagree |
| `depth_value_mult` | 0.4 | multiplier on non-need depth (pays 40%, not 40% off) |
| `shade` | 0.15 | fraction of value held back when bidding |
| `max_pick_share` | 0.5 | most of the legal ceiling one buy may consume |
| `vorp_weight` | 0.0 | blend points-above-replacement into the market anchor |

`shade` matters most. In a first-price sealed auction an agent bidding its full
value wins only when its own jitter ran highest, and pays exactly what it thought
the player was worth — the winner's curse. Unshaded, that spread starter points
across seats by ~751 at seed 1 (1198 to 1949), swamping any archetype difference
Stage 4 would introduce. Shading cuts it to ~300.

Two rails bound every bid: the budget **reserve** (`max_bid`/`can_bid`) and a
**slot rail** (never spend a slot on depth while an unmet starter need still
claims it). They guarantee a seat keeps the *room and money* to finish a legal
lineup — not that it wins the positions it needs. Legality is checked after the
fact by `engine.invariant_violations`.

**Not a bug:** managers spending nearly their whole budget, with ~40% of picks at
`$1`. Aggregate `$PROJ` across the drafted players is about the same as the
league's total budget, so a full spend with a long `$1` tail is the correct shape
for a 12-team `$200` auction. Stage 5 metrics should not chase it.
