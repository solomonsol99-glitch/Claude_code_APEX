"use client";

import { useActionState } from "react";
import { setPassword, type FormState } from "../actions";
import { Field, Status, Submit } from "../form-bits";

export function SetPasswordForm() {
  const [state, action] = useActionState<FormState, FormData>(setPassword, {});
  return (
    <form action={action} className="grid gap-[18px]" noValidate>
      <Field id="password" label="New password" type="password" autoComplete="new-password" minLength={12} required />
      <Field id="confirm" label="Confirm password" type="password" autoComplete="new-password" required />
      <p className="text-[0.8rem] text-muted">At least 12 characters.</p>
      <Status state={state} />
      <Submit>Save password</Submit>
    </form>
  );
}
