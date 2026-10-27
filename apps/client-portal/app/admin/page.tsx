import type { Metadata } from "next";
import { requireAdmin } from "@/lib/auth";

export const metadata: Metadata = { title: "Admin" };

export default async function AdminPage() {
  const viewer = await requireAdmin();
  return (
    <section className="grid gap-5">
      <div>
        <p className="label">Admin console</p>
        <h1 className="text-[1.6rem] font-bold">Signed in as {viewer.email}</h1>
      </div>
      <div className="panel">
        <p className="text-muted">Two-step verification passed. Client management, approvals, deliveries, the audit log and seed lookup arrive in step 3.</p>
      </div>
    </section>
  );
}
