import { test } from "node:test";
import assert from "node:assert/strict";
import { condenseTimeline } from "./story-timeline.ts";
import type { StoryTimelineEvent } from "../types/domain.ts";

const ev = (over: Partial<StoryTimelineEvent>): StoryTimelineEvent => ({
  date: "2026-07-25T10:00:00Z",
  type: "publisher_join",
  label: "x joined",
  ...over,
});

test("collapses a consecutive join run into one row carrying every publisher", () => {
  const rows = condenseTimeline([
    ev({ type: "first_report", publisher: "A", date: "2026-07-25T09:00:00Z", label: "first" }),
    ev({ publisher: "B", date: "2026-07-25T10:00:00Z" }),
    ev({ publisher: "C", date: "2026-07-25T10:05:00Z" }),
    ev({ publisher: "D", date: "2026-07-25T10:10:00Z" }),
    ev({ type: "milestone", date: "2026-07-25T11:00:00Z", label: "5 articles", count: 5 }),
  ]);
  assert.deepEqual(
    rows.map((r) => r.kind),
    ["day", "event", "joins", "event"],
  );
  const joins = rows[2];
  assert.ok(joins.kind === "joins");
  assert.deepEqual(joins.publishers, ["B", "C", "D"]);
  assert.equal(joins.date, "2026-07-25T10:10:00Z"); // the run's completion time
});

test("a single join stays an individual event; milestones break runs", () => {
  const rows = condenseTimeline([
    ev({ publisher: "B", date: "2026-07-25T10:00:00Z" }),
    ev({ type: "milestone", date: "2026-07-25T10:30:00Z", label: "2 articles" }),
    ev({ publisher: "C", date: "2026-07-25T11:00:00Z" }),
  ]);
  assert.deepEqual(
    rows.map((r) => (r.kind === "event" ? `event:${r.event.type}` : r.kind)),
    ["day", "event:publisher_join", "event:milestone", "event:publisher_join"],
  );
});

test("emits a day marker per calendar day and splits runs across days", () => {
  const rows = condenseTimeline([
    ev({ publisher: "A", date: "2026-07-24T23:50:00Z" }),
    ev({ publisher: "B", date: "2026-07-24T23:55:00Z" }),
    ev({ publisher: "C", date: "2026-07-25T00:05:00Z" }),
    ev({ publisher: "D", date: "2026-07-25T00:10:00Z" }),
  ]);
  assert.deepEqual(
    rows.map((r) => r.kind),
    ["day", "joins", "day", "joins"],
  );
  const first = rows[1];
  const second = rows[3];
  assert.ok(first.kind === "joins" && second.kind === "joins");
  assert.deepEqual(first.publishers, ["A", "B"]);
  assert.deepEqual(second.publishers, ["C", "D"]);
});

test("a join without a publisher name never joins a group, and order is preserved", () => {
  const rows = condenseTimeline([
    ev({ publisher: "A", date: "2026-07-25T10:00:00Z" }),
    ev({ publisher: undefined, date: "2026-07-25T10:05:00Z", label: "someone joined" }),
    ev({ publisher: "B", date: "2026-07-25T10:10:00Z" }),
  ]);
  assert.deepEqual(
    rows.map((r) => r.kind),
    ["day", "event", "event", "event"],
  );
});

test("empty input yields no rows", () => {
  assert.deepEqual(condenseTimeline([]), []);
});
