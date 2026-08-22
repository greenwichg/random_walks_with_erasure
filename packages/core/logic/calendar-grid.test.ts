import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildCalendarGrid, firstDayOfWeek, WINDOW_DAYS } from "./calendar-grid.ts";

/** A local-midnight date, matching how the grid normalizes. */
const D = (y: number, m: number, d: number) => new Date(y, m - 1, d);
/** Reads on a given local day, as the entries shape the component passes through. */
const reads = (y: number, m: number, d: number, n: number) =>
  Array.from({ length: n }, () => ({ readAt: new Date(y, m - 1, d, 12, 0, 0).toISOString() }));

describe("buildCalendarGrid", () => {
  it("emits whole weeks that start on the locale's first weekday", () => {
    // 2026-08-19 is a Wednesday; the 35-day window opens Thursday 2026-07-16.
    const g = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: 0 });
    assert.ok(g.weeks.length === 5 || g.weeks.length === 6);
    for (const w of g.weeks) assert.equal(w.length, 7);
    for (const w of g.weeks) assert.equal(w[0].date.getDay(), 0);
    // every rendered day is consecutive across the row boundaries
    const flat = g.weeks.flat();
    for (let i = 1; i < flat.length; i++) {
      const gap = flat[i].date.getTime() - flat[i - 1].date.getTime();
      assert.ok(gap >= 23 * 3600e3 && gap <= 25 * 3600e3, "consecutive days (DST-tolerant)");
    }
  });

  it("honours a Monday-first locale without changing the window", () => {
    const sun = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: 0 });
    const mon = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: 1 });
    for (const w of mon.weeks) assert.equal(w[0].date.getDay(), 1);
    // the reading window is a property of the data, not of the locale
    assert.equal(sun.windowStart.getTime(), mon.windowStart.getTime());
    assert.equal(sun.windowEnd.getTime(), mon.windowEnd.getTime());
    assert.equal(sun.weeks.flat().filter((c) => c.inWindow).length, WINDOW_DAYS);
    assert.equal(mon.weeks.flat().filter((c) => c.inWindow).length, WINDOW_DAYS);
  });

  it("marks exactly the 35-day window and pads the rest as out-of-window", () => {
    const g = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: 0 });
    const flat = g.weeks.flat();
    const inW = flat.filter((c) => c.inWindow);
    assert.equal(inW.length, WINDOW_DAYS);
    assert.equal(inW[0].key, "2026-07-16");
    assert.equal(inW[inW.length - 1].key, "2026-08-19");
    // padding days carry no reading data at all — they exist to complete the rectangle
    for (const c of flat.filter((c) => !c.inWindow)) {
      assert.equal(c.count, 0);
      assert.equal(c.level, 0);
    }
  });

  it("never marks a future day as today or in-window", () => {
    const g = buildCalendarGrid(reads(2026, 8, 20, 9), { today: D(2026, 8, 19), weekStartsOn: 0 });
    const flat = g.weeks.flat();
    assert.equal(flat.filter((c) => c.isToday).length, 1);
    assert.equal(flat.find((c) => c.isToday)?.key, "2026-08-19");
    const tomorrow = flat.find((c) => c.key === "2026-08-20");
    if (tomorrow) {
      assert.equal(tomorrow.inWindow, false);
      assert.equal(tomorrow.count, 0, "reads dated after today are never drawn into the grid");
    }
  });

  it("preserves per-day counts and scales intensity against the busiest day", () => {
    const g = buildCalendarGrid(
      [...reads(2026, 8, 19, 8), ...reads(2026, 8, 18, 4), ...reads(2026, 8, 17, 1)],
      { today: D(2026, 8, 19), weekStartsOn: 0 },
    );
    const by = new Map(g.weeks.flat().map((c) => [c.key, c]));
    assert.equal(g.max, 8);
    assert.equal(by.get("2026-08-19")?.count, 8);
    assert.equal(by.get("2026-08-19")?.level, 4);
    assert.equal(by.get("2026-08-18")?.count, 4);
    assert.equal(by.get("2026-08-18")?.level, 2);
    assert.equal(by.get("2026-08-17")?.level, 1, "a single read is still visibly non-empty");
    assert.equal(by.get("2026-08-16")?.count, 0);
    assert.equal(by.get("2026-08-16")?.level, 0);
  });

  it("counts every read in the window — the total is conserved", () => {
    const entries = [...reads(2026, 8, 19, 3), ...reads(2026, 8, 1, 2), ...reads(2026, 7, 16, 5)];
    const g = buildCalendarGrid(entries, { today: D(2026, 8, 19), weekStartsOn: 0 });
    const total = g.weeks.flat().reduce((s, c) => s + c.count, 0);
    assert.equal(total, entries.length);
  });

  it("flags the first of each month for the month label", () => {
    const g = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: 0 });
    const firsts = g.weeks.flat().filter((c) => c.isFirstOfMonth).map((c) => c.key);
    assert.deepEqual(firsts, ["2026-08-01"]);
  });

  it("gives seven weekday reference dates in display order", () => {
    for (const start of [0, 1]) {
      const g = buildCalendarGrid([], { today: D(2026, 8, 19), weekStartsOn: start });
      assert.equal(g.weekdays.length, 7);
      assert.deepEqual(g.weekdays.map((d) => d.getDay()),
        Array.from({ length: 7 }, (_, i) => (start + i) % 7));
    }
  });

  it("stays a whole-week rectangle whatever weekday the window opens on", () => {
    // sweep a full week of "today"s: the grid is always 5 or 6 complete weeks
    for (let i = 0; i < 7; i++) {
      for (const start of [0, 1]) {
        const g = buildCalendarGrid([], { today: D(2026, 8, 16 + i), weekStartsOn: start });
        assert.equal(g.weeks.flat().length % 7, 0);
        assert.equal(g.weeks.flat().filter((c) => c.inWindow).length, WINDOW_DAYS);
      }
    }
  });

  it("survives a spring-forward DST boundary without dropping or doubling a day", () => {
    // US DST 2026 begins Sunday 8 March; a window ending 2026-03-20 spans it.
    const g = buildCalendarGrid([], { today: D(2026, 3, 20), weekStartsOn: 0 });
    const keys = g.weeks.flat().filter((c) => c.inWindow).map((c) => c.key);
    assert.equal(keys.length, WINDOW_DAYS);
    assert.equal(new Set(keys).size, WINDOW_DAYS, "no duplicated day");
    assert.ok(keys.includes("2026-03-08"));
  });
});

describe("firstDayOfWeek", () => {
  it("uses Sunday for en and Monday for the European catalogs", () => {
    assert.equal(firstDayOfWeek("en"), 0);
    for (const l of ["es", "fr", "de", "pt"]) assert.equal(firstDayOfWeek(l), 1);
  });
});
