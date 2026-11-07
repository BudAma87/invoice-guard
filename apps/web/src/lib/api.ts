// Thin client for the review API, proxied same-origin at /api (next.config.ts).

const USER_KEY = "ig.user";

export function getUser(): string {
  if (typeof window === "undefined") return "n.silva";
  try {
    return window.localStorage.getItem(USER_KEY) || "n.silva";
  } catch {
    return "n.silva";
  }
}

export function setUser(id: string) {
  try {
    window.localStorage.setItem(USER_KEY, id);
  } catch {
    /* private mode: identity resets on reload */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown; version?: number } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-User", getUser());
  if (init.json !== undefined) headers.set("Content-Type", "application/json");
  if (init.version !== undefined) headers.set("If-Match", String(init.version));
  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
    cache: "no-store",
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : res.statusText;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}
