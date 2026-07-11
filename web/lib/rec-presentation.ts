/**
 * Recommendation presentation (Commit 22) — the pure mapping from the Evidence Resolver's
 * structured explanation to what the card RENDERS: a claim key, receipt rows, a context-aware
 * CTA key, and the story comparison payload. Presentation only — it invents no evidence, it just
 * selects catalog keys and re-shapes fields the resolver already computed. No React, no imports
 * (type-only import is erased), so it runs under `node --test` like i18n-core.
 *
 * The card's three altitudes (Commit 22 IA):
 *   claim    one bold line — WHY spend the next minutes here (catalog key, licensed by `type`)
 *   receipt  the checkable facts under it (structured rows / the story comparison block)
 *   proof    the existing Why? drawer (untouched source of truth)
 *
 * Types without a safe claim (coverage_breadth, unknown, missing) return claimKey null — the
 * card falls back to the resolver's validated sentence via localizeExplanation, so nothing is
 * ever over-claimed. Keys are string literals on purpose: the check:i18n unused-key scanner
 * reads them straight from this file.
 */
import type { RecommendationExplanation } from "../types/domain";

export interface ReceiptRow {
  /** Catalog key for the row's text. */
  key: string;
  /** Interpolation params — values straight from the explanation's evidence. */
  params?: Record<string, unknown>;
}

export interface StoryComparison {
  variant: "same_event" | "follow_up" | "following";
  readPublisher: string;
  recPublisher: string;
  readAt: string | null;
  recAt: string | null;
  /** Reader's reads inside this story (>= 2 ⇒ the "following" variant). */
  storyReads: number;
  /** Whole hours the rec postdates the cited read; null when either timestamp is unusable. */
  hoursAfterRead: number | null;
}

export interface RecPresentation {
  /** Catalog key for the one-line claim; null ⇒ render the localized resolver sentence. */
  claimKey: string | null;
  /** Small structured receipt rows (joined with " · "); empty for claim-free types. */
  receipts: ReceiptRow[];
  /** Catalog key for the CTA label; null ⇒ the Read button's default label. */
  ctaKey: string | null;
  /** Deep link to the proving Story page, when the explanation is story-backed. */
  storyHref: string | null;
  /** The two-publisher comparison block payload (story_match only). */
  comparison: StoryComparison | null;
}

const NONE: RecPresentation = {
  claimKey: null, receipts: [], ctaKey: null, storyHref: null, comparison: null,
};

/** Display arithmetic on evidence the resolver already gated on — not new evidence. */
export function hoursAfter(later: unknown, earlier: unknown): number | null {
  if (typeof later !== "string" || typeof earlier !== "string" || !later || !earlier) return null;
  const a = Date.parse(later);
  const b = Date.parse(earlier);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((a - b) / 3_600_000);
}

/** The one entry point: resolver explanation → what the card renders. */
export function presentRecommendation(
  exp?: RecommendationExplanation | null,
): RecPresentation {
  if (!exp || !exp.type) return NONE;
  const ev = (exp.evidence ?? {}) as Record<string, unknown>;

  switch (exp.type) {
    case "story_match": {
      const variant: StoryComparison["variant"] =
        exp.variant === "follow_up" || exp.variant === "following" ? exp.variant : "same_event";
      const storyId = typeof ev.storyId === "string" && ev.storyId ? ev.storyId : null;
      const comparison: StoryComparison = {
        variant,
        readPublisher: String(ev.readPublisher ?? ""),
        recPublisher: String(ev.recPublisher ?? ""),
        readAt: typeof ev.readPublishedAt === "string" ? ev.readPublishedAt : null,
        recAt: typeof ev.recPublishedAt === "string" ? ev.recPublishedAt : null,
        storyReads: Number(ev.storyReads ?? 0) || 0,
        hoursAfterRead: hoursAfter(ev.recPublishedAt, ev.readPublishedAt),
      };
      return {
        claimKey:
          variant === "follow_up" ? "rec.claim.story_match.follow_up"
          : variant === "following" ? "rec.claim.story_match.following"
          : "rec.claim.story_match.same_event",
        receipts: [],
        ctaKey: variant === "follow_up" ? "rec.cta.update" : "rec.cta.compare",
        storyHref: storyId ? `/stories/${encodeURIComponent(storyId)}` : null,
        comparison,
      };
    }

    case "bridge":
      return {
        claimKey: "rec.claim.bridge",
        receipts: [{ key: "rec.receipt.crossCutting" }],
        ctaKey: "rec.cta.perspective",
        storyHref: null,
        comparison: null,
      };

    case "new_publisher": {
      const publisher = String(ev.publisher ?? "");
      const reads = Number(ev.reads ?? 0) || 0;
      return {
        claimKey: "rec.claim.new_publisher",
        receipts: [
          ev.band === "never"
            ? { key: "rec.receipt.publisherNever", params: { publisher } }
            : { key: "rec.receipt.publisherRarely", params: { publisher, n: reads } },
        ],
        ctaKey: "rec.cta.explore",
        storyHref: null,
        comparison: null,
      };
    }

    case "topic_continuity":
      return {
        claimKey: "rec.claim.topic_continuity",
        receipts: [{ key: "rec.receipt.topTopic", params: { topic: ev.topic } }],
        ctaKey: null,
        storyHref: null,
        comparison: null,
      };

    case "long_tail":
      return {
        claimKey: "rec.claim.long_tail",
        receipts: [{ key: "rec.receipt.longTail" }],
        ctaKey: null,
        storyHref: null,
        comparison: null,
      };

    // coverage_breadth is claim-free by design — the resolver's own sentence is the claim.
    case "coverage_breadth":
    default:
      return NONE;
  }
}
