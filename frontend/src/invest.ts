/** Small helpers for the Investments page. */
import type { HoldingRow } from "./api/client";
import { money } from "./format";

export const CLASS_COLORS: Record<HoldingRow["asset_class"], string> = {
  equity: "var(--c1)",
  debt: "var(--c2)",
  hybrid: "var(--c3)",
  gold: "var(--c4)",
  other: "var(--ink4)",
};

const KIND_LABELS: Record<string, string> = { mf: "Fund", equity: "Share", bond: "Bond", nps: "NPS", other: "Holding" };

/** "Fund · Mutual funds · folio ••7890" */
export function holdingSource(h: Pick<HoldingRow, "kind" | "account_name" | "folio" | "name">): string {
  const kind = h.kind === "equity" && /\bETF\b/i.test(h.name) ? "ETF" : (KIND_LABELS[h.kind] ?? "Holding");
  return [kind, h.account_name, h.folio ? `folio ${h.folio}` : null].filter(Boolean).join(" · ");
}

/** "▲ ₹98,900 · 12.2%" or "▼ ₹4,000 · 3.1%"; null when the cost isn't known. */
export function gainLabel(gain: number | null | undefined, cost: number | null | undefined): string | null {
  if (gain === null || gain === undefined || !cost) return null;
  const pct = Math.abs((gain / cost) * 100).toFixed(1);
  return `${gain >= 0 ? "▲" : "▼"} ${money(Math.abs(gain), { exact: false })} · ${pct}%`;
}

/** The same, split for a narrow table cell: { amount: "▲ ₹98,900", pct: "12.2%" }. */
export function gainParts(gain: number | null | undefined, cost: number | null | undefined): { amount: string; pct: string } | null {
  if (gain === null || gain === undefined || !cost) return null;
  return {
    amount: `${gain >= 0 ? "▲" : "▼"} ${money(Math.abs(gain), { exact: false })}`,
    pct: `${gain >= 0 ? "+" : "−"}${Math.abs((gain / cost) * 100).toFixed(1)}%`,
  };
}

export function pctLabel(pct: number | null | undefined): string | null {
  if (pct === null || pct === undefined) return null;
  return `${pct.toFixed(1)}%`;
}

/** Group a number of units for reading: "1,034.912". */
export function unitsLabel(units: string): string {
  const [whole, frac] = units.split(".");
  const n = Number(whole);
  const grouped = Number.isFinite(n) ? n.toLocaleString("en-IN") : whole;
  return frac ? `${grouped}.${frac}` : grouped;
}
