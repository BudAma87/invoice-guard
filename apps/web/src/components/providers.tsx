"use client";

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { api, getUser, setUser } from "@/lib/api";
import type { Me } from "@/lib/types";

type Theme = "system" | "light" | "dark";
type Density = "comfortable" | "compact";

interface Prefs {
  theme: Theme;
  setTheme: (t: Theme) => void;
  density: Density;
  setDensity: (d: Density) => void;
  userId: string;
  switchUser: (id: string) => void;
  helpOpen: boolean;
  setHelpOpen: (o: boolean) => void;
}

const PrefsCtx = React.createContext<Prefs | null>(null);

function read<T extends string>(key: string, fallback: T): T {
  try {
    return (window.localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, v: string) {
  try {
    window.localStorage.setItem(key, v);
  } catch {
    /* ignore */
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { refetchOnWindowFocus: true, staleTime: 5_000, retry: 1 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <PrefsProvider>{children}</PrefsProvider>
    </QueryClientProvider>
  );
}

function PrefsProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [theme, setThemeState] = React.useState<Theme>("system");
  const [density, setDensityState] = React.useState<Density>("comfortable");
  const [userId, setUserId] = React.useState("n.silva");
  const [helpOpen, setHelpOpen] = React.useState(false);
  // Identity and prefs live in localStorage, which the server can't see: render the app
  // only after mount so server and client HTML never disagree (no hydration mismatch).
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
    setThemeState(read<Theme>("ig.theme", "system"));
    setDensityState(read<Density>("ig.density", "comfortable"));
    setUserId(getUser());
  }, []);

  React.useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") el.removeAttribute("data-theme");
    else el.setAttribute("data-theme", theme);
    el.setAttribute("data-density", density);
  }, [theme, density]);

  const value: Prefs = {
    theme,
    setTheme: (t) => {
      setThemeState(t);
      write("ig.theme", t);
    },
    density,
    setDensity: (d) => {
      setDensityState(d);
      write("ig.density", d);
    },
    userId,
    switchUser: (id) => {
      setUser(id);
      setUserId(id);
      qc.invalidateQueries();
    },
    helpOpen,
    setHelpOpen,
  };
  return <PrefsCtx.Provider value={value}>{mounted ? children : null}</PrefsCtx.Provider>;
}

export function usePrefs() {
  const v = React.useContext(PrefsCtx);
  if (!v) throw new Error("usePrefs outside Providers");
  return v;
}

export function useMe() {
  const { userId } = usePrefs();
  return useQuery({ queryKey: ["me", userId], queryFn: () => api<Me>("/me") });
}
