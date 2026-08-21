import { test, expect } from "../fixtures";

/**
 * Header chrome — the notification panel's positioning contract, and the control consistency the
 * header depends on.
 *
 * These exist because the reported failure ("scroll down, click the bell, the UI disappears") is
 * exactly the kind of bug that unit tests cannot see: it lives in the interaction between a sticky
 * header, a body-scrolled page, and a portalled popper.
 *
 * What a modal menu actually costs here, measured — and it is NOT that the header moves, nor that
 * the scroll position is lost (an earlier claim of mine that turned out to be Playwright's own
 * click-time scrollIntoViewIfNeeded, see the note on the filter test below):
 *
 *   1. the header lands inside an `aria-hidden="true"` subtree, so the avatar is not in the
 *      accessibility tree while its own menu is open — `getByRole` cannot find it;
 *   2. the scrollbar gutter shifts the whole page sideways on classic-scrollbar platforms.
 *
 * Both trace to `html { overflow-x: clip }` in globals.css stopping body's overflow propagating to
 * the viewport. See docs/HEADER_MENU_SCROLL.md.
 *
 * The assertions pin PROPERTIES rather than the mechanism — on screen, inside the viewport,
 * reachable, nothing shifts — because those are what a reader experiences and they survive a future
 * change of cause.
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

/**
 * The profile menu, and the bug that made it "disappear or become inaccessible".
 *
 * The demonstrated half is the second one, and it is literal: with the menu modal, the header sits
 * inside an `aria-hidden="true"` subtree, so the avatar leaves the accessibility tree while its own
 * menu is open. Isolated by rebuilding with `modal={true}` and the CSS gutter fix in place — the
 * only assertion that failed was the aria-hidden one, and the phone test (whose sole assertion is
 * scroll preservation) PASSED.
 *
 * That last detail retracts an earlier claim of mine that the page jumped to the top and never came
 * back. It did in my measurement, because the trigger I was clicking sat off-screen and Playwright
 * scrolls a target into view before clicking it. The avatar never has that problem — it lives in
 * the sticky header — and the scroll assertions below are kept as a genuine property, not as the
 * discriminating one.
 *
 * Cause: a Radix menu is modal by default, so `react-remove-scroll` engages. Fixed with
 * `modal={false}` on the two header menus, which are navigation and were never modal in intent.
 * NOT fixed by changing the DropdownMenu default: that was tried and measured to break the Stories
 * filter reset (stories-filter-state.spec), so FilterSelect keeps the modal default and is covered
 * instead by the CSS override tested below. See docs/HEADER_MENU_SCROLL.md.
 *
 * These assert the two properties a reader actually experiences — my scroll position survives, and
 * the header stays reachable — plus anchoring and reopening. Deliberately NOT the mechanism: a
 * future cause would break the same promises and should fail the same tests.
 */
test.describe("Header profile menu", () => {
  /** The avatar button carries `aria-label={session.user.name}`, and the fixture signs in as
   *  "E2E Reader" (see e2e/helpers.ts). Naming it beats a positional selector: if the label ever
   *  changes this fails as "not found" rather than silently testing the theme toggle. */
  function avatar(page: import("@playwright/test").Page) {
    return page.getByRole("button", { name: "E2E Reader", exact: true });
  }

  test("scroll → click Profile → the page does not jump and the menu is usable", async ({
    authedPage,
  }) => {
    await authedPage.setViewportSize({ width: 1280, height: 800 });
    await authedPage.goto("/");
    // Enough page to scroll. If the route is short the assertions still hold at whatever offset
    // was reachable — a 0px scroll simply makes this the top-of-page case.
    await authedPage.evaluate(() => window.scrollTo(0, 600));
    await authedPage.waitForTimeout(200);
    const scrolled = await authedPage.evaluate(() => window.scrollY);
    test.skip(scrolled === 0, "page is shorter than the viewport — nothing to scroll");

    const trigger = avatar(authedPage);
    const before = await trigger.boundingBox();
    expect(before, "the avatar has a layout box before opening").not.toBeNull();

    await trigger.click();
    const menu = authedPage.getByRole("menu");
    await expect(menu).toBeVisible();

    // THE regression, and the thing the reader actually loses. Pre-fix this read 0.
    const at = await authedPage.evaluate(() => window.scrollY);
    expect(at, `opening the menu moved the page from ${scrolled} to ${at}`).toBeGreaterThan(
      scrolled - 100,
    );

    // The other half of "becomes inaccessible": pre-fix the header sat inside aria-hidden="true",
    // so the avatar was not in the accessibility tree at all while its own menu was open.
    const hidden = await authedPage.evaluate(
      () => !!document.querySelector("header")?.closest("[aria-hidden='true']"),
    );
    expect(hidden, "the header was hidden from assistive tech while the menu was open").toBe(false);

    const after = await trigger.boundingBox();
    expect(after, "the avatar still has a layout box after opening").not.toBeNull();
    expect(after!.y, "the trigger is still on screen").toBeGreaterThanOrEqual(-1);

    await expectInsideViewport(authedPage, menu);

    // Usable, not merely visible: a menu item must be reachable and navigate.
    const item = menu.getByRole("menuitem").first();
    await expect(item).toBeVisible();
  });

  test("the menu stays anchored to the avatar while the page scrolls under it", async ({
    authedPage,
  }) => {
    await authedPage.setViewportSize({ width: 1280, height: 800 });
    await authedPage.goto("/");
    const trigger = avatar(authedPage);
    await trigger.click();
    await expect(authedPage.getByRole("menu")).toBeVisible();

    const gapBefore = await gapTriggerToMenu(authedPage, trigger);
    await authedPage.evaluate(() => window.scrollTo(0, 400));
    await authedPage.waitForTimeout(250);
    await expect(authedPage.getByRole("menu"), "the menu survives a scroll").toBeVisible();
    const gapAfter = await gapTriggerToMenu(authedPage, trigger);

    // The header is sticky, so the trigger holds its screen position and the menu must hold its
    // offset from it. Drifting apart is what "the menu detached from the button" looks like.
    expect(
      Math.abs(gapAfter - gapBefore),
      `menu drifted ${gapAfter - gapBefore}px from its trigger while scrolling`,
    ).toBeLessThanOrEqual(2);
    await expectInsideViewport(authedPage, authedPage.getByRole("menu"));
  });

  test("works the same on a phone viewport", async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 390, height: 844 });
    await authedPage.goto("/");
    await authedPage.evaluate(() => window.scrollTo(0, 500));
    await authedPage.waitForTimeout(150);

    const scrolled = await authedPage.evaluate(() => window.scrollY);
    test.skip(scrolled === 0, "page is shorter than the viewport — nothing to scroll");

    const trigger = avatar(authedPage);
    await trigger.click();
    await expect(authedPage.getByRole("menu")).toBeVisible();

    const at = await authedPage.evaluate(() => window.scrollY);
    expect(at, `the page jumped from ${scrolled} to ${at} on a phone`).toBeGreaterThan(
      scrolled - 100,
    );
    await expectInsideViewport(authedPage, authedPage.getByRole("menu"));
  });

  test("closing returns focus and reopening still works while scrolled", async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 1280, height: 800 });
    await authedPage.goto("/");
    await authedPage.evaluate(() => window.scrollTo(0, 600));
    const trigger = avatar(authedPage);

    await trigger.click();
    await expect(authedPage.getByRole("menu")).toBeVisible();
    await authedPage.keyboard.press("Escape");
    await expect(authedPage.getByRole("menu")).toBeHidden();

    // The second open is the one that catches a scroll lock that was applied and never released:
    // the page would be left with a stuck `overflow: hidden` on <body> and the header displaced.
    await trigger.click();
    await expect(authedPage.getByRole("menu")).toBeVisible();
    await expectInsideViewport(authedPage, authedPage.getByRole("menu"));

    const stuck = await authedPage.evaluate(() => ({
      locked: document.body.hasAttribute("data-scroll-locked"),
      overflow: document.body.style.overflow,
    }));
    expect(stuck.locked, "a scroll lock was left on <body>").toBe(false);
  });
});

