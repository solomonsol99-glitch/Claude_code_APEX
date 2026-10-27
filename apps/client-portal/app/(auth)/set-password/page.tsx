import type { Metadata } from "next";
import { requireViewer } from "@/lib/auth";
import { SetPasswordForm } from "./set-password-form";

export const metadata: Metadata = { title: "Set your password" };

export default async function SetPasswordPage() {
  const viewer = await requireViewer();
  return (
    <>
      <div>
        <h1 className="text-[1.6rem] font-bold">Set your password</h1>
        <p className="mt-1.5 text-muted">For {viewer.email}</p>
      </div>
      <SetPasswordForm />
    </>
  );
}
