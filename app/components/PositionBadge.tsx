import { positionColor } from "@/lib/positions";

export function PositionBadge({ position }: { position: string | null | undefined }) {
  if (!position) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${positionColor(position)}`}
    >
      {position}
    </span>
  );
}
