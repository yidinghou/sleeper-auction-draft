"""Run one auction draft and print the resulting rosters — the Stage-3 human check.

    python scripts/simulate.py --seed 1

Rerun with the same seed to confirm the result is byte-identical (determinism).
Stage 5 grows this into the batch/metrics CLI.
"""

from __future__ import annotations

import argparse

from draftsim.agents import build_field
from draftsim.auction import MIN_BID
from draftsim.config import DraftConfig
from draftsim.engine import invariant_violations, run_draft
from draftsim.roster import starters
from draftsim.valuation import load_players

# How many of each manager's most expensive buys to show per line.
TOP_BUYS_SHOWN = 6


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one deterministic auction draft.")
    parser.add_argument("--seed", type=int, default=1, help="master RNG seed")
    parser.add_argument("--teams", type=int, default=12)
    args = parser.parse_args()

    config = DraftConfig(teams=args.teams)
    players = load_players()
    agents = build_field(config.teams, seed=args.seed)

    result = run_draft(agents, config, players)

    # What each manager paid for each player it won. Built once; looking this up
    # by scanning result.picks per player is quadratic.
    price_of = {(pk.winner_id, pk.player.id): pk.price for pk in result.picks}

    print(
        f"Draft: {config.teams} teams x {config.roster_size} slots, "
        f"${config.budget} budget, seed {args.seed}, {len(players)} players in pool"
    )
    for manager_id in sorted(result.managers):
        manager = result.managers[manager_id]
        lineup = [s for s in starters(manager.roster, config) if s is not None]
        starter_points = sum(s.points for s in lineup)

        # The six priciest buys, as "POS:LastName($price)".
        top_buys = sorted(
            manager.roster,
            key=lambda p: -price_of[(manager_id, p.id)],
        )[:TOP_BUYS_SHOWN]
        buys = "  ".join(
            f"{p.pos}:{p.name.split()[-1]}(${price_of[(manager_id, p.id)]})"
            for p in top_buys
        )

        print(
            f"{manager_id}  spent ${result.spend(manager_id):>3}  "
            f"left ${manager.budget:>3}  start_pts {starter_points:>6.1f}  | {buys} ..."
        )

    _print_summary(result)


def _print_summary(result) -> None:
    """Invariant check plus two numbers worth eyeballing after a tuning change."""
    min_bid_picks = sum(1 for pk in result.picks if pk.price == MIN_BID)
    print(
        f"\n{len(result.picks)} picks, {min_bid_picks} at ${MIN_BID} "
        f"({min_bid_picks / max(1, len(result.picks)):.0%} of the draft)"
    )

    # A full-budget spend with a long $1 tail is the expected shape for a
    # 12-team $200 auction, not a defect -- aggregate $PROJ across the drafted
    # players is about the same as the league's total budget.
    problems = invariant_violations(result)
    print("Invariants:", "OK" if not problems else f"{len(problems)} VIOLATIONS")
    for problem in problems:
        print("  !", problem)


if __name__ == "__main__":
    main()
