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
import type { CoachMessage, MetricKey } from "@/types/domain";

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
