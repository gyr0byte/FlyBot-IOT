// Real-browser check of the dashboard (headless Chromium via Playwright; browsers in
// PLAYWRIGHT_BROWSERS_PATH on D:). Run by scripts/smoke_all.py --browser with the stack running.
// Prints one JSON line: checks, this tab's sender name (so the orchestrator can validate every message
// it sent), frame rate, screenshot paths.
import fs from "node:fs";
import path from "node:path";
import { chromium, type Page } from "playwright";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:3100";
const OUT = process.env.SCREEN_DIR ?? path.resolve(import.meta.dirname, "..", "..", ".tmp", "smoke");
const local = fs.readFileSync(path.resolve(import.meta.dirname, "..", ".env.local"), "utf8");
const PASSWORD = /^DASHBOARD_PASSWORD=(.*)$/m.exec(local)?.[1].trim() ?? "";
const checks: Record<string, boolean | string> = {};
const consoleErrors: string[] = [];

async function statValue(page: Page, label: string): Promise<string> {
  return (await page.locator(".stat", { has: page.locator(".stat-label", { hasText: label }) }).locator(".stat-value")
    .first().textContent())?.trim() ?? "";
}

async function waitStat(page: Page, label: string, pred: (v: string) => boolean, ms = 20000): Promise<string> {
  const end = Date.now() + ms;
  let v = "";
  while (Date.now() < end) {
    v = await statValue(page, label);
    if (pred(v)) return v;
    await page.waitForTimeout(250);
  }
  return v;
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  page.on("dialog", (d) => d.accept());

  // ---------------------------------------------------------------- login
  await page.goto(`${WEB}/`);
  checks["redirected to login"] = page.url().includes("/login");
  await page.fill("input[name=password]", "wrong");
  await page.click("button[type=submit]");
  checks["wrong password shows error"] = await page.locator(".bad-text").isVisible();
  await page.fill("input[name=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(`${WEB}/`);
  checks["signed in"] = true;

  // ---------------------------------------------------------------- panels update with live data
  checks["broker online"] = (await waitStat(page, "Broker", (v) => v === "online")) === "online";
  checks["robot online"] = (await waitStat(page, "Robot", (v) => v === "online")) === "online";
  checks["brain online"] = (await waitStat(page, "Brain", (v) => v.startsWith("online"))).startsWith("online");
  checks["robot state shown"] = await waitStat(page, "Robot state", (v) => v !== "–");
  checks["decision shown"] = await waitStat(page, "Decision", (v) => v !== "–");
  checks["battery shown"] = await waitStat(page, "Battery", (v) => v.endsWith("V"));
  checks["brain lag shown"] = await waitStat(page, "Brain lag", (v) => v.endsWith("msgs"));
  const count = async () => Number((await page.locator(".events .card-title .muted").first().textContent())?.split(" ")[0] ?? 0);
  const c1 = await count();
  await page.waitForTimeout(3000);
  const c2 = await count();
  checks["messages keep arriving"] = c2 > c1 + 20 ? `${c1} -> ${c2}` : false;
  checks["gauges show numbers"] = /\d\.\d\d/.test((await page.locator(".gauge-value").first().textContent()) ?? "");
  const spikes = await page.locator(".bar-row").count();
  checks["spike bars drawn"] = spikes > 0 ? `${spikes} groups` : false;
  checks["weights heat maps drawn"] = (await page.locator("figcaption").first().textContent())?.includes("mean 1.000") ?? false;
  const chartPixels = await page.evaluate(() => Array.from(document.querySelectorAll("canvas.chart-canvas"))
    .map((el) => {
      const c = el as HTMLCanvasElement;
      const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
      let lit = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i + 2] > 150 || d[i] > 200) lit++;   // series colours, not grid
      return lit;
    }));
  checks["all three charts have lines"] = chartPixels.length === 3 && chartPixels.every((n) => n > 50)
    ? chartPixels.join(" / ") + " px" : false;
  await page.screenshot({ path: path.join(OUT, "dashboard-desktop.png"), fullPage: true });

  // ---------------------------------------------------------------- every control, clicked in the page
  const src = ((await page.locator(".controls .card-title .muted").textContent()) ?? "").replace("as ", "").trim();
  const click = async (name: string) => { await page.getByRole("button", { name, exact: true }).click(); await page.waitForTimeout(300); };
  await click("Manual");
  const pad = await page.locator(".pad").boundingBox();
  if (pad) {
    await page.mouse.move(pad.x + pad.width / 2, pad.y + pad.height / 2);
    await page.mouse.down();
    await page.mouse.move(pad.x + pad.width * 0.6, pad.y + pad.height * 0.2, { steps: 5 });
    await page.waitForTimeout(700);                               // ~7 cmd/manual at 10 Hz
    await page.mouse.up();
  }
  await click("Auto");
  for (const b of ["Reward", "Punish", "Sleep", "Wake", "Set hunger", "Set fear", "Set both"]) await click(b);
  const lesion = page.locator(".toggle", { hasText: "mushroom body" });
  await lesion.click();
  const end = Date.now() + 5000;
  while (Date.now() < end && !(await lesion.getAttribute("class"))?.includes("on")) await page.waitForTimeout(200);
  checks["lesion switch reflects brain/state.les"] = (await lesion.getAttribute("class"))?.includes("on") ?? false;
  await lesion.click();
  await page.waitForTimeout(800);
  await page.fill("input.text", "browser");
  for (const b of ["Save", "Load", "Reset state", "Reset weights"]) await click(b);
  await page.waitForTimeout(600);
  checks["brain answered this tab"] = ((await page.locator(".controls p.muted").last().textContent()) ?? "").includes("ok");
  await click("E-STOP");
  await click("Auto");

  // ---------------------------------------------------------------- smoothness: frames over 15 s of live updates
  const fps = await page.evaluate(() => new Promise<number>((resolve) => {
    let frames = 0;
    const t0 = performance.now();
    const step = () => { frames++; if (performance.now() - t0 < 15000) requestAnimationFrame(step); else resolve(frames / 15); };
    requestAnimationFrame(step);
  }));
  checks["smooth (>= 30 frames/s while live)"] = fps >= 30 ? `${fps.toFixed(0)} fps` : `${fps.toFixed(0)} fps`;
  if (fps < 30) checks["smooth (>= 30 frames/s while live)"] = false;

  // ---------------------------------------------------------------- phone layout and history page
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(800);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  checks["phone: no sideways scrolling"] = overflow <= 1 ? true : `${overflow}px wider`;
  if (overflow > 1) checks["phone: no sideways scrolling"] = false;
  await page.screenshot({ path: path.join(OUT, "dashboard-phone.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${WEB}/history`);
  await page.waitForTimeout(2500);
  const relRows = await page.locator("table").last().locator("tbody tr").count();
  checks["history page shows logged data"] = relRows > 0 ? `${relRows} reliability rows` : false;
  await page.screenshot({ path: path.join(OUT, "history-desktop.png"), fullPage: true });

  checks["no console errors"] = consoleErrors.length === 0;
  await browser.close();
  console.log(JSON.stringify({ src, checks, fps, consoleErrors, screenshots: ["dashboard-desktop.png", "dashboard-phone.png", "history-desktop.png"] }));
}

main().catch((e) => { console.log(JSON.stringify({ error: String(e), checks })); process.exit(1); });
