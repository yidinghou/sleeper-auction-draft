import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { verifyPin } from "@/lib/pin";
import { createSession } from "@/lib/auth";

interface LoginRequestBody {
  sleeperLeagueId: string;
  displayName: string;
  pin: string;
}

export async function POST(req: NextRequest) {
  const body = (await req.json()) as Partial<LoginRequestBody>;
  const { sleeperLeagueId, displayName, pin } = body;

  if (!sleeperLeagueId || !displayName || !pin) {
    return NextResponse.json(
      { error: "sleeperLeagueId, displayName, and pin are required" },
      { status: 400 },
    );
  }

  const league = await prisma.league.findUnique({ where: { sleeperLeagueId } });
  if (!league) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  const manager = await prisma.manager.findUnique({
    where: { leagueId_displayName: { leagueId: league.id, displayName } },
  });
  if (!manager) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  const valid = await verifyPin(pin, manager.pinHash);
  if (!valid) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  await createSession({
    managerId: manager.id,
    leagueId: league.id,
    isAdmin: manager.isAdmin,
  });

  return NextResponse.json({
    manager: {
      id: manager.id,
      displayName: manager.displayName,
      isAdmin: manager.isAdmin,
      budgetRemaining: manager.budgetRemaining,
    },
  });
}
