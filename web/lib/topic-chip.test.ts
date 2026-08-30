// A story with no topic must render no topic chip.
//
// `_mode_topic` in the Story Service used to tally the empty string alongside real categories, so a
// cluster with more uncategorized members than any single category resolved to "" — "we don't know"
// outvoted the evidence. That is fixed at the source, but the blank case did not go away with it:
// a story really can have no categorized member, and then `story.topic` is "" by design (the
// classifier returns "" rather than inventing a "General").
//
// Every surface has to survive that. Three of the four here did not: `HeroStory` interpolated
// `story.topic` into an always-rendered `bg-accent` pill, so a blank topic became an empty coloured
// lozenge; `StoryFeatureCard` and `StoryListItem` guarded on the `showTopic` PROP — a layout
// preference ("this section header already names the topic") — which says nothing about whether
// there is a topic to show, so they emitted an empty span and, worse, an otherwise-empty dateline
// row still carrying its margin.
//
// This renders the real components rather than grepping them, because the defect is in the rendered
// output, not in the source text: the components are transpiled with the repo's own TypeScript and
// rendered through react-dom/server against stubbed leaves. A guard removed anywhere below fails
// here with the empty chip in the diff.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const WEB = join(import.meta.dirname, "..");

/** The leaves. None of them renders the topic, so stubbing them cannot hide the thing under test. */
const STUBS: Record<string, unknown> = {
  react: React,
  "react/jsx-runtime": jsxRuntime,
  "next/link": {
    __esModule: true,
    default: (p: any) => React.createElement("a", { href: p.href }, p.children),
  },
  "lucide-react": new Proxy({}, { get: () => () => null }),
  "@/lib/utils": { cn: (...a: unknown[]) => a.filter(Boolean).join(" ") },
  "@/lib/analytics": { track: () => {}, urlHost: () => "" },
  // `index: 8` below keeps every card off the animated path, so `motion.div` is never constructed;
  // the stub only has to satisfy the import.
  "framer-motion": { motion: new Proxy({}, { get: (_t, tag: string) => tag }) },
  "@/components/stories/coverage-plate": { CoveragePlate: () => null },
  "@/lib/i18n": {
    useTranslation: () => ({
      // Keys, not prose: a translated string must never be mistaken for the topic.
      t: (k: string) => k,
      formatCompact: (n: number) => String(n),
      timeAgo: () => "recently",
    }),
  },
  "@/components/shared/article-image": { ArticleImage: () => null },
  "@/components/shared/spectrum-bar": { SpectrumBar: () => null },
  "@/components/stories/freshness-badge": { FreshnessBadge: () => null },
  "@ih/core/logic/metrics": {
    LEAN_META: { left: { color: "#a00" }, center: { color: "#666" }, right: { color: "#00a" } },
  },
};

function load(rel: string): Record<string, any> {
  const js = ts.transpileModule(readFileSync(join(WEB, rel), "utf8"), {
    compilerOptions: {
      // The automatic runtime, which is what Next compiles these with — the sources import no
      // `React` binding, so the classic transform would emit calls to a name that isn't there.
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

const TOPIC = "Politics";

const story = (topic: string) => ({
  id: "s1",
  title: "A headline that is not the topic",
  topic,
  summary: "A summary that is not the topic.",
  image: null,
  totalCoverage: 6,
  publisherCount: 3,
  distribution: { left: 2, center: 2, right: 2 },
  updatedAt: "2026-08-30T00:00:00.000Z",
  freshness: null,
  blindspotSide: null,
});

const SURFACES: [string, string][] = [
  ["components/home/hero-story.tsx", "HeroStory"],
  ["components/home/story-feature-card.tsx", "StoryFeatureCard"],
  ["components/home/story-list-item.tsx", "StoryListItem"],
  ["components/stories/story-card.tsx", "StoryCard"],
];

/** `index: 8` puts StoryCard on its static path; the other three ignore it. */
const render = (C: any, topic: string, extra: object = {}) =>
  renderToStaticMarkup(React.createElement(C, { story: story(topic), index: 8, ...extra }));

/**
 * The opening tag of the element the topic is rendered INSIDE — the chip itself.
 *
 * Asserting "no empty elements anywhere" would be wrong: these layouts contain deliberate empty
 * elements (StoryFeatureCard's `<div className="mt-3 flex-1" />` spacer, for one). The defect is
 * specifically that the chip survives its own content, so the test locates the chip by rendering a
 * topic, then demands that exact element be gone — not emptied — when the topic is blank.
 */
function chipTag(html: string, name: string): string {
  const m = html.match(new RegExp(`<(\\w+)([^>]*)>${TOPIC}</\\1>`));
  assert.ok(m, `${name} does not render the topic inside an element of its own`);
  return `<${m[1]}${m[2]}>`;
}

for (const [rel, name] of SURFACES) {
  test(`${name}: a categorized story shows its topic`, () => {
    const C = load(rel)[name];
    assert.equal(typeof C, "function", `${rel} does not export ${name}`);
    // The control. Without it, the blank-topic assertion below would also pass on a component that
    // had simply stopped rendering the topic at all.
    assert.match(render(C, TOPIC), new RegExp(`>${TOPIC}<`), `${name} dropped a topic it should show`);
  });

  test(`${name}: an uncategorized story renders no empty chip`, () => {
    const C = load(rel)[name];
    const tag = chipTag(render(C, TOPIC), name);
    const blank = render(C, "");
    assert.ok(
      !blank.includes(tag),
      `${name} still renders the topic chip for a blank topic — an empty ${tag}</…>`,
    );
  });
}

/**
 * The story DETAIL page carries the same chip and got the same guard, but it is a Next route with a
 * long import surface — stubbing it to render here would be a bigger fake than the thing it tests.
 * So it is pinned statically instead: every interpolation of the topic must be matched by a guard.
 * Deleting the guard drops the guard count to zero; adding a second unguarded render pushes the
 * render count past it. Both fail. It is weaker than a render, and it is not nothing.
 */
test("the story detail page guards every topic interpolation", () => {
  const src = readFileSync(join(WEB, "app/(app)/stories/[id]/page.tsx"), "utf8");
  const renders = src.split("{story.topic}").length - 1;
  const guards = src.split("{story.topic && (").length - 1;
  assert.ok(renders > 0, "the detail page no longer renders a topic — retarget this test");
  assert.equal(guards, renders, `${renders} topic render(s) behind ${guards} guard(s)`);
});

test("StoryFeatureCard: showTopic={false} hides a topic that exists", () => {
  const C = load("components/home/story-feature-card.tsx").StoryFeatureCard;
  const html = renderToStaticMarkup(
    React.createElement(C, { story: story(TOPIC), showTopic: false }),
  );
  // The layout preference still has to work — the blank-topic guard must not have replaced it.
  assert.doesNotMatch(html, new RegExp(TOPIC));
});

test("StoryListItem: showTopic={false} hides a topic that exists", () => {
  const C = load("components/home/story-list-item.tsx").StoryListItem;
  const html = renderToStaticMarkup(
    React.createElement(C, { story: story(TOPIC), showTopic: false }),
  );
  assert.doesNotMatch(html, new RegExp(TOPIC));
});
