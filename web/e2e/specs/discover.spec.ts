import path from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect } from "../fixtures";
import { engineGet } from "../helpers";

/**
 * Discover after the 2026-08-16 layout revert: the ORIGINAL uniform card grid, carrying the
 * layout-independent fixes that survived — publisher interleave, the imageSuspect/branding
 * guard's text-first fallback, lean said once (pill only, no house-lean dot), and the shared
 * read/save flows. The rhythm experiment (beats, landmarks, compact rows) must leave no trace.
 */
const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

let seeded = false;

function seedDiscover(): void {
  if (seeded) return;
  seeded = true;
  const py = `
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
now = datetime.now(timezone.utc)

# (slug, publisher, minutes-ago, lean-or-None, image-url-or-None). Sigma Post files a BURST —
# the interleave must spread it. The "suspect" article's image URL carries a furniture token, so
# the engine marks imageSuspect and the card must render text-first.
rows = [
    ("burst-one",  "Sigma Post",    1, -1.0, None),
    ("burst-two",  "Sigma Post",    2, -1.0, None),
    ("burst-three","Sigma Post",    3, -1.0, None),
    ("other-one",  "Tau Tribune",   4,  0.5, None),
    ("suspect",    "Upsilon Wire",  5, -1.0, "https://cdn.upsilon.example.com/img/site_logo.png"),
    ("plain",      "Phi Courier",   6, None, None),
]
for slug, pub, mins, lean, img in rows:
    url = f"https://{slug}.example.com/a"
    scored = {"article_id": url, "outlet": pub, "category": "Politics", "title": slug}
    if lean is not None:
        scored["lean"] = lean
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title=f"Story about {slug.replace('-', ' ')} unfolds", description="A deterministic dek.",
        body=None, published_at=(now - timedelta(minutes=mins)).isoformat(),
        source_feed="e2e", image=img,
        image_source=("media:content" if img else None),
        scored=scored)
`;
  execFileSync("python", ["-c", py], { cwd: REPO_ROOT, stdio: "pipe" });
}

test.describe("Discover (reverted layout, kept fixes)", () => {
  test.beforeEach(async ({ authedPage: page }) => {
    seedDiscover();
    // Seeded article urls are unreachable by design; abort every non-localhost navigation.
    await page.context().route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort());
    await page.goto("/discover");
    await expect(page.getByText("Story about burst one unfolds")).toBeVisible();
  });

  test("original grid, no rhythm artifacts, burst interleaved", async ({ authedPage: page }) => {
    await expect(page.getByTestId("river-beat")).toHaveCount(0);
    await expect(page.getByTestId("river-mark")).toHaveCount(0);
    await expect(page.getByTestId("river-row")).toHaveCount(0);
    // The Sigma Post burst may not occupy the first two cards back to back: the second card
    // must be another outlet (the interleave keeps working on the card grid).
    const cards = page.locator("main article, article");
    await expect(cards.first()).toContainText("Sigma Post");
    await expect(cards.nth(1)).not.toContainText("Sigma Post");
  });

  test("lean is said once per card — the pill, no house-lean dot duplication", async ({
    authedPage: page,
  }) => {
    const card = page.locator("article", { hasText: "Story about burst one unfolds" });
    await expect(card.getByText("Lean left", { exact: true })).toHaveCount(1);
    const unknown = page.locator("article", { hasText: "Story about plain unfolds" });
    await expect(unknown.getByText("Unknown lean", { exact: true })).toHaveCount(1);
  });

  test("an engine-flagged branding image renders text-first, never the furniture", async ({
    authedPage: page,
  }) => {
    const card = page.locator("article", { hasText: "Story about suspect unfolds" });
    await expect(card).toBeVisible();
    await expect(card.locator('img[alt="Story about suspect unfolds"]')).toHaveCount(0);
  });

  test("Read button records the read; Save toggles without opening", async ({
    authedPage: page,
    uid,
  }) => {
    const card = page.locator("article", { hasText: "Story about other one unfolds" });
    const [popup] = await Promise.all([
      page.context().waitForEvent("page"),
      card.getByRole("button", { name: /Read article/ }).click(),
    ]);
    await popup.close();
    await expect
      .poll(async () => {
        const history = await engineGet<Array<{ id?: string; article?: { url?: string } }>>(
          uid, "/api/me/history");
        return history.filter((h) =>
          `${h.id ?? ""} ${h.article?.url ?? ""}`.includes("other-one")).length;
      })
      .toBe(1);

    let popups = 0;
    page.context().on("page", () => popups++);
    const save = card.getByRole("button", { name: /^(Save|Saved)$/ });
    await save.click();
    await expect(save).toHaveAttribute("aria-pressed", "true");
    expect(popups, "saving must never open the article").toBe(0);
  });
});
