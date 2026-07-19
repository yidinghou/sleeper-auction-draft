import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

const ROUND_DURATION_MS = 60_000;

interface CreateRoundBody {
  draftPoolId: string;
}

export async function GET() {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const rounds = await prisma.nominationRound.findMany({
    where: { leagueId: manager.leagueId },
    orderBy: { opensAt: "desc" },
    take: 25,
    include: {
      draftPool: { include: { sleeperPlayer: true } },
      winningManager: true,
    },
  });

  return NextResponse.json({ rounds });
}

export async function POST(req: NextRequest) {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await req.json()) as Partial<CreateRoundBody>;
  if (!body.draftPoolId) {
    return NextResponse.json({ error: "draftPoolId is required" }, { status: 400 });
  }

  const existingOpen = await prisma.nominationRound.findFirst({
    where: { leagueId: manager.leagueId, status: { in: ["pending", "open"] } },
  });
  if (existingOpen) {
    return NextResponse.json(
      { error: "A round is already pending or open for this league" },
      { status: 409 },
    );
  }

  const poolEntry = await prisma.draftPool.findUnique({
    where: { id: body.draftPoolId },
  });
  if (!poolEntry || poolEntry.leagueId !== manager.leagueId) {
    return NextResponse.json({ error: "Invalid draftPoolId" }, { status: 400 });
  }
  if (poolEntry.isDrafted || poolEntry.isNominated) {
    return NextResponse.json(
      { error: "Player already nominated or drafted" },
      { status: 409 },
    );
  }

  const now = new Date();
  const endsAt = new Date(now.getTime() + ROUND_DURATION_MS);

  const round = await prisma.$transaction(async (tx) => {
    await tx.draftPool.update({
      where: { id: poolEntry.id },
      data: { isNominated: true },
    });
    return tx.nominationRound.create({
      data: {
        leagueId: manager.leagueId,
        draftPoolId: poolEntry.id,
        nominatedByManagerId: manager.id,
        status: "open",
        opensAt: now,
        endsAt,
      },
    });
  });

  return NextResponse.json({ round });
}
