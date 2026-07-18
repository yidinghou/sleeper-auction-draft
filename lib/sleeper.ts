const SLEEPER_API_BASE =
  process.env.SLEEPER_API_BASE ?? "https://api.sleeper.app/v1";

const FANTASY_POSITIONS = new Set([
  "QB",
  "RB",
  "WR",
  "TE",
  "K",
  "DEF",
]);

export interface SleeperLeague {
  league_id: string;
  name: string;
  season: string;
  status: string;
}

export interface SleeperUser {
  user_id: string;
  display_name: string;
  metadata?: { team_name?: string };
}

export interface SleeperRoster {
  roster_id: number;
  owner_id: string;
  players: string[] | null;
}

export interface SleeperPlayerRaw {
  player_id: string;
  full_name?: string;
  first_name?: string;
  last_name?: string;
  position?: string | null;
  team?: string | null;
  status?: string | null;
  active?: boolean;
}

async function sleeperFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${SLEEPER_API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`Sleeper API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function fetchSleeperLeague(leagueId: string) {
  return sleeperFetch<SleeperLeague>(`/league/${leagueId}`);
}

export function fetchSleeperUsers(leagueId: string) {
  return sleeperFetch<SleeperUser[]>(`/league/${leagueId}/users`);
}

export function fetchSleeperRosters(leagueId: string) {
  return sleeperFetch<SleeperRoster[]>(`/league/${leagueId}/rosters`);
}

/**
 * Sleeper's full player dump is ~5-10MB and has no filtering endpoint.
 * Callers should slice it down to fantasy-relevant, active players before
 * persisting — otherwise every sync upserts thousands of irrelevant rows.
 */
export async function fetchSleeperFantasyPlayers(): Promise<
  Record<string, SleeperPlayerRaw>
> {
  const all = await sleeperFetch<Record<string, SleeperPlayerRaw>>(
    "/players/nfl",
  );
  const filtered: Record<string, SleeperPlayerRaw> = {};
  for (const [id, player] of Object.entries(all)) {
    if (!player.position || !FANTASY_POSITIONS.has(player.position)) continue;
    if (player.active === false) continue;
    filtered[id] = player;
  }
  return filtered;
}

export function sleeperPlayerFullName(p: SleeperPlayerRaw): string {
  return p.full_name ?? [p.first_name, p.last_name].filter(Boolean).join(" ");
}
