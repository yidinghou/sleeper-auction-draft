"""Run one auction draft and print the resulting rosters — the Stage-3 human check.

    python scripts/simulate.py --seed 1

Rerun with the same seed to confirm the result is byte-identical (determinism).
Stage 5 grows this into the batch/metrics CLI.
"""

from __future__ import annotations

import argparse

from draftsim.agents import build_field
from draftsim.config import DraftConfig
from draftsim.engine import result_invariants_ok, run_draft
from draftsim.roster import starters
from draftsim.valuation import load_players


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one deterministic auction draft.")
    parser.add_argument("--seed", type=int, default=1, help="master RNG seed")
    parser.add_argument("--teams", type=int, default=12)
    args = parser.parse_args()

    config = DraftConfig(teams=args.teams)
    players = load_players()
    agents = build_field(config.teams, seed=args.seed)

    result = run_draft(agents, config, players)

    print(
        f"Draft: {config.teams} teams x {config.roster_size} slots, "
        f"${config.budget} budget, seed {args.seed}, {len(players)} players in pool"
    )
    for mid in sorted(result.managers):
        m = result.managers[mid]
        spent = result.spend(mid)
        start = [s for s in starters(m.roster, config) if s is not None]
        start_pts = sum(s.points for s in start)
        line = "  ".join(
            f"{p.pos}:{p.name.split()[-1]}(${_price(result, mid, p)})"
            for p in sorted(m.roster, key=lambda x: -_price(result, mid, x))[:6]
        )
        print(
            f"{mid}  spent ${spent:>3}  left ${m.budget:>3}  "
            f"start_pts {start_pts:>6.1f}  | {line} ..."
        )

    problems = result_invariants_ok(result)
    print("\nInvariants:", "OK" if not problems else f"{len(problems)} VIOLATIONS")
    for p in problems:
        print("  !", p)


def _price(result, manager_id, player) -> int:
    for pk in result.picks:
        if pk.winner_id == manager_id and pk.player is player:
            return pk.price
    return 0


if __name__ == "__main__":
    main()
