import type { Metadata } from "next";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { error } = await searchParams;
  return (
    <>
      <div>
        <h1 className="text-[1.6rem] font-bold">Sign in to your data</h1>
        <p className="mt-1.5 text-muted">Client access to the Coreset audience database.</p>
      </div>
      {error === "link" && (
        <p role="alert" className="notice bg-warn-bg text-warn">
          That link has expired or was already used. Request a new one below.
        </p>
      )}
      <LoginForm />
    </>
  );
}
