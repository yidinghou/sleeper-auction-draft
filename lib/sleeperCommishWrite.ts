/**
 * Undocumented Sleeper commissioner API. This is NOT part of the public,
 * documented api.sleeper.app/v1 surface (which is read-only) — it's the
 * internal GraphQL endpoint Sleeper's own web client calls when a
 * commissioner force-sets a draft pick, discovered by inspecting network
 * traffic from an authenticated session.
 *
 * Risks, by design not hidden from callers:
 * - Unsupported and undocumented; Sleeper can change or remove it without
 *   notice.
 * - Using it programmatically is very likely outside Sleeper's Terms of
 *   Service (automated use of a non-public endpoint).
 * - Requires the caller's live Sleeper session token, which is
 *   credential-equivalent material. This module never persists it —
 *   callers must pass it in per-call and it is used immediately.
 *
 * Authentication, established empirically (see scripts/probe-auth.ts):
 * the token goes in an `authorization` header with NO "Bearer" prefix, and it
 * must be the *inner* API token, not the outer session JWT. Sending the outer
 * token, or using a `Cookie` header, returns `unauthorized` even for a league
 * commissioner — which is how this shipped originally and why it never worked.
 */

const SLEEPER_GRAPHQL_URL = "https://sleeper.com/graphql";

export interface ForceAuctionPickParams {
  /**
   * Either the inner Sleeper API token, or the outer `sleeper-web` session JWT
   * that wraps it — the latter is what you get from browser storage, so it is
   * unwrapped automatically.
   */
  sessionToken: string;
  draftId: string;
  playerId: string;
  slot: number;
  amount: number;
}

/**
 * The token Sleeper's web client stores is a session JWT whose payload carries
 * the real API token under `sleeperToken`. Accept either form so callers can
 * paste whichever they have.
 */
export function resolveApiToken(token: string): string {
  const trimmed = token.trim().replace(/^token=/, "");
  try {
    const payload = trimmed.split(".")[1];
    const claims = JSON.parse(
      Buffer.from(payload, "base64").toString("utf8"),
    ) as { sleeperToken?: unknown };
    if (typeof claims.sleeperToken === "string") return claims.sleeperToken;
  } catch {
    // Not a JWT we can read; assume it's already the API token.
  }
  return trimmed;
}

export interface ForceAuctionPickResult {
  draft_id: string;
  pick_no: number;
  player_id: string;
  picked_by: string | null;
}

const PICK_FIELDS = `
          draft_id
          pick_no
          player_id
          picked_by
          is_keeper
          metadata
          reactions`;

/**
 * Runs a commissioner mutation and unwraps its payload.
 *
 * Sleeper returns HTTP 200 with an `errors` array for domain failures
 * (unauthorized, "This player is not draftable"), so a 200 alone means
 * nothing — the errors array is the real signal.
 */
async function sleeperMutation<T>(
  sessionToken: string,
  operationName: string,
  query: string,
): Promise<T> {
  const res = await fetch(SLEEPER_GRAPHQL_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      authorization: resolveApiToken(sessionToken),
    },
    body: JSON.stringify({ operationName, query, variables: {} }),
  });

  if (!res.ok) {
    throw new Error(`Sleeper ${operationName} failed: ${res.status} ${res.statusText}`);
  }

  const body = await res.json();
  if (body.errors) {
    throw new Error(`Sleeper ${operationName} returned errors: ${JSON.stringify(body.errors)}`);
  }

  return body.data[operationName] as T;
}

export function forceAuctionPick({
  sessionToken,
  draftId,
  playerId,
  slot,
  amount,
}: ForceAuctionPickParams): Promise<ForceAuctionPickResult> {
  return sleeperMutation<ForceAuctionPickResult>(
    sessionToken,
    "draft_force_auction_pick",
    `mutation draft_force_auction_pick {
        draft_force_auction_pick(sport: "nfl",player_id: "${playerId}",draft_id: "${draftId}",slot: ${slot},amount: ${amount},is_keeper: false){${PICK_FIELDS}
        }
      }`,
  );
}

export interface RemovePickParams {
  sessionToken: string;
  draftId: string;
  /** Sleeper's pick_no — removal is keyed by pick number, not player. */
  pickNo: number;
}

/**
 * Removes a single pick from a draft board. This is the "clear player" action
 * in Sleeper's commissioner UI; signature taken from their own web bundle.
 *
 * Destructive and not individually confirmed — callers are responsible for
 * confirming intent before looping over a board.
 */
export function removePick({
  sessionToken,
  draftId,
  pickNo,
}: RemovePickParams): Promise<ForceAuctionPickResult> {
  return sleeperMutation<ForceAuctionPickResult>(
    sessionToken,
    "draft_remove_pick",
    `mutation draft_remove_pick {
        draft_remove_pick(sport: "nfl",draft_id: "${draftId}",pick_no: ${pickNo}){${PICK_FIELDS}
        }
      }`,
  );
}
