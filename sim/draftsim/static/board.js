
// Which seat is yours arrives with the state, resolved from `--user` against
// the draft's own order. It used to be a dropdown backed by localStorage --
// the same answer re-entered every session, kept in a place the server could
// not see. Null when the draft has no order to look you up in, which is every
// mock: the board then simply marks no card.
let mySeat = null;

const maxBtn = document.getElementById("max");
// Maximized, nothing folds: the overlay is the whole viewport and all twelve
// cards fit it open, so a folded row would be putting away something there was
// room for. Folding exists to buy height on the compact board, and there is
// none to buy here.
//
// The folds are ignored rather than cleared -- `applyRows` reads this class and
// treats every row as open -- so minimizing comes back to the board you left.
function setMaxed(on) {
  document.body.classList.toggle("maxed", on);
  maxBtn.textContent = on ? "Minimize" : "Maximize";
  applyRows();
}
maxBtn.addEventListener("click", () => {
  setMaxed(!document.body.classList.contains("maxed"));
});
document.getElementById("close").addEventListener("click", () => setMaxed(false));

const pressureEl = document.getElementById("pressure");

// The four positions the board has an opinion about, in the order everything
// keyed by position is laid out in: the pressure cards, the rail they fold into,
// and both sets of filter buttons.
const POSITIONS = ["QB", "RB", "WR", "TE"];

// Which pane each position card is showing. The twin of `views` above: that one
// is keyed by seat, this one by position, and both exist because the markup they
// describe is thrown away and rebuilt every two seconds.
const PVIEWS = ["runs", "tier"];
let pviews = {};
try { pviews = JSON.parse(localStorage.getItem("draftsim.pviews")) || {}; } catch (e) {}

function applyPViews() {
  document.querySelectorAll(".pcard").forEach((card) => {
    const view = PVIEWS.includes(pviews[card.dataset.pos])
      ? pviews[card.dataset.pos] : PVIEWS[0];
    card.classList.toggle("view-tier", view === "tier");
    card.querySelectorAll(".pseg button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.pane === view));
    });
  });
}

// The RUNS / TIER toggle, and the only thing a click inside a pressure card
// does. The header used to fold the card as well, which meant this listener had
// to run two jobs in the one order that kept them apart -- the pane switch
// claiming the click first, or switching a pane would fold the card you were
// switching. The band's filter owns folding now, so the header is not a control
// and the ordering problem is gone with it.
pressureEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".pseg button");
  if (btn) {
    pviews[btn.closest(".pcard").dataset.pos] = btn.dataset.pane;
    localStorage.setItem("draftsim.pviews", JSON.stringify(pviews));
    applyPViews();
    return;
  }
  // The rest of the segment -- its gaps and its border -- is not the fold
  // either, or a miss between two buttons would put the card away.
  if (e.target.closest(".pseg")) return;
  const head = e.target.closest(".phd");
  // The same toggle the filter button pulls, so folding from the card and
  // folding from the header are one act on one list. Works in the rail too:
  // a folded card's header is the whole card, and clicking it brings it back.
  if (head) toggleRun(head.closest(".pcard").dataset.pos);
});

// Which positions the run pressure band has open. The rest fold into the rail
// down its right -- so this is one list rather than a filter and a fold state,
// because "which positions am I working on" is one question and two controls
// answering it can disagree.
//
// Multi-select: it is usually two or three, the run you are in and the one you
// are about to be in. Selecting none is allowed and is the whole band put away;
// ALL brings it back, so the state cannot be got stuck in.
//
// Unlike `filters` below, this does need re-applying after every swap: the cards
// it describes are destroyed and rebuilt twice a second, and which container
// each one belongs in is not something the server knows to render.
let runsel = null;
try { runsel = JSON.parse(localStorage.getItem("draftsim.runsel")); } catch (e) {}
if (!Array.isArray(runsel)) runsel = POSITIONS.slice();

