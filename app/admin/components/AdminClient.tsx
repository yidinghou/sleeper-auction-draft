"use client";

import { useCallback, useEffect, useState } from "react";
import { useRoundStatus } from "@/hooks/useRoundStatus";
import { CountdownTimer } from "@/app/draft/components/CountdownTimer";
import { RevealModal } from "@/app/draft/components/RevealModal";

interface PoolEntry {
  draftPoolId: string;
  playerId: string;
  fullName: string;
  position: string | null;
  team: string | null;
}

export function AdminClient() {
  const [roundId, setRoundId] = useState<string | null>(null);
  const { round, secondsRemaining } = useRoundStatus(roundId);

  const [search, setSearch] = useState("");
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [nominating, setNominating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [extendSeconds, setExtendSeconds] = useState(30);

  // Used by button handlers below (event handlers, not effects), so an
  // immediate imperative refetch is fine here.
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

  const canNominate = !round || round.status === "revealed";

  useEffect(() => {
    if (!canNominate) return; // only browse the pool between rounds
    const handle = setTimeout(() => {
      fetch(`/api/draft-pool?search=${encodeURIComponent(search)}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((data) => setPool(data.entries ?? []));
    }, 250);
    return () => clearTimeout(handle);
  }, [search, canNominate]);

  async function nominate(draftPoolId: string) {
    setNominating(draftPoolId);
    setError(null);
    const res = await fetch("/api/rounds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draftPoolId }),
    });
    setNominating(null);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Failed to nominate");
      return;
    }
    refreshCurrentRound();
  }

  async function extendTimer() {
    if (!roundId) return;
    const endsAt = new Date(Date.now() + extendSeconds * 1000).toISOString();
    await fetch(`/api/rounds/${roundId}/timer`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endsAt }),
    });
  }

  async function forceReveal() {
    if (!roundId) return;
    await fetch(`/api/rounds/${roundId}/reveal`, { method: "POST" });
    refreshCurrentRound();
  }

  async function triggerSync(formData: FormData) {
    setError(null);
    const res = await fetch("/api/sleeper/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sleeperLeagueId: formData.get("sleeperLeagueId"),
        adminBootstrapPin: formData.get("adminBootstrapPin"),
        budgetPerManager: Number(formData.get("budgetPerManager")),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Sync failed");
      return;
    }
    const body = await res.json();
    alert(
      `Synced ${body.league.name}: ${body.newManagers.length} new managers, ${body.playersSynced} players.`,
    );
  }

  return (
    <div className="space-y-8">
      <section className="rounded-lg border border-black/10 p-4 dark:border-white/15">
        <h2 className="mb-3 font-semibold">Sleeper League Sync</h2>
        <form
          action={triggerSync}
          className="flex flex-wrap items-end gap-3"
        >
          <label className="flex flex-col gap-1 text-sm">
            League ID
            <input name="sleeperLeagueId" className="rounded border border-black/15 px-2 py-1 dark:border-white/20 dark:bg-transparent" required />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Budget / manager
            <input name="budgetPerManager" type="number" defaultValue={200} className="w-28 rounded border border-black/15 px-2 py-1 dark:border-white/20 dark:bg-transparent" required />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Admin bootstrap PIN
            <input name="adminBootstrapPin" type="password" className="rounded border border-black/15 px-2 py-1 dark:border-white/20 dark:bg-transparent" required />
          </label>
          <button type="submit" className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background">
            Sync
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-black/10 p-4 dark:border-white/15">
        <h2 className="mb-3 font-semibold">Round Control</h2>

        {round?.status === "open" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">{round.player.fullName}</h3>
              <CountdownTimer secondsRemaining={secondsRemaining} />
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={extendSeconds}
                onChange={(e) => setExtendSeconds(Number(e.target.value))}
                className="w-20 rounded border border-black/15 px-2 py-1 dark:border-white/20 dark:bg-transparent"
              />
              <button onClick={extendTimer} className="rounded border border-black/15 px-3 py-1 text-sm dark:border-white/20">
                Set timer to +{extendSeconds}s from now
              </button>
              <button onClick={forceReveal} className="rounded bg-red-600 px-3 py-1 text-sm text-white">
                Force reveal now
              </button>
            </div>
          </div>
        )}

        {round?.status === "revealed" && <RevealModal round={round} />}

        {canNominate && (
          <div className="mt-4 space-y-3">
            <input
              placeholder="Search players..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded border border-black/15 px-3 py-2 dark:border-white/20 dark:bg-transparent"
            />
            <ul className="max-h-80 divide-y divide-black/10 overflow-y-auto dark:divide-white/10">
              {pool.map((p) => (
                <li key={p.draftPoolId} className="flex items-center justify-between py-2 text-sm">
                  <span>
                    {p.fullName} {p.position ? `(${p.position}${p.team ? `, ${p.team}` : ""})` : ""}
                  </span>
                  <button
                    onClick={() => nominate(p.draftPoolId)}
                    disabled={nominating === p.draftPoolId}
                    className="rounded border border-black/15 px-3 py-1 text-xs dark:border-white/20"
                  >
                    Nominate
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </section>
    </div>
  );
}
