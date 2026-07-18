-- CreateEnum
CREATE TYPE "RoundStatus" AS ENUM ('pending', 'open', 'revealed', 'cancelled');

-- CreateTable
CREATE TABLE "League" (
    "id" TEXT NOT NULL,
    "sleeperLeagueId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "seasonYear" INTEGER NOT NULL,
    "budgetPerManager" INTEGER NOT NULL,
    "draftStatus" TEXT NOT NULL DEFAULT 'not_started',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "League_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Manager" (
    "id" TEXT NOT NULL,
    "leagueId" TEXT NOT NULL,
    "sleeperUserId" TEXT,
    "sleeperRosterId" TEXT,
    "displayName" TEXT NOT NULL,
    "pinHash" TEXT NOT NULL,
    "isAdmin" BOOLEAN NOT NULL DEFAULT false,
    "budgetRemaining" INTEGER NOT NULL,

    CONSTRAINT "Manager_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "SleeperPlayer" (
    "id" TEXT NOT NULL,
    "fullName" TEXT NOT NULL,
    "position" TEXT,
    "team" TEXT,
    "status" TEXT,
    "rawJson" JSONB NOT NULL,
    "lastSyncedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "SleeperPlayer_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "DraftPool" (
    "id" TEXT NOT NULL,
    "leagueId" TEXT NOT NULL,
    "sleeperPlayerId" TEXT NOT NULL,
    "isNominated" BOOLEAN NOT NULL DEFAULT false,
    "isDrafted" BOOLEAN NOT NULL DEFAULT false,
    "nominationOrder" INTEGER,

    CONSTRAINT "DraftPool_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "NominationRound" (
    "id" TEXT NOT NULL,
    "leagueId" TEXT NOT NULL,
    "draftPoolId" TEXT NOT NULL,
    "nominatedByManagerId" TEXT,
    "status" "RoundStatus" NOT NULL DEFAULT 'pending',
    "opensAt" TIMESTAMP(3),
    "endsAt" TIMESTAMP(3),
    "revealedAt" TIMESTAMP(3),
    "winningManagerId" TEXT,
    "winningBidAmount" INTEGER,

    CONSTRAINT "NominationRound_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Bid" (
    "id" TEXT NOT NULL,
    "nominationRoundId" TEXT NOT NULL,
    "managerId" TEXT NOT NULL,
    "amount" INTEGER NOT NULL,
    "submittedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Bid_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RosterEntry" (
    "id" TEXT NOT NULL,
    "leagueId" TEXT NOT NULL,
    "managerId" TEXT NOT NULL,
    "sleeperPlayerId" TEXT NOT NULL,
    "nominationRoundId" TEXT,
    "priceAcquired" INTEGER NOT NULL,
    "acquiredAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "RosterEntry_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "League_sleeperLeagueId_key" ON "League"("sleeperLeagueId");

-- CreateIndex
CREATE UNIQUE INDEX "Manager_leagueId_displayName_key" ON "Manager"("leagueId", "displayName");

-- CreateIndex
CREATE UNIQUE INDEX "DraftPool_leagueId_sleeperPlayerId_key" ON "DraftPool"("leagueId", "sleeperPlayerId");

-- CreateIndex
CREATE UNIQUE INDEX "Bid_nominationRoundId_managerId_key" ON "Bid"("nominationRoundId", "managerId");

-- CreateIndex
CREATE UNIQUE INDEX "RosterEntry_nominationRoundId_key" ON "RosterEntry"("nominationRoundId");

-- AddForeignKey
ALTER TABLE "Manager" ADD CONSTRAINT "Manager_leagueId_fkey" FOREIGN KEY ("leagueId") REFERENCES "League"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "DraftPool" ADD CONSTRAINT "DraftPool_leagueId_fkey" FOREIGN KEY ("leagueId") REFERENCES "League"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "DraftPool" ADD CONSTRAINT "DraftPool_sleeperPlayerId_fkey" FOREIGN KEY ("sleeperPlayerId") REFERENCES "SleeperPlayer"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NominationRound" ADD CONSTRAINT "NominationRound_leagueId_fkey" FOREIGN KEY ("leagueId") REFERENCES "League"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NominationRound" ADD CONSTRAINT "NominationRound_draftPoolId_fkey" FOREIGN KEY ("draftPoolId") REFERENCES "DraftPool"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NominationRound" ADD CONSTRAINT "NominationRound_nominatedByManagerId_fkey" FOREIGN KEY ("nominatedByManagerId") REFERENCES "Manager"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NominationRound" ADD CONSTRAINT "NominationRound_winningManagerId_fkey" FOREIGN KEY ("winningManagerId") REFERENCES "Manager"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Bid" ADD CONSTRAINT "Bid_nominationRoundId_fkey" FOREIGN KEY ("nominationRoundId") REFERENCES "NominationRound"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Bid" ADD CONSTRAINT "Bid_managerId_fkey" FOREIGN KEY ("managerId") REFERENCES "Manager"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RosterEntry" ADD CONSTRAINT "RosterEntry_leagueId_fkey" FOREIGN KEY ("leagueId") REFERENCES "League"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RosterEntry" ADD CONSTRAINT "RosterEntry_managerId_fkey" FOREIGN KEY ("managerId") REFERENCES "Manager"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RosterEntry" ADD CONSTRAINT "RosterEntry_sleeperPlayerId_fkey" FOREIGN KEY ("sleeperPlayerId") REFERENCES "SleeperPlayer"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RosterEntry" ADD CONSTRAINT "RosterEntry_nominationRoundId_fkey" FOREIGN KEY ("nominationRoundId") REFERENCES "NominationRound"("id") ON DELETE SET NULL ON UPDATE CASCADE;
