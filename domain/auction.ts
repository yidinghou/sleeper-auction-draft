import type { BidInput } from "./types";

/**
 * Resolve the winner of a sealed-bid auction round.
 *
 * Rule: the highest amount wins; the earliest `submittedAt` breaks ties.
 * Returns `null` when there are no bids. Order-independent: the input array
 * may be in any order.
 */
export function resolveAuctionRound(
  bids: BidInput[],
): { winnerId: string; amount: number } | null {
  let best: BidInput | null = null;

  for (const bid of bids) {
    if (
      best === null ||
      bid.amount > best.amount ||
      (bid.amount === best.amount && bid.submittedAt < best.submittedAt)
    ) {
      best = bid;
    }
  }

  return best ? { winnerId: best.managerId, amount: best.amount } : null;
}
