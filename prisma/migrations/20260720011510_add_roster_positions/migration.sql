-- AlterTable
ALTER TABLE "League" ADD COLUMN     "rosterPositions" TEXT[] DEFAULT ARRAY[]::TEXT[];
