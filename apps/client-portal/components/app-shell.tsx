import Link from "next/link";
import { Brand } from "@/components/logo";
import { signOut } from "@/app/(auth)/actions";

export type NavItem = { href: string; label: string; icon: React.ReactNode };

export function AppShell(props: {
  nav: NavItem[];
  active: string;
  badge?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto grid max-w-[1320px] grid-cols-1 gap-5 px-4 py-4 md:grid-cols-[220px_minmax(0,1fr)]">
      <header className="col-span-full flex flex-wrap items-center gap-3 pb-1">
        <span className="mr-auto"><Brand size={26} /></span>
        {props.badge}
        <form action={signOut}>
          <button className="btn btn-ghost" type="submit">Sign out</button>
        </form>
      </header>
      <nav aria-label="Portal" className="flex gap-1 overflow-x-auto pb-1 md:sticky md:top-4 md:flex-col md:gap-0.5 md:self-start md:overflow-visible">
        {props.nav.map((n) => {
          const current = props.active === n.href;
          return (
            <Link
              key={n.href}
              href={n.href}
              aria-current={current ? "page" : undefined}
              className={`flex flex-none items-center gap-2.5 rounded-lg px-3 py-2 font-medium md:py-[9px] ${
                current
                  ? "bg-surface text-ink shadow-[inset_0_-2px_0_var(--accent)] md:shadow-[inset_3px_0_0_var(--accent)]"
                  : "text-muted hover:bg-sunk hover:text-ink"
              }`}
            >
              {n.icon}
              {n.label}
            </Link>
          );
        })}
        {props.footer && <div className="mt-[18px] hidden gap-1.5 border-t border-line p-3 text-[0.8rem] md:grid">{props.footer}</div>}
      </nav>
      <main className="grid min-w-0 content-start gap-5">{props.children}</main>
    </div>
  );
}

const svg = (d: React.ReactNode) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">{d}</svg>
);

export const icons = {
  overview: svg(<><rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" /><rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" /></>),
  builder: svg(<path d="M2 3h12l-4.5 5.5V13l-3 1.5V8.5z" />),
  datasets: svg(<><ellipse cx="8" cy="3.5" rx="5.5" ry="2" /><path d="M2.5 3.5v9c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-9M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" /></>),
  saved: svg(<path d="M4 2h8v12l-4-3-4 3z" />),
  deliveries: svg(<path d="M14 2 7 9M14 2l-4.5 12-2.5-5-5-2.5z" />),
  ai: svg(<path d="M8 1.5 9.4 6.6 14.5 8 9.4 9.4 8 14.5 6.6 9.4 1.5 8 6.6 6.6z" />),
  shield: svg(<path d="M8 1.5 2.5 4v4c0 3.2 2.3 5.6 5.5 6.5 3.2-.9 5.5-3.3 5.5-6.5V4z" />),
};
