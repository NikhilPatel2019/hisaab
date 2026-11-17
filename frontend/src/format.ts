/** Indian number formatting: ₹12,48,300 (lakh/crore grouping), with an honest minus sign. */
export function formatINR(value: number, opts: { paise?: boolean } = {}): string {
  const digits = opts.paise ? 2 : 0;
  const abs = Math.abs(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return (value < 0 ? "−₹" : "₹") + abs;
}

/** Paise from the API → "₹12,48,300". Shows paise only when there are some. */
export function money(paise: number, opts: { sign?: boolean; exact?: boolean } = {}): string {
  const rupees = paise / 100;
  const text = formatINR(rupees, { paise: opts.exact ?? paise % 100 !== 0 });
  return opts.sign && paise > 0 ? "+" + text : text;
}

/** Short form for chart axes: ₹45k, ₹12.5L, ₹1.2Cr. */
export function compact(paise: number): string {
  const r = Math.abs(paise / 100);
  const sign = paise < 0 ? "−" : "";
  const trim = (n: number) => (n >= 100 ? n.toFixed(0) : n.toFixed(1).replace(/\.0$/, ""));
  if (r >= 1e7) return `${sign}₹${trim(r / 1e7)}Cr`;
  if (r >= 1e5) return `${sign}₹${trim(r / 1e5)}L`;
  if (r >= 1e3) return `${sign}₹${trim(r / 1e3)}k`;
  return `${sign}₹${r.toFixed(0)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-17" → "17 Nov" (or "17 Nov 2025" when not this year). */
export function shortDate(iso: string, today = new Date()): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}${y !== today.getFullYear() ? ` ${y}` : ""}`;
}

export function monthLabel(ym: string, withYear = false): string {
  const [y, m] = ym.split("-").map(Number);
  return withYear ? `${MONTHS[m - 1]} ${y}` : MONTHS[m - 1];
}

export function longDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
}

/** "1,23,456.78" / "-500" typed by a person → paise, or null if it isn't a number. */
export function parseRupees(text: string): number | null {
  const s = text.replace(/[₹,\s]/g, "");
  if (!/^[-+]?\d+(\.\d{1,2})?$/.test(s)) return null;
  return Math.round(Number(s) * 100);
}

export const ACCOUNT_KINDS: Record<string, string> = {
  bank: "Bank account",
  cash: "Cash",
  wallet: "Wallet",
  deposit: "Fixed / recurring deposit",
  investment: "Investments",
  retirement: "Retirement (EPF, PPF, NPS)",
  property: "Property",
  other_asset: "Other asset",
  credit_card: "Credit card",
  loan: "Loan",
  other_liability: "Other liability",
};
