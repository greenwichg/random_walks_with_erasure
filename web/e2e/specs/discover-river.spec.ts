import path from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect } from "../fixtures";
import { engineGet } from "../helpers";
import { bucketLabel, type MarkLabel } from "../../lib/discover-order";

/**
 * Discover river rhythm (the approved River Rhythm mock, 2026-08-16): time landmarks from stored
 * `publishedAt`, a featured beat at every 9th river slot (next imaged within the look-ahead),
 * lean-edge text rows for KNOWN lean only, lean said exactly once per row, and the row itself as
 * the Read affordance with the compact corner Save.
 *
 * Seeded deterministically: 15 articles, all distinct publishers (interleave is then a no-op, so
 * beat placement is exact): 3 front-page picks, 11 past-hour river articles where ONLY the one at
 * river slot 10 carries an image (the slot-9 beat must pull it forward), and one 30-hour-old
 * article that forces a second landmark. The 30h label depends on the reader's local midnight, so
 * the expectation is computed with the SAME `bucketLabel` the page uses, never hardcoded.
 */
const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

const MARK_TEXT: Record<MarkLabel, string> = {
  pastHour: "Past hour",
  earlierToday: "Earlier today",
  yesterday: "Yesterday",
  earlier: "Earlier",
};

const OLD_MINS = 30 * 60; // 30 hours
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

# (slug, publisher, minutes-ago, lean-or-None, image?)  — headlines derived from the slug.
rows = [
    ("front-lead",   "Alpha Wire",      5,  -1.0, True),
    ("front-two",    "Beta Journal",    7,   0.0, False),
    ("front-three",  "Gamma Post",      9,   1.0, False),
    ("river-one",    "Delta Times",    11,  -1.0, False),   # text row, KNOWN lean -> edge
    ("river-two",    "Epsilon Ledger", 12,  None, False),   # unknown lean -> no edge
    ("river-three",  "Zeta Herald",    13,   0.5, False),
    ("river-four",   "Eta Gazette",    14,  -0.5, False),
    ("river-five",   "Theta Daily",    15,   1.5, False),
    ("river-six",    "Iota Courier",   16,  -1.0, False),
    ("river-seven",  "Kappa Review",   17,   0.0, False),
    ("river-eight",  "Mu Observer",    18,   1.0, False),
    ("river-nine",   "Nu Dispatch",    19,  -0.5, False),
    ("river-ten",    "Xi Star",        20,  -1.0, True),    # the beat: only imaged river item
    ("river-eleven", "Omicron Sun",    21,   0.5, False),
    ("old-one",      "Omega Chronicle", ${OLD_MINS}, 1.0, False),
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
        source_feed="e2e",
        image=(f"https://img.{slug}.example.com/hero.jpg" if img else None),
        image_width=(1600 if img else None), image_height=(900 if img else None),
        image_source=("media:content" if img else None),
        scored=scored)
