"use client";

import { useCallback, useEffect, useState } from "react";
import { useRoundStatus } from "@/hooks/useRoundStatus";
import { CountdownTimer } from "./CountdownTimer";
import { BidForm } from "./BidForm";
import { RevealModal } from "./RevealModal";
import { DraftBoard } from "./DraftBoard";

export function DraftClient({
  managerId,
  initialBudgetRemaining,
}: {
  managerId: string;
  initialBudgetRemaining: number;
}) {
  const [roundId, setRoundId] = useState<string | null>(null);
  const [budgetRemaining, setBudgetRemaining] = useState(initialBudgetRemaining);
  const { round, secondsRemaining } = useRoundStatus(roundId);

  // Used by the bid form's onSubmitted callback (an event handler, not an
  // effect), so an immediate imperative refetch is fine here.
  const refreshCurrentRound = useCallback(async () => {
    const res = await fetch("/api/rounds/current", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    setRoundId(data.roundId ?? null);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      const res = await fetch("/api/rounds/current", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (!cancelled) setRoundId(data.roundId ?? null);
    }

    poll();
    const handle = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, []);

  useEffect(() => {
    if (round?.status !== "revealed") return;

    let cancelled = false;
    fetch("/api/board", { cache: "no-store" })
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        const me = data.managers?.find((m: { id: string }) => m.id === managerId);
        if (me) setBudgetRemaining(me.budgetRemaining);
      });

    return () => {
      cancelled = true;
    };
  }, [round?.status, round?.revealedAt, managerId]);

  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-sm font-medium text-black/60 dark:text-white/60">
          Current Nomination
        </h2>
        {!round && (
          <p className="rounded-lg border border-dashed border-black/15 p-4 text-sm text-black/60 dark:border-white/20 dark:text-white/60">
            Waiting for the admin to nominate a player...
          </p>
        )}

        {round?.status === "open" && (
          <div className="rounded-lg border border-black/10 p-4 dark:border-white/15">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">
                {round.player.fullName} ({round.player.position}
                {round.player.team ? `, ${round.player.team}` : ""})
              </h3>
              <CountdownTimer secondsRemaining={secondsRemaining} />
            </div>
            <div className="mt-4">
              <BidForm
                round={round}
                budgetRemaining={budgetRemaining}
                onSubmitted={refreshCurrentRound}
              />
            </div>
          </div>
        )}

        {round?.status === "revealed" && <RevealModal round={round} />}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-black/60 dark:text-white/60">
          Draft Board
        </h2>
        <DraftBoard refreshKey={round?.revealedAt ?? ""} />
      </section>
    </div>
  );
}
