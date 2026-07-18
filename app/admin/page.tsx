import { requireAdmin } from "@/lib/auth";
import { redirect } from "next/navigation";

export default async function AdminPage() {
  const manager = await requireAdmin();
  if (!manager) redirect("/login");

  return (
    <main className="flex-1 p-6">
      <h1 className="text-lg font-semibold">Commissioner Panel</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        Signed in as {manager.displayName}
      </p>
    </main>
  );
}
