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
});
