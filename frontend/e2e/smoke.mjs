// Smoke test: log in, open every page on the demo dataset, fail on any console error.
// Usage: HISAAB_URL=http://127.0.0.1:8000 HISAAB_PASSWORD=... node e2e/smoke.mjs [outdir]
import { chromium } from "playwright";

const base = process.env.HISAAB_URL ?? "http://127.0.0.1:8000";
const password = process.env.HISAAB_PASSWORD;
const out = process.argv[2] ?? "e2e/out";
if (!password) throw new Error("Set HISAAB_PASSWORD");

const browser = await chromium.launch();
let failures = 0;
for (const theme of ["dark", "light"]) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((t) => localStorage.setItem("hisaab-theme", t), theme);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && !m.text().includes("401") && errors.push(m.text()));

  await page.goto(base + "/");
  await page.fill("#pw", password);
  await page.click("button[type=submit]");
  await page.getByText("Net worth", { exact: true }).waitFor();

  const accounts = await (await ctx.request.get(base + "/api/accounts")).json();
  const bank = accounts.find((a) => a.tracking === "transactions");
  const epf = accounts.find((a) => a.tracking === "balance");
  const pages = [
    ["home", "/", "Net worth"],
    ["accounts", "/accounts", "Bank & cash"],
    ["account", `/accounts/${bank.id}`, "Check against the bank"],
    ["account-balance", `/accounts/${epf.id}`, "Update the value"],
    ["transactions", "/transactions", "transactions ·"],
    ["sort", "/transactions?uncategorised=1", "transactions ·"],
    ["import", "/import", "Drop a CSV here"],
    ["settings", "/settings", "Download a backup"],
  ];
  for (const [name, path, text] of pages) {
    await page.goto(base + path);
    try {
      await page.getByText(text).first().waitFor({ timeout: 8000 });
    } catch {
      console.error(`✗ ${theme} ${path}: didn't find "${text}"`);
      failures++;
    }
    if (name === "home") {
      const chart = page.locator('[role="img"]').first();
      const box = await chart.boundingBox();
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2);
    }
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${out}/${name}-${theme}.png`, fullPage: true });
  }
  if (errors.length) {
    console.error(`✗ ${theme}: console errors\n  ${errors.join("\n  ")}`);
    failures++;
  } else console.log(`✓ ${theme}: ${pages.length} pages, no console errors`);
  await ctx.close();
}
await browser.close();
process.exit(failures ? 1 : 0);
