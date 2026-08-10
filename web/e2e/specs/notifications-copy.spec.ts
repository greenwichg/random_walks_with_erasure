import { test, expect } from "../fixtures";

/**
 * The notification panel's content contract: "Recommendations waiting" says that recommendations
 * exist and never how many.
 *
 * The number it used to interpolate came from the reader's unopened-recommendation tally, which
 * counts cards SURFACED and not clicked — it grows with browsing, not with availability, and the
 * feed behind it is regenerated and re-ranked on every request. Rendered as "{count}
 * recommendations are waiting for you" it described a queue that does not exist; production showed
 * a reader 3,023.
 *
 * This runs end-to-end rather than as a unit test because the risk lives in the seam: the row is
 * `t(bodyKey, item.payload)` over STORED JSON, and every notification already in the database was
 * written with `{"count": N}`. Nothing migrates those rows, so the guarantee has to survive the old
 * payload meeting the new copy in the real bundle, with the real catalog.
 *
 * Notifications reach the client inside `/api/bootstrap` (the shell's single seeding fetch), so
 * that is the response the fixture rewrites — the browser never calls `/api/me/notifications`.
 */
const LEGACY_COUNT = 3023;

test("the recommendations row states that recs exist, never how many — even for a legacy row", async ({
  authedPage,
}) => {
  await authedPage.route("**/api/bootstrap*", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const now = new Date().toISOString();
    body.notifications = [
      // The row shape production is full of: materialised before the count was withdrawn.
      { id: 1, kind: "recommendations_waiting", createdAt: now, seenAt: null,
        payload: { count: LEGACY_COUNT } },
      // …and the shape the engine writes now.
      { id: 2, kind: "recommendations_waiting", createdAt: now, seenAt: null, payload: {} },
    ];
    await route.fulfill({ response: res, body: JSON.stringify(body) });
  });

  await authedPage.goto("/");
  await authedPage.getByRole("button", { name: /notifications/i }).first().click();
  const panel = authedPage.getByRole("menu");
  await expect(panel).toBeVisible();

  const rows = panel.getByRole("menuitem");
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) {
    const text = (await row.innerText()).replace(/just now|\d+[smhd] ago/gi, ""); // the timestamp is a legitimate number
    expect(text, "the row still says something").not.toBe("");
    expect(text, `no quantity may reach the reader — got "${text}"`).not.toMatch(/\d/);
    expect(text, `no unfilled placeholder — got "${text}"`).not.toMatch(/[{}]/);
  }
  // Both rows render identically: the stored count changes nothing the reader can see.
  const [a, b] = await rows.allInnerTexts();
  expect(a, "a legacy row and a new row are indistinguishable").toBe(b);
});
