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
 *   - `recommendation` / `explanation` / `personal` are never modelled here (A3/A4);
 *   - every KNOWN backend note maps to a localized message, and any UNRECOGNIZED note is preserved
 *     as a "technical" note (rendered under a "Technical note" label) rather than silently dropped.
 */
import type {
  AnalysisResult,
  AnalysisScoring,
  AnalysisSource,
  AnalysisStory,
  EmotionShare,
  LeanBucket,
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

export interface AnalysisPresentation {
  status: "analyzed" | "invalid_url";
  provenance: AnalysisProvenance | null;
  scoring: ScoringPresentation | null;
  story: StoryPresentation | null;
  notes: NotePresentation[];
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

function mapStory(story: AnalysisStory | null): StoryPresentation | null {
  if (!story) return null;
  if (story.matched) {
    return {
      kind: "member",
      distribution: story.distribution,
      missingViewpoints: story.missingViewpoints ?? [],
    };
  }
  return story.similarStory ? { kind: "similar" } : { kind: "none" };
}

/**
 * The single mapping the analyzer page renders from. Pure and total: any result (including an
 * `invalid_url` or an unexpectedly-shaped one) yields a safe presentation, and identical input
 * yields deep-equal output (the endpoint is deterministic; nothing here adds time or randomness).
 */
export function analysisPresentation(result: AnalysisResult): AnalysisPresentation {
  const notes = Array.isArray(result?.notes) ? result.notes.map(mapNote) : [];
  if (result?.status !== "analyzed") {
    return { status: "invalid_url", provenance: null, scoring: null, story: null, notes };
  }
  return {
    status: "analyzed",
    provenance: mapProvenance(result.source),
    scoring: mapScoring(result.scoring),
    story: mapStory(result.story),
    notes,
  };
}
