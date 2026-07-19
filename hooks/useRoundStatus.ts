"use client";

import { useEffect, useMemo, useState } from "react";

export interface RoundStatus {
  id: string;
  status: "pending" | "open" | "revealed" | "cancelled";
  opensAt: string | null;
  endsAt: string | null;
  revealedAt: string | null;
  player: { id: string; fullName: string; position: string | null; team: string | null };
  hasSubmittedBid: boolean;
  yourBidAmount: number | null;
  winningManager: { id: string; displayName: string } | null;
  winningBidAmount: number | null;
  bids?: { managerId: string; displayName: string; amount: number }[];
}

const POLL_INTERVAL_MS = 2500;

export function useRoundStatus(roundId: string | null) {
  const [round, setRound] = useState<RoundStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!roundId) return;

    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`/api/rounds/${roundId}/status`, { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as RoundStatus;
        if (!cancelled) {
          setRound(data);
          setLoading(false);
        }
      } catch {
        // transient network error, next poll tick will retry
      }
    }

    poll();
    const pollHandle = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(pollHandle);
    };
  }, [roundId]);

  useEffect(() => {
    const tickHandle = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tickHandle);
  }, []);

  // round can lag behind a roundId change by one poll tick; only trust it
  // once it actually reflects the id we're currently tracking.
  const effectiveRound = roundId && round?.id === roundId ? round : null;

  const secondsRemaining = useMemo(() => {
    if (!effectiveRound?.endsAt || effectiveRound.status !== "open") return null;
    return Math.max(0, Math.round((new Date(effectiveRound.endsAt).getTime() - now) / 1000));
  }, [effectiveRound, now]);

  return { round: effectiveRound, secondsRemaining, loading: roundId ? loading : false };
}