function applyRuns() {
  const grid = pressureEl.querySelector(".pgrid");
  const rail = pressureEl.querySelector(".prail");
  // Both arrive together in the same swap, so either missing means the first
  // fetch has not landed and there is nothing to sort yet.
  if (grid && rail) {
    // Walked in `POSITIONS` order rather than in click order, so the rail always
    // reads QB / RB / WR / TE down the page and a card you put away is found
    // where you last left it rather than where you happened to close it.
    POSITIONS.forEach((pos) => {
      const card = pressureEl.querySelector('.pcard[data-pos="' + pos + '"]');
      if (!card) return;
      const open = runsel.includes(pos);
      card.classList.toggle("collapsed", !open);
      // A no-op when the card is already the last child of the right box, which
      // is the common case -- four moves every two seconds either way, which is
      // nothing next to the innerHTML swap that just rebuilt them.
      (open ? grid : rail).appendChild(card);
    });
  }
  document.querySelectorAll(".rseg button").forEach((b) => {
    // ALL is pressed only when every position is -- it reports the set rather
    // than being a member of it.
    b.setAttribute("aria-pressed", String(b.dataset.pos
      ? runsel.includes(b.dataset.pos)
      : runsel.length === POSITIONS.length));
  });
}

function setRuns(next) {
  runsel = next;
  localStorage.setItem("draftsim.runsel", JSON.stringify(runsel));
  applyRuns();
}

// One position on or off. Two things call this -- the filter button and the
// card's own header -- and they are the same act on the same list, which is why
// it is a function rather than two copies: a header that folded a card without
// the filter hearing about it would leave the band showing one thing and the
// buttons claiming another, and the next swap would decide which of them won.
function toggleRun(pos) {
  setRuns(runsel.includes(pos) ? runsel.filter((p) => p !== pos) : runsel.concat(pos));
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".rseg button");
  if (!btn) return;
  if (btn.dataset.pos) toggleRun(btn.dataset.pos);
  else setRuns(POSITIONS.slice());
});

// Which position the pool and the log are narrowed to. Empty is everyone.
//
// Unlike `views` and `pviews`, this needs no re-apply after a refresh: the
// buttons live in the shell and the chosen position is written on the list
// wrapper, both of which outlive the swap. The hiding itself is a CSS rule
// keyed on that attribute, so a filtered pane costs nothing per tick.
let filters = {};
try { filters = JSON.parse(localStorage.getItem("draftsim.filters")) || {}; } catch (e) {}

function applyFilters() {
  document.querySelectorAll(".fseg").forEach((seg) => {
    const pane = seg.dataset.for;
    const pos = POSITIONS.includes(filters[pane]) ? filters[pane] : "";
    const list = document.getElementById(pane);
    if (list) list.dataset.filter = pos;
    seg.querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.pos === pos));
    });
    // A filter that matches nothing leaves an empty box, which reads as a
    // broken pane rather than as an answer -- and "no tight end has gone yet"
    // is an answer. The one thing here CSS cannot do on its own: it can hide
    // the rows that do not match, but it cannot ask whether any are left.
    const none = document.querySelector('.fnone[data-for="' + pane + '"]');
    if (!none || !list) return;
    // `rows` guards the tick before the first fetch lands: a list with nothing
    // in it yet has not answered the question, and must not claim it has.
    const rows = list.querySelectorAll(".prow, .lrow").length;
    const empty = pos && rows > 0 && !list.querySelector('[data-pos="' + pos + '"]');
    none.hidden = !empty;
    none.textContent = pane === "log"
      ? "no " + pos + " has gone yet" : "no " + pos + " left";
  });
}

// One listener for both panes. Independent by design: narrowing the pool to
// running backs while the log still shows every sale is the pairing you want
// when you are deciding what to nominate next.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".fseg button");
  if (!btn) return;
  filters[btn.closest(".fseg").dataset.for] = btn.dataset.pos;
  localStorage.setItem("draftsim.filters", JSON.stringify(filters));
  applyFilters();
});

// Escape minimizes -- unless the seat menu is open, which is the one thing on
// this page that covers anything. Innermost first: the key closes what it opened
// last, and does not also throw you out of the maximized board you were naming
// a seat on.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && menuSeat === null) setMaxed(false);
});

