/** Thin client for the hisaab API. Same-origin only; the session lives in an HttpOnly cookie. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface SessionState {
  authenticated: boolean;
  setup_required: boolean;
}

export interface Me {
  version: string;
  database: string;
  secrets_enabled: boolean;
}

/** Every state-changing call carries this header; the server rejects cross-site requests without it. */
export const CSRF_HEADERS = { "X-Hisaab": "1" } as const;

export async function request<T>(
  path: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET" && method !== "HEAD") {
    for (const [k, v] of Object.entries(CSRF_HEADERS)) headers.set(k, v);
  }
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetcher(path, { ...init, method, headers, credentials: "same-origin" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  session: () => request<SessionState>("/api/auth/session"),
  login: (password: string) =>
    request<void>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<Me>("/api/me"),
};
