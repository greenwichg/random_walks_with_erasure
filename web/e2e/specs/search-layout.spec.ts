import type { Page } from "@playwright/test";

import { test, expect } from "../fixtures";

/**
 * How much of the screen the search overlay gives back.
 *
 * Reported from a phone: an oversized input, a panel that wastes the screen, and results with almost
 * nowhere to go. Three causes, each measurable here:
 *
 *   - The field inherited `Input`'s 14px `text-sm`. iOS Safari magnifies the whole page when a
 *     focused field is under 16px and does not undo it on blur, so opening search left the page
 *     zoomed — which is what an "oversized" field and a cramped panel look like afterwards. Asserted
 *     as the computed font size, because that threshold IS the rule.
 *   - The results box was a fixed `max-h-[50vh]`. `vh` is the LARGEST the viewport ever gets (URL
 *     bar retracted), so on a phone it over-reports from the first paint, and it caps the list at
 *     half the screen even where there is room.
 *   - Nothing shrank when the keyboard opened, because nothing measured the visual viewport.
 *
 * A real software keyboard cannot be raised in Playwright, so the keyboard case is exercised the way
 * it actually breaks layouts — a SHORT viewport. 390x340 is roughly the band left above the keys on
 * a 390x844 phone. That tests the property the fix rests on (the panel is bounded by what is visible
 * and the list takes the remainder) without pretending a real keyboard was involved.
 *
 * The regions are located structurally rather than by test id: the panel is the form's parent, and
 * the scrolling results region is the form's only sibling. That keeps the assertions independent of
 * the copy, which is localized.
 */
const PHONE = { width: 390, height: 844 };
const PHONE_WITH_KEYBOARD = { width: 390, height: 340 };
const DESKTOP = { width: 1280, height: 800 };

async function openSearch(page: Page) {
  await page.getByRole("button", { name: /search/i }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const form = dialog.locator("form");
  return { dialog, form, panel: form.locator(".."), results: form.locator("xpath=following-sibling::div") };
}

test.describe("Search overlay layout (mobile)", () => {
  test.use({ viewport: PHONE });

  test("the field is at least 16px, so focusing it cannot zoom the page", async ({ authedPage }) => {
    await authedPage.goto("/");
    await openSearch(authedPage);

    const size = await authedPage
      .getByPlaceholder(/search/i)
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size, "iOS Safari zooms the page for a focused field under 16px, and never zooms back")
      .toBeGreaterThanOrEqual(16);
  });

  test("the panel fits on screen and the results take most of it", async ({ authedPage }) => {
    await authedPage.goto("/");
    const { panel, form, results } = await openSearch(authedPage);

    const panelBox = (await panel.boundingBox())!;
    expect(Math.round(panelBox.y + panelBox.height), "the panel ends on screen")
      .toBeLessThanOrEqual(PHONE.height);

    const headerBox = (await form.boundingBox())!;
    const resultsBox = (await results.boundingBox())!;
    expect(resultsBox.height, "the results region gets more of the panel than the header does")
      .toBeGreaterThan(headerBox.height);
  });

  test("in the band a keyboard leaves, the panel shrinks instead of running under it", async ({
    authedPage,
  }) => {
    await authedPage.setViewportSize(PHONE_WITH_KEYBOARD);
    await authedPage.goto("/");
    const { panel, results } = await openSearch(authedPage);

    const panelBox = (await panel.boundingBox())!;
    expect(
      Math.round(panelBox.y + panelBox.height),
      `the panel must end inside the ${PHONE_WITH_KEYBOARD.height}px band, not under the keyboard`,
    ).toBeLessThanOrEqual(PHONE_WITH_KEYBOARD.height);

    // …and it shrank by giving up result space rather than by clipping the input away.
    await expect(authedPage.getByPlaceholder(/search/i)).toBeVisible();
    const resultsBox = (await results.boundingBox())!;
    expect(resultsBox.height, "the list keeps a usable amount of room").toBeGreaterThan(40);
    const overflow = await results.evaluate((el) => getComputedStyle(el).overflowY);
    expect(overflow, "and it scrolls rather than pushing the panel past the screen").toBe("auto");
  });
});

/**
 * Desktop searches from the header itself now, so the layout question is not "how much screen does
 * the overlay give back" but "does activating it move anything". It must not: the field takes the
 * pill's slot and the row's right-hand controls stay exactly where they were.
 */
test.describe("Header search layout (desktop)", () => {
  test.use({ viewport: DESKTOP });

  test("activating the field moves nothing else in the header", async ({ authedPage }) => {
    await authedPage.goto("/");
    // The account trigger is labelled with the reader's NAME, so it is located structurally: the
    // last control in the bar, and the one furthest right of search.
    const account = authedPage.locator("header button").last();
    const before = (await account.boundingBox())!;
    const headerBefore = (await authedPage.locator("header").boundingBox())!;

    await authedPage.getByRole("button", { name: /search/i }).first().click();
    const field = authedPage.getByRole("searchbox");
    await expect(field).toBeFocused();

    const after = (await account.boundingBox())!;
    expect(Math.round(after.x), "the controls beside search do not shift").toBe(Math.round(before.x));
    expect(Math.round(after.y)).toBe(Math.round(before.y));
    const headerAfter = (await authedPage.locator("header").boundingBox())!;
    expect(Math.round(headerAfter.height), "and the bar does not change height")
      .toBe(Math.round(headerBefore.height));

    const size = await field.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size, "desktop keeps the 14px field it always had").toBe(14);
  });

  test("the results hang under the field, bounded, with the page still behind them", async ({
    authedPage,
  }) => {
    await authedPage.goto("/");
    await authedPage.getByRole("button", { name: /search/i }).first().click();
    const field = authedPage.getByRole("searchbox");
    await field.fill("the");

    // The panel is the field's sibling in the header's search box.
    const panel = authedPage.locator("header form[role='search'] ~ div").first();
    await expect(panel).toBeVisible();
    const fieldBox = (await field.boundingBox())!;
    const panelBox = (await panel.boundingBox())!;
    expect(panelBox.y, "it opens below the field, not over the page").toBeGreaterThan(fieldBox.y);
    expect(panelBox.height, "and is bounded well short of the screen")
      .toBeLessThanOrEqual(DESKTOP.height * 0.6 + 4);
    await expect(authedPage.getByRole("dialog"), "still no modal").toHaveCount(0);
  });
});