// Per-card pane. "lineup" first: it is what the board loads on, and it is the
// one a card with no stored choice must show.
const VIEWS = ["lineup", "bench"];
let views = {};
try { views = JSON.parse(localStorage.getItem("draftsim.views")) || {}; } catch (e) {}

// #rosters is replaced wholesale every tick, so the chosen pane lives here and
// is re-applied after each swap -- otherwise every card would snap back to the
// lineup twice a second. The segment's pressed state is set here too, for the
// same reason: the buttons are new markup on every refresh.
function applyViews() {
  document.querySelectorAll("section.card").forEach((card) => {
    const view = VIEWS.includes(views[card.dataset.seat])
      ? views[card.dataset.seat] : VIEWS[0];
    card.classList.toggle("view-bench", view === "bench");
    card.querySelectorAll(".seg button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.pane === view));
    });
  });
}

// Delegated: the segment buttons are destroyed and rebuilt on every refresh, so
// nothing may hold a reference to them.
document.getElementById("rosters").addEventListener("click", (e) => {
  const btn = e.target.closest(".seg button");
  if (!btn) return;
  const slot = btn.closest("section.card").dataset.seat;
  views[slot] = btn.dataset.pane;
  localStorage.setItem("draftsim.views", JSON.stringify(views));
  applyViews();
});

// What is folded away, and remembered. Four consumers -- the bands down the
// left, the pool/log panes, the four pressure cards, and the roster grid's rows
// -- because they are the same gesture: give the room to whatever you are
// actually reading.
//
// Keyed by a stable name (`data-band`, the position for a card, the row number
// for the grid) rather than by index into the markup, so a collapsed thing stays
// collapsed even though the markup holding it is thrown away and rebuilt twice a
// second.
let collapsed = [];
try { collapsed = JSON.parse(localStorage.getItem("draftsim.collapsed")) || []; } catch (e) {}

// Four across, from the grid's own `grid-template-columns`.
const GRID_COLS = 4;

// Fold by row, not by card. Four cards side by side share a row's height, so
// folding one alone frees nothing -- its neighbours still need the room. A whole
// row is the smallest thing whose height can actually go somewhere.
//
// Rows are positional, and deliberately so: the board can be dragged into any
// order, and what you folded is the row you were looking at, not whichever seats
// happened to be in it.
// The seats in a row, as short as they can be said. Untouched, an unmoved board
// spells out "S1 · S2 · S3 · S4" over every row -- four tokens saying one thing,
// next to a heading that already said which row this is. Consecutive seats
// collapse to "S1–S4"; once cards have been dragged the run breaks and the list
// comes back, which is exactly when it is worth reading.
function seatRange(names) {
  const nums = names.map((n) => +n.slice(1));
  const run = nums.every((n, i) => i === 0 || n === nums[i - 1] + 1);
  if (run && names.length > 2) return names[0] + "–" + names[names.length - 1];
  return names.join(" · ");
}

