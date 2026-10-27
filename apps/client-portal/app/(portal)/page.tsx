import type { Metadata } from "next";
import { requireClient } from "@/lib/auth";

export const metadata: Metadata = { title: "Overview" };

export default async function OverviewPage() {
  const viewer = await requireClient();
  const first = viewer.fullName?.split(" ")[0];
  return (
    <section className="grid gap-5">
      <div>
        <p className="label">{viewer.org.name}</p>
        <h1 className="text-[1.6rem] font-bold">Welcome back{first ? `, ${first}` : ""}</h1>
      </div>
      <div className="panel">
        <p className="text-muted">
          You’re signed in. The audience builder, datasets, saved audiences, deliveries and AI connector arrive in the next build step.
        </p>
      </div>
    </section>
  );
}
