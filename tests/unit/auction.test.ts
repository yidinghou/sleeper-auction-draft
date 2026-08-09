import { describe, it, expect } from "vitest";
import { resolveAuctionRound } from "@/domain/auction";
import type { BidInput } from "@/domain/types";

const bid = (managerId: string, amount: number, submittedAt: number): BidInput => ({
  managerId,
  amount,
  submittedAt,
});

describe("resolveAuctionRound", () => {
  it("returns null when there are no bids", () => {
    expect(resolveAuctionRound([])).toBeNull();
  });

  it("returns the only bid", () => {
    expect(resolveAuctionRound([bid("A", 5, 0)])).toEqual({ winnerId: "A", amount: 5 });
  });

  it("picks the highest amount", () => {
    expect(resolveAuctionRound([bid("A", 5, 0), bid("B", 9, 1)])).toEqual({
      winnerId: "B",
      amount: 9,
    });
  });

  it("breaks ties by earliest submittedAt", () => {
    expect(resolveAuctionRound([bid("A", 7, 10), bid("B", 7, 5)])).toEqual({
      winnerId: "B",
      amount: 7,
    });
  });

  it("breaks a 3-way tie by earliest submittedAt", () => {
    const result = resolveAuctionRound([
      bid("A", 7, 30),
      bid("C", 7, 10),
      bid("B", 7, 20),
    ]);
    expect(result).toEqual({ winnerId: "C", amount: 7 });
  });

  it("treats a zero bid as a valid winner", () => {
    expect(resolveAuctionRound([bid("A", 0, 0)])).toEqual({ winnerId: "A", amount: 0 });
  });

  it("is order-independent", () => {
    const bids = [bid("A", 3, 5), bid("B", 8, 9), bid("C", 8, 2)];
    const forward = resolveAuctionRound(bids);
    const reversed = resolveAuctionRound([...bids].reverse());
    expect(forward).toEqual({ winnerId: "C", amount: 8 });
    expect(reversed).toEqual(forward);
  });
});