function applyRows() {
  const grid = document.querySelector("#rosters .grid");
  if (!grid) return;
  // Reading order, not markup order -- `applyOrder` has already written the
  // `order` each card sits at, and that is what decides which row it renders in.
  const cards = [...grid.querySelectorAll("section.card")]
    .sort((a, b) => (+a.style.order || 0) - (+b.style.order || 0));
  const heads = [...grid.querySelectorAll(".rowhd")];
  const rows = Math.ceil(cards.length / GRID_COLS);
  // A header row and a card row per row of the board, in that order -- the same
  // interleaving `slotOrder` lays the items out in.
  const tmpl = [];
  let shutRows = 0;
  // Maximized, every row is open whatever the stored folds say: see `setMaxed`.
  const foldable = !document.body.classList.contains("maxed");
  for (let r = 0; r < rows; r++) {
    const shut = foldable && collapsed.includes("row:" + r);
    if (shut) shutRows++;
    const mine = cards.slice(r * GRID_COLS, (r + 1) * GRID_COLS);
    mine.forEach((c) => {
      c.classList.toggle("collapsed", shut);
      // Which row a card is in after a drag is the client's answer to give, and
      // the tint that bands it to its bar is cut from that number.
      c.dataset.row = r;
    });
    const head = heads[r];
    if (head) {
      head.style.order = r * (GRID_COLS + 1);
      head.classList.toggle("shut", shut);
      head.dataset.row = r;
      // Which seats are in this row is the client's answer to give: a dragged
      // card changes it, and the server never hears about the drag.
      head.querySelector(".note").textContent =
        seatRange(mine.map((c) => "S" + c.dataset.seat));
    }
    tmpl.push("var(--rowbar)", shut ? "auto" : "minmax(0, var(--rowfull))");
  }
  // Seeded before the template that reads it. This grid is thrown away and
  // rebuilt every two seconds, and the one that arrives has no `--rowfull` of
  // its own -- so a template referencing it would be a declaration with an
  // undefined variable in it, which CSS drops whole. `grid-auto-rows` would then
  // size all 2N tracks evenly, and the strip height read back off them below
  // would be a sixth of the board rather than a folded row.
  grid.style.setProperty("--rowfull", evenRows(rows));
  // Explicit rows, because `grid-auto-rows: minmax(0, 1fr)` would hand a folded
  // row its full third back however little is left in it -- and would give each
  // header bar a third of the board besides.
  grid.style.gridTemplateRows = tmpl.join(" ");
  // Now that the folded rows are `auto` tracks, they can say how tall a strip is.
  grid.style.setProperty("--rowfull", rowHeight(grid, rows, shutRows));
}

// The board with nothing folded: the pane, less a bar per row and a gap between
// every one of the 2N tracks, split N ways.
function evenRows(rows) {
  return `calc((100% - ${rowChrome(rows)}) / ${rows})`;
}

function rowChrome(rows) {
  return `(${rows} * var(--rowbar) + ${2 * rows - 1} * 5px)`;
}

// How tall a folded row came out. Read back off the tracks just written, where a
// shut row is `auto` and the browser has therefore already resolved it to the
// height of the strip in it -- the layout engine's own answer, and the only one
// that is not a guess about when the fold took effect. Measuring the card
// instead reports whatever track it is stretched into, which is a stale row
// height on the pass right after a swap.
function stripHeight(grid, rows) {
  const used = getComputedStyle(grid).gridTemplateRows.split(" ");
  let tallest = 0;
  for (let r = 0; r < rows; r++) {
    if (!collapsed.includes("row:" + r)) continue;
    tallest = Math.max(tallest, parseFloat(used[r * 2 + 1]) || 0);
  }
  return tallest;
}

// What an open row is worth. Folding is for making the rows you kept bigger, so
// an open row takes an even share of what the folded ones gave up -- but only up
// to the height two open rows get, which is where the growing stops. Past that
// the type is fixed and the lineup is eight slots long, so a taller card is
// white space, and the last row on the board would be a different object from
// the one you were reading a moment ago.
function rowHeight(grid, rows, shutRows) {
  const open = rows - shutRows;
  const s = shutRows && open ? stripHeight(grid, rows) : 0;
  if (!s) return evenRows(rows);
  const chrome = rowChrome(rows);
  return `min(calc((100% - ${chrome} - ${shutRows} * ${s}px) / ${open}),` +
    ` calc((100% - ${chrome} - ${s}px) / 2))`;
}

// Bands and rows. The pressure cards used to fold from here too, under `pos:`
// keys; they answer to `runsel` now, and any of those keys still sitting in a
// browser's storage from before simply match nothing.
function applyCollapsed() {
  document.querySelectorAll("[data-band]").forEach((el) => {
    el.classList.toggle("collapsed", collapsed.includes("band:" + el.dataset.band));
  });
  applyRows();
}

// Where the nth card of the board sits in the grid's `order`, with a row header
// taking the place ahead of every run of four. So a row of the board costs five
// order slots: the bar, then its four cards.
function slotOrder(pos) {
  return pos + Math.floor(pos / GRID_COLS) + 1;
}

