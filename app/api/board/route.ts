import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

export async function GET() {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const managers = await prisma.manager.findMany({
    where: { leagueId: manager.leagueId },
    orderBy: { displayName: "asc" },
    include: {
      rosterEntries: { include: { sleeperPlayer: true } },
    },
  });

  return NextResponse.json({
    managers: managers.map((m) => ({
      id: m.id,
      displayName: m.displayName,
      budgetRemaining: m.budgetRemaining,
      roster: m.rosterEntries.map((r) => ({
        playerId: r.sleeperPlayerId,
        fullName: r.sleeperPlayer.fullName,
        position: r.sleeperPlayer.position,
        priceAcquired: r.priceAcquired,
      })),
    })),
  });
}
