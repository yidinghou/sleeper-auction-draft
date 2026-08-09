/**
 * Export Sleeper player projections to a CSV.
 *
 * Columns: Sleeper player_id, player, position, team, Sleeper rank, bye week,
 * Sleeper projected auction $, half-PPR season total, and half-PPR points for
 * the first N weeks (default 3).
 *
 * player_id is the join key back to Sleeper — draft picks arrive keyed by it,
 * so the live board (python/liveboard/liveboard/live.py) needs it to match a pick to a
 * projection. Team defenses use the team abbreviation as their id ("KC").
 *
 * Rank, bye, and the projected auction dollar come from the live auction draft
 * board (Sleeper computes $PROJ client-side — it is not in any API), captured
 * into data/sleeper-board-<season>.csv keyed by player_id. If that file is
 * absent those three columns are left blank; everything else still exports.
 *
 * Usage:
 *   npm run export:projections                 # season 2026, weeks 1-3
 *   tsx scripts/export-projections.ts 2026 3   # season, weeks override
 *
 * Output: data/projections-<season>.csv
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import {
  fetchSleeperFantasyPlayers,
  fetchSleeperProjections,
  fetchSleeperWeeklyProjections,
  sleeperPlayerFullName,
} from "../lib/sleeper";

const SCORING = "pts_half_ppr" as const;

interface BoardEntry {
  rank: string;
  bye: string;
  dollar: string;
}

/**
 * Load the scraped auction-board data (player_id -> rank/bye/$). Returns an
 * empty map if the file hasn't been captured yet.
 */
function loadBoard(season: number): Map<string, BoardEntry> {
  const map = new Map<string, BoardEntry>();
  const path = join("data", `sleeper-board-${season}.csv`);
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    console.warn(`No board file at ${path} — rank/bye/$ columns will be blank.`);
    return map;
  }
  // Simple parse: header is player_id,sleeper_rank,bye_week,sleeper_proj_dollar
  for (const line of text.trim().split("\n").slice(1)) {
    const [pid, rank, bye, dollar] = line.split(",");
    if (pid) map.set(pid, { rank, bye, dollar });
  }
  return map;
}

function csvCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  // Quote if the cell contains a comma, quote, or newline.
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function round(n: number | undefined): number | "" {
  return typeof n === "number" ? Math.round(n * 100) / 100 : "";
}

async function main() {
  const season = Number(process.argv[2] ?? 2026);
  const weekCount = Number(process.argv[3] ?? 3);
  const weeks = Array.from({ length: weekCount }, (_, i) => i + 1);

  console.log(`Fetching players + ${season} projections (weeks 1-${weekCount})…`);
  const [players, seasonProj, ...weekProj] = await Promise.all([
    fetchSleeperFantasyPlayers(),
    fetchSleeperProjections(season),
    ...weeks.map((w) => fetchSleeperWeeklyProjections(season, w)),
  ]);
  const board = loadBoard(season);

  const header = [
    "player_id",
    "player",
    "position",
    "team",
    "sleeper_rank",
    "bye_week",
    "sleeper_proj_dollar",
    `season_${SCORING}`,
    ...weeks.map((w) => `week${w}_${SCORING}`),
  ];

  const rows = Object.entries(players).map(([id, player]) => {
    const seasonPts = round(seasonProj[id]?.[SCORING]);
    const weekly = weekProj.map((wp) => round(wp[id]?.[SCORING]));
    const b = board.get(id);
    return {
      id,
      name: sleeperPlayerFullName(player),
      position: player.position ?? "",
      team: player.team ?? "",
      rank: b?.rank ?? "",
      bye: b?.bye ?? "",
      dollar: b?.dollar ?? "",
      seasonPts,
      weekly,
      // Sort by Sleeper board rank (matches the draft screen); ranked players
      // first, everyone else after, tie-broken by projected points.
      rankKey: b?.rank ? Number(b.rank) : Number.POSITIVE_INFINITY,
      ptsKey: typeof seasonPts === "number" ? seasonPts : -1,
    };
  });

  rows.sort((a, b) => a.rankKey - b.rankKey || b.ptsKey - a.ptsKey);

  const lines = [
    header.join(","),
    ...rows.map((r) =>
      [
        r.id,
        r.name,
        r.position,
        r.team,
        r.rank,
        r.bye,
        r.dollar,
        r.seasonPts,
        ...r.weekly,
      ]
        .map(csvCell)
        .join(","),
    ),
  ];

  mkdirSync("data", { recursive: true });
  const outPath = join("data", `projections-${season}.csv`);
  writeFileSync(outPath, lines.join("\n") + "\n");
  console.log(
    `Wrote ${rows.length} players (${board.size} with board rank/bye/$) → ${outPath}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
