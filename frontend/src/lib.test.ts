import { describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./api";
import { formatINR } from "./format";

describe("formatINR", () => {
  it("groups in lakhs and crores", () => {
    expect(formatINR(1248300)).toBe("₹12,48,300");
    expect(formatINR(12345678)).toBe("₹1,23,45,678");
  });
  it("uses a real minus sign and optional paise", () => {
    expect(formatINR(-3016)).toBe("−₹3,016");
    expect(formatINR(13300, { paise: true })).toBe("₹13,300.00");
  });
});

describe("request", () => {
  it("adds the CSRF header to state-changing calls only", async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
    await request("/api/auth/logout", { method: "POST" }, fetcher);
    await request("/api/me", {}, fetcher);
    const post = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    const get = fetcher.mock.calls[1] as unknown as [string, RequestInit];
    expect(new Headers(post[1].headers).get("X-Hisaab")).toBe("1");
    expect(new Headers(get[1].headers).get("X-Hisaab")).toBeNull();
  });

  it("surfaces the server's message", async () => {
    const fetcher = vi.fn(
      async () => new Response(JSON.stringify({ detail: "That password didn't match." }), { status: 401 }),
    );
    await expect(request("/api/auth/login", { method: "POST" }, fetcher)).rejects.toEqual(
      new ApiError(401, "That password didn't match."),
    );
  });
});
