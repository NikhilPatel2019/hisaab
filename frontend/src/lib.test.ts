import { describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./api";
import { niceTicks } from "./components/Charts";
import { compact, formatINR, money, parseRupees } from "./format";
import { match } from "./router";

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

describe("money helpers", () => {
  it("formats paise, showing paise only when present", () => {
    expect(money(1248300_00)).toBe("₹12,48,300");
    expect(money(1110709_50)).toBe("₹11,10,709.50");
    expect(money(20000_00, { sign: true })).toBe("+₹20,000");
    expect(money(-45000)).toBe("−₹450");
  });
  it("abbreviates for chart axes", () => {
    expect(compact(1250000_00)).toBe("₹12.5L");
    expect(compact(45000_00)).toBe("₹45k");
    expect(compact(12000000_00)).toBe("₹1.2Cr");
  });
  it("reads what people type", () => {
    expect(parseRupees("1,23,456.78")).toBe(12345678);
    expect(parseRupees("₹ 500")).toBe(50000);
    expect(parseRupees("-1200.5")).toBe(-120050);
    expect(parseRupees("12.345")).toBeNull();
    expect(parseRupees("abc")).toBeNull();
  });
});

describe("charts and routing", () => {
  it("picks round axis ticks", () => {
    expect(niceTicks(7_80_000, 16_80_000)).toEqual([750000, 1000000, 1250000, 1500000, 1750000]);
    expect(niceTicks(0, 95)).toEqual([0, 25, 50, 75, 100]);
  });
  it("matches account paths", () => {
    expect(match("/accounts/:id", "/accounts/42")).toEqual({ id: "42" });
    expect(match("/accounts/:id", "/accounts")).toBeNull();
  });
});
