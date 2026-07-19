import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

export async function GET() {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const round = await prisma.nominationRound.findFirst({
    where: { leagueId: manager.leagueId, status: { in: ["pending", "open", "revealed"] } },
    orderBy: { opensAt: "desc" },
  });

  return NextResponse.json({ roundId: round?.id ?? null, status: round?.status ?? null });
}
