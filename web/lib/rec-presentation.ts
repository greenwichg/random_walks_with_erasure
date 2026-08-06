/**
 * Recommendation presentation (Commit 22, restructured in Commit 23) — the pure mapping from
 * the Evidence Resolver's structured explanation to what the card RENDERS.
 *
 * Commit 23: the resolver now emits SEMANTIC parts — `readerFact` / `contribution` as
 * `{key, params}` objects — and this module only translates those semantic keys to catalog
 * template keys (`rec.reader.*` / `rec.contribution.*`). It performs **no selection, no
 * composition, and no numeric thresholding**: the resolver decided everything (including the
 * bridge's `readerPoliticalProfile` banding); presentation is layout + localization.
 *
 * The card's three layers:
 *   reader fact    the bold line — what the system noticed about YOUR reading (C6: the concrete
 *                  measured share when the resolver's context carried it — never estimated)
 *   contribution   the supporting line — what this recommendation adds
 *   proof          the Why? drawer (C6 removed the card's generic "Helps {metric}" chip: it was
 *                  a strategy-derived label, not evidence; estimated effects live in the drawer,
 *                  clearly labeled as estimates)
 *
 * Types without parts (coverage_breadth, a balanced-profile bridge, unknown/missing
 * explanations) return nulls and the card falls back to the resolver's validated sentence via
 * localizeExplanation — nothing ever over-claims, and an unknown semantic key degrades the same
 * way (never renders a raw key). Template keys are string literals so the check:i18n unused-key
 * scanner reads them straight from this file. No React, no runtime imports — runs under
 * `node --test` like i18n-core.
 */
import type { ExplanationPart, RecommendationExplanation } from "../types/domain";

/** A localizable reference: the catalog template key + interpolation params. */
export interface PartRef {
  key: string;
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
  /** Localized readerFact reference (bold slot); null ⇒ no truthful reader fact. */
  reader: PartRef | null;
  /** Localized contribution reference (supporting slot; bold when reader is null). */
  contribution: PartRef | null;
  /** Story-card caption key (story cards keep their Commit 22 layout unchanged). */
  claimKey: string | null;
  /** Catalog key for the CTA label; null ⇒ the Read button's default label. */
  ctaKey: string | null;
  /** Deep link to the proving Story page, when the explanation is story-backed. */
  storyHref: string | null;
  /** The cluster this card is about, when it is a story card. Exposed alongside `storyHref` so a
   *  caller can COMPARE it (the feed suppresses its story card when the continuation strip is
   *  already offering the same story) without parsing an id back out of a URL. */
  storyId: string | null;
  /** The two-publisher comparison block payload (story_match only). */
  comparison: StoryComparison | null;
}

const NONE: RecPresentation = {
  reader: null, contribution: null, claimKey: null, ctaKey: null, storyHref: null, storyId: null,
  comparison: null,
};

/** Semantic part key → catalog template key. Literal strings on purpose (check:i18n scans them). */
const READER_TEMPLATES: Record<string, string> = {
  read_story_from: "rec.reader.read_story_from",
  following_story: "rec.reader.following_story",
  never_read_publisher: "rec.reader.never_read_publisher",
  rarely_read_publisher: "rec.reader.rarely_read_publisher",
  top_topic: "rec.reader.top_topic",
  political_lean_left: "rec.reader.political_lean_left",
  political_lean_right: "rec.reader.political_lean_right",
  // C6 · concrete measured variants — emitted only when the resolver's context carried the
  // reader's real shares ("Politics represents 42% of your recent reading").
  top_topic_share: "rec.reader.top_topic_share",
  political_lean_left_share: "rec.reader.political_lean_left_share",
  political_lean_right_share: "rec.reader.political_lean_right_share",
};
const CONTRIBUTION_TEMPLATES: Record<string, string> = {
  covered_same_story: "rec.contribution.covered_same_story",
  story_update: "rec.contribution.story_update",
  story_coverage: "rec.contribution.story_coverage",
  add_new_publisher: "rec.contribution.add_new_publisher",
  more_topic_coverage: "rec.contribution.more_topic_coverage",
  other_side_perspective: "rec.contribution.other_side_perspective",
  rare_in_feeds: "rec.contribution.rare_in_feeds",
};

/** Map a semantic part to its catalog template; unknown keys → null (message fallback). */
function toRef(part: ExplanationPart | undefined | null, table: Record<string, string>): PartRef | null {
  if (!part || typeof part.key !== "string") return null;
  const template = table[part.key];
  return template ? { key: template, params: part.params } : null;
}

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
  const reader = toRef(exp.readerFact, READER_TEMPLATES);
  const contribution = toRef(exp.contribution, CONTRIBUTION_TEMPLATES);

  if (exp.type === "story_match") {
    const ev = (exp.evidence ?? {}) as Record<string, unknown>;
    const variant: StoryComparison["variant"] =
      exp.variant === "follow_up" || exp.variant === "following" ? exp.variant : "same_event";
    const storyId = typeof ev.storyId === "string" && ev.storyId ? ev.storyId : null;
    return {
      reader,
      contribution,
      // story cards keep their Commit 22 structural layout (caption + comparison rows)
      claimKey:
        variant === "follow_up" ? "rec.claim.story_match.follow_up"
        : variant === "following" ? "rec.claim.story_match.following"
        : "rec.claim.story_match.same_event",
      ctaKey: variant === "follow_up" ? "rec.cta.update" : "rec.cta.compare",
      storyHref: storyId ? `/stories/${encodeURIComponent(storyId)}` : null,
      storyId,
      comparison: {
        variant,
        readPublisher: String(ev.readPublisher ?? ""),
        recPublisher: String(ev.recPublisher ?? ""),
        readAt: typeof ev.readPublishedAt === "string" ? ev.readPublishedAt : null,
        recAt: typeof ev.recPublishedAt === "string" ? ev.recPublishedAt : null,
        storyReads: Number(ev.storyReads ?? 0) || 0,
        hoursAfterRead: hoursAfter(ev.recPublishedAt, ev.readPublishedAt),
      },
    };
  }

  // CTAs stay type-keyed (a layout concern, Commit 22): the resolver's parts carry the words,
  // the button carries the action.
  const ctaKey =
    exp.type === "bridge" ? "rec.cta.perspective"
    : exp.type === "new_publisher" ? "rec.cta.explore"
    : null;

  return { reader, contribution, claimKey: null, ctaKey, storyHref: null, storyId: null,
           comparison: null };
}
