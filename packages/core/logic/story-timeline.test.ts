import { test } from "node:test";
import assert from "node:assert/strict";
import { condenseTimeline } from "./story-timeline.ts";
import type { StoryTimelineEvent } from "../domain/types.ts";

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

/**
 * Day dividers are grouped by the LOCAL calendar day, because that is the clock they are rendered
 * in. Before this, grouping keyed off the UTC ISO prefix while `formatDate` printed local time, so
 * one local day west of UTC produced two dividers that both read "Aug 29" — the panel claimed the
 * coverage spanned two days when it spanned one afternoon.
 *
 * Run under a fixed zone rather than the machine's: the whole point is behaviour away from UTC.
 */
test("one local day yields one divider, even across UTC midnight", () => {
  const prev = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    const rows = condenseTimeline([
      // 11:00 and 19:00 on Aug 29 in Los Angeles — one local day, two UTC days.
      ev({ type: "first_report", date: "2026-08-29T18:00:00Z", label: "first" }),
      ev({ type: "milestone", date: "2026-08-30T02:00:00Z", label: "5 articles" }),
    ]);
    assert.deepEqual(rows.map((r) => r.kind), ["day", "event", "event"]);
  } finally {
    process.env.TZ = prev;
  }
});

test("a genuine local-day change still divides", () => {
  const prev = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    const rows = condenseTimeline([
      ev({ type: "first_report", date: "2026-08-29T18:00:00Z", label: "first" }),   // Aug 29 local
      ev({ type: "milestone", date: "2026-08-30T18:00:00Z", label: "5 articles" }), // Aug 30 local
    ]);
    assert.deepEqual(rows.map((r) => r.kind), ["day", "event", "day", "event"]);
  } finally {
    process.env.TZ = prev;
  }
});
