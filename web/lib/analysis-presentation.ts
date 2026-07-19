/**
 * Article Analyzer presentation (A2-Web) — the pure, only mapping from an ANALYSIS CONTRACT v1
 * result to what the anonymous analyzer page RENDERS. Like `rec-presentation` / `notifications`,
 * it holds catalog keys as **string literals** so the check:i18n unused-key scanner reads them
 * straight from this file, and it carries no React and no runtime imports (runs under `node --test`).
 *
 * Honesty is structural here:
 *   - provenance ("Catalog" vs "Scored from URL only") is always derived from `source`;
 *   - an unknown outlet (`leanBucket: null`) maps to the explicit "unknown" lean — never a guess;
 *   - `register` and `confidence` are deliberately NOT surfaced (deferred; see A2-Web plan);
 *   - the reader sections (`recommendation` / `explanation` A3, `personal` A4) map ONLY when the
 *     engine filled them — anonymous / non-measured input maps to null, nothing renders;
 *   - every KNOWN backend note maps to a localized message, and any UNRECOGNIZED note is preserved
 *     as a "technical" note (rendered under a "Technical note" label) rather than silently dropped.
 */
import type {
  AnalysisNextArticle,
  AnalysisPersonal,
  AnalysisRecommendation,
  AnalysisResult,
  AnalysisScoring,
  AnalysisSource,
  AnalysisStory,
  Article,
  EmotionShare,
  LeanBucket,
  RecommendationExplanation,
  ViewpointDistribution,
} from "../types/domain";

export interface AnalysisProvenance {
  source: AnalysisSource;
  labelKey: string;
  hintKey: string;
  /** Badge tone: a catalog hit is corroborated (positive); a URL-only score is provisional (caution). */
  variant: "positive" | "caution";
}

/** Lean is shown from the numeric value when the outlet is known; otherwise an explicit "unknown". */
export type LeanPresentation = { known: true; lean: number; bucket: LeanBucket } | { known: false };

export interface ScoringPresentation {
  outlet: string;
  /** Prettified topic, or null when the engine couldn't classify it (the row is hidden). */
  topic: string | null;
  lean: LeanPresentation;
  political: boolean;
  /** Dominant emotion key (→ `emotion.<key>`), or null when no emotion was scored. */
  emotionKey: keyof EmotionShare | null;
}

export type StoryPresentation =
  | { kind: "member"; distribution: ViewpointDistribution; missingViewpoints: LeanBucket[] }
  | { kind: "similar" }
  | { kind: "none" };

/** A known note resolves to a catalog key; an unrecognized one is preserved verbatim as technical. */
export type NotePresentation = { kind: "known"; key: string } | { kind: "technical"; text: string };

/** One localized reader-facing statement built from a verdict reason code (never the raw code). */
export interface VerdictReason {
  key: string;
  params?: Record<string, unknown>;
}

/** A3.3 — the renderable "read next" pick: a validated Article, a localized source label (closed
 *  set; an unknown source degrades to the generic label, never a raw value), and the pick's raw
 *  resolver explanation for the component's reused renderer. */
export interface NextArticlePresentation {
  labelKey: string;
  article: Article;
  explanation: RecommendationExplanation | null;
}

/** The A3 reader verdict, mapped to catalog keys — the ONLY place the closed reason vocabulary is
 *  translated to copy. Unknown codes are dropped, so an implementation signal never reaches the UI. */
export interface RecommendationPresentation {
  wouldBroaden: boolean;
  headlineKey: string;
  reasons: VerdictReason[];
  /** The "read next" pick, or null (absent / malformed / no honest pick — nothing renders). */
  next: NextArticlePresentation | null;
}

/**
 * A4.2 — the renderable reader standing. Values pass through verbatim; the only presentation
 * choices are DISPLAY ones, documented here so they read as deliberate:
 *   - `publisher` renders only the "familiar" band — the never/rarely bands are already rendered
 *     as verdict reasons (same server-side band, computed once), so re-stating them would
 *     duplicate the card;
 *   - `topic.blindSpot` is not re-rendered — the verdict's blind-spot reason (with the topic
 *     param) already carries it;
 *   - the share becomes a whole display percent by the SAME rule the resolver's readerFacts use
 *     (round; a <1% share is no claim).
 */
export interface PersonalPresentation {
  alreadyRead: { at: string | null } | null;
  familiarPublisher: { name: string } | null;
  topicShare: { topic: string; percent: number } | null;
  viewpoint: { shares: ViewpointDistribution; addsMissing: boolean } | null;
  story: { readCount: number; bucketsRead: LeanBucket[]; addsBucket: LeanBucket | null } | null;
}

export interface AnalysisPresentation {
  status: "analyzed" | "invalid_url";
  provenance: AnalysisProvenance | null;
  scoring: ScoringPresentation | null;
  story: StoryPresentation | null;
  notes: NotePresentation[];
  // A3/A4 (present only for a signed-in measured reader; null for anonymous / non-measured):
  recommendation: RecommendationPresentation | null;
  /** The raw resolver explanation, passed through for the component's reused renderer
   *  (`presentRecommendation` + `localizeExplanation`) — never re-implemented here. */
  explanation: RecommendationExplanation | null;
  /** The reader standing, or null (anonymous / non-measured / nothing renderable). */
  personal: PersonalPresentation | null;
}

