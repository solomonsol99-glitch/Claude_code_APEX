import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type Viewer = {
  id: string;
  email: string;
  fullName: string | null;
  isAdmin: boolean;
  aal: "aal1" | "aal2";
  org: { id: string; name: string; role: "owner" | "member" } | null;
};

/**
 * The signed-in user, verified against Supabase Auth on every request.
 * Returns null when nobody is signed in. Cached per request.
 */
export const getViewer = cache(async (): Promise<Viewer | null> => {
  const supabase = await createClient();
  const { data: claimsData } = await supabase.auth.getClaims();
  const claims = claimsData?.claims;
  if (!claims?.sub) return null;

  const [{ data: profile }, { data: membership }] = await Promise.all([
    supabase.from("profiles").select("email, full_name, is_admin").eq("id", claims.sub).maybeSingle(),
    supabase.from("memberships").select("role, orgs(id, name)").eq("user_id", claims.sub).maybeSingle(),
  ]);
  if (!profile) return null;

  const org = membership?.orgs as unknown as { id: string; name: string } | null;
  return {
    id: claims.sub,
    email: profile.email,
    fullName: profile.full_name,
    isAdmin: profile.is_admin,
    aal: claims.aal === "aal2" ? "aal2" : "aal1",
    org: org ? { id: org.id, name: org.name, role: membership!.role } : null,
  };
});

export async function requireViewer(): Promise<Viewer> {
  const viewer = await getViewer();
  if (!viewer) redirect("/login");
  return viewer;
}

/** A client user with an org. Admins without an org go to the admin console. */
export async function requireClient(): Promise<Viewer & { org: NonNullable<Viewer["org"]> }> {
  const viewer = await requireViewer();
  if (!viewer.org) redirect(viewer.isAdmin ? "/admin" : "/no-access");
  return viewer as Viewer & { org: NonNullable<Viewer["org"]> };
}

/** A Coreset admin whose session passed MFA. Sends admins without MFA to set it up. */
export async function requireAdmin(): Promise<Viewer> {
  const viewer = await requireViewer();
  if (!viewer.isAdmin) redirect("/");
  if (viewer.aal !== "aal2") redirect("/mfa");
  return viewer;
}

/** Only allow same-site relative redirects. */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  return next;
}
