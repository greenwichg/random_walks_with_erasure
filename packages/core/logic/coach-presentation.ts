/**
 * Coach v2 presentation selection (M5) — the pure, React-free half of the coach surface,
 * mirroring rec-presentation.ts. The server reply is the source of truth; this module only
 * SELECTS from it (which chips to show, which echo to round-trip, how to label a citation).
 * Nothing here invents content, so it is trivially unit-testable (node --test).
 *
 * Every v2 field is OPTIONAL on the wire (RWE_COACH_V2 off ⇒ absent): each helper returns its
 * v1-neutral value (null / undefined) for a v1 payload, which is what keeps the page's flag-off
 * rendering byte-identical to today.
 */
import type { CoachMessage, MetricKey } from "../domain/types.ts";

/** The eight report metrics — a Record so tsc enforces exhaustiveness if MetricKey grows. */
const METRIC_KEY_FLAGS: Record<MetricKey, true> = {
  topicDiversity: true,
  sourceDiversity: true,
  reportingRatio: true,
  emotionalBalance: true,
  echoChamber: true,
  viewpointBalance: true,
  openMindedness: true,
  confidence: true,
};
const METRIC_KEYS: ReadonlySet<string> = new Set(Object.keys(METRIC_KEY_FLAGS));

/**
 * Catalog key for a citation badge, or null when the citation is not one of the eight report
 * metrics. v1 only ever cites metric keys; Coach v2 cites any engine evidence key ("served",
 * "sourceShare.NPR", …) — those render as the raw key (greppable, honest), never a broken
 * `metric.….label` lookup.
 */
export function citationLabelKey(metric: string): string | null {
  return METRIC_KEYS.has(metric) ? `metric.${metric}.label` : null;
}

/**
 * The structured echo to round-trip on the NEXT send: the most recent assistant echo in the
 * transcript. Binding-only by design (D6) — the client never reads inside it, so it stays an
 * opaque object here. Undefined until a v2 reply arrives (v1 replies carry no echo).
 */
export function lastEcho(messages: CoachMessage[]): Record<string, unknown> | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.role === "assistant" && m.echo && typeof m.echo === "object") return m.echo;
  }
  return undefined;
}

/**
 * Follow-up chips to offer: the LAST message must be an assistant reply carrying followUps
 * (mid-thought — after the user sends — there is nothing to accept). Null for v1 replies,
 * which is what lets the static starter suggestions keep their exact v1 behaviour.
 */
export function activeFollowUps(messages: CoachMessage[]): string[] | null {
  const m = messages[messages.length - 1];
  if (m?.role === "assistant" && Array.isArray(m.followUps) && m.followUps.length > 0) {
    return m.followUps;
  }
  return null;
}

// --------------------------------------------------------------------------- //
// Weekly Review presentation (pure) — label selection + derived insights for the
// dashboard card. Derivation is arithmetic over the server's own numbers, never
// new claims: a delta, a share, a "held steady" — nothing the payload can't prove.
// --------------------------------------------------------------------------- //

/** The trend series the engine emits (analytics-page vocabulary — labels already exist in every
 *  catalog). Unknown keys render raw, greppable, never a broken catalog lookup. */
const TREND_LABEL_KEYS: Record<string, string> = {
  healthImprovement: "analytics.healthImprovement",
  politicalDiversity: "analytics.politicalDiversity",
  publisherDiversity: "analytics.publisherDiversity",
  topicDiversity: "analytics.topicDiversity",
};

export function trendLabelKey(metric: string): string | null {
  return TREND_LABEL_KEYS[metric] ?? null;
}

/** Delta of one weekly trend, or null when either end is unmeasured (honest: no delta claim). */
export function weeklyTrendDelta(t: { first: number | null; last: number | null }): number | null {
  return t.first == null || t.last == null ? null : t.last - t.first;
}

export type WeeklyInsight =
  | { kind: "slip"; metric: string; delta: number }
  | { kind: "gain"; metric: string; delta: number }
  | { kind: "steady" }
  | { kind: "concentration"; publisher: string; share: number };

/**
 * Up to two derived insights, most consequential first: the biggest mover (slip preferred over
 * gain — the product's job is surfacing gaps), "all steady" when every measured trend is flat,
 * and a top-publisher concentration flag at ≥ 40% of the week's reads. Empty when the payload
 * has nothing measurable — the card then simply omits its insights row.
 */
export function weeklyInsights(review: {
  reads: number | null;
  topPublishers: { name: string; reads: number }[];
  trends: { metric: string; first: number | null; last: number | null }[];
}): WeeklyInsight[] {
  const out: WeeklyInsight[] = [];
  const measured = review.trends
    .map((t) => ({ metric: t.metric, delta: weeklyTrendDelta(t) }))
    .filter((t): t is { metric: string; delta: number } => t.delta != null);
  const [mover] = [...measured].sort(
    (a, b) => Math.abs(b.delta) - Math.abs(a.delta) || (a.delta > b.delta ? 1 : -1),
  );
  if (mover) {
    if (mover.delta < 0) out.push({ kind: "slip", metric: mover.metric, delta: mover.delta });
    else if (mover.delta > 0) out.push({ kind: "gain", metric: mover.metric, delta: mover.delta });
    else out.push({ kind: "steady" });
  }
  const top = review.topPublishers[0];
  if (top && review.reads != null && review.reads > 0) {
    const share = Math.round((top.reads / review.reads) * 100);
    if (share >= 40) out.push({ kind: "concentration", publisher: top.name, share });
  }
  return out.slice(0, 2);
}
