"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [sleeperLeagueId, setSleeperLeagueId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sleeperLeagueId, displayName, pin }),
    });

    setSubmitting(false);

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Login failed");
      return;
    }

    const body = await res.json();
    router.push(body.manager.isAdmin ? "/admin" : "/draft");
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-black/10 p-6 dark:border-white/15"
      >
        <h1 className="text-lg font-semibold">Manager Login</h1>

        <div className="space-y-1">
          <label className="text-sm font-medium" htmlFor="sleeperLeagueId">
            League ID
          </label>
          <input
            id="sleeperLeagueId"
            className="w-full rounded border border-black/15 px-3 py-2 dark:border-white/20 dark:bg-transparent"
            value={sleeperLeagueId}
            onChange={(e) => setSleeperLeagueId(e.target.value)}
            required
          />
        </div>

        <div className="space-y-1">
          <label className="text-sm font-medium" htmlFor="displayName">
            Display Name
          </label>
          <input
            id="displayName"
            className="w-full rounded border border-black/15 px-3 py-2 dark:border-white/20 dark:bg-transparent"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
          />
        </div>

        <div className="space-y-1">
          <label className="text-sm font-medium" htmlFor="pin">
            PIN
          </label>
          <input
            id="pin"
            type="password"
            inputMode="numeric"
            className="w-full rounded border border-black/15 px-3 py-2 dark:border-white/20 dark:bg-transparent"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            required
          />
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-foreground py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
