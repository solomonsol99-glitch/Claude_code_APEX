"use client";

import Link from "next/link";
import { useActionState } from "react";
import { sendPasswordReset, type FormState } from "../actions";
import { Field, Status, Submit } from "../form-bits";

export default function ForgotPasswordPage() {
  const [state, action] = useActionState<FormState, FormData>(sendPasswordReset, {});
  return (
    <>
      <div>
        <h1 className="text-[1.6rem] font-bold">Reset your password</h1>
        <p className="mt-1.5 text-muted">We’ll email you a link to choose a new one.</p>
      </div>
      <form action={action} className="grid gap-[18px]" noValidate>
        <Field id="email" label="Work email" type="email" autoComplete="username" required />
        <Status state={state} />
        <Submit>Send reset link</Submit>
        <Link href="/login" className="text-center text-[0.85rem] text-accent">Back to sign in</Link>
      </form>
    </>
  );
}
