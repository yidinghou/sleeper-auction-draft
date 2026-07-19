import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;

  const round = await prisma.nominationRound.findUnique({
    where: { id },
    include: {
      draftPool: { include: { sleeperPlayer: true } },
      winningManager: true,
    },
  });
  if (!round || round.leagueId !== manager.leagueId) {
    return NextResponse.json({ error: "Round not found" }, { status: 404 });
  }

  const ownBid =
    round.status === "open" || round.status === "pending"
      ? await prisma.bid.findUnique({
          where: { nominationRoundId_managerId: { nominationRoundId: id, managerId: manager.id } },
        })
      : null;

  const revealedBids =
    round.status === "revealed"
      ? await prisma.bid.findMany({
          where: { nominationRoundId: id },
          include: { manager: true },
          orderBy: { amount: "desc" },
        })
      : null;

  return NextResponse.json({
    id: round.id,
    status: round.status,
    opensAt: round.opensAt,
    endsAt: round.endsAt,
    revealedAt: round.revealedAt,
    player: {
      id: round.draftPool.sleeperPlayer.id,
      fullName: round.draftPool.sleeperPlayer.fullName,
      position: round.draftPool.sleeperPlayer.position,
      team: round.draftPool.sleeperPlayer.team,
    },
    hasSubmittedBid: Boolean(ownBid),
    yourBidAmount: ownBid?.amount ?? null,
    winningManager: round.winningManager
      ? { id: round.winningManager.id, displayName: round.winningManager.displayName }
      : null,
    winningBidAmount: round.winningBidAmount,
    bids: revealedBids?.map((b) => ({
      managerId: b.managerId,
      displayName: b.manager.displayName,
      amount: b.amount,
    })),
  });
}
