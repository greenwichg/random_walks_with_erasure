// Mobile screenshot harness for the MB1 workstream (dev-only tooling, not shipped behavior).
// Serves mock data via `next dev` (NODE_ENV=development → RWE_ALLOW_MOCK_FALLBACK on), mints the
// same NextAuth session cookie a real sign-in would set, then screenshots each surface at a set of
// mobile viewports. Usage: node scripts/mobile-shots.mjs <outDir> [port]
import { chromium } from "@playwright/test";
import { encode } from "next-auth/jwt";
import { mkdirSync } from "node:fs";
import path from "node:path";

const OUT = process.argv[2] || ".mb1-shots/before";
const PORT = Number(process.argv[3] || 3399);
const BASE = `http://localhost:${PORT}`;
const SECRET = "e2e-fixed-secret-not-for-production";

const VIEWPORTS = [
  { name: "360", width: 360, height: 800 },
  { name: "390", width: 390, height: 844 },
];

// Authenticated app surfaces (mock data populates them) + the public funnel.
const PAGES = [
  { slug: "dashboard", url: "/", auth: true },
  { slug: "report", url: "/report", auth: true },
  { slug: "recommendations", url: "/recommendations", auth: true },
  { slug: "history", url: "/history", auth: true },
  { slug: "analytics", url: "/analytics", auth: true },
  { slug: "settings", url: "/settings", auth: true },
  { slug: "saved", url: "/saved", auth: true },
  { slug: "search", url: "/search", auth: true },
  { slug: "coach", url: "/coach", auth: true },
  { slug: "profile", url: "/profile", auth: true },
  { slug: "signin", url: "/signin", auth: false },
  { slug: "onboarding", url: "/onboarding", auth: false },
];

async function main() {
  mkdirSync(OUT, { recursive: true });
  const token = await encode({
    token: { name: "Mobile Audit", email: "audit@infodiet.local", sub: "1", engineUserId: 1 },
    secret: SECRET,
  });
  const cookie = {
    name: "next-auth.session-token",
    value: token,
    domain: "localhost",
    path: "/",
    httpOnly: true,
    sameSite: "Lax",
  };

  const browser = await chromium.launch();
  const findings = [];
  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    await context.addCookies([cookie]);
    const page = await context.newPage();
    for (const p of PAGES) {
      try {
        await page.goto(`${BASE}${p.url}`, { waitUntil: "networkidle", timeout: 45000 });
        await page.waitForTimeout(1200); // settle animations/charts
        // Horizontal-overflow probe: does the document scroll sideways at this width?
        const overflow = await page.evaluate(() => {
          const de = document.documentElement;
          return { scrollW: de.scrollWidth, clientW: de.clientWidth, over: de.scrollWidth - de.clientWidth };
        });
        const file = path.join(OUT, `${p.slug}-${vp.name}.png`);
        await page.screenshot({ path: file, fullPage: true });
        const flag = overflow.over > 1 ? `  <-- OVERFLOW +${overflow.over}px` : "";
        findings.push(`${p.slug} @${vp.name}: scrollW=${overflow.scrollW} clientW=${overflow.clientW}${flag}`);
        console.log(`ok  ${file}${flag}`);
      } catch (e) {
        findings.push(`${p.slug} @${vp.name}: ERROR ${e.message}`);
        console.log(`ERR ${p.slug} @${vp.name}: ${e.message}`);
      }
    }
    await context.close();
  }
  await browser.close();
  console.log("\n=== OVERFLOW REPORT ===");
  for (const f of findings) console.log(f);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
