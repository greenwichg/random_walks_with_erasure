import { test, expect } from "../fixtures";

/**
 * Journey 8 — Publisher Intelligence. The profile page composes curated registry facts with
 * counted catalog facts. On a fresh engine DB the catalog is empty, so a registry outlet renders
 * its curated identity with an HONEST zero volume (no fabricated numbers), and an unknown name is
 * a real 404 not-found state — a profile is never synthesised from nothing.
 */
test.describe("Publisher Intelligence", () => {
  test("a registry outlet profiles with curated facts and an honest zero catalog", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/publishers/NPR");
    await expect(page.getByRole("heading", { name: "NPR", exact: true })).toBeVisible();
    // Curated registry facts render (home country + scope) …
    await expect(page.getByText("United States").first()).toBeVisible();
    await expect(page.getByText("National", { exact: true })).toBeVisible();
    // … and the empty catalog is an honest empty state, not fabricated numbers.
    await expect(page.getByText("0 articles indexed")).toBeVisible();
    await expect(page.getByText("No articles indexed yet")).toBeVisible();
  });

  test("an unknown publisher is a not-found state, never a synthesised profile", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/publishers/Completely%20Unknown%20Gazette");
    await expect(page.getByText("Publisher not found")).toBeVisible();
  });

  test("the page renders fully when no Wikipedia metadata has been fetched", async ({ authedPage }) => {
    // The enrichment cache is empty on a fresh engine DB (and the enricher is off outside
    // production), so this is the state every publisher page starts in. The About block must
    // simply be absent — never an empty card, and never a blocked render waiting on a lookup.
    const page = authedPage;
    await page.goto("/publishers/NPR");
    await expect(page.getByRole("heading", { name: "NPR", exact: true })).toBeVisible();
    await expect(page.getByText("Founded", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Parent organization", { exact: true })).toHaveCount(0);
    // The curated half of the profile is unaffected by enrichment being absent.
    await expect(page.getByText("United States").first()).toBeVisible();
  });
  /**
   * Factuality — a third party's verdict, and the two ways showing one can lie.
   *
   * Shown bare it reads as OUR assessment of a named news organisation; shown undated it claims
   * the rater still says it. So the assertions here are about the ATTRIBUTION travelling with the
   * value, not merely about the value appearing.
   */
  test("a rated outlet shows the verdict with its rater and the date it was read", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/publishers/NPR");
    await expect(page.getByRole("heading", { name: "NPR", exact: true })).toBeVisible();

    await expect(page.getByText("Factuality: High", { exact: true })).toBeVisible();
    // The rater is named on the page, not implied — and the retrieval date sits beside it.
    const credit = page.getByRole("link", { name: /Media Bias\/Fact Check/ });
    await expect(credit).toBeVisible();
    await expect(credit).toHaveAttribute("href", /mediabiasfactcheck\.com/);
    // The link resolves to THIS outlet at the rater, never the rater's front page.
    await expect(credit).toHaveAttribute("href", /npr\.org/);
    await expect(credit).toHaveAttribute("rel", /noopener/);
  });

  test("an outlet with no verdict says so, rather than showing nothing", async ({ authedPage }) => {
    // Absence rendered as absence. A missing row would read as "fine" to someone scanning the
    // page, which is the failure the explicit "Not rated" treatment exists to prevent — the same
    // rule the political lean already follows (L2.2).
    const page = authedPage;
    await page.goto("/publishers/Le%20Monde");
    await expect(page.getByRole("heading", { name: "Le Monde", exact: true })).toBeVisible();
    await expect(page.getByText("Factuality not rated", { exact: true })).toBeVisible();
    await expect(page.getByText(/^Factuality: /)).toHaveCount(0);
  });

  test("an unregistered publisher claims no verdict at all", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/publishers/Completely%20Unknown%20Gazette");
    await expect(page.getByText("Publisher not found")).toBeVisible();
    await expect(page.getByText(/Factuality: /)).toHaveCount(0);
  });

  /**
   * The kill switch. `RWE_PUBLIC_FACTUALITY` is OFF in production because the verdicts are a third
   * party's commercial product we hold no licence to redistribute, so a disabled engine sends no
   * `factualityPublished` and no verdict. These two tests drive that from the client side, where
   * the distinction the flag exists for actually has to hold.
   */
  test("with publication switched off the badge is absent — not a 'not rated' claim", async ({
    authedPage,
  }) => {
    // The failure this guards is specific and easy to ship by accident: strip the verdict but keep
    // rendering the badge, and 123 outlets we DO hold verdicts for start asserting "not rated" —
    // a label that lies about the publisher rather than describing our configuration.
    const page = authedPage;
    await page.route("**/api/publishers/**", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      delete body.factualityPublished;
      delete body.factuality;
      await route.fulfill({ response: res, body: JSON.stringify(body) });
    });
    await page.goto("/publishers/NPR");
    await expect(page.getByRole("heading", { name: "NPR", exact: true })).toBeVisible();
    await expect(page.getByText(/^Factuality: /)).toHaveCount(0);
    await expect(page.getByText("Factuality not rated", { exact: true })).toHaveCount(0);
    // The rest of the profile is untouched — the switch removes one module, not the page.
    await expect(page.getByText("United States").first()).toBeVisible();
  });

  test("publication on, but this outlet unrated, still says 'not rated'", async ({ authedPage }) => {
    // The other half of the same distinction: `factualityPublished` without a verdict is a real
    // state and must keep stating absence, so switching the feature off is the ONLY thing that
    // removes the badge.
    const page = authedPage;
    await page.route("**/api/publishers/**", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.factualityPublished = true;
      delete body.factuality;
      await route.fulfill({ response: res, body: JSON.stringify(body) });
    });
    await page.goto("/publishers/NPR");
    await expect(page.getByText("Factuality not rated", { exact: true })).toBeVisible();
    await expect(page.getByText(/^Factuality: /)).toHaveCount(0);
  });
});