function toggleCollapsed(id) {
  const at = collapsed.indexOf(id);
  if (at === -1) collapsed.push(id); else collapsed.splice(at, 1);
  localStorage.setItem("draftsim.collapsed", JSON.stringify(collapsed));
  applyCollapsed();
}

// Double-click a header to fold what is under it: a band's on the left, a row's
// on the right. One gesture, one kind of control, both sides of the board.
//
// The fold lives on the row bar rather than on the cards under it, which is what
// keeps it clear of the drag: a card header is a grab handle, and a click that
// ends a one-pixel drag must not put four cards away.
//
// Delegated from the document because every one of these headers is rebuilt
// every two seconds, and one listener beats nine.
document.addEventListener("dblclick", (e) => {
  // All three filters and the checkpoint nav sit inside a band header, so two
  // quick taps on ALL -- or on ◀/▶ stepping through picks -- would otherwise
  // fold the very band holding them. On the run pressure filter, where
  // toggling positions off one at a time is the ordinary gesture, two taps in
  // a row is not even an accident.
  if (e.target.closest(".fseg") || e.target.closest(".rseg") || e.target.closest(".navseg")) return;
  const head = e.target.closest(".bandhd");
  if (head) {
    const band = head.closest("[data-band]");
    if (band) toggleCollapsed("band:" + band.dataset.band);
    return;
  }
  const rowHead = e.target.closest(".rowhd");
  // Maximized the rows do not fold, so the gesture does nothing rather than
  // quietly recording a fold you would only see on minimizing.
  if (rowHead && !document.body.classList.contains("maxed")) {
    toggleCollapsed("row:" + rowHead.dataset.row);
  }
});

// A double-click is two clicks on the *same element*, and the fragments holding
// these headers are replaced every two seconds -- so a swap landing between the
// two clicks eats the gesture, and the fold silently does nothing about one time
// in ten. The board holds the swap for a moment after a press on a foldable
// header, the same way it holds it while a card is being dragged.
//
// `.phd` is here for a nearer version of the same problem. A pressure card folds
// on a single click, and a click is a mousedown and a mouseup on one element --
// so a swap landing between them destroys the header that was pressed and the
// click never fires at all. Holding covers the gap.
let heldAt = 0;
document.addEventListener("mousedown", (e) => {
  if (e.target.closest(".rowhd") || e.target.closest(".phd")) {
    heldAt = Date.now();
  }
});

// Seat order, dragged by hand and remembered. Presentational: the server always
// sends seat order and this reorders the grid with CSS `order`, so nothing has
// to be re-fetched and seat order is never lost -- clearing the list restores it.
//
// One array, and every grid keyed by seat obeys it: the roster cards and the
// twelve tiles inside each run-pressure card all carry `data-seat`, so the seat
// you dragged to the top-left of the board is top-left everywhere. That is why
// this selects on the attribute rather than on `section.card` -- two grids
// syncing to each other would disagree for a frame after every drag; two grids
// reading the same array in the same pass cannot.
let order = [];
try { order = JSON.parse(localStorage.getItem("draftsim.order")) || []; } catch (e) {}

// Seats as the roster cards report them. The cards are the authority on which
// seats exist -- a tile grid is a view of the same twelve and must not be able
// to introduce a thirteenth.
function slots() {
  return [...document.querySelectorAll("section.card")].map((c) => c.dataset.seat);
}

// Reconcile the stored list against the seats actually on the board: drop seats
// that are gone, append ones it has never seen. A saved order from a 10-team
// league must not hide seats 11 and 12 of a 12-team one.
function normalizeOrder() {
  const present = slots();
  order = order.filter((slot) => present.includes(slot));
  present.forEach((slot) => { if (!order.includes(slot)) order.push(slot); });
}

function applyOrder() {
  normalizeOrder();
  document.querySelectorAll("[data-seat]").forEach((el) => {
    // Gapped, to leave a place for each row bar. The pressure tiles read the
    // same numbers and have no bars to leave room for, but they only ever care
    // which of them comes first -- and the gaps do not change that.
    el.style.order = slotOrder(order.indexOf(el.dataset.seat));
  });
}

