import { test, expect } from "../fixtures";

/**
 * Where the two report notifications take the reader.
 *
 * Both used to resolve to `/report` — the CURRENT full health report. It is neither weekly nor
 * monthly, it is what the sidebar already links to, and following either notification produced the
 * identical page, so the announcement told the reader nothing about what it had announced. Each now
 * has its own period page.
 *
 * Driven through a real click on a real row rather than by asserting the table, because the table
 * being right is not the same as the row navigating: `notificationHref` is resolved per-row, the
 * click also marks the row seen, and a route that does not exist still renders a 404 from a
 * perfectly correct href. Notifications reach the client inside `/api/bootstrap`, so that is the
 * response the fixture rewrites.
 */
const CASES = [
  { kind: "weekly_report", path: "/report/weekly", titleKey: "Weekly report" },
  { kind: "monthly_deep_dive", path: "/report/monthly", titleKey: "Monthly deep dive" },
] as const;

for (const { kind, path, titleKey } of CASES) {
  test(`the ${kind} notification opens ${path}`, async ({ authedPage }) => {
    await authedPage.route("**/api/bootstrap*", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.notifications = [
        { id: 1, kind, createdAt: new Date().toISOString(), seenAt: null, payload: { overall: 61 } },
      ];
      await route.fulfill({ response: res, body: JSON.stringify(body) });
    });

    await authedPage.goto("/");
    await authedPage.getByRole("button", { name: /notifications/i }).first().click();
    await expect(authedPage.getByRole("menu")).toBeVisible();
    await authedPage.getByRole("menuitem").first().click();

    await expect(authedPage).toHaveURL(new RegExp(`${path}$`));
    // The page must be the period's own, not the generic report re-titled.
    await expect(authedPage.getByRole("heading", { level: 1 })).toHaveText(titleKey);
    // And it must actually render — a route that threw would leave the error boundary here.
    await expect(authedPage.getByText(/last \d+ days/i)).toBeVisible();
    // The header label follows too. These routes sit under /report, so the nav's prefix match
    // claims them for "Health Report" unless the header special-cases them.
    await expect(authedPage.locator("header")).toContainText(titleKey);
    await expect(authedPage.locator("header")).not.toContainText("Health Report");
  });
}

test("a card with nothing to plot says so; a card with data still draws", async ({ authedPage }) => {
  // The fixture mints a fresh engine user, so this account has a seeded report snapshot and ZERO
  // reads — which is exactly the mixed state worth pinning: some series in the window, others
  // empty. Recharts draws an empty grid for an empty series, and a card headed "Reading volume"
  // over blank space reads as broken rather than as quiet.
  await authedPage.goto("/report/weekly");
  const card = (title: string) =>
    authedPage.locator(".rounded-lg.border").filter({
      has: authedPage.getByRole("heading", { level: 3, name: title, exact: true }),
    }).last();

  await expect(card("Reading volume")).toContainText("No data yet");
  // The guard must key on THIS card's data, not on the page having any gap in it — a blanket
  // empty state would swallow the charts that do have something to show.
  await expect(card("Health improvement")).not.toContainText("No data yet");
});

test("the two destinations are different pages", async ({ authedPage }) => {
  // The failure being fixed was not "the link is broken" but "both links go to the same place",
  // which every per-route assertion above would still pass if they were merged again.
  const headings: string[] = [];
  for (const { path } of CASES) {
    await authedPage.goto(path);
    headings.push(await authedPage.getByRole("heading", { level: 1 }).innerText());
  }
  expect(new Set(headings).size, `both periods rendered "${headings[0]}"`).toBe(2);
});
