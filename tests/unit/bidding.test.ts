import { describe, it, expect } from "vitest";
import { validateBid, type BidCheck } from "@/domain/bidding";

const base: BidCheck = {
  amount: 10,
  roundStatus: "open",
  endsAtMs: 1_000,
  nowMs: 500,
  budgetRemaining: 100,
};

const codeOf = (v: ReturnType<typeof validateBid>) => (v.ok ? "ok" : v.code);

describe("validateBid", () => {
  it("accepts a valid bid within budget and window", () => {
    expect(validateBid(base).ok).toBe(true);
  });

  it("accepts a bid exactly equal to remaining budget", () => {
    expect(validateBid({ ...base, amount: 100 }).ok).toBe(true);
  });

  it("accepts a zero bid", () => {
    expect(validateBid({ ...base, amount: 0 }).ok).toBe(true);
  });

  it("accepts when there is no closing time", () => {
    expect(validateBid({ ...base, endsAtMs: null }).ok).toBe(true);
  });

  it("rejects a non-integer amount", () => {
    expect(codeOf(validateBid({ ...base, amount: 5.5 }))).toBe("invalid-amount");
  });

  it("rejects a negative amount", () => {
    expect(codeOf(validateBid({ ...base, amount: -1 }))).toBe("invalid-amount");
  });

  it("rejects NaN", () => {
    expect(codeOf(validateBid({ ...base, amount: Number.NaN }))).toBe("invalid-amount");
  });

  it.each(["pending", "revealed", "cancelled"] as const)(
    "rejects when the round status is %s",
    (status) => {
      expect(codeOf(validateBid({ ...base, roundStatus: status }))).toBe("round-not-open");
    },
  );

  it("rejects once the window has closed", () => {
    expect(codeOf(validateBid({ ...base, endsAtMs: 400, nowMs: 500 }))).toBe("window-closed");
  });

  it("rejects a bid over remaining budget", () => {
    expect(codeOf(validateBid({ ...base, amount: 101 }))).toBe("over-budget");
  });

  it("reports the amount problem before the budget problem", () => {
    // amount is both non-integer AND over budget; amount check wins.
    expect(codeOf(validateBid({ ...base, amount: 999.9 }))).toBe("invalid-amount");
  });
});
