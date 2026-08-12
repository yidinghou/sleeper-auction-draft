# What the draft simulator does

This is a specification of behavior, not of code. It names no function, no class,
and no file. It is written as a list of **claims** — things that are true about a
fantasy football auction draft, each of which a test could assert. Build the
module by turning these claims into tests, and the tests into code.

Every claim below must end up with a test. That is the completeness check.

Two things to hold onto while you read, because they explain most of the rest:

- **The record of sales is the only truth.** Everything else — who owns whom,
  what a seat has spent, what it can still bid, what its best lineup is — is
  computed from that record and can be thrown away.
- **A player is worth what he adds to *your starting lineup* over a
  freely-available body**, not what he scores, and not what he adds to an empty
  slot.

And one about the writing: **readability is the deliverable.** The tests should
read as sentences about football, and the code should read as the steps a person
would take. If a design is correct but a reader has to reverse-engineer it, it is
not finished.

---

## 1. The league

Twelve seats. Each has $200 and sixteen roster slots. The minimum bid is $1.

The sixteen slots, in template order:

```
QB  RB  RB  WR  WR  TE  FLEX  REC_FLEX  SUPER_FLEX  DEF
BN  BN  BN  BN  BN  BN
```

Ten starters, six bench. A concrete slot takes only its own position. The flex
slots take:

- **FLEX** — a running back, a receiver, or a tight end
- **SUPER_FLEX** — a quarterback, a running back, a receiver, or a tight end
- **REC_FLEX** — a receiver or a tight end

