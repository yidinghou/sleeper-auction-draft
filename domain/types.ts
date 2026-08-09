// Pure domain types. No Prisma / Next / I/O imports allowed in domain/.

export type RoundStatus = "pending" | "open" | "revealed" | "cancelled";

/** A sealed bid, reduced to the only fields the rules care about. */
export interface BidInput {
  managerId: string;
  amount: number;
  /** Epoch milliseconds. Passed in so the domain never reads a clock itself. */
  submittedAt: number;
}

/** Result of a rule check. Carries a stable machine code plus a human message. */
export type Verdict =
  | { ok: true }
  | { ok: false; code: string; message: string };

export const OK: Verdict = { ok: true };

export function fail(code: string, message: string): Verdict {
  return { ok: false, code, message };
}
