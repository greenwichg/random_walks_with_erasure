import { test, expect } from "../fixtures";

/**
 * Header chrome — the notification panel's positioning contract, and the control consistency the
 * header depends on.
 *
 * These exist because the reported failure ("scroll down, click the bell, the UI disappears") is
 * exactly the kind of bug that unit tests cannot see: it lives in the interaction between a sticky
 * header, a body-scrolled page, and a portalled popper. Two attempts to reproduce it headlessly by
 * simulating the scroll-lock came back with zero pixel movement, so rather than assert a mechanism
 * that was never confirmed, these pin the PROPERTIES that must hold whatever the mechanism was:
 * the panel is on screen, inside the viewport, and reachable — at the top of the page, scrolled
 * deep, on a phone, and after closing and reopening.
 */
const BELL = /notifications/i;

async function openBell(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: BELL }).first().click();
  const panel = page.getByRole("menu");
  await expect(panel).toBeVisible();
  return panel;
}

/** The panel's box must sit inside the viewport on every edge — the single property that "the UI
 *  disappeared" violates, however it came about. */
async function expectInsideViewport(page: import("@playwright/test").Page, panel: ReturnType<import("@playwright/test").Page["locator"]>) {
  const box = await panel.boundingBox();
  expect(box, "the panel has a layout box").not.toBeNull();
  const vp = page.viewportSize()!;
  expect(box!.width, "panel is not collapsed").toBeGreaterThan(100);
  expect(box!.height, "panel is not collapsed").toBeGreaterThan(20);
  expect(box!.y, "top edge on screen").toBeGreaterThanOrEqual(-1);
  expect(box!.x, "left edge on screen").toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width, "right edge on screen").toBeLessThanOrEqual(vp.width + 1);
  expect(box!.y + box!.height, "bottom edge on screen").toBeLessThanOrEqual(vp.height + 1);
}

test.describe("Header notification panel", () => {
  test("opens inside the viewport at the top of the page", async ({ authedPage }) => {
    await authedPage.goto("/");
    await expectInsideViewport(authedPage, await openBell(authedPage));
  });

  test("opens inside the viewport when the page is scrolled — the reported failure", async ({
    authedPage,
  }) => {
    await authedPage.goto("/");
    await authedPage.mouse.wheel(0, 1500);
    await authedPage.waitForTimeout(150);
    const scrolled = await authedPage.evaluate(() => window.scrollY);
    test.skip(scrolled === 0, "page is shorter than the viewport — nothing to scroll");

    const panel = await openBell(authedPage);
    await expectInsideViewport(authedPage, panel);

    // The trigger must still be on screen too: a sticky header that jumped away would take the
    // bell with it, which is the other half of what "the bell disappeared" describes.
    const bell = await authedPage.getByRole("button", { name: BELL }).first().boundingBox();
    expect(bell, "the bell still has a box").not.toBeNull();
    expect(bell!.y, "the bell is still on screen").toBeGreaterThanOrEqual(-1);
  });

  test("opening the panel does not move the page underneath it", async ({ authedPage }) => {
    // `modal={false}` removes react-remove-scroll, so no scroll lock and no scrollbar-gutter
    // compensation — the reader's scroll position and the layout width both hold.
    await authedPage.goto("/");
    await authedPage.mouse.wheel(0, 1200);
    await authedPage.waitForTimeout(150);
    const before = await authedPage.evaluate(() => ({
      y: window.scrollY,
      w: document.documentElement.clientWidth,
    }));
    await openBell(authedPage);
    const after = await authedPage.evaluate(() => ({
      y: window.scrollY,
      w: document.documentElement.clientWidth,
    }));
    expect(after.y, "scroll position is untouched").toBe(before.y);
    expect(after.w, "no scrollbar-gutter layout shift").toBe(before.w);
  });

  test("closes and reopens cleanly", async ({ authedPage }) => {
    await authedPage.goto("/");
    await openBell(authedPage);
    await authedPage.keyboard.press("Escape");
    await expect(authedPage.getByRole("menu")).toHaveCount(0);
    await expectInsideViewport(authedPage, await openBell(authedPage));
  });

  test("fits a phone viewport", async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 375, height: 667 });
    await authedPage.goto("/");
    await expectInsideViewport(authedPage, await openBell(authedPage));
  });

  test("caps the list on measured space, not on a constant, in a short viewport", async ({
    authedPage,
  }) => {
    // The box check alone does NOT prove this: a seeded test account has few notifications, so the
    // list never reaches 24rem and the panel fits whatever the cap says — verified by reverting the
    // fix, which still passed. So assert the CAP itself. 24rem of list plus ~5rem of panel chrome
    // cannot fit a 360px window, so a cap still sitting at 384px means the constant is in force and
    // a reader with a full inbox would have the bottom of the panel off-screen.
    await authedPage.setViewportSize({ width: 740, height: 360 });
    await authedPage.goto("/");
    const panel = await openBell(authedPage);
    await expectInsideViewport(authedPage, panel);

    const cap = await authedPage.evaluate(() => {
      const scroller = document.querySelector<HTMLElement>('[role="menu"] .overflow-y-auto');
      if (!scroller) return null; // empty inbox renders no scroller — nothing to cap
      return { max: getComputedStyle(scroller).maxHeight, vh: window.innerHeight };
    });
    test.skip(cap === null, "this account's inbox is empty, so there is no scrolling list");
    const px = parseFloat(cap!.max);
    expect(Number.isFinite(px), `max-height should resolve to px, got ${cap!.max}`).toBe(true);
    expect(px, "cap adapts to the viewport rather than staying at 24rem").toBeLessThan(384);
    expect(px, "cap leaves room for the panel's own chrome").toBeLessThan(cap!.vh);
  });
});

