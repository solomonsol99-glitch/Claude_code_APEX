"use client";

import Link from "next/link";
import { useActionState, useState } from "react";
import { sendMagicLink, signIn, type FormState } from "../actions";
import { Field, Status, Submit } from "../form-bits";

export function LoginForm() {
  const [mode, setMode] = useState<"password" | "link">("password");
  const [pwState, pwAction] = useActionState<FormState, FormData>(signIn, {});
  const [linkState, linkAction] = useActionState<FormState, FormData>(sendMagicLink, {});

  if (mode === "link") {
    return (
      <form action={linkAction} className="grid gap-[18px]" noValidate>
        <Field id="email" label="Work email" type="email" autoComplete="username" required />
        <Status state={linkState} />
        <Submit>Email me a sign-in link</Submit>
        <button type="button" className="text-[0.85rem] text-accent" onClick={() => setMode("password")}>
          Use my password instead
        </button>
      </form>
    );
  }

  return (
    <form action={pwAction} className="grid gap-[18px]" noValidate>
      <Field id="email" label="Work email" type="email" autoComplete="username" required />
      <Field id="password" label="Password" type="password" autoComplete="current-password" placeholder="Your password" required />
      <div className="flex items-center justify-between text-[0.85rem]">
        <button type="button" className="text-accent" onClick={() => setMode("link")}>
          Email me a link
        </button>
        <Link href="/forgot-password" className="text-accent">Forgot password?</Link>
      </div>
      <Status state={pwState} />
      <Submit>Sign in</Submit>
      <p className="rounded-lg bg-sunk px-3 py-2.5 text-[0.8rem] text-muted">
        Clients see counts and send audiences to their channels. Records never download.
      </p>
    </form>
  );
}
