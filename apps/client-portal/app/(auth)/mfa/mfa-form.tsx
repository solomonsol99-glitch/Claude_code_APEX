"use client";

import { useActionState, useState, useTransition } from "react";
import { startEnrollment, verifyCode, type EnrollState, type VerifyState } from "./actions";
import { Field, Submit } from "../form-bits";

export function MfaForm({ factorId }: { factorId?: string }) {
  const [enroll, setEnroll] = useState<EnrollState>({});
  const [starting, startTransition] = useTransition();
  const [state, action] = useActionState<VerifyState, FormData>(verifyCode, {});
  const id = factorId ?? enroll.factorId;

  if (!id) {
    return (
      <div className="grid gap-[18px]">
        {enroll.error && <p role="alert" className="notice bg-bad-bg text-bad">{enroll.error}</p>}
        <button
          type="button"
          className="btn btn-primary w-full py-[11px]"
          disabled={starting}
          onClick={() => startTransition(async () => setEnroll(await startEnrollment()))}
        >
          {starting ? "One moment…" : "Set up authenticator app"}
        </button>
      </div>
    );
  }

  return (
    <form action={action} className="grid gap-[18px]" noValidate>
      {enroll.qr && (
        <div className="grid justify-items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element -- data: URL SVG from Supabase */}
          <img src={enroll.qr} alt="QR code for your authenticator app" width={180} height={180} className="rounded-lg bg-white p-2" />
          <p className="text-center text-[0.8rem] text-muted">
            Scan this, or enter the key by hand:
            <br />
            <code className="num break-all text-ink">{enroll.secret}</code>
          </p>
        </div>
      )}
      <input type="hidden" name="factorId" value={id} />
      <Field id="code" label="6-digit code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={7} required />
      {state.error && <p role="alert" className="notice bg-bad-bg text-bad">{state.error}</p>}
      <Submit>Verify</Submit>
    </form>
  );
}
