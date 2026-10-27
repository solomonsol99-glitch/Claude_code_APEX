import { Brand } from "@/components/logo";
import { signOut } from "../(auth)/actions";

export default function NoAccessPage() {
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <div className="grid max-w-[420px] gap-5 rounded-[14px] border border-line bg-surface px-7 py-8">
        <Brand />
        <h1 className="text-xl font-bold">Your account isn’t linked to a client yet</h1>
        <p className="text-muted">Ask your Coreset account manager to add you to your company’s account.</p>
        <form action={signOut}><button className="btn">Sign out</button></form>
      </div>
    </main>
  );
}