// True while a card is in hand. The board refetches every 2s, and replacing
// #rosters mid-drag would delete the element being dragged and drop nothing --
// so the swap waits, and the next tick catches up.
let dragging = null;

const rosters = document.getElementById("rosters");

rosters.addEventListener("dragstart", (e) => {
  const card = e.target.closest("section.card");
  if (!card) return;
  dragging = card.dataset.seat;
  card.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox ignores a drag that carries no data.
  e.dataTransfer.setData("text/plain", dragging);
});

rosters.addEventListener("dragover", (e) => {
  const card = e.target.closest("section.card");
  if (dragging === null || !card) return;
  e.preventDefault();  // without this the drop event never fires
  e.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".dropzone").forEach((c) => c.classList.remove("dropzone"));
  if (card.dataset.seat !== dragging) card.classList.add("dropzone");
});

rosters.addEventListener("drop", (e) => {
  const card = e.target.closest("section.card");
  if (dragging === null || !card) return;
  e.preventDefault();
  const target = card.dataset.seat;
  if (target !== dragging) {
    // Pull the card out, then put it back at the target's position: everything
    // from there down shifts one place, which is what dropping "onto" a slot
    // means. Removing first is what keeps the target index right when the card
    // came from above it.
    normalizeOrder();
    order.splice(order.indexOf(dragging), 1);
    order.splice(order.indexOf(target), 0, dragging);
    localStorage.setItem("draftsim.order", JSON.stringify(order));
    applyOrder();
    // A card dragged across a row boundary changes which cards are folded --
    // the fold belongs to the row, so the card takes the state of where it lands.
    applyRows();
  }
  endDrag();
});

function endDrag() {
  dragging = null;
  document.querySelectorAll(".dragging, .dropzone").forEach((c) => {
    c.classList.remove("dragging", "dropzone");
  });
}
rosters.addEventListener("dragend", endDrag);

// The way out of an arrangement you regret. Dragging persists, so without this a
// board shuffled at 2am stays shuffled with no obvious way back.
document.getElementById("reorder").addEventListener("click", () => {
  order = [];
  localStorage.removeItem("draftsim.order");
  applyOrder();
  applyRows();
});

// Naming a seat by hand.
//
// The names on the board come from the league and are the server's business --
// the same argument `--user` settled above. This is the override for the two
// cases the scan cannot answer: a mock, which publishes no league at all, so
// every rehearsal starts with twelve numbered seats; and a real draft the scan
// got wrong or that moved somebody after it ran.
//
// So the override goes to the server too, which writes it beside the draft.
// Nothing about a name belongs in this tab's localStorage: it would be re-typed
// on every machine, and invisible to the fragments that draw it.
const seatMenu = document.getElementById("seatmenu");
const seatInput = document.getElementById("smname");
// Which seat the open menu is naming, and the guard that freezes the two-second
// swap while it is open -- the third of its kind, next to `dragging` and
// `heldAt`, and for the same reason: the board must not delete what is in hand.
let menuSeat = null;

// Names as the last snapshot had them, so the box opens on the name you can see
// rather than empty -- editing "Marc's Team" into "Marc" should not mean typing
// it out again.
let seatNames = {};

function closeMenu() {
  menuSeat = null;
  seatMenu.hidden = true;
}

function openMenu(slot, x, y) {
  menuSeat = slot;
  document.getElementById("smslot").textContent = "S" + slot;
  seatInput.value = seatNames[slot] || "";
  seatMenu.hidden = false;
  // Placed after unhiding, when the box has a size to keep inside the window:
  // right-clicking the bottom row otherwise opens a menu half off the screen.
  const box = seatMenu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - box.width - 6);
  const top = Math.min(y, window.innerHeight - box.height - 6);
  seatMenu.style.left = Math.max(6, left) + "px";
  seatMenu.style.top = Math.max(6, top) + "px";
  seatInput.focus();
  seatInput.select();
}

