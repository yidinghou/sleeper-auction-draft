import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

export async function GET(req: NextRequest) {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const search = req.nextUrl.searchParams.get("search")?.trim() ?? "";

  const entries = await prisma.draftPool.findMany({
    where: {
      leagueId: manager.leagueId,
      isDrafted: false,
      isNominated: false,
      ...(search
        ? { sleeperPlayer: { fullName: { contains: search, mode: "insensitive" } } }
        : {}),
    },
    include: { sleeperPlayer: true },
    orderBy: { sleeperPlayer: { fullName: "asc" } },
    take: 50,
  });

  return NextResponse.json({
    entries: entries.map((e) => ({
      draftPoolId: e.id,
      playerId: e.sleeperPlayer.id,
      fullName: e.sleeperPlayer.fullName,
      position: e.sleeperPlayer.position,
      team: e.sleeperPlayer.team,
    })),
  });
}
