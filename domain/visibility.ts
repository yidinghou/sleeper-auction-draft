import type { RoundStatus } from "./types";

export interface RoundBid {
  managerId: string;
  displayName: string;
  amount: number;
}

export interface RoundPlayer {
  id: string;
  fullName: string;
  position: string | null;
  team: string | null;
  projPtsPpr: number | null;
  projAdp2qb: number | null;
}

/**
 * A round plus the FULL set of bids. The persistence layer loads everything
 * and hands it here; this function is the single arbiter of what a viewer may
 * see. Callers must never expose raw bids directly.
 */
export interface RoundView {
  id: string;
  status: RoundStatus;
  opensAt: string | null;
  endsAt: string | null;
  revealedAt: string | null;
  player: RoundPlayer;
  bids: RoundBid[];
  winningManager: { id: string; displayName: string } | null;
  winningBidAmount: number | null;
}

export interface PublicRoundStatus {
  id: string;
  status: RoundStatus;
  opensAt: string | null;
  endsAt: string | null;
  revealedAt: string | null;
  player: RoundPlayer;
  hasSubmittedBid: boolean;
  yourBidAmount: number | null;
  winningManager: { id: string; displayName: string } | null;
  winningBidAmount: number | null;
  /** All bids, highest first — only present once the round is revealed. */
  bids: RoundBid[] | null;
}

/**
 * Project a round into what a specific viewer is allowed to see.
 *
 * Security property: before reveal, a viewer sees ONLY their own bid amount;
 * no other manager's bid ever leaks. After reveal, every bid and the winner
 * become visible to everyone.
 */
export function projectRoundStatus(
  round: RoundView,
  viewerManagerId: string,
): PublicRoundStatus {
  const revealed = round.status === "revealed";
  const ownBid = revealed
    ? null
    : (round.bids.find((b) => b.managerId === viewerManagerId) ?? null);

  return {
    id: round.id,
    status: round.status,
    opensAt: round.opensAt,
    endsAt: round.endsAt,
    revealedAt: round.revealedAt,
    player: round.player,
    hasSubmittedBid: ownBid !== null,
    yourBidAmount: ownBid?.amount ?? null,
    winningManager: revealed ? round.winningManager : null,
    winningBidAmount: revealed ? round.winningBidAmount : null,
    bids: revealed
      ? [...round.bids].sort((a, b) => b.amount - a.amount)
      : null,
  };
}
