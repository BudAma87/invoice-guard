"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, CheckCheck, Inbox, Keyboard, Monitor, Moon, ShieldCheck, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { useMe, usePrefs } from "@/components/providers";
import { Dialog, Kbd } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { Me } from "@/lib/types";

const NAV = [
  { href: "/queue", label: "Review queue", Icon: Inbox, role: null },
  { href: "/queue?kind=check", label: "Checker queue", Icon: CheckCheck, role: "checker" },
  { href: "/ops", label: "Ops dashboard", Icon: BarChart3, role: "supervisor" },
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me } = useMe();
  const { helpOpen, setHelpOpen } = usePrefs();
  const [search, setSearch] = React.useState("");

  React.useEffect(() => {
    setSearch(window.location.search);
  }, [pathname]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
      } else if (e.key === "g") {
        const next = (ev: KeyboardEvent) => {
          if (ev.key === "q") router.push("/queue");
          if (ev.key === "o") router.push("/ops");
          window.removeEventListener("keydown", next);
        };
        window.addEventListener("keydown", next, { once: true });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, setHelpOpen]);

  return (
    <div className="relative flex h-screen overflow-clip">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:bg-surface focus:p-2">
        Skip to content
      </a>
      <aside className="sticky top-0 flex h-screen w-[216px] shrink-0 flex-col border-r border-border bg-surface">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-accent-fg">
            <ShieldCheck className="size-4" aria-hidden />
          </span>
          <div className="leading-tight">
            <div className="font-semibold">Invoice Guard</div>
            <div className="text-[0.8rem] text-muted">AP exception review</div>
          </div>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-0.5 px-2">
          {NAV.filter((n) => !n.role || me?.roles.includes(n.role)).map((n) => {
            const [p, q] = n.href.split("?");
            const active = pathname === p && (q ? search.includes(q) : !search.includes("kind=check"));
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-fg-2 hover:bg-surface-2 hover:text-fg",
                  active && "bg-accent-tint font-medium text-accent hover:bg-accent-tint hover:text-accent",
                )}
              >
                <n.Icon className="size-4" aria-hidden />
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto space-y-3 border-t border-border p-3">
          <UserSwitcher me={me} />
          <ThemeSwitch />
          <button
            onClick={() => setHelpOpen(true)}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[0.93rem] text-muted hover:bg-surface-2"
          >
            <Keyboard className="size-4" aria-hidden /> Shortcuts <Kbd>?</Kbd>
          </button>
        </div>
      </aside>
      <main id="main" className="relative min-w-0 flex-1 overflow-y-auto">
        {children}
      </main>
      <ShortcutHelp open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}

function UserSwitcher({ me }: { me?: Me }) {
  const { userId, switchUser } = usePrefs();
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => api<Me[]>("/users") });
  return (
    <div>
      <label htmlFor="user" className="mb-1 block text-[0.8rem] text-muted">
        Signed in as <span className="rounded bg-warn-tint px-1 text-warn">dev</span>
      </label>
      <select
        id="user"
        value={userId}
        onChange={(e) => switchUser(e.target.value)}
        className="h-8 w-full rounded-md border border-border-strong bg-surface px-2 text-[0.93rem]"
      >
        {(users ?? []).map((u) => (
          <option key={u.id} value={u.id}>
            {u.name} · {u.roles.join(", ")}
          </option>
        ))}
      </select>
      {me ? <div className="mt-1 text-[0.8rem] text-muted">Entities: {me.entities.join(", ")}</div> : null}
    </div>
  );
}

function ThemeSwitch() {
  const { theme, setTheme, density, setDensity } = usePrefs();
  const opts = [
    { v: "light", Icon: Sun, label: "Light" },
    { v: "system", Icon: Monitor, label: "System" },
    { v: "dark", Icon: Moon, label: "Dark" },
  ] as const;
  return (
    <div className="flex items-center gap-2">
      <div role="radiogroup" aria-label="Theme" className="flex rounded-md border border-border p-0.5">
        {opts.map((o) => (
          <button
            key={o.v}
            role="radio"
            aria-checked={theme === o.v}
            aria-label={o.label}
            title={o.label}
            onClick={() => setTheme(o.v)}
            className={cn("rounded p-1 text-muted", theme === o.v && "bg-surface-3 text-fg")}
          >
            <o.Icon className="size-3.5" />
          </button>
        ))}
      </div>
      <button
        onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")}
        aria-pressed={density === "compact"}
        className="rounded-md border border-border px-2 py-1 text-[0.8rem] text-muted hover:bg-surface-2"
      >
        Compact
      </button>
    </div>
  );
}

const SHORTCUTS: [string, string][] = [
  ["J / K", "Next / previous issue"],
  ["E", "Edit the focused field"],
  ["Enter", "Confirm edit"],
  ["A", "Approve (or send to checker)"],
  ["O", "Approve with override"],
  ["R", "Reject"],
  ["D", "Mark duplicate"],
  ["H", "Hold – ask supplier"],
  ["N", "Next invoice (queue)"],
  ["G then Q / O", "Go to queue / ops"],
  ["?", "This help"],
];

function ShortcutHelp({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Keyboard shortcuts" description="A power user never needs the mouse.">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
        {SHORTCUTS.map(([k, d]) => (
          <React.Fragment key={k}>
            <dt>
              <Kbd>{k}</Kbd>
            </dt>
            <dd className="text-fg-2">{d}</dd>
          </React.Fragment>
        ))}
      </dl>
    </Dialog>
  );
}
