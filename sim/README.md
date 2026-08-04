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
python -m draftsim.live --draft-id <id>   # live board for a real Sleeper draft
```

## Live draft board

Points the same valuation at a **real Sleeper draft**: what's on the block right
now, and where all twelve rosters stand.

```bash
python -m draftsim.live --draft-id 1387809050569240576            # a mock
python -m draftsim.live --draft-id 1387809050569240576 --replay 60  # rehearse mid-draft
python -m draftsim.live --draft-id 1387810431371853824            # the real thing
```

Then open <http://127.0.0.1:8765> and pick your seat from the dropdown.

The screen splits in two: **left** is the draft's live state — what's on the
block, the current high bid, your seat picker — and **right** is all 12 rosters
in **one viewport height**, nothing to scroll mid-auction. Splitting it this way
gives the grid the full height instead of sharing it with the chrome. Space
under the left header is free. **Maximize** (or `Esc` to close) expands the
rosters to a
full-screen overlay with the bye/points columns revealed; it is a CSS toggle
over the same markup, so opening it costs no fetch and can never show a
different moment of the draft than the compact view.

Bench players show under the starters, **dimmed and a size smaller** — depth is
worth seeing, but the starting lineup is what the eye should land on first.
Maximized keeps one size, since there the point is reading the whole roster
rather than ranking it.

Type scales off one CSS variable, `--fs` (currently 1.1). Raising it eats
vertical room, so re-check that a full 16-man roster still clears the fold.

Compact names shorten to initial + surname (`J. Burrow`), which is what stops
them ellipsising at 115px; defenses use their nickname, since `K. City Chiefs`
helps nobody. Maximized restores full names, widens to 3 columns, and labels the
bye / points / price columns — fixed-width and tabular, so they read down.

Each card compresses the 10 starter slots into 5 rows of two:

| Row | | |
| --- | --- | --- |
| 1 | QB | SFLX |
| 2 | RB | WR |
| 3 | RB | WR |
| 4 | FLEX | W/T |
| 5 | DEF | TE |

Position shows as the color of the **slot chip** rather than a badge — a badge
costs more room than the name it labels, and tinting the whole cell made twelve
cards read as a wall of blocks. The chip names the slot while carrying the
player's colour, so a receiver in the flex reads `FLEX` in receiver blue.
Players sit in the slot they'd
**actually start in**, via the same `roster.display_slots` matching the engine
scores on, so a second RB shows up in FLEX. The card header totals the starting
lineup only, plus how much bench is still open.

Everything comes from two unauthenticated endpoints — no scraping, no websocket,
no session token:

| Endpoint | Carries |
| --- | --- |
| `GET /draft/{id}` | League settings, and in `metadata` the *in-flight* auction: `nominated_player_id`, `nominating_slot`, `highest_offer`, `offering_slot`. |
| `GET /draft/{id}/picks` | Settled picks, each with `metadata.amount` (price paid), `player_id`, `draft_slot`. |

Notes:

- **The config comes from the draft**, not from `DraftConfig`'s defaults, so a
  mock is valued exactly as the real draft is. An unmodelled roster slot is a
  hard error — silently dropping one would understate every roster and so
  inflate every max bid.
- **Seats are keyed by `draft_slot`**, not by user. Mock drafts return an empty
  `picked_by`, and `draft_order` is `null` until a draft starts, so the slot
  number is the only identifier that works in both.
- **Polling is cheap.** Only the small `/draft` endpoint is hit each tick
  (default 3s, ~20 requests/min); the much larger pick feed is refetched only
  when `draft_pulse` changes. A failed poll leaves the last good board up with a
  visible warning rather than blanking.
- **`--replay N` rewinds a finished mock** to N picks. A completed draft shows
  every seat broke and every need met, which is the one state the board is
  useless in.
- Picks resolve against the *full* sheet while the pool stays filtered, so a $1
  dart at an unsigned free agent keeps its name and projection. Requires the
  `player_id` column — re-run `npm run export:projections` if the board warns
  about players missing from the CSV.

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
| `max_pick_share` | 0.5 | most of the legal ceiling one buy may consume |
| `vorp_weight` | 0.5 | blend marginal VORP into the market anchor |

`max_pick_share` matters most. Uncapped, one seat can sink half its budget into a
single blowout buy whenever its jitter runs hot, and final rosters become a
function of the RNG rather than of the parameters: starter points spread ~764
across seeds 1/2/3/7. The cap collapses that to ~251, which is what leaves room
for a Stage-4 archetype difference to show up at all.

**Agents bid their full value — there is no shading.** A 15% holdback was tried
and removed. Measured per knob across seeds 1/2/3/7:

| | mean starter-point spread | max unspent |
| --- | --- | --- |
| shade 0, cap off | 764 | $0–10 |
| shade .15, cap off | 517 | $84–92 |
| **shade 0, cap .5** (today) | **251** | **$3–4** |
| shade .15, cap .5 | 306 | $40–92 |

Shading was credited with the 764 → ~300 improvement, but it shipped in the same
commit as the cap and the cap did the work; on top of the cap, shading makes the
spread *worse*. It also stranded budget — a uniform discount means the field
under-bids the board, so seats ran out of slots holding $40–$92.

The winner's curse it was meant to fix is small here: with 12 seats drawing ±15%
jitter the top two valuations are almost always within a dollar, so paying your
own bid costs about $1 per player over paying the runner-up's (league-wide spend
under a second-price rule is 2380 vs 2381). That is also why first-price with
full-value bidding is a fair stand-in for the ascending auction Sleeper really
runs. If a genuine open-outcry model is wanted later, `resolve_auction_round` is
the single seam to change.

Two rails bound every bid: the budget **reserve** (`max_bid`/`can_bid`) and a
**slot rail** (never spend a slot on depth while an unmet starter need still
claims it). They guarantee a seat keeps the *room and money* to finish a legal
lineup — not that it wins the positions it needs. Legality is checked after the
fact by `engine.invariant_violations`.

## The player pool

`load_players()` returns **draftable** players only — 1038 of the CSV's 3223
rows. The other 2185 have an empty `team`: retired or unsigned players (Stefon
Diggs, Joe Mixon, Najee Harris) who cannot score for anyone this season. Keeping
them made them two thirds of the pool, where they crowded out real players in
every tie broken below the `$1` price floor. Pass `free_agents=True` for the raw
sheet.

Nothing draftable is lost — the highest `$PROJ` among free agents is `$1`. VORP
replacement levels are unchanged too, since replacement is the Nth-best at a
position (36th WR, and so on) and the top N everywhere are all rostered.

## How nominations are ordered

By **whole dollars → points → Sleeper rank** (`HeuristicAgent._nomination_key`).
The quantization to whole dollars is the load-bearing part. Only ~126 draftable
players are priced above `$1`; the rest all anchor at exactly `$1`, so ordering
by raw valuation let the per-player jitter pick every nomination once the priced
board sold out — the back half of the draft was an RNG lottery among
interchangeable bodies, and a 0-point player could outrank a 150-point starter.
Rounding collapses that tail into one tie, which points then breaks.

With the free-agent filter, this cleared the dead weight completely: at seed 2,
picks spent on a free agent or a 0-point player went **46 → 0**, and the median
`$1` buy went from **0 to 109 projected points**. The worst player drafted now
projects 13.6.

## Why nobody drafts two defenses

No rule says so, and no position is named anywhere in the agent. Value is a blend
(`vorp_weight`, 0.5) of Sleeper's `$PROJ` and **marginal VORP** — the points a
player would add to *this seat's* optimal starting lineup, over a
replacement-level body in the same slot.

Marginal VORP is roster-relative, which is the whole trick. On an empty roster it
equals classic `vorp_value` exactly (RB Bijan 164.1, QB Allen 130.0, WR Nacua
113.4, TE McBride 65.8, **DEF LA Rams 19.0**, K 0.0). But there is one DEF slot
and no flex accepts DEF, so a seat that owns a defense gets **0** for another —
it cannot enter the lineup. Own a *weak* defense and a good one is still worth
+19, which a structural slot cap gets wrong.

`roster.marginal_thresholds()` computes, per position, the points a player must
clear to improve the lineup; marginal value is then a subtraction. That is what
makes it affordable — scoring a 1000-player pool costs one matching per position
rather than one per candidate. `tests/test_roster.py` checks the shortcut against
recomputing the lineup directly.

**Bench ordering.** Past roughly pick 120 nothing improves anyone's lineup — at a
realistic 10-starter roster only ~60 players league-wide still have positive
marginal — so something else has to choose. `_insurance` ranks by how much use a
body could ever be: startable slots × points as a share of replacement.

```
WR  Chris Bell       5 slots × (74.7/145.6) = 2.57
DEF Green Bay        1 slot  × (86.0/ 87.0) = 0.99
K   Brandon Aubrey   0 slots                = 0.00
```

Note it uses points *relative to* replacement, not VORP: VORP floors at zero,
which is exactly where every bench candidate sits, so it would tie them all at
0.0. Nothing is filtered out — defenses and kickers lose on merit.

This matters most on the **nomination** side. The engine forces a nominator to
open at `MIN_BID`, so a seat that nominates something it would never bid on ends
up buying it — which is exactly how spare defenses were landing on rosters, DEF
being the only cheap position still carrying a `$PROJ` (kickers carry none).

Across seeds 1/2/3/7: no seat holds more than one defense or any kicker, 7–10 of
12 seats build distinct positional shapes, starter-point spread is 67–105, and
seats finish within $1–2 of broke.

**One intended side effect:** the pure-`$1` tail is now identical across seeds,
since jitter no longer influences it. Early and mid draft still diverge normally.
Less RNG in the bench is the point — it's noise Stage 5 would have to average out.

**Not a bug:** managers spending nearly their whole budget, with ~40% of picks at
`$1`. Aggregate `$PROJ` across the drafted players is about the same as the
league's total budget, so a full spend with a long `$1` tail is the correct shape
for a 12-team `$200` auction. Stage 5 metrics should not chase it. What the tail
should *not* contain is 0-point players; that's the defect above, now fixed.
