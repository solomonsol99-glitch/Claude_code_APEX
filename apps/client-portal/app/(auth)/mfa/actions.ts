"use server";

import { redirect } from "next/navigation";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { getViewer } from "@/lib/auth";
import { audit } from "@/lib/audit";

export type EnrollState = { error?: string; factorId?: string; qr?: string; secret?: string };
export type VerifyState = { error?: string };

async function adminOnly() {
  const viewer = await getViewer();
  if (!viewer) redirect("/login");
  if (!viewer.isAdmin) redirect("/");
  return viewer;
}

export async function startEnrollment(): Promise<EnrollState> {
  await adminOnly();
  const supabase = await createClient();
  // Clear any half-finished enrollment so the user can start over.
  const { data: factors } = await supabase.auth.mfa.listFactors();
  for (const f of factors?.all ?? []) {
    if (f.status === "unverified") await supabase.auth.mfa.unenroll({ factorId: f.id });
  }
  const { data, error } = await supabase.auth.mfa.enroll({ factorType: "totp", friendlyName: "Coreset admin" });
  if (error || !data) return { error: error?.message ?? "Could not start setup." };
  return { factorId: data.id, qr: data.totp.qr_code, secret: data.totp.secret };
}

export async function verifyCode(_: VerifyState, form: FormData): Promise<VerifyState> {
  const viewer = await adminOnly();
  const parsed = z
    .object({ factorId: z.string().min(1), code: z.string().regex(/^\d{6}$/, "Enter the 6-digit code from your app.") })
    .safeParse({ factorId: form.get("factorId"), code: String(form.get("code") ?? "").replace(/\s/g, "") });
  if (!parsed.success) return { error: parsed.error.issues[0].message };

  const supabase = await createClient();
  const { error } = await supabase.auth.mfa.challengeAndVerify(parsed.data);
  if (error) return { error: "That code didn't work. Wait for the next one and try again." };

  await audit({ action: "auth.mfa_verified", via: "admin", userId: viewer.id });
  redirect("/admin");
}
