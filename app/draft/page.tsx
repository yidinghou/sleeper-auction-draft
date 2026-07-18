import { requireManager } from "@/lib/auth";
import { redirect } from "next/navigation";

export default async function DraftPage() {
  const manager = await requireManager();
  if (!manager) redirect("/login");

  return (
    <main className="flex-1 p-6">
      <h1 className="text-lg font-semibold">
        Welcome, {manager.displayName}
      </h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        Budget remaining: ${manager.budgetRemaining}
      </p>
    </main>
  );
}
