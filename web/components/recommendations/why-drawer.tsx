"use client";

import * as React from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import type { EmotionShare, Recommendation, RecommendationEvidence, RecommendationExplain } from "@/types/domain";
import { useRecommendationExplain } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * The "Why?" drawer (21a.2): the card stays simple; the proof lives one click away. Renders the
 * per-recommendation evidence from the engine's explain endpoint in scannable sections —
 * Recommendation · Bridge · Article metadata · History · Estimated effect · Technical. Every
 * line is a real value produced by the recommender (or the report pipeline); when a value can't
 * be shown the drawer says why instead of inventing one.
 */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border/60 px-3 py-2.5 first:border-t-0">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      <dl className="space-y-1">{children}</dl>
    </div>
  );
}

function Row({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={mono ? "text-right font-mono text-foreground/90" : "text-right text-foreground/90"}>
        {value}
      </dd>
    </div>
  );
}

/** Coarse reading aid next to the raw number — returns a catalog key (the number is authoritative). */
function sideOf(lean: number) {
  if (lean < -0.05) return "filter.left";
  if (lean > 0.05) return "filter.right";
  return "filter.center";
}

/** Compact L/C/R percentage readout. The single-letter axis abbreviations are intentionally not
 *  localized (they are symbolic column markers, like chart-axis ticks). */
function lcr(v: { left: number; center: number; right: number }) {
  return `L ${v.left}% · C ${v.center}% · R ${v.right}%`;
}

/** Technical vocabulary (ranks, IDs, hyperparameters, scores, model versions, connectivity,
 *  classifier confidence) is developer-facing (21a.3 removal list): rendered only in dev
 *  builds; the internal explain endpoint remains the full source of truth everywhere. */
const DEV_DETAIL = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