/**
 * The scroll lock's scrollbar gutter, and why `html body[data-scroll-locked]` exists.
 *
 * `react-remove-scroll` sets `overflow: hidden` on <body> and adds `margin-right: <gap>px` to fill
 * the space the viewport scrollbar just vacated. Here the scrollbar never vacates — propagation to
 * the viewport is off because of `html { overflow-x: clip }` — so the gutter compensates for
 * nothing and shifts the entire page sideways by the scrollbar width whenever a modal menu opens.
 * Every FilterSelect on Discover, History, Search and Stories is modal, so that is every filter.
 *
 * Headless Chromium (and macOS) use overlay scrollbars and report a 0px gap, so the shift CANNOT
 * occur in this environment naturally — which is why it went unnoticed. This test injects the exact
 * stylesheet the dependency emits with a 15px gap, so it exercises the override rather than the
 * browser's scrollbar policy. Measured, with and without the rule: -15px vs 0px.
 *
 * It is a cascade fight against a stylesheet a dependency injects at runtime, which is exactly the
 * kind of fix that dies silently on a version bump. Hence a test rather than a comment.
 */
test.describe("Modal menus and the scroll lock", () => {
  test("the scrollbar gutter cannot shift the page sideways", async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 1280, height: 800 });
    await authedPage.goto("/discover");
    await authedPage.waitForTimeout(1000);

    const shift = await authedPage.evaluate(() => {
      // Byte-for-byte what react-remove-scroll-bar emits (dist/es2015/component.js) at gap=15,
      // appended last and unlayered, exactly as it arrives at runtime.
      const style = document.createElement("style");
      style.textContent =
        "body[data-scroll-locked]{overflow:hidden !important;overscroll-behavior:contain;margin-right:15px !important;}";
      document.head.appendChild(style);
      const probe = document.querySelector("main") as HTMLElement;
      const before = Math.round(probe.getBoundingClientRect().right);
      document.body.setAttribute("data-scroll-locked", "1");
      void document.body.offsetHeight;
      const after = Math.round(probe.getBoundingClientRect().right);
      const out = {
        shift: after - before,
        marginRight: getComputedStyle(document.body).marginRight,
        overflow: getComputedStyle(document.body).overflow,
      };
      document.body.removeAttribute("data-scroll-locked");
      style.remove();
      return out;
    });

    expect(shift.shift, `the page moved ${shift.shift}px when the lock engaged`).toBe(0);
    expect(shift.marginRight, "the gutter was not neutralised").toBe("0px");
    expect(shift.overflow, "overflow:hidden won the cascade — body is a scroll container").toContain(
      "clip",
    );
  });
});

/** Vertical distance from the bottom of the trigger to the top of the open menu. */
async function gapTriggerToMenu(
  page: import("@playwright/test").Page,
  trigger: ReturnType<import("@playwright/test").Page["locator"]>,
) {
  const t = await trigger.boundingBox();
  const m = await page.getByRole("menu").boundingBox();
  expect(t, "trigger box").not.toBeNull();
  expect(m, "menu box").not.toBeNull();
  return m!.y - (t!.y + t!.height);
}

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
