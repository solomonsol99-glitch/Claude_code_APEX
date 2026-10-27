import { requireAdmin } from "@/lib/auth";
import { AppShell, icons } from "@/components/app-shell";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireAdmin();
  return (
    <AppShell
      active="/admin"
      nav={[{ href: "/admin", label: "Clients", icon: icons.shield }]}
      badge={<span className="pill bg-warn-bg text-warn">Coreset admin</span>}
    >
      {children}
    </AppShell>
  );
}
