import { execSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "../fixtures";

/**
 * Coverage Comparison L0 (docs/COVERAGE_COMPARISON_DESIGN.md) end to end: a clustered article
 * renders counted cluster facts with openable evidence, and an article below the publisher floor
 * renders no card at all — the refusal is silent by design, never a placeholder.
 *
 * The catalog is seeded through the REAL store so the engine's own clustering decides membership;
 * nothing about the comparison is stubbed.
 */

const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

function seedCluster(prefix: string): void {
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
desc = "Councillors voted seven to two on Tuesday evening to approve the harbour plan. " * 3
cluster = [
    ("Harbour Gazette", "Council approves the harbour redevelopment after a long hearing", "08"),
    ("Meridian Wire",   "Council approves harbour redevelopment following lengthy hearing", "09"),
    ("Ledger Daily",    "Council approves the harbour redevelopment plan after hearing",    "10"),
    ("City Chronicle",  "Council approves harbour redevelopment at a long council hearing", "11"),
]
for i, (pub, headline, hh) in enumerate(cluster):
    url = f"https://{'''${prefix}'''}-{i}.example.com/harbour"
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title=headline, description=desc, body=None,
        published_at=f"2026-08-02T{hh}:00:00Z", source_feed="e2e",
        scored={"article_id": url, "outlet": pub, "category": "Politics",
                "lean": (0.0, -1.0, 0.0, 0.0)[i], "political": True, "title": headline})
# a two-publisher story: below the floor, must render nothing
for i, pub in enumerate(["Solo Post", "Duo News"]):
    url = f"https://{'''${prefix}'''}-ferry-{i}.example.com/ferry"
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title="Ferry terminal refurbishment contract awarded to a local firm",
        description="The ferry terminal refurbishment contract was awarded on Monday. " * 3,
        body=None, published_at="2026-08-02T12:00:00Z", source_feed="e2e",
        scored={"article_id": url, "outlet": pub, "category": "Politics", "lean": 0.0,
                "political": True, "title": "ferry"})
print("seeded")
`;
  execSync("python3 -", { input: py, stdio: ["pipe", "inherit", "inherit"] });
}

test.describe("Coverage Comparison (L0)", () => {
  test("counted cluster facts render with evidence; a small cluster renders nothing",
    async ({ authedPage, uid }) => {
      const prefix = `cc${uid}`;
      seedCluster(prefix);

      await authedPage.goto("/analyze", { waitUntil: "networkidle" });
      await authedPage.getByLabel("Article URL").fill(`https://${prefix}-0.example.com/harbour`);
      await authedPage.getByRole("button", { name: "Analyze" }).click();

      const card = authedPage.getByText("Coverage comparison");
      await expect(card).toBeVisible();
      // the counted scope line, the balance section, and the standing caveat
      await expect(authedPage.getByText(/outlets, \d+ articles covering this story/)).toBeVisible();
      await expect(authedPage.getByText("The wider coverage")).toBeVisible();
      await expect(authedPage.getByText("Unique to this article")).toBeVisible();
      await expect(authedPage.getByText("First report in this coverage.")).toBeVisible();
      await expect(
        authedPage.getByText(/doesn't say what this article left out/),
      ).toBeVisible();
      // evidence is openable: a sibling outlet is linked out of the card
      await expect(
        authedPage.getByRole("link", { name: "Meridian Wire" }).first(),
      ).toBeVisible();

      // …and the two-publisher story is refused silently — no card, no placeholder.
      await authedPage.getByLabel("Article URL").fill(`https://${prefix}-ferry-0.example.com/ferry`);
      await authedPage.getByRole("button", { name: "Analyze" }).click();
      await expect(authedPage.getByText("Political lean")).toBeVisible();   // analysis rendered
      await expect(authedPage.getByText("Coverage comparison")).toBeHidden();
    });
});
