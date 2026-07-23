"use client";

import { useState } from "react";
import Image from "next/image";
import { sleeperHeadshotUrl, sleeperTeamLogoUrl } from "@/lib/sleeper";

export interface PlayerMetaProps {
  playerId: string;
  fullName: string;
  position?: string | null;
  team?: string | null;
  projPtsPpr?: number | null;
  projAdp2qb?: number | null;
}

export function PlayerMeta({
  playerId,
  fullName,
  position,
  team,
  projPtsPpr,
  projAdp2qb,
}: PlayerMetaProps) {
  const [headshotFailed, setHeadshotFailed] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);

  return (
    <div className="flex items-center gap-2">
      {!headshotFailed ? (
        <Image
          src={sleeperHeadshotUrl(playerId)}
          alt={fullName}
          width={32}
          height={32}
          className="shrink-0 rounded-full bg-surface-light object-cover"
          onError={() => setHeadshotFailed(true)}
          unoptimized
        />
      ) : (
        <div className="h-8 w-8 shrink-0 rounded-full bg-surface-light" />
      )}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-text-primary">{fullName}</span>
          {team && !logoFailed && (
            <Image
              src={sleeperTeamLogoUrl(team)}
              alt={team}
              width={14}
              height={14}
              onError={() => setLogoFailed(true)}
              unoptimized
            />
          )}
        </div>
        <div className="flex flex-wrap gap-x-2 text-xs text-text-secondary">
          {position && <span>{position}{team ? ` - ${team}` : ""}</span>}
          {projPtsPpr != null && (
            <span className="text-accent-gold">{projPtsPpr.toFixed(1)} proj pts</span>
          )}
          {projAdp2qb != null && <span>ADP {projAdp2qb.toFixed(1)}</span>}
        </div>
      </div>
    </div>
  );
}
