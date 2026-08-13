# liveboard — live board for a real Sleeper draft

Points `draftsim`'s valuation at a **real Sleeper draft**: what's on the block
right now, and where all twelve rosters stand. Depends on `draftsim` (see
`../draftsim/README.md`) so a mock rehearsal and the real draft are valued
identically.

## Setup

```bash
cd python/draftsim && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
cd ../liveboard && pip install -e ".[dev]"   # into the same venv
```

## Run

```bash
python -m pytest                                                   # tests
python -m liveboard.live --draft-id 1387809050569240576            # a mock
python -m liveboard.live --draft-id 1387809050569240576 --replay 60  # rehearse mid-draft
python -m liveboard.live --draft-id 1387810431371853824            # the real thing
python -m liveboard.live --draft-id <id> --user someone-else       # another account
```

Then open <http://127.0.0.1:8765>.

It runs in the foreground — `Ctrl-C` stops it. If a prior instance was
backgrounded (`&`) or its shell got closed, `--port 8765` is still taken and
the new one exits with `Address already in use`. Stop whatever holds the port
and start again in one line:

```bash
kill $(lsof -ti :8765) 2>/dev/null; python -m liveboard.live --draft-id <id>
```

The `2>/dev/null` is for the ordinary case where nothing is listening: `kill`
gets no arguments and says so, which is not a failure worth reading.

To look before killing — worth doing if the port might be holding something
that isn't a board:

```bash
lsof -i :8765             # PID listening on the board's port
kill <pid>                 # ask it to stop
```

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

## Who is in each seat

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

Hand-typed names are one of two things the board writes to disk, at
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

## The live band: two panels, and who is bidding

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
* the bid panel is two rows — identity and the two figures on top, and under
  them the **bid timeline**: every raise on this lot as its own card, seat tag
  over dollar figure, oldest on the left, scrolling sideways as the bidding
  runs on. The filled card at the right-hand end *is* the seat holding the
  price above it; on a settled lot the strip ends green at what it went for.
  This replaced a chip-per-seat row plus an arrow-joined trail under it: both
  were runs of text on the axis the panel has least of, and both gave up
  exactly when a lot got interesting — the chips clipped the late arrivals, the
  trail ellipsised the *recent* raises off its right end. The timeline also
  keeps the repetition the chips collapsed: a seat that leads, is outbid, and
  leads again gets a card each time.

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

Which is why it is the second thing written to disk. Each lot's trail goes into
`data/bid-log-<draft_id>.json` as the lot closes, and is read back on start —
otherwise rewinding a finished draft in a fresh run showed every lot as the one
rung the pick feed can vouch for, its price, and the whole record of how the
room got there died with the process that watched it. A lot no run of the board
was up for still shows only that single rung: the file holds what was witnessed
and never guesses. Where two runs both saw a lot, the longer trail wins.

**The live board is light; the post-mortem report is dark.** That is deliberate:
the board sits open beside Sleeper's own dark app for three hours, and looking
nothing like it is how you never misread one for the other mid-auction. The two
palettes live side by side in `draftsim`'s `theme.py` (`BASE_CSS` / `POS_COLOR`
dark, `BASE_CSS_LIGHT` / `POS_COLOR_LIGHT` light) and each surface picks one —
the report is a separate page with its own dark CSS and is not part of the
switch.

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
owed. A seat whose max bid can no longer win anyone turns red.

Each card carries **two panes over the same roster**, picked by the segmented
control in its header. Both ship in the markup and CSS shows one, so
switching costs no fetch and the two panes can never show different moments of
the draft:

| Pane | Shows |
| --- | --- |
| `LINEUP` | Every starting slot, one player per line, in the slot they'd **actually start in** — via `draftsim.roster.display_slots`, the same matching the engine scores on, so a second RB shows up in FLEX. Unfilled slots stay visible; the hole is the point. |
| `BN` | The bench, each row tinted by the player's **real position** rather than labelled a fungible `BN` — in a pane of its own nothing else says what these bodies are. Dimmed a step, so depth still reads as depth. |

Position is **the pale tint of the row itself**, not a coloured chip: a 26% mix
of the position colour against white, with the slot label left plain grey. A
coloured chip put the loudest thing in the row right beside the thing you
actually read; this way a row reads as "a receiver" at a glance and nothing
competes with the name. An unfilled slot is flat grey instead — a hole should
read as absence, not as a position.

Folding a card's row (double-click the row bar) swaps it for a **position
summary strip** instead of hiding it outright: which positions a seat has
filled, and where it's carrying depth, read off `live_state.DRAFT_TARGETS`
(fractional — QB 3, RB 2.5, WR 3.5, TE 1 — because a flex slot is genuinely
shared and rounding 2.5 up to 3 made a filled seat look short) against
`draftsim.config.owned_starters()`. Those targets are a draft *plan*,
deliberately not `config.starter_shares()` (QB 2.00, RB 2.77, WR 3.23) — that
is the structural split and the right input to replacement level; this is what
you mean to buy.

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
  useless in. It replays *picks*, not bidding: the pick feed carries the price
  and nothing else, so a lot's trail comes back only if some run of the board
  watched it and left it in `data/bid-log-<draft_id>.json`.
- Picks resolve against the *full* sheet while the pool stays filtered, so a $1
  dart at an unsigned free agent keeps its name and projection. Requires the
  `player_id` column — re-run `npm run export:projections` if the board warns
  about players missing from the CSV.
