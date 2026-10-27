import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireViewer } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { MfaForm } from "./mfa-form";

export const metadata: Metadata = { title: "Two-step verification" };

export default async function MfaPage() {
  const viewer = await requireViewer();
  if (!viewer.isAdmin) redirect("/");
  if (viewer.aal === "aal2") redirect("/admin");

  const supabase = await createClient();
  const { data } = await supabase.auth.mfa.listFactors();
  const factor = data?.totp.find((f) => f.status === "verified");

  return (
    <>
      <div>
        <h1 className="text-[1.6rem] font-bold">Two-step verification</h1>
        <p className="mt-1.5 text-muted">
          {factor
            ? "Enter the 6-digit code from your authenticator app."
            : "Admin accounts need an authenticator app (1Password, Google Authenticator, Authy). Set it up once."}
        </p>
      </div>
      <MfaForm factorId={factor?.id} />
    </>
  );
}
