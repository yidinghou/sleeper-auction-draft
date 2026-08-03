"""Render a finished draft as a self-contained HTML debug board.

One public function, `render_html(result, seed)`, returns a full dark-themed page
(inline CSS, no external assets, no JS) with three sections:

  * **Rosters** — a grid of team cards laid out like Sleeper's post-draft view:
    the slotted starting lineup (via the optimal, order-invariant
    `roster.starters()`) plus bench, one row per player with a colored position
    badge and the price paid. Team and bye week hide in the name's hover tooltip
    to keep the row narrow. Seats holding dead weight (no NFL team, or 0.0
    projected points) or sitting on unspent budget are flagged.
  * **Points by position** — a manager x position matrix of starter and total
    points, so you can see where a roster's scoring actually comes from.
  * **Pick timeline** — every sale in order with its nominator, winner, price and
    runner-up; expand a row for the full sealed-bid log, including which seats
    sat the round out.

The last two exist to debug results that look wrong from the final board alone:
a seat that finishes with money left, or rosters full of players who aren't on
any NFL team. Colors mirror the Next.js app (`lib/positions.ts` /
`app/globals.css`) so it reads like the real thing.
"""

from __future__ import annotations

import html
from typing import Dict, List, Optional, Sequence, Tuple

from .auction import MIN_BID, Bid
from .config import BENCH, CONCRETE_POSITIONS, DraftConfig
from .engine import DraftResult, Pick
from .roster import starters
from .theme import BASE_CSS, SLOT_LABEL, badge as _badge
from .valuation import Player

# Leftover budget (in dollars) above which a seat is flagged as having failed to
# spend. A full-budget auction should end every seat within a few dollars of $0;
# anything much larger means it stopped finding things worth bidding on.
_UNSPENT_WARN = 10


def _is_dead_weight(player: Player) -> bool:
    """Whether this player is a body rather than a real asset: not on an NFL team,
    or projected for nothing. A roster full of these is the symptom that the
    valuation ran out of signal, not a real drafting decision."""
    return not player.team or player.points <= 0.0


def _slot_rows(
    roster: List[Player], config: DraftConfig
) -> List[Tuple[str, Optional[Player]]]:
    """Assign the roster to display slots: starter slots take the optimal lineup
    (slot-aligned), bench slots take the leftovers in order."""
    lineup = starters(roster, config)  # aligned to config.starter_slots
    used = {id(p) for p in lineup if p is not None}
    bench = [p for p in roster if id(p) not in used]

    rows: List[Tuple[str, Optional[Player]]] = []
    si = bi = 0
    for slot in config.roster_slots:
        if slot == BENCH:
            rows.append((slot, bench[bi] if bi < len(bench) else None))
            bi += 1
        else:
            rows.append((slot, lineup[si] if si < len(lineup) else None))
            si += 1
    rows.extend((BENCH, p) for p in bench[bi:])  # overflow, if any
    return rows


# -- section 1: roster cards -------------------------------------------------


def _player_row(slot: str, player: Optional[Player], price: int) -> str:
    label = SLOT_LABEL.get(slot, slot)
    if player is None:
        return (
            f'<div class="row empty"><span class="slot">{label}</span>'
            f'<span class="name">—</span></div>'
        )
    dead = " dead" if _is_dead_weight(player) else ""
    bye = f" · BYE {player.bye}" if player.bye else ""
    # Team and bye live in the hover tooltip rather than a visible column, so the
    # name gets the full width of the card and stops truncating.
    tip = html.escape(f"{player.team or 'FA'}{bye}")
    return (
        f'<div class="row{dead}">'
        f'<span class="slot">{label}</span>'
        f"{_badge(player.pos)}"
        f'<span class="name" title="{tip}">{html.escape(player.name)}</span>'
        f'<span class="meta">{player.points:.0f}</span>'
        f'<span class="price">${price}</span>'
        f"</div>"
    )


def _team_card(result: DraftResult, manager_id: str) -> str:
    config = result.config
    manager = result.managers[manager_id]
    price_of = {pk.player.id: pk.price for pk in result.picks if pk.winner_id == manager_id}

    lineup = [p for p in starters(manager.roster, config) if p is not None]
    starter_points = sum(p.points for p in lineup)
    dead_count = sum(1 for p in manager.roster if _is_dead_weight(p))

    left_class = "warn" if manager.budget > _UNSPENT_WARN else ""
    flags = (
        f'<span class="flag">{dead_count} dead</span>' if dead_count else ""
    )
    rows = "".join(
        _player_row(slot, player, price_of.get(player.id, 0) if player else 0)
        for slot, player in _slot_rows(manager.roster, config)
    )
    return (
        f'<section class="card">'
        f'<header><span class="team">{html.escape(manager_id)}</span>'
        f'<span class="totals">spent ${result.spend(manager_id)} · '
        f'left <span class="{left_class}">${manager.budget}</span> · '
        f"{starter_points:.0f} pts {flags}</span></header>"
        f"{rows}</section>"
    )


