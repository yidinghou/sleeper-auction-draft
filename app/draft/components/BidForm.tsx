"use client";

import { useState } from "react";
import type { RoundStatus } from "@/hooks/useRoundStatus";

export function BidForm({
  round,
  budgetRemaining,
  onSubmitted,
}: {
  round: RoundStatus;
  budgetRemaining: number;
  onSubmitted: () => void;
}) {
  const [amount, setAmount] = useState(round.yourBidAmount?.toString() ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const parsed = Number(amount);
    if (!Number.isInteger(parsed) || parsed < 0) {
      setError("Enter a whole number");
      return;
    }

    setSubmitting(true);
    const res = await fetch(`/api/rounds/${round.id}/bid`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: parsed }),
    });
    setSubmitting(false);

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Failed to submit bid");
      return;
    }

    onSubmitted();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-sm">$</span>
        <input
          type="number"
          min={0}
          max={budgetRemaining}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-28 rounded border border-black/15 px-3 py-2 dark:border-white/20 dark:bg-transparent"
          required
        />
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {round.hasSubmittedBid ? "Update sealed bid" : "Submit sealed bid"}
        </button>
      </div>
      {round.hasSubmittedBid && (
        <p className="text-xs text-black/60 dark:text-white/60">
          Your current sealed bid: ${round.yourBidAmount}. You can change it until the round closes.
        </p>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </form>
  );
}
