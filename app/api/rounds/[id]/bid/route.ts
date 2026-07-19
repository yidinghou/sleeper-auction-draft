import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireManager } from "@/lib/auth";

interface BidRequestBody {
  amount: number;
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const manager = await requireManager();
  if (!manager) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const body = (await req.json()) as Partial<BidRequestBody>;
  const amount = body.amount;

  if (typeof amount !== "number" || !Number.isInteger(amount) || amount < 0) {
    return NextResponse.json({ error: "amount must be a non-negative integer" }, { status: 400 });
  }

  const round = await prisma.nominationRound.findUnique({ where: { id } });
  if (!round || round.leagueId !== manager.leagueId) {
    return NextResponse.json({ error: "Round not found" }, { status: 404 });
  }
  if (round.status !== "open") {
    return NextResponse.json({ error: "Round is not open for bidding" }, { status: 409 });
  }
  if (round.endsAt && round.endsAt.getTime() < Date.now()) {
    return NextResponse.json({ error: "Bidding window has closed" }, { status: 409 });
  }
  if (amount > manager.budgetRemaining) {
    return NextResponse.json({ error: "Bid exceeds remaining budget" }, { status: 400 });
  }

  const bid = await prisma.bid.upsert({
    where: { nominationRoundId_managerId: { nominationRoundId: id, managerId: manager.id } },
    update: { amount, submittedAt: new Date() },
    create: { nominationRoundId: id, managerId: manager.id, amount },
  });

  return NextResponse.json({ ok: true, submittedAt: bid.submittedAt });
}
