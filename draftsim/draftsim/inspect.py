"""Human-check helper for Stage 1: print top players by each value model.

    python -m draftsim.inspect
"""

from __future__ import annotations

from .config import DraftConfig
from .valuation import (
    load_players,
    market_value,
    replacement_points,
    vorp_value,
)


def main() -> None:
    config = DraftConfig()
    players = load_players()
    print(
        f"Loaded {len(players)} draftable players (free agents excluded) "
        f"| starter counts: {config.starter_counts()}"
    )

    repl = replacement_points(players, config)
    print("Replacement points/pos:", {k: round(v, 1) for k, v in repl.items()})

    print("\nTop 10 by market ($PROJ):")
    for p in sorted(players, key=market_value, reverse=True)[:10]:
        print(f"  ${market_value(p):>3.0f}  {p.pos:<3} {p.name} ({p.team})")

    print("\nTop 10 by VORP (points over replacement):")
    for p in sorted(players, key=lambda x: vorp_value(x, repl), reverse=True)[:10]:
        print(f"  {vorp_value(p, repl):>6.1f}  {p.pos:<3} {p.name} ({p.team})")


if __name__ == "__main__":
    main()