`;
  execFileSync("python", ["-c", py], { cwd: REPO_ROOT, stdio: "pipe" });
}

test.describe("Discover river rhythm", () => {
  test.beforeEach(async ({ authedPage: page }) => {
    seedDiscover();
    // Seeded article urls are unreachable by design; abort every non-localhost navigation so the
    // Read popup neither hangs nor hits the network (image 404s are fine — the beat's VARIANT is
    // decided by engine data, not by whether pixels load).
    await page.context().route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort());
    await page.goto("/discover");
    await expect(page.getByTestId("river-row").first()).toBeVisible();
  });

  test("beat lands on the 9th river slot with the pulled-forward imaged article", async ({
    authedPage: page,
  }) => {
    const beats = page.getByTestId("river-beat");
    await expect(beats).toHaveCount(1);
    await expect(beats).toContainText("Story about river ten unfolds");
    // 9th ARTICLE slot in document order (landmarks are headers, not slots)
    const slots = page.locator('[data-testid="river-row"], [data-testid="river-beat"]');
    await expect(slots.nth(8)).toHaveAttribute("data-testid", "river-beat");
    // deterministic across reloads
    await page.reload();
    await expect(page.getByTestId("river-beat")).toContainText("Story about river ten unfolds");
  });

  test("landmarks come from stored publishedAt, first Past hour, then the 30h bucket", async ({
    authedPage: page,
  }) => {
    const marks = page.getByTestId("river-mark");
    await expect(marks).toHaveCount(2);
    await expect(marks.nth(0)).toContainText(MARK_TEXT.pastHour);
    // 30h ago is "Yesterday" or "Earlier" depending on the local clock — same rule as the page.
    const expected = MARK_TEXT[bucketLabel(new Date(Date.now() - OLD_MINS * 60_000).toISOString(), new Date())];
    await expect(marks.nth(1)).toContainText(expected);
    await expect(page.getByTestId("river-row").last()).toContainText("Story about old one unfolds");
  });

  test("lean: said once per row; edge only for KNOWN lean on text rows", async ({
    authedPage: page,
  }) => {
    const edged = page.locator('[data-testid="river-row"]', { hasText: "Story about river one unfolds" });
    await expect(edged).toHaveAttribute("data-lean-edge", "left");
    await expect(edged.getByText("Lean left", { exact: true })).toHaveCount(1);

    const unknown = page.locator('[data-testid="river-row"]', { hasText: "Story about river two unfolds" });
    await expect(unknown).not.toHaveAttribute("data-lean-edge", /.+/);
    await expect(unknown.getByText("Unknown lean", { exact: true })).toHaveCount(1);

    const beat = page.getByTestId("river-beat");
    await expect(beat).not.toHaveAttribute("data-lean-edge", /.+/);
    await expect(beat.getByText("Lean left", { exact: true })).toHaveCount(1);
  });

  test("the row is the Read affordance: one click, one popup, one recorded read", async ({
    authedPage: page,
    uid,
  }) => {
    const row = page.locator('[data-testid="river-row"]', { hasText: "Story about river three unfolds" });
    const [popup] = await Promise.all([page.context().waitForEvent("page"), row.click()]);
    await popup.close();
    await expect
      .poll(async () => {
        const history = await engineGet<Array<{ id?: string; article?: { url?: string } }>>(
          uid, "/api/me/history");
        return history.filter((h) =>
          `${h.id ?? ""} ${h.article?.url ?? ""}`.includes("river-three")).length;
      })
      .toBe(1);
  });

  test("compact Save toggles without opening the article", async ({ authedPage: page, uid }) => {
    const row = page.locator('[data-testid="river-row"]', { hasText: "Story about river four unfolds" });
    await row.hover();
    const save = row.locator("button[aria-pressed]");
    let popups = 0;
    page.context().on("page", () => popups++);
    await save.click();
    await expect(save).toHaveAttribute("aria-pressed", "true");
    await expect
      .poll(async () => {
        const saved = await engineGet<Array<{ articleId?: string }>>(uid, "/api/me/saved");
        return saved.some((s) => String(s.articleId ?? "").includes("river-four"));
      })
      .toBe(true);
    expect(popups, "saving must never also open the article").toBe(0);
  });

  test("responsive: two columns on desktop, one on mobile, beat full-width on both", async ({
    authedPage: page,
  }) => {
    const rows = page.getByTestId("river-row");
    const beat = page.getByTestId("river-beat");
    // Desktop (default viewport): the first two rows share a grid row — same y, different x.
    const [a, b] = [await rows.nth(0).boundingBox(), await rows.nth(1).boundingBox()];
    expect(a && b && Math.abs(a.y - b.y) < 2).toBeTruthy();
    expect(a && b && a.x !== b.x).toBeTruthy();
    const beatBox = await beat.boundingBox();
    expect(beatBox && a && beatBox.width > (a.width ?? 0) * 1.8, "beat spans both columns").toBeTruthy();

    await page.setViewportSize({ width: 390, height: 844 });
    const [m1, m2] = [await rows.nth(0).boundingBox(), await rows.nth(1).boundingBox()];
    expect(m1 && m2 && Math.abs(m1.x - m2.x) < 2, "stacked on mobile").toBeTruthy();
    const beatM = await beat.boundingBox();
    const rowM = m1;
    expect(beatM && rowM && Math.abs(beatM.width - rowM.width) < 4, "beat matches row width on mobile").toBeTruthy();
    await expect(page.getByTestId("river-mark").first()).toBeVisible();
  });
});
