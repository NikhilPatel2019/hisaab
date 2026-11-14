/** Indian number formatting: ₹12,48,300 (lakh/crore grouping), with an honest minus sign. */
export function formatINR(value: number, opts: { paise?: boolean } = {}): string {
  const digits = opts.paise ? 2 : 0;
  const abs = Math.abs(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return (value < 0 ? "−₹" : "₹") + abs;
}