# -- section 2: points by position -------------------------------------------


def _positions_in_play(result: DraftResult) -> List[str]:
    """Positions to give a column, in a stable order: the league's startable ones
    first, then anything else that actually got drafted."""
    drafted = {p.pos for m in result.managers.values() for p in m.roster}
    ordered = [pos for pos in CONCRETE_POSITIONS if pos in drafted]
    extra = sorted(drafted - set(CONCRETE_POSITIONS))
    return ordered + extra


def _points_by_pos(
    roster: Sequence[Player], config: DraftConfig
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """(starter points, total points) per position for one roster.

    A player counts as a starter iff the optimal lineup uses it. Compared by
    identity, matching `_slot_rows` — `Player` is a value-equal frozen dataclass,
    so two twins with the same name/pos/team would otherwise be indistinguishable.
    """
    lineup_ids = {id(p) for p in starters(roster, config) if p is not None}
    start: Dict[str, float] = {}
    total: Dict[str, float] = {}
    for player in roster:
        total[player.pos] = total.get(player.pos, 0.0) + player.points
        if id(player) in lineup_ids:
            start[player.pos] = start.get(player.pos, 0.0) + player.points
    return start, total


def _position_table(result: DraftResult) -> str:
    """Manager x position matrix: starter points, with roster total underneath."""
    config = result.config
    positions = _positions_in_play(result)
    head = "".join(f"<th>{html.escape(pos)}</th>" for pos in positions)

    body: List[str] = []
    league_start: Dict[str, float] = {pos: 0.0 for pos in positions}
    league_total: Dict[str, float] = {pos: 0.0 for pos in positions}

    for manager_id in sorted(result.managers):
        start, total = _points_by_pos(result.managers[manager_id].roster, config)
        cells = []
        for pos in positions:
            s, t = start.get(pos, 0.0), total.get(pos, 0.0)
            league_start[pos] += s
            league_total[pos] += t
            cells.append(
                f'<td><span class="pts">{s:.0f}</span>'
                f'<span class="sub-pts">{t:.0f}</span></td>'
            )
        row_start = sum(start.values())
        row_total = sum(total.values())
        body.append(
            f'<tr><th class="rowhead">{html.escape(manager_id)}</th>{"".join(cells)}'
            f'<td class="rowtotal"><span class="pts">{row_start:.0f}</span>'
            f'<span class="sub-pts">{row_total:.0f}</span></td></tr>'
        )

    foot_cells = "".join(
        f'<td><span class="pts">{league_start[pos]:.0f}</span>'
        f'<span class="sub-pts">{league_total[pos]:.0f}</span></td>'
        for pos in positions
    )
    foot = (
        f'<tr class="leaguetotal"><th class="rowhead">league</th>{foot_cells}'
        f'<td><span class="pts">{sum(league_start.values()):.0f}</span>'
        f'<span class="sub-pts">{sum(league_total.values()):.0f}</span></td></tr>'
    )

    return (
        '<div class="tablewrap"><table class="matrix">'
        f'<thead><tr><th class="rowhead"></th>{head}<th>total</th></tr></thead>'
        f'<tbody>{"".join(body)}{foot}</tbody></table></div>'
        '<p class="legend">Big number: points from players in the optimal starting '
        "lineup. Small number: points across the whole roster, bench included.</p>"
    )


# -- section 3: pick timeline ------------------------------------------------


def _ranked_bids(bids: Sequence[Bid]) -> List[Bid]:
    """Bids in the order the auction ranked them — the same key
    `auction.resolve_auction_round` uses, so the top of this list is the winner."""
    return sorted(bids, key=lambda b: (-b.amount, b.submitted_at, b.manager_id))


def _bid_log(pick: Pick, manager_ids: Sequence[str]) -> str:
    """The expanded body of a timeline row: every offer, then who sat out."""
    ranked = _ranked_bids(pick.bids)
    if not ranked:
        return '<div class="bidlog"><span class="muted">no bid log recorded</span></div>'

    offers = "".join(
        f'<span class="bid{" win" if b.manager_id == pick.winner_id else ""}">'
        f"{html.escape(b.manager_id)} ${b.amount}</span>"
        for b in ranked
    )
    bidders = {b.manager_id for b in ranked}
    # Absent from the log means the seat never made a legal offer: a full roster,
    # a budget down to its reserve, or a bid under MIN_BID. See `Pick.bids`.
    sat_out = [m for m in manager_ids if m not in bidders]
    out = (
        f'<div class="satout">sat out ({len(sat_out)}): '
        f'{html.escape(" ".join(sat_out))}</div>'
        if sat_out
        else ""
    )
    return f'<div class="bidlog">{offers}</div>{out}'


def _price_cells(pick: Pick) -> str:
    """Winning price next to Sleeper's projected price, and the gap between them.

    The gap is the number worth watching: the field should disagree with the
    market and with itself. Prices landing on `$PROJ` every time means the jitter
    isn't doing anything and the draft is just replaying the projection sheet.
    Players with no `$PROJ` (most of the pool) have nothing to compare against.
    """
    proj = pick.player.proj_dollar
    if proj is None or proj <= 0:
        return (
            f'<span class="price">${pick.price}</span>'
            f'<span class="proj muted">—</span><span class="delta"></span>'
        )
    diff = (pick.price - proj) / proj
    direction = "over" if diff > 0 else "under" if diff < 0 else "even"
    return (
        f'<span class="price">${pick.price}</span>'
        f'<span class="proj">${proj}</span>'
        f'<span class="delta {direction}">{diff:+.0%}</span>'
    )


def _timeline_row(pick: Pick, manager_ids: Sequence[str]) -> str:
    player = pick.player
    dead = " dead" if _is_dead_weight(player) else ""
    # The $1 tail is the shape worth spotting: once it starts, the valuation has
    # stopped separating players and the rest of the draft is noise.
    cheap = " cheap" if pick.price <= MIN_BID else ""
    return (
        f'<details class="pick{cheap}">'
        f"<summary>"
        f'<span class="no">#{pick.pick_no}</span>'
        f'<span class="nom">{html.escape(pick.nominator_id)} nom</span>'
        f"{_badge(player.pos)}"
        f'<span class="name{dead}">{html.escape(player.name)}</span>'
        f'<span class="meta">{html.escape(player.team or "FA")} · {player.points:.0f}</span>'
        f'<span class="won">{html.escape(pick.winner_id)}</span>'
        f"{_price_cells(pick)}"
        f"</summary>"
        f"{_bid_log(pick, manager_ids)}"
        f"</details>"
    )


def _price_spread(result: DraftResult) -> str:
    """How far winning prices strayed from `$PROJ`, across the priced players.

    Restricted to picks whose player has a real `$PROJ`; the rest of the pool
    anchors at $1 or nothing, so including it would drown the signal in ties.
    """
    gaps = [
        (pick.price - pick.player.proj_dollar) / pick.player.proj_dollar
        for pick in result.picks
        if pick.player.proj_dollar and pick.player.proj_dollar > 0
    ]
    if not gaps:
        return '<p class="legend">No drafted player carried a $PROJ to compare against.</p>'

    gaps.sort()
    median = gaps[len(gaps) // 2]
    mean_abs = sum(abs(g) for g in gaps) / len(gaps)
    tight = sum(1 for g in gaps if abs(g) <= 0.10) / len(gaps)
    over = sum(1 for g in gaps if g > 0)
    return (
        f'<p class="legend">Against $PROJ across {len(gaps)} priced picks: '
        f"median {median:+.0%}, mean gap {mean_abs:.0%}, "
        f"range {gaps[0]:+.0%} to {gaps[-1]:+.0%} · "
        f"{tight:.0%} landed within ±10% · {over} over / {len(gaps) - over} under."
        "</p>"
    )


def _timeline(result: DraftResult) -> str:
    manager_ids = sorted(result.managers)
    rows = "".join(_timeline_row(pick, manager_ids) for pick in result.picks)
    head = (
        '<div class="pickhead">'
        '<span class="no">#</span><span class="nom">nom</span><span></span>'
        "<span>player</span><span></span>"
        '<span class="won">won</span><span class="price">paid</span>'
        '<span class="proj">$PROJ</span><span class="delta">gap</span>'
        "</div>"
    )
    return (
        f'<div class="timeline">{head}{rows}</div>'
        f"{_price_spread(result)}"
        '<p class="legend">Click a pick to see every sealed bid. A seat listed as '
        "&ldquo;sat out&rdquo; made no legal offer at all &mdash; a full roster, a budget "
        "down to its $1-per-slot reserve, or a bid under the minimum.</p>"
    )


# -- page --------------------------------------------------------------------


def render_html(result: DraftResult, seed: Optional[int] = None) -> str:
    """Full self-contained HTML page for a finished draft."""
    config = result.config
    seed_txt = "" if seed is None else f" · seed {seed}"
    cards = "".join(_team_card(result, mid) for mid in sorted(result.managers))
    unspent = [m.budget for m in result.managers.values()]
    unspent_txt = (
        f"{sum(unspent) / len(unspent):.0f} avg / ${max(unspent)} max unspent"
        if unspent
        else "no managers"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Draft results{seed_txt}</title>
<style>{BASE_CSS}
  .grid {{
    display: grid; gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  }}
  .card {{ background: #131b38; border: 1px solid #414566; border-radius: 10px; padding: 10px 12px; }}
  .card header {{ display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #252942; }}
  .team {{ font-weight: 700; }}
  .totals {{ color: #98b3d6; font-size: 11px; }}
  .row {{ display: grid; grid-template-columns: 40px 34px 1fr auto auto;
    align-items: center; gap: 6px; padding: 3px 0; }}
  .row.empty {{ color: #4a5170; }}
  .row.dead .name {{ color: #6b7395; text-decoration: line-through; }}
  .slot {{ color: #98b3d6; font-size: 10px; text-transform: uppercase; }}
  .name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    cursor: default; }}
  .meta {{ color: #98b3d6; font-size: 11px; }}
  .price {{ color: #ffab0e; font-weight: 700; min-width: 34px; text-align: right; }}

  .matrix {{ border-collapse: collapse; font-size: 12px; }}
  .matrix th, .matrix td {{ border: 1px solid #252942; padding: 4px 10px; text-align: right; }}
  .matrix thead th {{ color: #98b3d6; font-weight: 600; background: #131b38; }}
  .rowhead {{ text-align: left; font-weight: 700; }}
  .matrix .pts {{ font-weight: 700; }}
  .matrix .sub-pts {{ color: #98b3d6; font-size: 11px; margin-left: 6px; }}
  .rowtotal, .leaguetotal td {{ background: #131b38; }}
  .leaguetotal th {{ background: #131b38; color: #98b3d6; }}

  .timeline {{ border: 1px solid #414566; border-radius: 10px; overflow: hidden; }}
  .pick {{ border-bottom: 1px solid #252942; }}
  .pick:last-child {{ border-bottom: 0; }}
  .pickhead, .pick > summary {{ display: grid;
    grid-template-columns: 46px 74px 34px minmax(120px, 1fr) 110px 46px 44px 48px 52px;
    align-items: center; gap: 8px; padding: 5px 10px; }}
  .pickhead {{ color: #98b3d6; font-size: 11px; text-transform: uppercase;
    background: #131b38; border-bottom: 1px solid #414566; }}
  .pick > summary {{ cursor: pointer; list-style: none; }}
  .pick > summary::-webkit-details-marker {{ display: none; }}
  .pick > summary:hover {{ background: #131b38; }}
  .pick.cheap > summary {{ color: #6b7395; background: #080d24; }}
  .no {{ color: #98b3d6; font-size: 11px; }}
  .nom {{ color: #98b3d6; font-size: 11px; }}
  .won {{ font-weight: 700; text-align: right; }}
  .proj {{ color: #98b3d6; font-size: 12px; text-align: right; }}
  .delta {{ font-size: 11px; text-align: right; }}
  .delta.over {{ color: #ff6482; }}
  .delta.under {{ color: #28e757; }}
  .delta.even {{ color: #98b3d6; }}
  .name.dead {{ color: #6b7395; text-decoration: line-through; }}
  .bidlog {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 10px 2px 56px; }}
  .bid {{ font-size: 11px; color: #98b3d6; background: #131b38;
    border: 1px solid #252942; border-radius: 5px; padding: 1px 6px; }}
  .bid.win {{ color: #05091d; background: #ffab0e; border-color: #ffab0e; font-weight: 700; }}
  .satout {{ color: #4a5170; font-size: 11px; padding: 2px 10px 8px 56px; }}
</style></head>
<body>
  <h1>Auction draft results</h1>
  <p class="sub">{config.teams} teams · ${config.budget} budget · {config.roster_size} slots{seed_txt} · {len(result.picks)} picks · ${unspent_txt}</p>
  <p class="nav"><a href="#rosters">Rosters</a><a href="#positions">Points by position</a><a href="#timeline">Pick timeline</a></p>

  <h2 id="rosters">Rosters</h2>
  <div class="grid">{cards}</div>

  <h2 id="positions">Points by position</h2>
  {_position_table(result)}

  <h2 id="timeline">Pick timeline</h2>
  {_timeline(result)}
</body></html>
"""