async function post(path, body) {
  // The JSON content type is what the server checks for, and it is the check:
  // a form posted from another tab cannot set this header without a preflight.
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

async function saveName() {
  const slot = menuSeat;
  if (slot === null) return;
  const name = seatInput.value.trim();
  closeMenu();
  // Optimistic, so the name is on the card before the next poll comes back --
  // and corrected by that poll if the server cleaned it up or refused it.
  seatNames[slot] = name;
  await post("/api/seat-name", { slot: +slot, name: name });
}

// Anything carrying a seat: a roster card, or one of the twelve tiles inside a
// run-pressure card. They are the same seat, and either is a reasonable place to
// notice you have been reading an anonymous column for ten minutes.
document.addEventListener("contextmenu", (e) => {
  const el = e.target.closest("[data-seat]");
  if (!el || seatMenu.contains(e.target)) return;
  e.preventDefault();
  openMenu(el.dataset.seat, e.clientX, e.clientY);
});

seatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveName();
  else if (e.key === "Escape") { closeMenu(); e.stopPropagation(); }
});

document.getElementById("smscan").addEventListener("click", async () => {
  closeMenu();
  await post("/api/rescan-names");
});

// Clicking away is the ordinary way out of a menu, and the board is one big
// click target -- so this closes rather than saving. A half-typed name is not
// worth committing, and the gesture that commits is the one in the box.
document.addEventListener("mousedown", (e) => {
  if (menuSeat !== null && !seatMenu.contains(e.target)) closeMenu();
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
});
// Fixed position, so a scrolled page would leave the menu hanging over whatever
// scrolled under it.
window.addEventListener("scroll", closeMenu, true);

function highlight() {
  const mine = mySeat === null ? "" : String(mySeat);
  document.querySelectorAll("section.card").forEach((card) => {
    card.classList.toggle("me", mine !== "" && card.dataset.seat === mine);
  });
}

// Swap a scroller's rows without losing where you were reading.
//
// The log grows at the *top* -- newest first -- so a new pick would also shove
// the rows under your eye down by one. Anchoring to the height instead of the
// raw offset keeps the row you were looking at where it was, and a scroller
// still at the top stays pinned there so new picks arrive in view.
function swapKeepingScroll(id, html) {
  const el = document.getElementById(id);
  const wasAtTop = el.scrollTop === 0;
  const before = el.scrollHeight;
  const top = el.scrollTop;
  el.innerHTML = html;
  el.scrollTop = wasAtTop ? 0 : top + (el.scrollHeight - before);
}

// Set while a pointer is down on the slider, cleared on release. A poll
// landing mid-drag must not yank the thumb out from under the pointer still
// holding it -- the same reasoning as the `dragging`/`menuSeat` freeze guards
// above, for the same reason: a gesture in progress owns the element until it
// lets go.
let navSliding = false;

function applyNav(nav) {
  if (!nav) return;
  const slider = document.getElementById("navslider");
  slider.disabled = nav.total === 0;
  slider.min = 0;
  slider.max = nav.total;
  if (!navSliding) {
    slider.value = nav.index;
    document.getElementById("navpos").textContent =
      nav.total ? `${nav.index} / ${nav.total}` : "0 / 0";
  }
  document.getElementById("navprev").disabled = nav.total === 0 || nav.index === 0;
  document.getElementById("navnext").disabled = nav.index >= nav.total;
  document.getElementById("navlive")
    .setAttribute("aria-pressed", nav.live ? "true" : "false");
}