(A fourth flavour, **WRRB_FLEX**, takes a receiver or a running back. This league
doesn't use it, but the model should know it, because leagues that do exist.)

Claims:

- **A kicker can never start.** No slot in this template takes one, and no flex
  accepts one either. This has consequences all the way through the valuation, so
  it is worth stating as its own fact rather than as a consequence of the table.
- **A second defense can never start.** One concrete slot takes a defense and no
  flex does, so the second one is a body that cannot enter the lineup at any
  price.
- **Counting how many slots a position could ever occupy** — its own slot plus
  every flex that accepts it — gives: receivers 5, backs 4, tight ends 4,
  quarterbacks 2, defenses 1, kickers 0. That is the structural measure of how
  much a league plays a position. It is a ceiling on useful bodies, not a count
  of starters.
- **A league whose budget cannot afford a dollar per slot is not a league.** With
  sixteen slots at a $1 minimum, a budget under $16 would freeze a manager out of
  auctions under the reserve rule (§5), so it must be refused outright rather
  than allowed to fail later.
- A league with no seats, or no roster slots, or a minimum bid below a dollar, is
  likewise not a league.
- The league configuration is fixed for the duration of a draft and shared by
  everyone. Nothing about it knows a draft is in progress.

Positions arrive from a spreadsheet as strings — `QB`, `RB`, `WR`, `TE`, `K`,
`DEF` — and every comparison in this system is against one of those literals.
Treating them as strings rather than as a closed enumeration is a deliberate
choice; don't spend effort fighting it.

---

## 2. How much of each position the league wants

This is the input to everything about value, and it is the one place where the
model uses a *measured* number rather than a derived one.

A concrete slot wants exactly one player at its own position. A flex slot wants
one player too, but split across the positions that compete for it — and **they
do not compete equally.**

Measured over 96 filled lineups (eight simulated drafts, twelve seats each):

| Slot | Split |
| --- | --- |
| FLEX | 77% running back, 23% receiver |
| SUPER_FLEX | 100% quarterback |
| REC_FLEX | 100% receiver |
| WRRB_FLEX | 50/50 back and receiver (unused here, so unmeasured) |

The superflex went to a quarterback 97.9% of the time and the receiver flex to a
receiver every single time, so those round to whole. Only the ordinary flex is
genuinely contested.

**Why this matters enough to measure rather than assume**, and this must survive
into the code as a comment or docstring, because it is the kind of thing someone
will later "simplify": splitting every flex slot evenly across its eligible
positions instead would put quarterback replacement at the 15th quarterback
rather than the 24th — fifty points of difference — and tight end replacement at
the 25th tight end rather than the 12th. That is a factor of two, and it comes
from assuming a tight end is as likely to take the superflex as a quarterback.

If the lineup template ever changes, these get re-measured: run drafts, tally
which position filled each slot, normalise per slot.

Claims:

- **Per seat, the league wants** 2.00 quarterbacks, 2.77 running backs, 3.23
  receivers, 1.00 tight end, 1.00 defense — and 0 kickers. These are fractional
  on purpose; at twelve seats, a fifth of a slot is more than two players of
  movement in the rankings.
- **They always sum to the number of starting slots** — ten, here. Whatever the
  template, and however the flexes divide, the total is the slot count. This is
  worth its own test; it catches a mis-typed share table immediately.
- **Rounded to whole players — "how many should I buy?" — it reads** 2
  quarterbacks, 3 running backs, 3 receivers, 1 tight end, 1 defense — which
  fills exactly the ten starting slots.
- **Floored instead — "how many does the lineup seat outright?" — it reads** 2
  quarterbacks, 2 running backs, 3 receivers, 1 tight end, 1 defense, and *no*
  claim on the flex by either the backs or the receivers. These two questions are
  different and both get asked: rounding answers what to shop for, flooring
  answers how many bodies the template guarantees a home. A body past the floored
  count may well start; what it is not is a slot the roster was owed.

---

## 3. The board: reading players in

Players come from a spreadsheet with these columns:

```
player_id, player, position, team, sleeper_rank, bye_week,
sleeper_proj_dollar, season_pts_half_ppr,
week1_pts_half_ppr, week2_pts_half_ppr, week3_pts_half_ppr
```

`team` is an NFL team abbreviation — never a draft seat. `player_id` is the
external provider's key, used to match live picks coming off a real draft board.
`sleeper_proj_dollar` and `sleeper_rank` are what the *market* thinks, which is a
different kind of fact from what the player is, and the model should keep them
visibly apart from the football facts so that any valuation leaning on the market
has to say so out loud.

Claims:

- **A row with no name, or no position, is not a player.** Skip it.
- **A row with no NFL team is a free agent, and free agents are not draftable.**
  Skip them by default, with a way to ask for them. They are two thirds of the
  sheet — retired and unsigned players who cannot score for anyone this season.
  Nothing draftable is lost: the highest projected price among them is $1. But
  *keeping* them makes them the bulk of the pool, where they crowd out real
  players in any tie broken below the price floor.
- **A missing season projection is zero points.** A player with no forecast on a
  roster is worth nothing, which is the right answer and not an error.
- **A missing weekly projection is not zero — it is unknown.** A body with no
  early-week read is not the same as one projected for zero in that week, and the
  distinction must survive into the model. (The weekly columns exist because the
  spreadsheet sometimes predates them entirely.)
- **A rank or price cell may read empty or `-`; both mean unknown.**
- **A player's identity is derived from his name, position, and NFL team**, not
  from the provider's id — so a spreadsheet exported before the `player_id`
  column existed still loads and still joins. Identity is case- and
  spacing-insensitive.
- **Player identities are unique across the sheet.**
- **A player knows nothing about any draft.** No owned flag, no owner, no price
  paid. Those are answers the sales record already has, and a second copy of a
  fact is a chance for the two to disagree.
- **Projections live beside the player table, not inside it** — a mapping from
  player identity to season points. Re-running a draft against a different
  forecast should mean supplying different numbers, not rebuilding every player.
- **A player absent from the projections scores zero rather than raising.**
- There must also be a way to index players by the external provider's id, for
  matching live picks; players from a sheet without that column are simply absent
  from the index, and the caller falls back to name/position/team.

The real sheet lives at `data/projections-2026.csv` (about 3,200 rows, produced
by a separate export). A frozen 292-row slice is committed at
`tests/data/board-2026.csv` — top 60 per position, all 32 defenses, 20 kickers —
specifically so that re-exporting the real sheet can never move a test. The two
produce the same replacement bar and the same exchange rate.

Reading the *real* sheet in at least one test is deliberate: it makes the test
suite a schema check on the export.

---

## 4. The record of sales

**One sale records exactly one fact:** this seat bought this player, for this
price, at this moment. Nothing else.

Claims:

- **There is no owned flag, no owner stored on a player, and no stored remaining
  budget.** Those would be four copies of one fact, and keeping four copies in
  agreement is where draft applications go wrong. Whether a player is gone, what
  a seat has left, and what it can bid are all answered from the sales record.
- **The record is append-only.** A sale, once recorded, is not edited. Undoing
  means removing the last one (§7).
- **Losing bids never enter the record.** A losing bid is a fact about a moment,
  not about the draft; nothing derives from it and no rule depends on it. Storing
  bids alongside sales would force every later calculation to filter for "the
  winning one", which is the kind of condition that rots. If bid history is ever
  wanted, it belongs in a separate log that nothing reads from.
- **A live auction — the player on the block, who nominated him, the deadline,
  and the bids standing so far — exists between nomination and hammer and is then
  discarded.** The model should have somewhere for it to live, and a way to ask
  for the standing high bid (which is absent before the opening bid lands). But
  nothing in the valuation or the cache reads it. *(No auction loop is in scope;
  this is a landing pad, not a driver.)*
- **Only one lot is open at a time.** This is a real constraint, not an
  incidental one: it is what makes "what can this seat bid" a single subtraction
  (§5). Parallel lots would mean a seat leading three auctions has committed
  money the sales record hasn't seen, and every budget question would have to
  subtract it.

---

## 5. What a seat has, and what it can bid

Everything here is computed from the sales record.

Claims:

- **A seat's open slots** are its roster size less the number of players it has
  bought.
- **A seat's remaining money** is the budget less what it has spent.
- **A seat can bid everything it holds, minus a dollar reserved for every slot
  this purchase would not fill.** With $200, sixteen slots and nothing bought,
  that is $185 — the dollar for the slot being bid on is *not* reserved, because
  this bid fills it. Buy a player for $61 and it becomes $125.
- **A seat with a full roster can bid nothing.** Not "its remaining money" —
  nothing, because there is nowhere to put the player.
- The reserve rule is why §1 refuses a budget under a dollar per slot: otherwise
  a seat could be structurally unable to bid the minimum.
- **A seat carries the league it plays in.** It should not be possible to ask a
  seat what it can afford under rules it was not built with.

---

## 6. Whose turn it is

**Nomination rotates in seat order, and whose turn it is is read off the count of
completed sales.** Twelve seats and seven sales means the eighth seat nominates.

Claim: this must be *derived*, not tracked. A stored turn pointer is a second
copy of a fact the sales record already holds, and it can drift from what has
actually been sold.

---

## 7. What must be true for a sale to happen

There is **one gate**, and both a live bid and the hammer run it. That single
implementation is the point: a board can gray out exactly what a seat cannot
afford, using precisely the rules the sale will face, rather than a second
slightly-different copy of them.

A sale is refused when:

- the seat is not in this league
- the player is not on this board
- someone already bought him — and the refusal should say who
- the seat's roster is full
- the price is below the minimum bid
- the price is above what the seat can bid (§5) — and the refusal should say what
  the seat's ceiling is, what it holds, and how many slots it must still fill,
  because that is what a person needs to understand the number

Claims:

- **A refused sale leaves the record untouched.** Not partially applied, not
  applied and rolled back — untouched.
- Asking "would this be legal?" and attempting it must agree, always. They are
  the same check.
- **Undo removes the last sale and recomputes everything from what remains.** It
  does not invert the sale. The computed state is disposable, so an inverse
  operation would be extra code that can rot for no benefit — and recomputing
  once costs about what one draft's worth of incremental updates costs, total.
- Undo with nothing sold yet is not an error; there is simply nothing to undo.
- Seat identities must be unique within a league, and a league with no seats is
  not a league.

---

## 8. The computed state, and the property that guards it

Everything derived from the sales record — each seat's roster, what it spent, its
best lineup, and a fast index of who owns whom — is a **cache**. It is a pure
function of (league rules, board, seats, sales, projections). It is updated at
exactly one place in the system, and it can be discarded and recomputed at any
moment. Nothing in it is a source of truth.

There are two ways to arrive at it:

- **fold in one sale at a time**, as the draft runs — append to the roster, slot
  the player into his position bucket, add the price, re-solve that one seat's
  lineup. This happens roughly 200 times a draft and must be fast.
- **compute the whole thing from the complete record**, grouping and sorting from
  scratch. This is the slow, obviously-correct version.

**The central claim of this module, and the one to write as a property test:**
across many random legal sequences of sales, folding them in one at a time and
computing from scratch produce *identical* results — same rosters, same position
buckets, same spend, same lineup, same ownership, seat by seat and field by
field. Run it over many seeds.

**And the claim that gives that property teeth:** the from-scratch path must be
written *independently* of the incremental one — genuinely a different
computation, not a loop over the incremental step. If it were a loop, the two
would agree by construction, the property would be a tautology, and the whole
arrangement would be decoration. This is itself worth a test, or at minimum a
test named so that a future reader cannot collapse the two without noticing.

Two supporting facts that the property depends on:

- **A seat's roster is kept in the order players were bought**, so the bench
  reads like a draft history.
- **Each position bucket is kept sorted best-first**, so lineup solves and value
  probes never have to re-sort. Best-first means most points first, with the
  player's identity breaking ties — and **the tiebreak is load-bearing**: without
  it, two players projected at the same points could land in either order, and
  the two paths above would disagree about rosters that are in fact identical.
- After every sale, in debug builds, the two should be checked against each other
  immediately, so a divergence surfaces on the sale that caused it rather than at
  the end of a draft.

---

## 9. The best lineup

Seats are scored on their **starters**, so this has to be correct, not merely
plausible.

Claims:

- **It is the highest-scoring legal starting lineup the roster can field.** Beat
  a first-fit fill, and have a test that shows it: an elite tight end starts at
  tight end rather than in the flex, when that frees the flex for more points
  than putting him there would have earned.
- **The answer does not depend on the order players were bought.** Shuffle the
  roster, get the same points. This is not tidiness — it is what makes §8's
  property possible at all, since the two paths see players in different orders.
  A greedy fill would make them disagree by construction.
- **A third receiver fills the flex and is still measured against the bar** —
  i.e. slots beyond the concrete ones really do get used.
- **A second defense adds nothing**, and **a kicker never appears**, per §1.
- **An unfilled slot reads as a gap**, so a half-built roster still shows its
  shape. A roster with nobody on it has a lineup worth zero points with every
  slot open.
- **A lineup should read best-first down the template.** The matching that
  maximises points does not care which of two interchangeable slots a player
  lands in — a back is worth the same in RB2 as in the flex — so the labels come
  out in whatever order the solve happened to produce, which tends to be exactly
  backwards: the best back in the flex and the worst in RB1. Fix the labels so
  the best player appears in the earliest slot that can hold him and the overflow
  spills into the flexes.
  - **And relabelling must never cost a point.** Same players, same count, only
    the labels move — swap only between slots that can legally trade players.
    This gets its own test.
  - The stable labelling matters beyond looks: §11 counts *which* slots are still
    open, so the labelling has to be a fact rather than one arbitrary rendering
    of the same matching.
- **A roster is legal when every starting slot can be filled at once** by an
  eligible player. That is a different question from the best lineup — it ignores
  points entirely — and it is worth being able to ask on its own.
- **Laying a roster out for display**: one row per roster slot, in template
  order. Starting slots show the players who would actually start there — not the
  slot they were bought for. Bench rows take the leftovers in purchase order.
  Unfilled rows show as gaps. **Anything past the last bench slot is appended
  rather than dropped**, because silently losing a player from a display is worse
  than an ugly one.

Note on how to get there: a max-weight bipartite matching does this correctly.
Players in descending points, each added via an augmenting path, is optimal here
because of the structure of the problem, and the sizes are tiny (ten slots,
sixteen players) which is what lets this run once per candidate inside a single
bid. You are free to reach it another way if it reads better, but it must be
optimal and order-independent.

---

## 10. The bar: what a freely-available player scores

For each position: rank the points **still on the board**, and take the first
player *past* what the league **still wants** there.

Twelve seats wanting 2.77 running backs each want about 33. So the 34th back —
index 33 in a zero-based ranking — is what a dollar buys, and anything above his
points is what you are actually paying for.

Claims:

- **The bar is an index into a ranked line.** Given the points on the board and
  the remaining need, it is that one lookup. Test it that way — with the numbers
  written on the page — before testing it against a real draft.
- **Fractional need is rounded exactly once, here.** At twelve seats a fifth of a
  slot is more than two players of movement in the ranking, so the fraction is
  carried all the way to this point rather than rounded at the source.
- **Both halves shrink as the draft runs.** Supply shrinks because players
  already bought leave the ranking. Demand shrinks because it counts only
  *unfilled* starting slots.
- **So the bar falls as a position fills up.** Once nine of twelve seats have
  their tight end, the bar drops to the third-best tight end left — which is
  exactly why a mediocre tight end is genuinely worth something to the three
  seats still short. This is the single most important behavioral claim in this
  section and it deserves a test that reads like that sentence.
- **Remaining need is counted from the open slots themselves**, not by
  subtracting each seat's starters from its share. Subtracting double-counts: a
  seat that started four running backs has consumed the superflex its quarterback
  share was counting on, and walking the unfilled slots handles that for free —
  the slot is simply gone. Each open slot contributes exactly one player's worth,
  however §2 divides it across the positions competing for it.
- **At the opening bell, before anything is sold, remaining league demand is
  exactly the per-seat shares times the number of seats.** It drifts from there
  only as real slots fill.
- **Past the last man at a position, the bar is zero.** There is nobody left to
  be had at any price.
- **With no remaining need at a position, the bar is the best player left.**
  Nobody has a slot for him, so nobody is paying above a dollar for him.
- **At a position the lineup can never start — a kicker — nothing is worth
  anything, and that never moves.** This is structural, not a market condition,
  and it stays fixed for the whole draft. Represent it as an unreachable bar
  rather than as a special case sprinkled through the callers.

---

## 11. What a player is worth to you

Not his projection. **What he adds to your starting lineup over a
freely-available body at his position.**

The method: solve your lineup with him on the roster; solve it again with a
generic body at his position projected at the bar; the difference is his worth.

Claims:

- **A third elite tight end is worth nothing to you, and a great deal to the seat
  with none.** The tight end slot, the flex, the receiver flex and the superflex
  are already his — there is nowhere for a fourth to go.
- **A player who cannot crack your lineup is worth nothing.**
- **A player at a position you have already filled is measured against whoever he
  actually displaces**, not against a bar he never has to beat.
- **Nobody is ever worth less than nothing.** A player below the bar is worth
  zero, not a negative number.
- **At a position that can never start, he is worth nothing**, with no arithmetic
  needed.

**Solve both sides through the lineup rather than subtracting the bar from a
gain.** It looks like extra work and it is the thing that makes all the awkward
cases above answer themselves rather than needing a branch each.

**The baseline is the freely-available body, never an empty slot.** State this in
the code, with the reason, because it is the module's founding bug and someone
will try to simplify it back:

> You are never going to field an empty slot, so an empty slot is not what a
> player has to beat — the dollar guy is. Measured against an empty slot, a
> player is worth his entire projection against an empty roster; doing it that
> way priced this league's 192 sold players at $13,472 against the $2,400 of
> money that exists.

It is also the only baseline consistent with §12: the exchange rate is dollars
per point *above replacement*, so the points figure has to be above replacement
too. For that reason, **worth must not have a default baseline** — there is no
honest default, and the zero that used to be assumed was the bug. Make the
caller say what the bar is.

A position the bar does not mention at all is free — nobody has priced it, so
there is nothing to beat.

---

## 12. The exchange rate: dollars per point

**Biddable money, divided by the value still to be bought.**

- The numerator: what every seat still holds, less the dollar each must reserve
  for every slot it has yet to fill. Only what is left over is genuinely
  biddable.
- The denominator: the surplus over the bar of the players who will *actually be
  bought* — the best N of them, where N is how many slots remain league-wide.
  Not everyone left on the board; most of them will go undrafted.

Claims:

- **With no slots left, the rate is zero.**
- **With nobody above the bar, the rate is zero.** Both would otherwise be a
  division by nothing, and in both the honest answer is that there is no market
  here to price.
- **A position with no bar contributes no surplus** — an unreachable bar puts
  everyone there at zero.
- **Read off the sales record, this is an inflation index as much as an exchange
  rate. An early spending spree makes the back half cheap.** A league that blows
  its budget in the first hour leaves everyone bidding into a cheaper market; a
  disciplined early market makes the back half dear. Test this as the sentence it
  is: spend heavily early, and watch the rate fall.
- **At the opening bell it equals the static form** — seats times (budget less
  roster size), over the surplus of the players who will go — because nothing has
  been spent.

The bar and the rate travel together, as of one moment in the record. The rate's
denominator is measured over *that* bar, so handing a caller two numbers derived
from different moments is a mistake worth designing out rather than documenting.
Recomputing after each sale is cheap, and is the whole of the "prices move"
behavior.

Where this lives matters: the record and its cache must not know how anything is
valued. Valuation reads them; they do not reach up into it.

---

## 13. The price

**The minimum bid, plus his worth times the rate, capped by what the seat can
legally bid.** Whole dollars, rounded down.

Claims:

- **A player worth nothing still costs a dollar**, because a body in an empty
  slot beats an empty slot.
- **Unless there is no slot** — a seat with a full roster prices everyone at
  nothing, because it can bid nothing.
- The price is never negative.
- The seat's ceiling is §5's: what it holds, less a dollar reserved for every
  slot it would still have to fill.
- Worth and rate must be measured against the same bar, per §11 and §12.

---

## 14. What a roster still needs

**How many more bodies of each position a seat needs to field a legal lineup**,
against §2's rounded counts — 2 quarterbacks, 3 backs, 3 receivers, 1 tight end,
1 defense.

Claim: **bodies beyond that count are depth, never a need.** A seat with five
receivers needs zero more, not negative one.

---

## 15. The books balance

The end-to-end claim, and the one that catches an error anywhere in §10–§13:

**Priced this way, what the model says the league's players are worth sums to
about the money that actually exists.** Twelve seats at $200 is $2,400. If the
model prices the 192 players who will be bought at five times that, something
upstream is measuring against the wrong baseline.

Write this as a test that reads like an accountant's check.

---

## 16. How to test this

**Two fixture registers, and choosing between them is a decision to make
consciously each time.**

- **A declared board**, where every number is written down on the page. Four
  backs at 100, 80, 60, 8 points, a need of three, and the bar is 8. Use this
  whenever the claim is *arithmetic* — a bar is an index into a ranked line, a
  price is one multiply between two caps. The reader should be able to do the sum
  in their head without leaving the test.
- **The frozen real board** at `tests/data/board-2026.csv`. Use this when the
  claim is that *reality supplies a shape* — that the export parses, that the
  positions come out in the proportions the league actually plays, that a real
  draft's books balance.

Reaching for a whole simulated league to prove a claim about arithmetic is the
main failure mode here. If proving that a player below the bar is worth nothing
requires drafting a two-seat league that hoards defenses until the bar collapses
to zero, the test is testing the fixture. Declare the bar as zero and assert the
claim.

**Test names are full sentences about football.** Name the concept, never the
function, never a rank, never a generated identity:

- `test_a_third_elite_tight_end_is_worth_nothing_to_you`
- `test_an_early_spending_spree_makes_the_back_half_cheap`
- `test_the_bar_falls_as_the_league_fills_its_tight_end_slots`
- `test_max_bid_reserves_a_dollar_for_every_slot_the_bid_will_not_fill`
- `test_a_kicker_can_never_start`

**Every test file opens with a docstring saying what the file is for**, and long
files are broken up with prose headings rather than left as a wall.

At least one test reads the real spreadsheet at `data/projections-2026.csv`, so
the suite doubles as a schema check on the export.

---

## 17. Explicitly out of scope

No auction loop. No bidding agent that plays a draft on its own. No command-line
tool, no batch runner, no web layer. The live-auction shape in §4 exists so that
an auction layer has somewhere to land; nothing drives it.

---

## 18. Build order

Five stages. Stop at the end of each one.

1. **The league and the board** — §1, §2, §3. Done when the per-seat shares read
   2.00 / 2.77 / 3.23 / 1.00 / 1.00, the real spreadsheet parses, and a kicker
   can never start.
2. **The best lineup** — §9. Done when it beats a first-fit fill, is invariant to
   the order players arrive in, and relabelling costs no points.
3. **The record and the cache** — §4, §5, §6, §7, §8. Done when the property test
   passes across many random legal sequences, with the two paths written
   independently.
4. **The bar and what a player is worth** — §10, §11, §14. Done when a third
   elite tight end is worth nothing and the bar falls as seats fill a position.
5. **The price** — §12, §13, §15, and a README explaining the model. Done when
   the books balance.
