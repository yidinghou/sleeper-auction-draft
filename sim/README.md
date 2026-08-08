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
python -m draftsim.live --draft-id <id> --user someone-else       # another account
```

Then open <http://127.0.0.1:8765>.

It runs in the foreground — `Ctrl-C` stops it. If a prior instance was
backgrounded (`&`) or its shell got closed, `--port 8765` is still taken and
the new one exits with `Address already in use`. Find and stop the old one
first:

```bash
lsof -i :8765             # PID listening on the board's port
kill <pid>                 # ask it to stop
```

Then re-run `python -m draftsim.live --draft-id <id>`.

`--user` takes a Sleeper username and finds that account's slot in the draft's
own `draft_order`, so the card is marked and the money band's dashed line is
your own ceiling. **It defaults to `yidinghou`** (`live.DEFAULT_USER`) — one
person runs this, and a board that comes up anonymous looks exactly like a bug.
`--user ""` opts out.

The band always says which of these it is, because they used to look identical:
seated (`yidinghou · S7 — …`), no username, no such account, or a draft that
publishes no order yet. The header names the draft too (`L13 · …854144`), since
a finished mock and tonight's league draft otherwise render the same picture —
which is exactly how an id from the wrong tab goes unnoticed.

### Who is in each seat

The other eleven managers are named too, from `GET /league/{league_id}/users` —
their **team name** if they set one, their account name otherwise. Rescanned
every five minutes, so a team renamed mid-draft catches up on its own.

Every seat keeps its **slot number** beside the name: Sleeper's own board is
numbered, this one can be dragged out of seat order, and `S5` is what the two
have in common. Where twelve seats sit shoulder to shoulder — the money band's
tags, the run-pressure tiles, the log's buyer column — the name is cut to its
first word, and the tooltip has the whole of it. Maximize for the full name on
every card.

**Right-click any card or pressure tile to name a seat by hand.** Enter saves,
an empty box clears back to whatever the league says. This is for the two cases
the scan cannot answer:

* **a mock**, which publishes `league_id: null` — there is no league to read, so
  every rehearsal starts with twelve numbered seats and stays that way unless
  you type the names;
* **a real draft** the scan got wrong, or that moved somebody after it ran.

Hand-typed names are the only thing the board writes to disk, at
`data/seat-names-<draft_id>.json`. Per draft rather than per league: slots
belong to the draft, and last year's sat the same twelve people in different
chairs. Scanned names are never written there — a team renamed on Sleeper must
not find last season's name frozen in a file the board then trusts over the API.

The screen splits in two: **left** is the draft's live state — what the room has
spent, every seat's buying power, what's on the block — and **right** is all 12 rosters
in **one viewport height**, nothing to scroll mid-auction. Splitting it this way
gives the grid the full height instead of sharing it with the chrome. Space
under the left header is free. **Maximize** (or `Esc` to close) expands the
rosters to a
full-screen overlay with the bye/points columns revealed; it is a CSS toggle
over the same markup, so opening it costs no fetch and can never show a
different moment of the draft than the compact view.

### The live band: two panels, and who is bidding

The top band runs **across**, not down — **on the block** on the left two fifths,
**buying power** on the right three. Both halves used to take the column's full
~930px for a name and a price on one and twelve columns on the other, and the
band was as tall as the two of them stacked.

It is also pinned to **20% of the column** (`.band-live { flex: 0 0 20% }`)
rather than sized to its content, which is how it had grown to a third of the
screen answering a question you glance at. Three things follow from the pin:

* the chart's bars are drawn as **percentages, not pixels** — the plot takes
  whatever is left after the legend, the tags and your standing line, a height
  the server cannot know and no longer needs to (`_LEDGER_H` is now `100.0`);
* **what the room has spent moved into the band header**, next to the draft
  label. One line about the whole draft is what a header is for, and in the body
  it was thirty pixels off the chart;
* the bid panel is three rows, with the price and the bidder chips sharing the
  last one — the filled chip *is* the seat holding that price.

**Every seat in the bidding lights up amber** — in the money chart's column and
tag, and on that seat's four run-pressure tiles. Pale for a seat that has been in
front at some point on this lot, solid for whoever holds the offer right now.
The chart says who *can* outbid you; the amber says who *is*.

A caveat worth knowing, because it is the API's and not the board's: Sleeper
publishes the bid **on the clock** and no history — one player, one price, one
seat. So "bid by" is the board's own memory of every seat it has *seen* holding
the offer since the lot opened, and a seat that raises and is outbid between two
polls was never visible to it at all. That is why the poll drops to **1s while a
player is on the block** (`live.LIVE_INTERVAL`) and goes back to `--interval`
between lots: the picks are still there in an hour, a bid that was outbid is not.
`--interval` faster than 1s is left alone.

**The live board is light; the post-mortem report is dark.** That is deliberate:
the board sits open beside Sleeper's own dark app for three hours, and looking
nothing like it is how you never misread one for the other mid-auction. The two
palettes live side by side in `theme.py` (`BASE_CSS` / `POS_COLOR` dark,
`BASE_CSS_LIGHT` / `POS_COLOR_LIGHT` light) and each surface picks one — the
report is a separate page with its own dark CSS and is not part of the switch.

`POS_COLOR_LIGHT` is a different set of hues from the app-mirroring `POS_COLOR`,
because the app's were chosen to glow on near-black and go acidic when mixed
down to a pale tint on white.

The grid is **four cards across, three down**, in both sizes — a row per player
wants height, and maximizing keeps the shape so a card is where it was, only
bigger. Lineup rows **flex to share the card's height** rather than taking a
fixed one: a row height tall enough to fill a 1000px window scrolled an 820px
one, so the line-height is only a floor.

Type scales off one CSS variable, `--fs` (currently 1.17). Raising it eats
vertical room, so re-check that a full lineup still clears the fold.

Compact names shorten to initial + surname (`J. Burrow`), which is what stops
them ellipsising; defenses use their nickname, since `K. City Chiefs` helps
nobody. Maximized restores full names and labels the bye / points / price
columns — fixed-width and tabular, so they read down.

**The card header is money and nothing else**: dollars left in the biggest type
on the card, the max bid beside it, and a bar of the two — green for what is
spendable, grey for the dollar-per-open-slot that is in the account but already
owed. A seat whose max bid can no longer win anyone turns red. Points and slot
counts used to sit here; they were read occasionally and competed with the two
numbers read constantly, so they moved into NEED.

Each card carries **three panes over the same roster**, picked by the segmented
control in its header. All three ship in the markup and CSS shows one, so
switching costs no fetch and two panes can never show different moments of the
draft:

| Pane | Shows |
| --- | --- |
| `LINEUP` | Every starting slot, one player per line, in the slot they'd **actually start in** — via the same `roster.display_slots` matching the engine scores on, so a second RB shows up in FLEX. Unfilled slots stay visible; the hole is the point. |
| `BN` | The bench, each row tinted by the player's **real position** rather than labelled a fungible `BN` — in a pane of its own nothing else says what these bodies are. Dimmed a step, so depth still reads as depth. |
| `NEED` | Position targets, plus a pace line (`slots left`, `$/slot`) — the balance alone doesn't say whether a seat is ahead or behind. |

Position is **the pale tint of the row itself**, not a coloured chip: a 26% mix
of the position colour against white, with the slot label left plain grey. A
coloured chip put the loudest thing in the row right beside the thing you
actually read; this way a row reads as "a receiver" at a glance and nothing
competes with the name. An unfilled slot is flat grey instead — a hole should
read as absence, not as a position.

`NEED` targets are **fractional** (`live_state.DRAFT_TARGETS`: QB 3, RB 2.5,
WR 3.5, TE 1), because a flex slot is genuinely shared and rounding 2.5 up to 3
made a filled seat look short. These are a draft *plan*, deliberately not
`config.starter_shares()` (QB 2.00, RB 2.77, WR 3.23) — that is the structural
split and the right input to replacement level; this is what you mean to buy. So
`position_summary` and `seat.needs` disagree on purpose: legality versus plan.

The indicator is a run of **pips**, not a fill bar: one pip per whole starter
wanted, the half-slot drawn half as wide, so the row's own length *is* the
requirement. A ratio bar pinned at 100% the moment you reached the target, which
drew `4/2.5` and `2.5/2.5` identically when they mean opposite things — surplus
now sits past the target as narrow outlined pips.

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
  `picked_by`, so the slot number is the only identifier the pick feed carries.
  `draft_order` is the one exception and the only thing `--user` uses: a mock
  does publish it for the human in it, but a draft that has not been seated has
  it as `null`, so resolving a username to a seat is always allowed to fail.
- **Polling is cheap.** Only the small `/draft` endpoint is hit each tick
  (default 3s, ~20 requests/min); the much larger pick feed is refetched only
  when `draft_pulse` changes. A failed poll leaves the last good board up with a
  visible warning rather than blanking.
- **The money band is `max_bid`, not balance.** A seat holding eight open slots
  owes a dollar of its balance to each, so balance overstates it — and the
  auction is decided on what can go on one player. Ranked, top three annotated;
  the point of the chart is the nine you don't have to read.
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
