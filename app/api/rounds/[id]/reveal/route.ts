import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const admin = await requireAdmin();
  if (!admin) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 403 });
  }

  const { id } = await params;

  const round = await prisma.nominationRound.findUnique({
    where: { id },
    include: { draftPool: true },
  });
  if (!round || round.leagueId !== admin.leagueId) {
    return NextResponse.json({ error: "Round not found" }, { status: 404 });
  }
  if (round.status !== "open") {
    return NextResponse.json({ error: "Round is not open" }, { status: 409 });
  }

  const bids = await prisma.bid.findMany({
    where: { nominationRoundId: id },
    orderBy: [{ amount: "desc" }, { submittedAt: "asc" }],
  });

  const winningBid = bids[0] ?? null;

  const result = await prisma.$transaction(async (tx) => {
    if (!winningBid) {
      await tx.draftPool.update({
        where: { id: round.draftPoolId },
        data: { isNominated: false },
      });
      return tx.nominationRound.update({
        where: { id },
        data: { status: "revealed", revealedAt: new Date() },
      });
    }

    await tx.manager.update({
      where: { id: winningBid.managerId },
      data: { budgetRemaining: { decrement: winningBid.amount } },
    });

    await tx.rosterEntry.create({
      data: {
        leagueId: round.leagueId,
        managerId: winningBid.managerId,
        sleeperPlayerId: round.draftPool.sleeperPlayerId,
        nominationRoundId: round.id,
        priceAcquired: winningBid.amount,
      },
    });

    await tx.draftPool.update({
      where: { id: round.draftPoolId },
      data: { isDrafted: true },
    });

    return tx.nominationRound.update({
      where: { id },
      data: {
        status: "revealed",
        revealedAt: new Date(),
        winningManagerId: winningBid.managerId,
        winningBidAmount: winningBid.amount,
      },
    });
  });

  return NextResponse.json({ round: result });
}
