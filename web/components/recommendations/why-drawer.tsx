"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { Recommendation, RecommendationEvidence, RecommendationExplain } from "@/types/domain";
import { useRecommendationExplain } from "@/hooks/use-data";
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

/** Coarse reading aid next to the raw number — the number is the authoritative value. */
function sideOf(lean: number) {
  if (lean < -0.05) return "Left";
  if (lean > 0.05) return "Right";
  return "Center";
}

function lcr(v: { left: number; center: number; right: number }) {
  return `L ${v.left}% · C ${v.center}% · R ${v.right}%`;
}

function EvidenceSections({
  rec,
  ev,
  explain,
}: {
  rec: Recommendation;
  ev: RecommendationEvidence;
  explain: RecommendationExplain;
}) {
  const chosen = explain.trace.strategies[ev.chosenBy];
  const params = (chosen?.paramsUsed ?? {}) as Record<string, unknown>;
  const fam = ev.outletFamiliarity;
  const reads = explain.trace.reader.reads;
  return (
    <>
      <Section title="Recommendation">
        {explain.explainId && (
          <Row label="ID" value={<span className="break-all text-[10px]">{explain.explainId}</span>} />
        )}
        <Row label="Strategy" value={ev.chosenBy.toUpperCase()} />
        <Row label="Rank" value={`#${ev.rank} of ${chosen?.candidates ?? "?"} candidates`} />
        <Row label="Match" value={ev.match} mono={false} />
      </Section>

      <Section title="Bridge">
        <Row
          label="Your position"
          value={`${sideOf(ev.crossCutting.userMeanLean)} (${ev.crossCutting.userMeanLean.toFixed(2)})`}
        />
        <Row
          label="Article"
          value={`${sideOf(ev.crossCutting.articleLean)} (${ev.crossCutting.articleLean.toFixed(2)})`}
        />
        <Row label="Gap" value={ev.leanGap.toFixed(2)} />
        <Row label="Cross-cutting" value={ev.crossCutting.value ? "yes" : "no"} mono={false} />
      </Section>

      <Section title="Article metadata">
        <Row label="Political leaning" value={rec.article.leanBucket} mono={false} />
        <Row label="Classifier confidence" value={`${Math.round(rec.article.confidence * 100)}%`} />
        <Row label="Publisher" value={rec.article.publisher} mono={false} />
        <Row label="Category" value={rec.article.topic} mono={false} />
      </Section>

      <Section title="History">
        {ev.topicShare && (
          <Row
            label={ev.topicShare.topic}
            value={`${Math.round(ev.topicShare.share * 100)}% of your reading`}
          />
        )}
        <Row
          label={`Previous reads from ${ev.publisher}`}
          value={fam.reads === 0 ? "0 — new outlet for you" : `${fam.reads} (${Math.round(fam.share * 100)}%)`}
        />
        <Row
          label="Graph connectivity"
          value={`${ev.connectivity.readsWithinTwoHops} of ${ev.connectivity.graphReads} reads within 2 hops`}
        />
      </Section>

      <Section title="Estimated effect">
        {ev.viewpointShift ? (
          <>
            <Row label="Current" value={lcr(ev.viewpointShift.current)} />
            <Row label="After reading" value={lcr(ev.viewpointShift.after)} />
            <p className="pt-1 text-[10px] leading-snug text-muted-foreground">
              Estimated — {ev.viewpointShift.basis}.
            </p>
          </>
        ) : (
          <p className="text-[10px] leading-snug text-muted-foreground">
            No viewpoint projection for this article — it isn&apos;t political, or your political
            history is below the report&apos;s own minimum.
          </p>
        )}
      </Section>

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
    </>
  );
}

export function WhyDrawer({ rec, open }: { rec: Recommendation; open: boolean }) {
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
              <p className="p-3 text-xs text-muted-foreground">
                Explanation unavailable — the engine could not be reached (there is no mock for
                evidence: it&apos;s either real or absent).
              </p>
            )}
            {!isLoading && data && !ev && (
              <p className="p-3 text-xs text-muted-foreground">
                No explanation for this card — the feed has likely refreshed since it loaded.
                Reload the page to re-sync cards and evidence.
              </p>
            )}
            {!isLoading && data && ev && <EvidenceSections rec={rec} ev={ev} explain={data} />}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
