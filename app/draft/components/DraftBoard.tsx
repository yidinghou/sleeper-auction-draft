"use client";

import { useEffect, useState } from "react";

interface BoardManager {
  id: string;
  displayName: string;
  budgetRemaining: number;
  roster: { playerId: string; fullName: string; position: string | null; priceAcquired: number }[];
}

export function DraftBoard({ refreshKey }: { refreshKey: string | number }) {
  const [managers, setManagers] = useState<BoardManager[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/board", { cache: "no-store" })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setManagers(data.managers ?? []);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {managers.map((m) => (
        <div key={m.id} className="rounded-lg border border-black/10 p-3 dark:border-white/15">
          <div className="flex items-baseline justify-between">
            <h3 className="font-medium">{m.displayName}</h3>
            <span className="text-sm font-mono">${m.budgetRemaining}</span>
          </div>
          <ul className="mt-2 space-y-1 text-sm text-black/70 dark:text-white/70">
            {m.roster.map((r) => (
              <li key={r.playerId} className="flex justify-between">
                <span>
                  {r.fullName} {r.position ? `(${r.position})` : ""}
                </span>
                <span className="font-mono">${r.priceAcquired}</span>
              </li>
            ))}
            {m.roster.length === 0 && <li className="text-black/40 dark:text-white/40">No players yet</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}
