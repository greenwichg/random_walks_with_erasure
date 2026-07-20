// One-off: on a mobile viewport, list any element whose right edge exceeds the viewport width —
// i.e. real content the overflow-x:clip safety net would be hiding. node scripts/mb1-overflow-diag.mjs [port]
import { chromium } from "@playwright/test";
import { encode } from "next-auth/jwt";

const PORT = Number(process.argv[2] || 3399);
const VW = Number(process.argv[3] || 360);
const BASE = `http://localhost:${PORT}`;
const token = await encode({
  token: { name: "Diag", email: "d@infodiet.local", sub: "1", engineUserId: 1 },
  secret: "e2e-fixed-secret-not-for-production",
});
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: VW, height: 800 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
await ctx.addCookies([{ name: "next-auth.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
const page = await ctx.newPage();
for (const url of ["/", "/analytics", "/report", "/profile", "/recommendations", "/history", "/saved", "/search", "/coach", "/settings", "/discover", "/stories"]) {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const offenders = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out = [];
    for (const el of Array.from(document.querySelectorAll("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 1) {
        out.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.getAttribute("class") || "").slice(0, 70),
          right: Math.round(r.right),
          width: Math.round(r.width),
        });
      }
    }
    // keep the widest few, de-noise nested duplicates
    return out.sort((a, b) => b.right - a.right).slice(0, 6);
  });
  console.log(`\n${url}  (vw=${VW})`);
  if (!offenders.length) console.log("  none — nothing exceeds the viewport");
  for (const o of offenders) console.log(`  right=${o.right} w=${o.width} <${o.tag}> ${o.cls}`);
}
await browser.close();
