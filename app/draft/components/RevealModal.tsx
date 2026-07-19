import type { RoundStatus } from "@/hooks/useRoundStatus";

export function RevealModal({ round }: { round: RoundStatus }) {
  return (
    <div className="rounded-lg border border-black/10 p-4 dark:border-white/15">
      <h3 className="font-semibold">
        {round.player.fullName} ({round.player.position}
        {round.player.team ? `, ${round.player.team}` : ""})
      </h3>
      {round.winningManager ? (
        <p className="mt-1 text-sm">
          Won by <span className="font-medium">{round.winningManager.displayName}</span> for{" "}
          <span className="font-medium">${round.winningBidAmount}</span>
        </p>
      ) : (
        <p className="mt-1 text-sm text-black/60 dark:text-white/60">
          No bids were submitted — player returns to the pool.
        </p>
      )}
      {round.bids && round.bids.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {round.bids.map((b) => (
            <li key={b.managerId} className="flex justify-between">
              <span>{b.displayName}</span>
              <span className="font-mono">${b.amount}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