// The one place a snapshot -- live or a rewound checkpoint -- is drawn.
// `force` skips the drag/menu/hold freeze: a nav click is a deliberate
// request for a different moment of the draft, not a poll that might land
// mid-gesture, so it must never be swallowed the way a stray tick would be.
function applyState(s, force) {
  mySeat = s.my_seat === undefined ? null : s.my_seat;
  // Only for prefilling the rename box: every fragment already arrives with
  // its own names drawn in. Skipped while the box is open, or a poll landing
  // mid-edit would argue with what is being typed.
  if (menuSeat === null) seatNames = s.seat_names || {};
  document.getElementById("sub").textContent = s.subtitle;
  document.getElementById("draft").textContent = s.draft_label || "";
  document.getElementById("spend").innerHTML = s.spend_html || "";
  document.getElementById("ledger").innerHTML = s.ledger_html;
  document.getElementById("block").innerHTML = s.nomination_html;
  // Everything else refreshes; the cards hold still until the drag lands, so
  // the element in hand isn't deleted out from under it. The pressure tiles
  // freeze with them rather than on their own: they are the same seats in the
  // same order, and half of that pair moving mid-drag is worse than neither.
  if (dragging === null && menuSeat === null && Date.now() - heldAt > 500 || force) {
    rosters.innerHTML = s.rosters_html;
    document.getElementById("pressure").innerHTML = s.pressure_html;
    // Replacing the rows empties the scroller for an instant, which drops it
    // back to the top. Unnoticeable over forty rows; over three hundred it
    // means you cannot read past the first screen without the board yanking
    // you back twice a second.
    swapKeepingScroll("pool", s.pool_html);
    swapKeepingScroll("log", s.log_html);
    highlight();
    applyViews();
    applyPViews();
    // Unlike the pool's filter, this one cannot ride out the swap on an
    // attribute: the swap rebuilt all four cards into `.pgrid`, so the folded
    // ones have to be walked back to the rail.
    applyRuns();
    // Not to re-apply the filter -- the wrapper kept its attribute through
    // the swap -- but to re-ask whether it still matches anything. A tight
    // end going off the board is exactly what turns that answer over.
    applyFilters();
    applyOrder();
    applyCollapsed();
  }
  document.getElementById("pulse").textContent = s.polled_at;
  document.getElementById("warn").textContent = s.warning || "";
  applyNav(s.nav);
}

async function tick() {
  const dot = document.getElementById("dot");
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    applyState(await res.json());
    dot.classList.remove("stale");
  } catch (err) {
    dot.classList.add("stale");
    document.getElementById("pulse").textContent = "server unreachable";
  }
}

// The step/live/slider controls post straight to `/api/nav` and draw its
// answer on the spot -- waiting for the next 2s tick would make every click
// feel like it did nothing for up to two seconds.
async function nav(action, index) {
  try {
    const body = index === undefined ? { action } : { action, index };
    const res = await post("/api/nav", body);
    applyState(await res.json(), true);
  } catch (err) {
    // The next regular tick will report the outage; a nav click failing
    // silently is preferable to it fighting the poller for the dot.
  }
}
document.getElementById("navprev").addEventListener("click", () => nav("prev"));
document.getElementById("navnext").addEventListener("click", () => nav("next"));
document.getElementById("navlive").addEventListener("click", () => nav("live"));

// The slider is for covering a hundred picks in one gesture, not for a
// request per pixel dragged: `input` only moves the label, optimistically,
// the same "corrected by the next poll if wrong" trick the seat-rename box
// uses; the single `nav("goto", …)` fires on release.
const navSlider = document.getElementById("navslider");
navSlider.addEventListener("pointerdown", () => { navSliding = true; });
navSlider.addEventListener("input", () => {
  document.getElementById("navpos").textContent =
    navSlider.max > 0 ? `${navSlider.value} / ${navSlider.max}` : "0 / 0";
});
navSlider.addEventListener("change", () => {
  navSliding = false;
  nav("goto", +navSlider.value);
});
// Belt and suspenders: a pointer released off the track still fires `change`
// on a range input, but not every input method is a pointer.
navSlider.addEventListener("pointerup", () => { navSliding = false; });
// Before the first fetch, so a folded band never flashes open on load -- and so
// the fold survives a server that is down when the page opens.
applyCollapsed();
applyPViews();
applyFilters();
// No cards to sort yet, but the filter buttons are in the shell and can be shown
// pressed before the first fetch lands -- so the band never opens on ALL and then
// visibly folds three cards a moment later.
applyRuns();
tick();
setInterval(tick, 2000);
