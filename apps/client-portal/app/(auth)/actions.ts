"use server";

import { redirect } from "next/navigation";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { createServiceClient } from "@/lib/supabase/service";
import { appUrl } from "@/lib/env";
import { audit } from "@/lib/audit";
import { getViewer } from "@/lib/auth";

export type FormState = { error?: string; message?: string };

const email = z.string().trim().toLowerCase().pipe(z.email("Enter a valid work email."));
const SENT = "If that email has a Coreset account, we've sent a link. Check your inbox.";

export async function signIn(_: FormState, form: FormData): Promise<FormState> {
  const parsed = z
    .object({ email, password: z.string().min(1, "Enter your password.") })
    .safeParse({ email: form.get("email"), password: form.get("password") });
  if (!parsed.success) return { error: parsed.error.issues[0].message };

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithPassword(parsed.data);
  if (error || !data.user) return { error: "That email and password don't match." };

  const svc = createServiceClient();
  const [{ data: profile }, { data: membership }] = await Promise.all([
    svc.from("profiles").select("is_admin").eq("id", data.user.id).maybeSingle(),
    svc.from("memberships").select("org_id").eq("user_id", data.user.id).maybeSingle(),
  ]);
  await audit({ action: "auth.sign_in", via: "portal", userId: data.user.id, orgId: membership?.org_id, detail: { method: "password" } });

  redirect(profile?.is_admin ? "/mfa" : "/");
}

export async function sendMagicLink(_: FormState, form: FormData): Promise<FormState> {
  const parsed = email.safeParse(form.get("email"));
  if (!parsed.success) return { error: parsed.error.issues[0].message };

  const supabase = await createClient();
  // shouldCreateUser: false — accounts are created by Coreset admins only.
  await supabase.auth.signInWithOtp({
    email: parsed.data,
    options: { shouldCreateUser: false, emailRedirectTo: `${appUrl()}/auth/confirm?next=/` },
  });
  return { message: SENT }; // same reply either way, so the form can't be used to test which emails exist
}

export async function sendPasswordReset(_: FormState, form: FormData): Promise<FormState> {
  const parsed = email.safeParse(form.get("email"));
  if (!parsed.success) return { error: parsed.error.issues[0].message };

  const supabase = await createClient();
  await supabase.auth.resetPasswordForEmail(parsed.data, {
    redirectTo: `${appUrl()}/auth/confirm?next=/set-password`,
  });
  return { message: SENT };
}

export async function setPassword(_: FormState, form: FormData): Promise<FormState> {
  const parsed = z
    .object({
      password: z.string().min(12, "Use at least 12 characters."),
      confirm: z.string(),
    })
    .refine((v) => v.password === v.confirm, { message: "The two passwords don't match.", path: ["confirm"] })
    .safeParse({ password: form.get("password"), confirm: form.get("confirm") });
  if (!parsed.success) return { error: parsed.error.issues[0].message };

  const viewer = await getViewer();
  if (!viewer) return { error: "Your link has expired. Request a new one from the sign-in page." };

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ password: parsed.data.password });
  if (error) return { error: error.message };

  await audit({ action: "auth.password_set", via: "portal", userId: viewer.id, orgId: viewer.org?.id });
  redirect(viewer.isAdmin ? "/mfa" : "/");
}

export async function signOut(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
