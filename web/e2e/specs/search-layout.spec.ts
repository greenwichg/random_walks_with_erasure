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

test.describe("Search overlay layout (desktop is unchanged)", () => {
  test.use({ viewport: DESKTOP });

  test("keeps its 14px field, its content height, and its fixed 50vh result box", async ({
    authedPage,
  }) => {
    await authedPage.goto("/");
    const { panel, results } = await openSearch(authedPage);

    const size = await authedPage
      .getByPlaceholder(/search/i)
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size, "desktop keeps the 14px field it always had").toBe(14);

    const maxHeight = await panel.evaluate((el) => getComputedStyle(el).maxHeight);
    expect(maxHeight, "desktop's panel is content-height, never bounded by the visible viewport")
      .toBe("none");

    const resultsBox = (await results.boundingBox())!;
    expect(resultsBox.height, "and its result box is still capped at 50vh")
      .toBeLessThanOrEqual(DESKTOP.height / 2);
  });
});
