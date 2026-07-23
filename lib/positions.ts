export const POSITION_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"];

export const POSITION_COLORS: Record<string, string> = {
  QB: "bg-danger/20 text-danger",
  RB: "bg-accent-green/20 text-accent-green",
  WR: "bg-accent-cyan/20 text-accent-cyan",
  TE: "bg-accent-gold/20 text-accent-gold",
};

const FALLBACK_COLOR = "bg-surface-light text-text-secondary";

export function positionColor(position: string | null | undefined): string {
  if (!position) return FALLBACK_COLOR;
  return POSITION_COLORS[position] ?? FALLBACK_COLOR;
}

function positionRank(position: string | null | undefined): number {
  if (!position) return POSITION_ORDER.length;
  const idx = POSITION_ORDER.indexOf(position);
  return idx === -1 ? POSITION_ORDER.length : idx;
}

// Fallback lineup template used when a league's real roster_positions haven't
// been synced yet. Mirrors a common PPR setup.
export const DEFAULT_ROSTER_SLOTS = [
  "QB",
  "RB",
  "RB",
  "WR",
  "WR",
  "WR",
  "TE",
  "FLEX",
  "K",
  "DEF",
  "BN",
  "BN",
  "BN",
  "BN",
  "BN",
  "BN",
];

// Which player positions each flex-type slot accepts.
const FLEX_ELIGIBILITY: Record<string, string[]> = {
  FLEX: ["RB", "WR", "TE"],
  SUPER_FLEX: ["QB", "RB", "WR", "TE"],
  REC_FLEX: ["WR", "TE"],
  WRRB_FLEX: ["WR", "RB"],
};

const BENCH_SLOT = "BN";

// Compact labels for slot tokens whose Sleeper names are too long for the badge.
const SLOT_SHORT_LABELS: Record<string, string> = {
  SUPER_FLEX: "SFLX",
  REC_FLEX: "RFLX",
  WRRB_FLEX: "W/R",
};

export function slotShortLabel(slot: string): string {
  return SLOT_SHORT_LABELS[slot] ?? slot;
}

/**
 * Greedily assign a team's players to an ordered lineup template.
 * Returns an array index-aligned to `template` (a player or null per slot),
 * with any leftover players appended as extra bench entries.
 */
export function fillRosterSlots<T extends { position: string | null }>(
  players: T[],
  template: string[],
): (T | null)[] {
  const remaining = [...players];
  const result: (T | null)[] = new Array(template.length).fill(null);

  const takeFirst = (predicate: (p: T) => boolean): T | null => {
    const idx = remaining.findIndex(predicate);
    if (idx === -1) return null;
    return remaining.splice(idx, 1)[0];
  };

  // Pass 1: concrete position slots (QB, RB, WR, TE, K, DEF, ...).
  template.forEach((slot, i) => {
    if (slot === BENCH_SLOT || FLEX_ELIGIBILITY[slot]) return;
    result[i] = takeFirst((p) => p.position === slot);
  });

  // Pass 2: flex slots.
  template.forEach((slot, i) => {
    const eligible = FLEX_ELIGIBILITY[slot];
    if (!eligible) return;
    result[i] = takeFirst((p) => !!p.position && eligible.includes(p.position));
  });

  // Pass 3: bench slots, then overflow as extra bench rows.
  template.forEach((slot, i) => {
    if (slot !== BENCH_SLOT) return;
    result[i] = remaining.shift() ?? null;
  });
  while (remaining.length > 0) {
    result.push(remaining.shift()!);
  }

  return result;
}

export function groupByPosition<T extends { position: string | null }>(
  items: T[],
): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = item.position ?? "FLEX";
    const list = groups.get(key);
    if (list) {
      list.push(item);
    } else {
      groups.set(key, [item]);
    }
  }
  return new Map(
    [...groups.entries()].sort((a, b) => positionRank(a[0]) - positionRank(b[0])),
  );
}
