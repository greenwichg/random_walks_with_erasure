/**
 * Regression: clicking a breaking-news notification whose story has since dissolved.
 *
 * The measured failure (2026-08-23): a week-old "Breaking news" inbox item deep-linked to
 * `/stories/st_…`; the catalog window had moved past the event, `/api/story/{id}` correctly
 * answered 404 — and the story page routed that 404 into the generic "Something went wrong /
 * Try again" error state (retrying a vanished story forever), while its own purpose-built
 * `stories.notFound` state sat unreachable behind a null-data branch the API never produces.
 *
 * Three pins, one per hop of the click:
 *  1. the deep link itself (pure function, real import) — the notification navigates by storyId;
 *  2. `useStory` surfaces a 404 immediately (no retries — a dissolved story is a permanent answer);
 *  3. the story page renders the not-found state for a 404, never the retry state.
 *
 * 2 and 3 are source-shape guards in the house style of core-import-guard / api-auth-guard:
 * the hook and the page are React modules a node:test process cannot render, but the load-bearing
 * lines are grep-stable and a regression deletes them.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { hrefFor } from "@ih/core/logic/notification-kinds";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

test("a breaking_story notification deep-links to its story page", () => {
  assert.equal(hrefFor("breaking_story", { storyId: "st_1234abcd" }), "/stories/st_1234abcd");
  // no storyId in the payload → the kind's static destination, never a broken path
  assert.equal(hrefFor("breaking_story", {}), "/stories");
});

test("an expired breaking deep link degrades to the live Stories page", () => {
  // The story a breaking notification points at dissolves when the catalog window moves past it;
  // its deep link then only answers "Story not found". Past the cutoff the row keeps working —
  // it just goes to the kind's static /stories (current events) instead of the dead id.
  const now = new Date("2026-08-23T12:00:00Z");
  // the fan-out's own cutoff wins when present…
  assert.equal(
    hrefFor("breaking_story", { storyId: "st_x", expiresAt: "2026-08-23T13:00:00Z" }, now),
    "/stories/st_x", "before the event's cutoff the deep link stands");
  assert.equal(
    hrefFor("breaking_story", { storyId: "st_x", expiresAt: "2026-08-23T11:00:00Z" }, now),
    "/stories", "past the event's cutoff the row degrades to the static href");
  // …legacy rows (materialised before expiresAt was carried) are judged by occurredAt + 6h
  assert.equal(
    hrefFor("breaking_story", { storyId: "st_x", occurredAt: "2026-08-23T08:00:00Z" }, now),
    "/stories/st_x", "a 4h-old legacy row still deep-links");
  assert.equal(
    hrefFor("breaking_story", { storyId: "st_x", occurredAt: "2026-08-15T08:00:00Z" }, now),
    "/stories", "the week-old legacy rows from the bug report stop dead-ending");
  // a payload with neither timestamp keeps the pre-expiry behaviour, and no other kind changes
  assert.equal(hrefFor("breaking_story", { storyId: "st_x" }, now), "/stories/st_x");
  assert.equal(hrefFor("weekly_report", { occurredAt: "2026-08-01T00:00:00Z" }, now), "/report/weekly");
});

test("the story proxy passes the engine's 404 through instead of flattening it to 503", () => {
  // The hop the first fix missed (verified by the reporter's second screenshot): the Next route
  // used `backendGet`, which collapses "story dissolved" (404) and "engine down" (transport)
  // into one null, so the browser saw engineUnavailable's 503 and the page's 404 branch could
  // never fire. The publisher route's status-preserving pattern is the contract here.
  const src = fs.readFileSync(
    path.join(WEB, "app", "api", "stories", "[id]", "route.ts"), "utf8");
  assert.match(src, /backendGetResult</, "the route must use the status-preserving fetch");
  assert.match(src, /status === 404/, "the route must branch on the engine's 404");
  assert.match(src, /\{ status: 404 \}/, "…and answer the browser with a real 404");
  assert.ok(!/backendGet</.test(src.replace(/backendGetResult</g, "")),
    "the status-collapsing backendGet must not creep back into this route");
});

test("useStory surfaces a 404 immediately instead of retrying a dissolved story", () => {
  const src = fs.readFileSync(path.join(WEB, "hooks", "use-data.ts"), "utf8");
  const hook = src.slice(src.indexOf("export const useStory"), src.indexOf("export const usePublisher"));
  assert.match(hook, /retry:.*status\s*!==\s*404/s,
    "useStory must not retry 404s — a dissolved story is a permanent answer, not a transient error");
});

test("the story page routes a 404 to its not-found state, never the retry state", () => {
  const src = fs.readFileSync(
    path.join(WEB, "app", "(app)", "stories", "[id]", "page.tsx"), "utf8");
  const errorBranch = src.slice(src.indexOf("if (isError)"), src.indexOf("if (!story)"));
  const notFound = errorBranch.indexOf("stories.notFound.title");
  const retry = errorBranch.indexOf("ErrorState");
  assert.ok(errorBranch.includes("status === 404"), "the error branch distinguishes 404");
  assert.ok(notFound !== -1, "the 404 path renders the stories.notFound state");
  assert.ok(retry !== -1, "non-404 errors keep the retry state");
  assert.ok(notFound < retry, "the 404 check runs before the generic retry fallback");
});