test.describe("Header control consistency", () => {
  test("every icon-only control shares one box, and every icon one size", async ({ authedPage }) => {
    await authedPage.goto("/");
    const controls = await authedPage.evaluate(() => {
      const header = document.querySelector("header")!;
      const out: { label: string; box: string; icon: string | null }[] = [];
      for (const b of Array.from(header.querySelectorAll("button"))) {
        // Icon-only controls: the search pill carries a visible label and sizes itself to it.
        if ((b.textContent || "").trim().length > 0) continue;
        const r = b.getBoundingClientRect();
        // VISIBLE ones only. The header keeps both responsive variants mounted (`lg:hidden` menu,
        // `sm:hidden` search) and a display:none element measures 0x0 — comparing a hidden control
        // against a rendered one is comparing nothing to something.
        if (r.width === 0 || r.height === 0) continue;
        const svg = b.querySelector("svg");
        const s = svg?.getBoundingClientRect();
        out.push({
          label: b.getAttribute("aria-label") || "?",
          box: `${Math.round(r.width)}x${Math.round(r.height)}`,
          // Nullable ON PURPOSE. The theme toggle renders a sized <div> placeholder until it has
          // mounted and resolved the theme, so requiring an svg here silently dropped it from the
          // sample — which is how an earlier version of this test "failed" on a header whose boxes
          // were in fact identical. The BOX is the property every control must share; the icon
          // size is asserted over whichever controls have actually painted one.
          icon: s ? `${Math.round(s.width)}x${Math.round(s.height)}` : null,
        });
      }
      return out;
    });

    const shown = JSON.stringify(controls);
    expect(controls.length, `expected several visible icon controls: ${shown}`).toBeGreaterThan(1);
    expect(new Set(controls.map((c) => c.box)).size, `boxes differ: ${shown}`).toBe(1);
    const icons = controls.map((c) => c.icon).filter(Boolean);
    expect(new Set(icons).size, `icon sizes differ: ${shown}`).toBe(1);
  });

  test("the search pill has a visible keyboard focus ring", async ({ authedPage }) => {
    // It is a bare <button>, not a <Button>, so it does not inherit the ring — it had none, which
    // made it the one header control a keyboard user could not see themselves on.
    await authedPage.setViewportSize({ width: 1280, height: 800 });
    await authedPage.goto("/");
    const pill = authedPage.getByRole("button", { name: /search/i }).first();
    await pill.focus();
    const ring = await pill.evaluate((el) => {
      const s = getComputedStyle(el);
      return { shadow: s.boxShadow, outline: s.outlineStyle };
    });
    expect(
      ring.shadow !== "none" || ring.outline !== "none",
      `no focus indicator: ${JSON.stringify(ring)}`,
    ).toBe(true);
  });
});
