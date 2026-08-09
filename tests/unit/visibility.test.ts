import { describe, it, expect } from "vitest";
import { projectRoundStatus, type RoundView } from "@/domain/visibility";

const player = {
  id: "player-1",
  fullName: "Test Player",
  position: "RB",
  team: "SF",
  projPtsPpr: 200,
  projAdp2qb: 12,
};

const roundView = (overrides: Partial<RoundView>): RoundView => ({
  id: "round-1",
  status: "open",
  opensAt: null,
  endsAt: null,
  revealedAt: null,
  player,
  bids: [],
  winningManager: null,
  winningBidAmount: null,
  ...overrides,
});

describe("projectRoundStatus", () => {
  it("shows a viewer only their own bid while the round is open", () => {
    const round = roundView({
      bids: [
        { managerId: "me", displayName: "Me", amount: 15 },
        { managerId: "rival", displayName: "Rival", amount: 40 },
      ],
    });
    const view = projectRoundStatus(round, "me");
    expect(view.hasSubmittedBid).toBe(true);
    expect(view.yourBidAmount).toBe(15);
    // Other bids are never exposed pre-reveal.
    expect(view.bids).toBeNull();
    expect(view.winningManager).toBeNull();
  });

  it("reports no submitted bid when the viewer has not bid", () => {
    const round = roundView({
      bids: [{ managerId: "rival", displayName: "Rival", amount: 40 }],
    });
    const view = projectRoundStatus(round, "me");
    expect(view.hasSubmittedBid).toBe(false);
    expect(view.yourBidAmount).toBeNull();
    expect(view.bids).toBeNull();
  });

  it("does not leak another manager's bid to the viewer pre-reveal", () => {
    const round = roundView({
      bids: [{ managerId: "rival", displayName: "Rival", amount: 40 }],
    });
    const view = projectRoundStatus(round, "me");
    const serialized = JSON.stringify(view);
    expect(serialized).not.toContain("40");
    expect(serialized).not.toContain("Rival");
  });

  it("reveals all bids (highest first) and the winner to everyone once revealed", () => {
    const round = roundView({
      status: "revealed",
      revealedAt: "2026-01-01T00:00:00.000Z",
      winningManager: { id: "rival", displayName: "Rival" },
      winningBidAmount: 40,
      bids: [
        { managerId: "me", displayName: "Me", amount: 15 },
        { managerId: "rival", displayName: "Rival", amount: 40 },
      ],
    });
    const view = projectRoundStatus(round, "me");
    expect(view.winningManager).toEqual({ id: "rival", displayName: "Rival" });
    expect(view.winningBidAmount).toBe(40);
    expect(view.bids?.map((b) => b.amount)).toEqual([40, 15]);
  });

  it("reveals with no winner when the round had no bids", () => {
    const round = roundView({
      status: "revealed",
      revealedAt: "2026-01-01T00:00:00.000Z",
      bids: [],
    });
    const view = projectRoundStatus(round, "me");
    expect(view.winningManager).toBeNull();
    expect(view.winningBidAmount).toBeNull();
    expect(view.bids).toEqual([]);
  });
});
