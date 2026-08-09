"""Write the position-tier board — where each position's points cliffs are.

    python scripts/tiers.py                  # -> out/tiers-2026.html
    python scripts/tiers.py --gap-factor 4   # fewer, coarser tiers
    python scripts/tiers.py --top QB=32,WR=60

Reads whatever is currently in `../../data/projections-2026.csv`, so the page is
only ever as fresh as the last `npm run export:projections`. The tiering rule
and the page itself live in `draftsim.tiers`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

from draftsim.tiers import (
    DEFAULT_GAP_FACTOR,
    TOP_N,
    rank_by_position,
    render_html,
    tier_count,
)
from draftsim.valuation import DEFAULT_CSV, load_players

# Generated HTML boards land here (gitignored), beside the simulator's.
OUT_DIR = Path(__file__).resolve().parents[1] / "out"


def _parse_top(raw: str) -> Dict[str, int]:
    """`--top QB=32,WR=60`, applied over the defaults rather than replacing them."""
    tops = dict(TOP_N)
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        pos, _, count = part.partition("=")
        if not count.strip():
            raise argparse.ArgumentTypeError(f"expected POS=N, got {part!r}")
        tops[pos.strip().upper()] = int(count)
    return tops


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gap-factor",
        type=float,
        default=DEFAULT_GAP_FACTOR,
        help="tier break at this multiple of the position's median gap",
    )
    parser.add_argument(
        "--top",
        type=_parse_top,
        default=dict(TOP_N),
        help="per-position depth, e.g. QB=32,WR=60 (over the defaults)",
    )
    parser.add_argument("--csv", type=Path, default=None, help="projections CSV")
    parser.add_argument("--out", type=Path, default=None, help="output HTML path")
    args = parser.parse_args()

    source = args.csv or DEFAULT_CSV
    by_pos = rank_by_position(load_players(args.csv), args.top)

    for pos, wanted in args.top.items():
        if len(by_pos[pos]) < wanted:
            print(f"note: only {len(by_pos[pos])} {pos} in the sheet, wanted {wanted}")

    out_path = args.out or OUT_DIR / f"tiers-{source.stem.split('-')[-1]}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(by_pos, gap_factor=args.gap_factor, source=source),
        encoding="utf-8",
    )

    for pos, ranked in by_pos.items():
        tiers = tier_count([p.points for p in ranked], args.gap_factor)
        print(f"{pos:>3}  {len(ranked):>2} players  {tiers:>2} tiers")
    print(f"\nHTML board: {out_path}")


if __name__ == "__main__":
    main()