/** Verdict reason code → catalog key. Literal keys on purpose (check:i18n scans them); a code
 *  outside this closed set is skipped, so the UI never shows a raw signal like "cross_cutting". */
const REASON_KEYS: Record<string, string> = {
  cross_cutting: "analyze.rec.reason.crossCutting",
  new_publisher: "analyze.rec.reason.newPublisher",
  rarely_read_publisher: "analyze.rec.reason.rarelyRead",
  blind_spot: "analyze.rec.reason.blindSpot",
  following_story: "analyze.rec.reason.following",
  familiar_topic: "analyze.rec.reason.familiarTopic",
};

/** Next-pick source → localized label key. Literal keys on purpose (check:i18n scans them);
 *  anything outside the closed set gets the generic label — a raw source value never renders. */
const NEXT_SOURCE_KEYS: Record<string, string> = {
  story: "analyze.next.story",
  feed: "analyze.next.feed",
};

function mapNextArticle(na: AnalysisNextArticle | null | undefined): NextArticlePresentation | null {
  if (!na || typeof na !== "object") return null;
  const art = na.article;
  // A renderable pick needs at least a real headline and an absolute-ish URL for the Read button;
  // anything short of that is malformed and renders nothing (never a placeholder).
  if (!art || typeof art !== "object") return null;
  if (typeof art.headline !== "string" || !art.headline.trim()) return null;
  if (typeof art.url !== "string" || !art.url.trim()) return null;
  return {
    labelKey: NEXT_SOURCE_KEYS[String(na.source)] ?? "analyze.next.generic",
    article: art,
    explanation: mapExplanation(na.explanation),
  };
}

function mapRecommendation(rec: AnalysisRecommendation | null): RecommendationPresentation | null {
  if (!rec || typeof rec !== "object") return null;
  const codes = Array.isArray(rec.reasons) ? rec.reasons : [];
  const reasons: VerdictReason[] = [];
  for (const code of codes) {
    const key = REASON_KEYS[code];
    if (!key) continue; // unknown / future code → never rendered as a raw signal
    reasons.push(
      code === "blind_spot" && rec.blindSpotTopic
        ? { key, params: { topic: rec.blindSpotTopic } }
        : { key },
    );
  }
  const wouldBroaden = Boolean(rec.wouldBroaden);
  return {
    wouldBroaden,
    headlineKey: wouldBroaden ? "analyze.rec.broadens" : "analyze.rec.familiar",
    reasons,
    next: mapNextArticle(rec.nextArticle),
  };
}

function mapExplanation(exp: unknown): RecommendationExplanation | null {
  return exp && typeof exp === "object" && typeof (exp as { type?: unknown }).type === "string"
    ? (exp as RecommendationExplanation)
    : null;
}

/** The closed lean-bucket vocabulary — an unexpected value never renders as a bucket. */
const BUCKETS: readonly LeanBucket[] = ["left", "center", "right"];

function isBucket(v: unknown): v is LeanBucket {
  return typeof v === "string" && (BUCKETS as readonly string[]).includes(v);
}

/** A4.2 — total mapping of the reader standing. Values pass through verbatim (the backend is the
 *  source of truth); anything unexpected degrades to a null block, and a `personal` with nothing
 *  renderable maps to null so the component renders nothing at all. */
function mapPersonal(p: AnalysisPersonal | null | undefined): PersonalPresentation | null {
  if (!p || typeof p !== "object") return null;

  const ar = p.alreadyRead;
  const alreadyRead =
    ar && typeof ar === "object"
      ? { at: typeof ar.at === "string" && ar.at.trim() ? ar.at : null }
      : null;

  // Only the "familiar" band is new information here — never/rarely already render as verdict
  // reasons (the same server-side band, computed once), so re-stating them would duplicate.
  const pub = p.publisher;
  const familiarPublisher =
    pub && typeof pub === "object" && pub.band === "familiar" &&
    typeof pub.name === "string" && pub.name.trim()
      ? { name: pub.name }
      : null;

  // Display formatting only: the resolver's whole-percent rule (round; <1% is no claim).
  let topicShare: PersonalPresentation["topicShare"] = null;
  const tp = p.topic;
  if (tp && typeof tp === "object" && typeof tp.topic === "string" && tp.topic.trim() &&
      typeof tp.share === "number" && tp.share >= 0 && tp.share <= 1) {
    const percent = Math.round(tp.share * 100);
    if (percent >= 1) topicShare = { topic: tp.topic, percent };
  }

  const vp = p.viewpoint;
  const viewpoint =
    vp && typeof vp === "object" && isDistribution(vp.readerShares)
      ? { shares: vp.readerShares, addsMissing: vp.addsMissing === true }
      : null;

  let story: PersonalPresentation["story"] = null;
  const st = p.story;
  if (st && typeof st === "object" && typeof st.readCount === "number" && st.readCount >= 1) {
    story = {
      readCount: st.readCount,
      bucketsRead: Array.isArray(st.bucketsRead) ? st.bucketsRead.filter(isBucket) : [],
      addsBucket: isBucket(st.addsBucket) ? st.addsBucket : null,
    };
  }

  return alreadyRead || familiarPublisher || topicShare || viewpoint || story
    ? { alreadyRead, familiarPublisher, topicShare, viewpoint, story }
    : null;
}

