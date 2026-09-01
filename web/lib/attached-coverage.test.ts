// Attached Tier B coverage renders as a labeled addendum, and never leaks into panel-derived facts.
//
// M4's engine-side containment proof ends at the wire: the client re-derives publisher counts, the
// register split and framing from `story.coverage`, so one unsplit `.map()` would quietly count
// coverage that never voted. The core rule is `splitCoverage`; this file checks the two web halves
// of the contract — the story page derives from the PANEL half, and the coverage list renders the
// attached half as its own labeled group ("from beyond the panel"), badged, lean-badge-free, and
// outside the filter counts.
//
// Rendered with the repo's own TypeScript against stubbed leaves (the topic-chip harness), because
// the count and the badge are rendered output, not source text; the page wiring is source-pinned
// because it is wiring.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const WEB = join(import.meta.dirname, "..");

function transpile(rel: string): Record<string, any> {
  const js = ts.transpileModule(readFileSync(join(WEB, rel), "utf8"), {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;
  const mod = { exports: {} as Record<string, any> };
  const req = (id: string) => {
    if (id in STUBS) return STUBS[id];
    throw new Error(`unstubbed import ${id} in ${rel} — add it to STUBS`);
  };
  new Function("exports", "require", "module", js)(mod.exports, req, mod);
  return mod.exports;
}

const STUBS: Record<string, unknown> = {
  react: React,
  "react/jsx-runtime": jsxRuntime,
  "next/link": {
    __esModule: true,
    default: (p: any) => React.createElement("a", { href: p.href }, p.children),
  },
  // The REAL split rule — stubbing it would test the stub. Same for the run grouping: the
  // attached group must render one row per attached article, and a stubbed grouper could
  // collapse them without the assertions noticing.
  "@ih/core/logic/story-attached": transpile("../packages/core/logic/story-attached.ts"),
  "@ih/core/logic/coverage-groups": transpile("../packages/core/logic/coverage-groups.ts"),
  "@ih/core/logic/publisher-logo": transpile("../packages/core/logic/publisher-logo.ts"),
  "@ih/core/logic/placeholder-art": transpile("../packages/core/logic/placeholder-art.ts"),
  "lucide-react": new Proxy({}, { get: () => () => null }),
  "@/components/shared/publisher-logo": { PublisherLogo: () => null },
  "@/components/shared/section-header": {
    SectionHeader: (p: any) => React.createElement("h2", { id: p.id }, p.title),
  },
  // Markers, not looks: the assertions grep for these strings.
  "@/components/shared/article-badges": {
    LeanBadge: (p: any) => React.createElement("i", null, `[lean:${p.bucket ?? "none"}]`),
    RegisterBadge: () => null,
  },
  "@/components/shared/continuation-strip": { ContinuationStrip: () => null },
  "@/components/shared/read-article-button": { ReadArticleButton: () => null },
  "@/components/shared/save-button": { SaveButton: () => null },
  "@/components/ui/button": {
    Button: (p: any) => React.createElement("button", null, p.children),
  },
  "@/components/ui/filter-chip": {
    FilterChip: (p: any) =>
      React.createElement("button", null, `${p.label}=${p.count ?? "-"}`),
  },
  "@/lib/i18n": {
    useTranslation: () => ({
      t: (k: string, params?: Record<string, unknown>) =>
        params && "n" in params ? `${k}(${params.n})` : k,
      formatCompact: (n: number) => String(n),
      timeAgo: () => "recently",
    }),
  },
};

const row = (publisher: string, extra: Record<string, unknown> = {}) => ({
  publisher,
  headline: `${publisher} covers the flood`,
  url: `https://${publisher.toLowerCase()}.example/flood`,
  publishedAt: "2026-08-30T00:00:00.000Z",
  ...extra,
});

const COVERAGE = [
  row("Alpha", { leanBucket: "left", lean: -1 }),
  row("Beta", { leanBucket: "right", lean: 1 }),
  row("Dantri", { tierB: true }),
  row("Ilta", { tierB: true }),
];

test("attached rows render as the labeled beyond-the-panel group, badged and lean-free", () => {
  const { CoverageList } = transpile("components/stories/coverage-list.tsx");
  const html = renderToStaticMarkup(React.createElement(CoverageList, { coverage: COVERAGE }));

  assert.ok(html.includes("story.beyondPanel(2)"), "the group header carries the attached count");
  assert.ok(html.includes("story.beyondPanelNote"), "the honesty note renders with the group");
  const badges = html.split("story.beyondPanelBadge").length - 1;
  assert.equal(badges, 2, "every attached row is badged — and only attached rows");
  assert.ok(html.includes("Dantri") && html.includes("Ilta"), "attached rows actually render");
  // The addendum is an addendum: both attached publishers appear AFTER both members.
  const last = Math.max(html.indexOf("Alpha"), html.indexOf("Beta"));
  assert.ok(html.indexOf("Dantri") > last && html.indexOf("Ilta") > last,
    "attached rows render after the panel rows — the divider is the tier boundary");
  // No fabricated lean on an unrated addendum; members keep theirs.
  assert.ok(html.includes("[lean:left]") && html.includes("[lean:right]"), "member lean badges kept");
  const attachedChunk = html.slice(html.indexOf("story.beyondPanel("));
  assert.ok(!attachedChunk.includes("[lean:"), "an attached row renders NO lean badge, not 'none'");
});

test("panel-derived numbers exclude the attached rows", () => {
  const { CoverageList } = transpile("components/stories/coverage-list.tsx");
  const html = renderToStaticMarkup(React.createElement(CoverageList, { coverage: COVERAGE }));
  assert.ok(html.includes("rec.filter.all=2"), "the 'all' chip counts the panel, not the addenda");
  assert.ok(html.includes(">2 / 2<"), "the N/M line describes the panel");
});

test("no attached rows, no group — the addendum never renders empty furniture", () => {
  const { CoverageList } = transpile("components/stories/coverage-list.tsx");
  const html = renderToStaticMarkup(
    React.createElement(CoverageList, { coverage: COVERAGE.filter((c) => !("tierB" in c)) }),
  );
  assert.ok(!html.includes("story.beyondPanel"), "no group header without attached rows");
});

test("the group is gated on filters at rest, and the page derives from the panel half", () => {
  // Source pins for what a static render cannot exercise (filter state) and for the page wiring.
  const list = readFileSync(join(WEB, "components/stories/coverage-list.tsx"), "utf8");
  assert.ok(
    /lean === "all" && register === "all"/.test(list),
    "the attached group must hide under an active filter — it carries no lean to filter BY",
  );
  const page = readFileSync(join(WEB, "app/(app)/stories/[id]/page.tsx"), "utf8");
  assert.ok(page.includes("splitCoverage(story.coverage)"), "the page splits once, at the top");
  for (const pin of [
    "coverage={panelCoverage} />", // StoryCoveragePanel + FramingComparison
    "new Set(panelCoverage.map((c) => c.publisher)).size",
    "for (const row of panelCoverage)",
  ]) {
    assert.ok(page.includes(pin), `page must derive from the panel half: missing ${pin}`);
  }
  assert.ok(
    page.includes("<CoverageList coverage={story.coverage}"),
    "the LIST alone receives the full coverage — it draws the boundary itself",
  );
});
