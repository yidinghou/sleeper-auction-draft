import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth";

interface TimerRequestBody {
  endsAt: string;
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const admin = await requireAdmin();
  if (!admin) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 403 });
  }

  const { id } = await params;
  const body = (await req.json()) as Partial<TimerRequestBody>;
  if (!body.endsAt) {
    return NextResponse.json({ error: "endsAt is required" }, { status: 400 });
  }

  const endsAt = new Date(body.endsAt);
  if (Number.isNaN(endsAt.getTime())) {
    return NextResponse.json({ error: "endsAt must be a valid date" }, { status: 400 });
  }

  const round = await prisma.nominationRound.findUnique({ where: { id } });
  if (!round || round.leagueId !== admin.leagueId) {
    return NextResponse.json({ error: "Round not found" }, { status: 404 });
  }
  if (round.status !== "open") {
    return NextResponse.json({ error: "Round is not open" }, { status: 409 });
  }

  const updated = await prisma.nominationRound.update({
    where: { id },
    data: { endsAt },
  });

  return NextResponse.json({ round: updated });
}
