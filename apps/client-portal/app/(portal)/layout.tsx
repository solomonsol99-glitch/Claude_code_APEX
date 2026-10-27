import { requireClient } from "@/lib/auth";
import { AppShell, icons } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/server";

const fmt = (n: number) => n.toLocaleString("en-US");

export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const viewer = await requireClient();
  const supabase = await createClient();
  const { data: org } = await supabase
    .from("orgs")
    .select("monthly_allowance, renewal_date, plans(name)")
    .eq("id", viewer.org.id)
    .single();
  const plan = (org?.plans as unknown as { name: string } | null)?.name ?? "Custom plan";

  return (
    <AppShell
      active="/"
      nav={[{ href: "/", label: "Overview", icon: icons.overview }]}
      badge={<span className="pill hidden bg-info-bg text-accent sm:inline-flex">Sample data · API not connected</span>}
      footer={
        <>
          <span className="label">Plan</span>
          <strong>{plan}</strong>
          <span className="text-muted">
            {fmt(Number(org?.monthly_allowance ?? 0))} delivered records / month
            {org?.renewal_date && <><br />Renews {new Date(org.renewal_date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</>}
          </span>
        </>
      }
    >
      {children}
    </AppShell>
  );
}
