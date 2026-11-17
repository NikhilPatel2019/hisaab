// Smoke test: log in, open every page on the demo dataset, fail on any console error.
// Usage: HISAAB_URL=http://127.0.0.1:8000 HISAAB_PASSWORD=... node e2e/smoke.mjs [outdir]
import { chromium } from "playwright";

const base = process.env.HISAAB_URL ?? "http://127.0.0.1:8000";
const password = process.env.HISAAB_PASSWORD;
const out = process.argv[2] ?? "e2e/out";
if (!password) throw new Error("Set HISAAB_PASSWORD");

/** A one-page PDF with these lines of text. Everything in it is made up. */
function samplePdf(lines) {
  const esc = (t) => t.replace(/[\\()]/g, (c) => "\\" + c);
  const stream = "BT /F1 10 Tf 40 800 Td 13 TL " + lines.map((l) => `(${esc(l)}) '`).join(" ") + " ET";
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = objs.map((o, i) => {
    const at = pdf.length;
    pdf += `${i + 1} 0 obj\n${o}\nendobj\n`;
    return at;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf, "latin1");
}

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
  const files = [
    ["demo-bank-statement.pdf", ["Demo Bank - fictional", "Statement of Account", "Account No XXXXXX4321", "Statement From : 01/10/2026 To : 31/10/2026"]],
    ["demo-letter.pdf", ["A made-up letter", "Nothing financial here"]],
  ];
  const uploaded = [];
  for (const [name, lines] of files) {
    const r = await ctx.request.post(base + "/api/documents", {
      headers: { "X-Hisaab": "1" },
      multipart: { files: { name, mimeType: "application/pdf", buffer: samplePdf(lines) } },
    });
    if (!r.ok()) throw new Error(`upload failed: ${r.status()} ${await r.text()}`);
    uploaded.push((await r.json())[0].document_id);
  }
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
    ["postbox", "/postbox", "How statements get here"],
    ["postbox-need", "/postbox?f=need", "What is it?"],
    ["postbox-doc", `/postbox?doc=${uploaded[0]}`, "Download original"],
    ["settings", "/settings", "Statement passwords"],
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
