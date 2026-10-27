"use client";

import { useFormStatus } from "react-dom";
import type { FormState } from "./actions";

export function Submit({ children, primary = true }: { children: React.ReactNode; primary?: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className={`btn ${primary ? "btn-primary" : ""} w-full py-[11px]`} disabled={pending}>
      {pending ? "One moment…" : children}
    </button>
  );
}

export function Status({ state }: { state: FormState }) {
  if (state.error) return <p role="alert" className="notice bg-bad-bg text-bad">{state.error}</p>;
  if (state.message) return <p role="status" className="notice bg-good-bg text-good">{state.message}</p>;
  return null;
}

export function Field(props: { id: string; label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const { id, label, ...rest } = props;
  return (
    <div className="grid gap-1.5">
      <label htmlFor={id} className="text-[0.85rem] font-medium">{label}</label>
      <input id={id} name={id} className="input" {...rest} />
    </div>
  );
}
