export function CountdownTimer({ secondsRemaining }: { secondsRemaining: number | null }) {
  if (secondsRemaining === null) return null;

  const mins = Math.floor(secondsRemaining / 60);
  const secs = secondsRemaining % 60;
  const urgent = secondsRemaining <= 10;

  return (
    <span
      className={`font-mono text-2xl tabular-nums ${urgent ? "text-red-600" : ""}`}
    >
      {mins}:{secs.toString().padStart(2, "0")}
    </span>
  );
}
