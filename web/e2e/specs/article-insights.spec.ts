import { execSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "../fixtures";

/**
 * Article Insights (docs/ARTICLE_INSIGHTS.md) — the serve+render path, end to end: a cached
 * insights row for a catalog article renders the "AI summary & framing" section on /analyze;
 * an article without one renders the analysis WITHOUT the section (no placeholder, no spinner).
 *
 * The row is seeded through the REAL store accessors (enqueue_insights + finish_insights)
 * against the e2e engine's own database — the generation worker itself is deliberately not run
 * here (it is an LLM network call, env-gated off in e2e; its logic is covered by the engine
 * unit suite). What this spec proves is the product contract: cache-only reads, nullable
 * attach, render-when-present.
 */

const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

function seedCatalogAndInsights(urlWith: string, urlWithout: string): void {
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
for i, url in enumerate([${JSON.stringify(urlWith)}, ${JSON.stringify(urlWithout)}]):
    canon = ingest.canonical_url(url)
    st.upsert_feed_article(
        canonical_url=canon, url=url, publisher=f"E2E Outlet {i}", source_publisher=None,
        title=f"E2E seeded headline {i} about one distinct event",
        description="A seeded description with plenty of grounding text. " * 5,
        body=None, published_at="2026-08-01T00:00:00Z", source_feed="e2e",
        scored={"article_id": canon, "outlet": f"E2E Outlet {i}", "category": "Politics",
                "lean": 0.0, "political": True, "title": f"E2E seeded headline {i}"})
canon = ingest.canonical_url(${JSON.stringify(urlWith)})
st.enqueue_insights(min_chars=0)
st.finish_insights(canon, ok=True, model="e2e:fake-model", payload={
    "summary": "E2E seeded summary sentence one. E2E seeded summary sentence two.",
    "bias": {"framing": "Foregrounds the seeded event.",
             "tone": "Neutral, e.g. 'seeded read'.",
             "loadedLanguage": ["seeded phrase"],
             "omissions": "No cost figures are given.",
             "viewpoint": "Centres the e2e harness."}})
assert canon in st.get_insights([canon]), "seeded insights row did not round-trip"
`;
  execSync("python3 -", { input: py, stdio: ["pipe", "inherit", "inherit"] });
}

test.describe("Article Insights on /analyze", () => {
  test("a cached artifact renders the section; absence renders nothing", async ({ authedPage, uid }) => {
    const withRow = `https://e2e.example/insights/${uid}/0`;
    const without = `https://e2e.example/insights/${uid}/1`;
    seedCatalogAndInsights(withRow, without);

    // Article WITH a cached artifact → the section renders summary + bias prose.
    await authedPage.goto("/analyze", { waitUntil: "networkidle" });
    await authedPage.getByLabel("Article URL").fill(withRow);
    await authedPage.getByRole("button", { name: "Analyze" }).click();
    await expect(authedPage.getByText("AI summary & framing")).toBeVisible();
    await expect(authedPage.getByText("E2E seeded summary sentence one.", { exact: false })).toBeVisible();
    await expect(authedPage.getByText("Foregrounds the seeded event.", { exact: false })).toBeVisible();
    await expect(authedPage.getByText("“seeded phrase”", { exact: false })).toBeVisible();

    // Article WITHOUT one → analysis completes, section absent (no placeholder of any kind).
    await authedPage.getByLabel("Article URL").fill(without);
    await authedPage.getByRole("button", { name: "Analyze" }).click();
    await expect(authedPage.getByText("E2E seeded summary sentence one.", { exact: false })).toBeHidden();
    await expect(authedPage.getByText("AI summary & framing")).toBeHidden();
  });
});
