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
  roster_positions: string[];
}

export interface SleeperUser {
  user_id: string;
  display_name: string;
  metadata?: { team_name?: string };
}

export interface SleeperRoster {
  roster_id: number;
  owner_id: string;
  co_owners: string[] | null;
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

export interface SleeperDraft {
  draft_id: string;
  league_id: string;
  type: string; // "auction" | "snake" | "linear"
  status: string;
  created: number;
}

export interface SleeperDraftPick {
  player_id: string;
  picked_by: string; // sleeper user_id
  roster_id: number | null; // null for slots with no user attached
  draft_slot: number;
  round: number;
  pick_no: number;
  metadata?: {
    amount?: string;
    first_name?: string;
    last_name?: string;
    position?: string;
    team?: string;
  };
}

async function sleeperFetch<T>(path: string, noCache = false): Promise<T> {
  // Sleeper serves this API through a CDN that holds responses well past the
  // point of usefulness — a draft pick confirmed written still read as absent
  // minutes later. Only a unique URL busts it; `cache: "no-store"` alone does
  // not, since the staleness is upstream of us.
  const url = noCache
    ? `${SLEEPER_API_BASE}${path}${path.includes("?") ? "&" : "?"}_cb=${Date.now()}`
    : `${SLEEPER_API_BASE}${path}`;

  const res = await fetch(url, noCache ? { cache: "no-store" } : undefined);
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

export function fetchSleeperDrafts(leagueId: string) {
  return sleeperFetch<SleeperDraft[]>(`/league/${leagueId}/drafts`);
}

export interface SleeperDraftDetail extends SleeperDraft {
  slot_to_roster_id: Record<string, number>;
}

export function fetchSleeperDraft(draftId: string) {
  return sleeperFetch<SleeperDraftDetail>(`/draft/${draftId}`);
}

/**
 * Always fetched uncached: callers use this to decide what still needs
 * drafting, and acting on a stale list means re-pushing picks that already
 * exist.
 */
export function fetchSleeperDraftPicks(draftId: string) {
  return sleeperFetch<SleeperDraftPick[]>(`/draft/${draftId}/picks`, true);
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

export interface SleeperProjection {
  pts_ppr?: number;
  adp_2qb?: number;
}

export function fetchSleeperProjections(season: number) {
  return sleeperFetch<Record<string, SleeperProjection>>(
    `/projections/nfl/regular/${season}`,
  );
}

export function sleeperHeadshotUrl(playerId: string): string {
  return `https://sleepercdn.com/content/nfl/players/${playerId}.jpg`;
}

export function sleeperTeamLogoUrl(team: string): string {
  return `https://sleepercdn.com/images/team_logos/nfl/${team.toLowerCase()}.png`;
}
