"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Tab = {
  href: string;
  label: string;
  icon: (active: boolean) => React.ReactNode;
};

const stroke = (active: boolean) => (active ? "var(--color-brand)" : "currentColor");

const TABS: Tab[] = [
  {
    href: "/",
    label: "Home",
    icon: (a) => (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={stroke(a)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3 10.5 12 3l9 7.5" />
        <path d="M5 9.5V21h14V9.5" />
      </svg>
    ),
  },
  {
    href: "/recipes",
    label: "Recipes",
    icon: (a) => (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={stroke(a)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M4 4v16" />
        <path d="M8 4v16" />
        <path d="M8 12H4" />
        <path d="M15 3c-1.7 1.5-2.5 3.5-2.5 6.5S13.3 15 15 16.5V21" />
      </svg>
    ),
  },
  {
    href: "/list",
    label: "List",
    icon: (a) => (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={stroke(a)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M9 6h11" />
        <path d="M9 12h11" />
        <path d="M9 18h11" />
        <path d="m4 6 1 1 2-2" />
        <path d="m4 12 1 1 2-2" />
        <path d="m4 18 1 1 2-2" />
      </svg>
    ),
  },
  {
    href: "/settings",
    label: "Settings",
    icon: (a) => (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={stroke(a)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
      </svg>
    ),
  },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export default function BottomTabBar() {
  const pathname = usePathname();
  const scanActive = pathname.startsWith("/scan");

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-hairline bg-surface"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="mx-auto flex h-16 max-w-md items-stretch justify-around px-2">
        {/* first two tabs */}
        {TABS.slice(0, 2).map((t) => (
          <TabLink key={t.href} tab={t} active={isActive(pathname, t.href)} />
        ))}

        {/* center Scan — prominent camera button */}
        <div className="relative flex w-16 shrink-0 items-center justify-center">
          <Link
            href="/scan"
            aria-label="Scan pantry"
            aria-current={scanActive ? "page" : undefined}
            className="absolute -top-6 flex h-16 w-16 items-center justify-center rounded-full bg-brand text-white shadow-lg shadow-brand/30 ring-4 ring-surface transition active:scale-95"
          >
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z" />
              <circle cx="12" cy="13" r="3.5" />
            </svg>
          </Link>
          <span className="mt-9 text-[11px] font-medium text-ink-soft">Scan</span>
        </div>

        {/* last two tabs */}
        {TABS.slice(2).map((t) => (
          <TabLink key={t.href} tab={t} active={isActive(pathname, t.href)} />
        ))}
      </div>
    </nav>
  );
}

function TabLink({ tab, active }: { tab: Tab; active: boolean }) {
  return (
    <Link
      href={tab.href}
      aria-current={active ? "page" : undefined}
      className={`flex min-w-[44px] flex-1 flex-col items-center justify-center gap-1 text-[11px] font-medium ${
        active ? "text-brand" : "text-ink-soft"
      }`}
    >
      {tab.icon(active)}
      <span>{tab.label}</span>
    </Link>
  );
}
