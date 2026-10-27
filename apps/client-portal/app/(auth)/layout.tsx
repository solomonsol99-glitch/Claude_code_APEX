import { Brand } from "@/components/logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center px-4 py-8">
      <div className="grid w-full max-w-[400px] gap-[22px] rounded-[14px] border border-line bg-surface px-7 py-8">
        <Brand />
        {children}
      </div>
    </main>
  );
}
