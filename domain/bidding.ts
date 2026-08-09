import { fail, OK, type RoundStatus, type Verdict } from "./types";

export interface BidCheck {
  amount: number;
  roundStatus: RoundStatus;
  /** Epoch ms the round closes, or null if unbounded. */
  endsAtMs: number | null;
  /** Epoch ms "now" — passed in for deterministic tests. */
  nowMs: number;
  budgetRemaining: number;
}

/**
 * Validate a bid submission. Checks run in the same order the original
 * handler enforced, so the first failing rule is the one reported:
 * amount shape -> round open -> window still open -> within budget.
 */
export function validateBid(input: BidCheck): Verdict {
  const { amount, roundStatus, endsAtMs, nowMs, budgetRemaining } = input;

  if (typeof amount !== "number" || !Number.isInteger(amount) || amount < 0) {
    return fail("invalid-amount", "amount must be a non-negative integer");
  }
  if (roundStatus !== "open") {
    return fail("round-not-open", "Round is not open for bidding");
  }
  if (endsAtMs !== null && endsAtMs < nowMs) {
    return fail("window-closed", "Bidding window has closed");
  }
  if (amount > budgetRemaining) {
    return fail("over-budget", "Bid exceeds remaining budget");
  }
  return OK;
}
