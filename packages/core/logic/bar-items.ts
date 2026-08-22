/**
 * The one rule for anything a ranked bar list is asked to draw: **a row must say what it is.**
 *
 * `ingest.classify_topic` returns a taxonomy member or `""` — uncategorized, deliberately, because
 * filing an unclassified article under a guessed topic is worse than admitting nothing is known.
 * Its docstring says the UI hides that segment, and the rest of the web tier does exactly that
 * (`history-insights.tally` skips falsy names; `home.ts` twice refuses to file an unclassified
 * event). The report's distribution card did not, so a reader with 18 unclassified reads got a
 * nameless row claiming 10% of their reading — a bar with a number and no subject, which reads as
 * a rendering fault rather than as "we could not classify these".
 *
 * Blank-keyed rows are also a React hazard: `BarList` keys by label, so two of them collide.
 *
 * Pure and dependency-free, so the rule is testable without rendering anything.
 */

/** Anything a bar list can draw: it must carry a label. */
export interface Labelled {
  label: string;
}

/** Whether a label actually names something. Whitespace is blank — it renders as a blank row. */
export function isLabelled(label: unknown): boolean {
  return typeof label === "string" && label.trim().length > 0;
}

/**
 * Drop every row that cannot name itself, preserving order.
 *
 * Filtering — never substituting. An "Other"/"Uncategorized" stand-in would be this layer
 * inventing a category the engine deliberately declined to assign, and it would disagree with
 * History and Home, which drop these rows silently today.
 */
export function labelledItems<T extends Labelled>(items: readonly T[]): T[] {
  return items.filter((item) => isLabelled(item?.label));
}