/** The valid emotion keys — a dominant outside this set degrades to "no emotion chip". */
const EMOTION_KEYS: readonly (keyof EmotionShare)[] = [
  "fear",
  "outrage",
  "analysis",
  "positive",
  "neutral",
];

/** Backend note → localized key. Matched on stable substrings (the note prose may evolve). Literal
 *  keys on purpose (check:i18n scans them). Anything unmatched is preserved as a technical note. */
const KNOWN_NOTES: readonly { match: RegExp; key: string }[] = [
  { match: /no page metadata/i, key: "analyze.note.noMetadata" },
  { match: /not in the registry/i, key: "analyze.note.unknownOutlet" },
  { match: /not a fetchable url/i, key: "analyze.note.invalidUrl" },
];

function mapNote(note: string): NotePresentation {
  for (const { match, key } of KNOWN_NOTES) {
    if (match.test(note)) return { kind: "known", key };
  }
  return { kind: "technical", text: note };
}

function mapProvenance(source: AnalysisSource | null): AnalysisProvenance | null {
  if (source === "catalog") {
    return {
      source,
      labelKey: "analyze.provenance.catalog",
      hintKey: "analyze.provenance.catalogHint",
      variant: "positive",
    };
  }
  if (source === "scored_url_only") {
    return {
      source,
      labelKey: "analyze.provenance.scoredUrlOnly",
      hintKey: "analyze.provenance.scoredUrlOnlyHint",
      variant: "caution",
    };
  }
  return null;
}

function dominantEmotion(emotion: Partial<EmotionShare> | null): keyof EmotionShare | null {
  if (!emotion) return null;
  let best: keyof EmotionShare | null = null;
  let bestVal = -Infinity;
  for (const key of EMOTION_KEYS) {
    const v = emotion[key];
    if (typeof v === "number" && v > bestVal) {
      best = key;
      bestVal = v;
    }
  }
  return best;
}

function mapScoring(s: AnalysisScoring | null): ScoringPresentation | null {
  if (!s) return null;
  const lean: LeanPresentation =
    s.leanBucket != null && typeof s.lean === "number"
      ? { known: true, lean: s.lean, bucket: s.leanBucket }
      : { known: false };
  return {
    outlet: s.outlet || "",
    topic: s.topic ? s.topic : null,
    lean,
    political: Boolean(s.political),
    emotionKey: dominantEmotion(s.emotion),
    // register / confidence intentionally omitted — deferred from the A2 UI.
  };
}

/** A contract-v1 membership always carries a numeric left/center/right distribution. Verify it at
 *  runtime so a malformed or future variant can't reach ``SpectrumBar`` and crash on ``.left`` (F1). */
function isDistribution(d: unknown): d is ViewpointDistribution {
  if (typeof d !== "object" || d === null) return false;
  const r = d as Record<string, unknown>;
  return typeof r.left === "number" && typeof r.center === "number" && typeof r.right === "number";
}

function mapStory(story: AnalysisStory | null): StoryPresentation | null {
  if (!story || typeof story !== "object") return null;
  // Strictly ``matched === true`` AND a well-formed distribution ⇒ membership; anything short of
  // that degrades to the advisory/none presentation rather than rendering a broken bar (F1). Valid
  // contract-v1 responses are unaffected.
  if (story.matched === true && isDistribution(story.distribution)) {
    return {
      kind: "member",
      distribution: story.distribution,
      missingViewpoints: Array.isArray(story.missingViewpoints) ? story.missingViewpoints : [],
    };
  }
  return "similarStory" in story && story.similarStory ? { kind: "similar" } : { kind: "none" };
}

/**
 * The single mapping the analyzer page renders from. Pure and total: any result (including an
 * `invalid_url` or an unexpectedly-shaped one) yields a safe presentation, and identical input
 * yields deep-equal output (the endpoint is deterministic; nothing here adds time or randomness).
 */
export function analysisPresentation(result: AnalysisResult): AnalysisPresentation {
  const notes = Array.isArray(result?.notes) ? result.notes.map(mapNote) : [];
  if (result?.status !== "analyzed") {
    return { status: "invalid_url", provenance: null, scoring: null, story: null, notes,
             recommendation: null, explanation: null, personal: null };
  }
  return {
    status: "analyzed",
    provenance: mapProvenance(result.source),
    scoring: mapScoring(result.scoring),
    story: mapStory(result.story),
    notes,
    // A3/A4 — non-null only when the engine enriched (a signed-in measured reader).
    recommendation: mapRecommendation(result.recommendation),
    explanation: mapExplanation(result.explanation),
    personal: mapPersonal(result.personal),
  };
}