function EvidenceSections({
  rec,
  ev,
  explain,
}: {
  rec: Recommendation;
  ev: RecommendationEvidence;
  explain: RecommendationExplain;
}) {
  const { t, localizeExplanation, formatDate } = useTranslation();
  const chosen = explain.trace.strategies[ev.chosenBy];
  const params = (chosen?.paramsUsed ?? {}) as Record<string, unknown>;
  const fam = ev.outletFamiliarity;
  const reads = explain.trace.reader.reads;
  const explanation = ev.explanation ?? rec.explanation;
  // Story relationship rows (Commit 22): rendered only when the resolver proved membership —
  // the values below are the same evidence fields validate() re-derives, never re-inferred here.
  const sev = (explanation?.type === "story_match" ? explanation.evidence : null) as
    | Record<string, unknown>
    | null;
  const withDate = (publisher: unknown, iso: unknown) => {
    const p = String(publisher ?? "—");
    const d = typeof iso === "string" && iso ? formatDate(iso, { month: "short", day: "numeric" }) : "";
    return d ? `${p} · ${d}` : p;
  };
  // Dominant emotion for the demoted card chip's new home (same rule the badge used).
  const dominant =
    rec.article.dominantEmotion ??
    (Object.keys(rec.article.emotion) as (keyof EmotionShare)[]).reduce((a, b) =>
      rec.article.emotion[a] >= rec.article.emotion[b] ? a : b,
    );
  return (
    <>
      {explanation && (
        <Section title={t("why.explanation")}>
          <p className="text-xs leading-snug text-foreground/90">{localizeExplanation(explanation)}</p>
          {DEV_DETAIL && (
            <Row label="Type" value={`${explanation.type} (P${explanation.priority})`} />
          )}
        </Section>
      )}

      {sev && (
        <Section title={t("why.story")}>
          <Row label={t("rec.receipt.youRead")} value={withDate(sev.readPublisher, sev.readPublishedAt)} mono={false} />
          <Row label={t("why.thisCoverage")} value={withDate(sev.recPublisher, sev.recPublishedAt)} mono={false} />
          <Row label={t("why.storyReads")} value={String(sev.storyReads ?? 1)} />
          {typeof sev.storyId === "string" && sev.storyId && (
            <div className="pt-1 text-right">
              <Link
                href={`/stories/${encodeURIComponent(sev.storyId)}`}
                className="text-xs font-medium text-primary hover:underline"
              >
                {t("rec.viewStory")} →
              </Link>
            </div>
          )}
        </Section>
      )}
      {DEV_DETAIL && (
        <Section title="Recommendation">
          {explain.explainId && (
            <Row label="ID" value={<span className="break-all text-[10px]">{explain.explainId}</span>} />
          )}
          <Row label="Strategy" value={ev.chosenBy.toUpperCase()} />
          <Row label="Rank" value={`#${ev.rank} of ${chosen?.candidates ?? "?"} candidates`} />
          <Row label="Match" value={ev.match} mono={false} />
        </Section>
      )}

      <Section title={t("why.history")}>
        {ev.topicShare && (
          <Row
            label={ev.topicShare.topic}
            value={t("why.shareOfReading", { pct: Math.round(ev.topicShare.share * 100) })}
          />
        )}
        <Row
          label={t("why.prevReads", { publisher: ev.publisher })}
          value={fam.reads === 0 ? t("why.newOutlet") : `${fam.reads} (${Math.round(fam.share * 100)}%)`}
        />
        {DEV_DETAIL && (
          <Row
            label="Graph connectivity"
            value={`${ev.connectivity.readsWithinTwoHops} of ${ev.connectivity.graphReads} reads within 2 hops`}
          />
        )}
      </Section>

      <Section title={t("why.bridge")}>
        <Row
          label={t("why.yourPosition")}
          value={`${t(sideOf(ev.crossCutting.userMeanLean))} (${ev.crossCutting.userMeanLean.toFixed(2)})`}
        />
        <Row
          label={t("why.article")}
          value={`${t(sideOf(ev.crossCutting.articleLean))} (${ev.crossCutting.articleLean.toFixed(2)})`}
        />
        <Row label={t("why.gap")} value={ev.leanGap.toFixed(2)} />
        <Row label={t("why.crossCutting")} value={ev.crossCutting.value ? t("common.yes") : t("common.no")} mono={false} />
      </Section>

      <Section title={t("why.estEffect")}>
        {ev.viewpointShift ? (
          <>
            <Row label={t("why.current")} value={lcr(ev.viewpointShift.current)} />
            <Row label={t("why.afterReading")} value={lcr(ev.viewpointShift.after)} />
            <p className="pt-1 text-[10px] leading-snug text-muted-foreground">
              {t("why.estimatedBasis", { basis: ev.viewpointShift.basis })}
            </p>
          </>
        ) : (
          <p className="text-[10px] leading-snug text-muted-foreground">{t("why.noProjection")}</p>
        )}
      </Section>

      <Section title={t("why.articleMeta")}>
        <Row label={t("why.leaning")} value={t(`filter.${rec.article.leanBucket}`)} mono={false} />
        {DEV_DETAIL && (
          <Row label="Classifier confidence" value={`${Math.round(rec.article.confidence * 100)}%`} />
        )}
        <Row label={t("filter.publisher")} value={rec.article.publisher} mono={false} />
        <Row label={t("why.category")} value={rec.article.topic} mono={false} />
        {/* register + emotion live here now — demoted from the card face (Commit 22) */}
        <Row label={t("why.coverageType")} value={t(`register.${rec.article.register}`)} mono={false} />
        <Row label={t("filter.emotion")} value={t(`emotion.${dominant}`)} mono={false} />
      </Section>

      {DEV_DETAIL && (
      <Section title="Technical">
        {"epsilon" in params && <Row label="ε (RWE-B erasure)" value={String(params.epsilon)} />}
        {"beta" in params && <Row label="β (RWE-D long-tail)" value={String(params.beta)} />}
        <Row label="Hyperparameters" value={String(params.source ?? "defaults")} mono={false} />
        <Row label="Score" value={ev.byStrategy[ev.chosenBy]?.score.toExponential(2) ?? "—"} />
        {reads.joined !== null && <Row label="Joined reads" value={`${reads.joined}/${reads.total}`} />}
        {explain.corpusGeneration !== undefined && (
          <Row label="Corpus generation" value={explain.corpusGeneration} />
        )}
        {explain.modelVersion && (
          <Row label="Model version" value={`reads ${explain.modelVersion.readingVersion}`} />
        )}
      </Section>
      )}
    </>
  );
}

export function WhyDrawer({ rec, open }: { rec: Recommendation; open: boolean }) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useRecommendationExplain(open);
  const ev = data?.recommendations.find((e) => e.articleId === rec.article.id);
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="overflow-hidden"
        >
          <div className="mt-3 rounded-lg border bg-muted/20">
            {isLoading && (
              <div className="space-y-2 p-3">
                <Skeleton className="h-3 w-2/3" />
                <Skeleton className="h-3 w-1/2" />
                <Skeleton className="h-3 w-3/5" />
              </div>
            )}
            {!isLoading && (isError || !data) && (
              <p className="p-3 text-xs text-muted-foreground">{t("why.unavailable")}</p>
            )}
            {!isLoading && data && !ev && (
              <p className="p-3 text-xs text-muted-foreground">{t("why.stale")}</p>
            )}
            {!isLoading && data && ev && <EvidenceSections rec={rec} ev={ev} explain={data} />}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
