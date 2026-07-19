import { NextRequest, NextResponse } from "next/server";
import { Prisma } from "@/app/generated/prisma/client";
import { prisma } from "@/lib/prisma";
import { generatePin, hashPin } from "@/lib/pin";
import {
  fetchSleeperFantasyPlayers,
  fetchSleeperLeague,
  fetchSleeperRosters,
  fetchSleeperUsers,
  sleeperPlayerFullName,
} from "@/lib/sleeper";

interface SyncRequestBody {
  sleeperLeagueId: string;
  adminBootstrapPin: string;
  budgetPerManager: number;
  adminSleeperUserId?: string;
}

function chunk<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

export async function POST(req: NextRequest) {
  const body = (await req.json()) as Partial<SyncRequestBody>;
  const { sleeperLeagueId, adminBootstrapPin, budgetPerManager, adminSleeperUserId } =
    body;

  if (!sleeperLeagueId || !adminBootstrapPin || !budgetPerManager) {
    return NextResponse.json(
      { error: "sleeperLeagueId, adminBootstrapPin, and budgetPerManager are required" },
      { status: 400 },
    );
  }

  if (adminBootstrapPin !== process.env.ADMIN_BOOTSTRAP_PIN) {
    return NextResponse.json({ error: "Invalid admin bootstrap PIN" }, { status: 403 });
  }

  const [sleeperLeague, sleeperUsers, sleeperRosters] = await Promise.all([
    fetchSleeperLeague(sleeperLeagueId),
    fetchSleeperUsers(sleeperLeagueId),
    fetchSleeperRosters(sleeperLeagueId),
  ]);

  const league = await prisma.league.upsert({
    where: { sleeperLeagueId },
    update: {
      name: sleeperLeague.name,
      seasonYear: Number(sleeperLeague.season),
      budgetPerManager,
    },
    create: {
      sleeperLeagueId,
      name: sleeperLeague.name,
      seasonYear: Number(sleeperLeague.season),
      budgetPerManager,
    },
  });

  const rosterByOwner = new Map(sleeperRosters.map((r) => [r.owner_id, r]));

  const managerResults: { displayName: string; pin: string; isAdmin: boolean }[] = [];

  for (const user of sleeperUsers) {
    const roster = rosterByOwner.get(user.user_id);
    const existing = await prisma.manager.findUnique({
      where: { leagueId_displayName: { leagueId: league.id, displayName: user.display_name } },
    });

    if (existing) {
      await prisma.manager.update({
        where: { id: existing.id },
        data: {
          sleeperUserId: user.user_id,
          sleeperRosterId: roster ? String(roster.roster_id) : null,
        },
      });
      continue;
    }

    const pin = generatePin();
    const pinHash = await hashPin(pin);
    const isAdmin = adminSleeperUserId ? user.user_id === adminSleeperUserId : false;

    await prisma.manager.create({
      data: {
        leagueId: league.id,
        sleeperUserId: user.user_id,
        sleeperRosterId: roster ? String(roster.roster_id) : null,
        displayName: user.display_name,
        pinHash,
        isAdmin,
        budgetRemaining: budgetPerManager,
      },
    });

    managerResults.push({ displayName: user.display_name, pin, isAdmin });
  }

  const fantasyPlayers = await fetchSleeperFantasyPlayers();
  const playerEntries = Object.entries(fantasyPlayers);

  for (const batch of chunk(playerEntries, 500)) {
    const rows = batch.map(([id, p]) =>
      Prisma.sql`(${id}, ${sleeperPlayerFullName(p)}, ${p.position ?? null}, ${p.team ?? null}, ${p.status ?? null}, ${JSON.stringify(p)}::jsonb, now())`,
    );
    await prisma.$executeRaw`
      INSERT INTO "SleeperPlayer" (id, "fullName", position, team, status, "rawJson", "lastSyncedAt")
      VALUES ${Prisma.join(rows)}
      ON CONFLICT (id) DO UPDATE SET
        "fullName" = EXCLUDED."fullName",
        position = EXCLUDED.position,
        team = EXCLUDED.team,
        status = EXCLUDED.status,
        "rawJson" = EXCLUDED."rawJson",
        "lastSyncedAt" = EXCLUDED."lastSyncedAt"
    `;
  }

  for (const batch of chunk(playerEntries, 500)) {
    const rows = batch.map(([id]) =>
      Prisma.sql`(gen_random_uuid()::text, ${league.id}, ${id}, false, false)`,
    );
    await prisma.$executeRaw`
      INSERT INTO "DraftPool" (id, "leagueId", "sleeperPlayerId", "isNominated", "isDrafted")
      VALUES ${Prisma.join(rows)}
      ON CONFLICT ("leagueId", "sleeperPlayerId") DO NOTHING
    `;
  }

  return NextResponse.json({
    league: { id: league.id, name: league.name, seasonYear: league.seasonYear },
    newManagers: managerResults,
    playersSynced: playerEntries.length,
  });
}
