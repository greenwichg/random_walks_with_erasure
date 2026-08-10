import { test, expect } from "../fixtures";

/**
 * Getting OUT of the search overlay.
 *
 * Reported from a phone: with the overlay open and the keyboard up there was no obvious way to
 * close it. Three separate reasons, all real:
 *   - `hideClose` on the Sheet meant there was no close control at all, and the only hint that
 *     existed ("ESC") is `hidden sm:block` — desktop-only, and a phone has no Escape key anyway.
 *   - The Sheet's content is `inset-y-0 w-full` on a phone, so it covers the screen and Radix's
 *     Overlay — the element that takes an outside press — sits entirely behind it. There was no
 *     "outside" left to tap.
 *   - Back navigated the page away instead of closing the overlay, which on a phone is the gesture
 *     a reader reaches for first.
 *
 * A real software keyboard cannot be raised in Playwright, so the keyboard-open case is exercised
 * as the thing that actually breaks layouts: a SHORT viewport. 390x340 is about the band left
 * visible above the keyboard on a 390x844 phone. That tests the property the fix rests on — the
 * dismiss control rides in the input's own row, near the top — without pretending a real keyboard
 * was involved.
 */
const PHONE = { width: 390, height: 844 };
const PHONE_WITH_KEYBOARD = { width: 390, height: 340 };

async function openSearch(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: /search/i }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test.describe("Search overlay dismissal (mobile)", () => {
  test.use({ viewport: PHONE });

  test("a visible Cancel closes it and gives the page back", async ({ authedPage }) => {
    await authedPage.goto("/");
    await openSearch(authedPage);

    const cancel = authedPage.getByRole("button", { name: /^(cancel|cancelar|annuler|abbrechen)$/i });
    await expect(cancel, "a phone needs a control, not an ESC hint").toBeVisible();
    await cancel.click();

    await expect(authedPage.getByRole("dialog")).toHaveCount(0);
    // …and the page underneath is usable again: the modal's scroll lock must not outlive it.
    const overflow = await authedPage.evaluate(() => getComputedStyle(document.body).overflow);
    expect(overflow, "body scroll lock was released").not.toBe("hidden");
    await expect(authedPage.getByRole("button", { name: /search/i }).first()).toBeVisible();
  });

  test("Cancel stays on screen in the band a keyboard leaves visible", async ({ authedPage }) => {
    await authedPage.setViewportSize(PHONE_WITH_KEYBOARD);
    await authedPage.goto("/");
    await openSearch(authedPage);

    const cancel = authedPage.getByRole("button", { name: /^(cancel|cancelar|annuler|abbrechen)$/i });
    const box = await cancel.boundingBox();
    expect(box, "Cancel has a layout box").not.toBeNull();
    expect(box!.y, "Cancel's top is on screen").toBeGreaterThanOrEqual(0);
    expect(
      box!.y + box!.height,
      `Cancel's bottom must be inside the ${PHONE_WITH_KEYBOARD.height}px band, not under the keyboard`,
    ).toBeLessThanOrEqual(PHONE_WITH_KEYBOARD.height);
    // It must also sit in the input's row rather than somewhere further down the overlay — that
    // adjacency is what keeps it visible when the browser scrolls the focused field into view.
    const input = await authedPage.getByPlaceholder(/search/i).boundingBox();
    expect(Math.abs(box!.y - input!.y), "Cancel rides in the input's row").toBeLessThan(40);
  });

  test("the Back gesture closes it instead of leaving the page", async ({ authedPage }) => {
    await authedPage.goto("/stories");
    await openSearch(authedPage);

    await authedPage.goBack();

    await expect(authedPage.getByRole("dialog")).toHaveCount(0);
    await expect(authedPage, "Back consumed the overlay, not the page").toHaveURL(/\/stories$/);
  });

  test("closing by Cancel leaves no history entry behind", async ({ authedPage }) => {
    // The other half of the Back support: if the pushed entry is not taken back out, the reader's
    // next Back appears to do nothing at all.
    await authedPage.goto("/");
    await authedPage.goto("/stories");
    await openSearch(authedPage);
    await authedPage.getByRole("button", { name: /^(cancel|cancelar|annuler|abbrechen)$/i }).click();
    await expect(authedPage.getByRole("dialog")).toHaveCount(0);

    await authedPage.goBack();
    await expect(authedPage, "Back works normally again").toHaveURL(/\/$/);
  });

  test("a press outside the panel closes it; a press inside does not", async ({ authedPage }) => {
    await authedPage.goto("/");
    const dialog = await openSearch(authedPage);

    // Inside first — a tap on the search field must never dismiss what the reader is using.
    await authedPage.getByPlaceholder(/search/i).click();
    await expect(dialog).toBeVisible();

    // Then the empty area well below the panel.
    await authedPage.mouse.click(PHONE.width / 2, PHONE.height - 60);
    await expect(authedPage.getByRole("dialog")).toHaveCount(0);
  });
});

test.describe("Search overlay dismissal (desktop is unchanged)", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("keeps the ESC hint and no Cancel button, and Escape still closes", async ({ authedPage }) => {
    await authedPage.goto("/");
    const dialog = await openSearch(authedPage);
    await expect(dialog).toContainText("ESC");
    await expect(
      authedPage.getByRole("button", { name: /^(cancel|cancelar|annuler|abbrechen)$/i }),
    ).toBeHidden();

    await authedPage.keyboard.press("Escape");
    await expect(authedPage.getByRole("dialog")).toHaveCount(0);
  });
});
