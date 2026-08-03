"use client";

import * as React from "react";
import { AlertCircle, BarChart3, Building2, Check, ExternalLink, History, Info, Layers, Globe,
         Sparkles, type LucideIcon } from "lucide-react";
import type { AnalysisResult, CoverageFinding, LeanBucket } from "@/types/domain";
import { analysisPresentation } from "@/lib/analysis-presentation";
import { presentRecommendation } from "@/lib/rec-presentation";
import { Badge } from "@/components/ui/badge";
import { SectionCard } from "@/components/shared/section-card";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LeanBadge, PublisherBadge } from "@/components/shared/article-badges";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Renders one ANALYSIS CONTRACT v1 result, honestly. Everything shown is derived through
 * `analysisPresentation` (the pure mapper): provenance is always explicit, an unknown outlet shows
 * "unknown" (never a guessed lean), and `register` / `confidence` are deferred. The reader-relative
 * `explanation` + `recommendation` (A3) render ONLY when the engine enriched them — a signed-in
 * measured reader; anonymous / non-measured readers get exactly the A2 sections, no placeholder.
 * The explanation reuses the recommendation-card renderer (`presentRecommendation` +
 * `localizeExplanation`); `personal` (A4) renders as the compact "You and this article" standing
 * inside the same card — facts only, each line present only when the engine licensed it. Known
 * backend notes are localized; an unrecognized note is preserved under a "Technical note" label
 * rather than dropped.
 */
export function AnalysisResult({ result }: { result: AnalysisResult }) {
  const { t } = useTranslation();
  const view = analysisPresentation(result);

  // An unparseable URL is guidance, not a failure — the engine returns 200 with this status.
  if (view.status !== "analyzed") {
    return (
      <div role="status" className="space-y-4">
        <div className="flex items-start gap-3 rounded-lg border border-caution/30 bg-caution/[0.05] px-4 py-3 text-sm">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-caution" aria-hidden />
          <p>{t("analyze.invalidUrl")}</p>
        </div>
        <NotesPanel notes={view.notes} />
      </div>
    );
  }

  const prov = view.provenance;
  const article = result.article;

  return (
    <div role="status" aria-live="polite" className="space-y-4">
      {/* Provenance — always visible, always explained. */}
      {prov && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={prov.variant} className="gap-1">
            {prov.source === "catalog" ? (
              <Layers className="h-3 w-3" aria-hidden />
            ) : (
              <Globe className="h-3 w-3" aria-hidden />
            )}
            {t(prov.labelKey)}
          </Badge>
          <span className="text-xs text-muted-foreground">{t(prov.hintKey)}</span>
        </div>
      )}

      {/* Catalog hit: the real article header (never fabricated for a URL-only score). */}
      {article && (
        <div className="flex items-start gap-3">
          {article.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={article.image}
              alt=""
              className="h-16 w-16 shrink-0 rounded-md object-cover"
              loading="lazy"
            />
          ) : null}
          <div className="min-w-0">
            <h2 className="text-base font-semibold leading-snug">{article.headline}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <PublisherBadge name={article.publisher} lean={article.publisherLean} />
              {article.url ? (
                <a
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  {t("analyze.openArticle")} <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* Scoring — the analyzed attributes (register / confidence deliberately not shown). */}
      {view.scoring && (
        <SectionCard title={t("analyze.scoring.title")}>
          <div className="space-y-3">
            {view.scoring.outlet ? (
              <Row label={t("analyze.scoring.outlet")}>
                <span className="inline-flex items-center gap-1.5 text-sm font-medium">
                  <Building2 className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  {view.scoring.outlet}
                </span>
              </Row>
            ) : null}

            <Row label={t("analyze.scoring.lean")}>
              {view.scoring.lean.known ? (
                <LeanBadge lean={view.scoring.lean.lean} bucket={view.scoring.lean.bucket} />
              ) : (
                <Badge variant="secondary" title={t("analyze.scoring.leanUnknownHint")}>
                  {t("analyze.scoring.leanUnknown")}
                </Badge>
              )}
            </Row>

            {view.scoring.topic ? (
              <Row label={t("analyze.scoring.topic")}>
                <Badge variant="secondary">{view.scoring.topic}</Badge>
              </Row>
            ) : null}

            <Row label={t("analyze.scoring.political")}>
              <span className="text-sm">
                {view.scoring.political ? t("analyze.scoring.politicalYes") : t("analyze.scoring.politicalNo")}
              </span>
            </Row>

            {view.scoring.emotionKey ? (
              <Row label={t("analyze.scoring.emotion")}>
                <Badge variant="secondary">{t(`emotion.${view.scoring.emotionKey}`)}</Badge>
              </Row>
            ) : null}
          </div>
        </SectionCard>
      )}

      {/* Coverage comparison (L0) — counted facts about this article's story cluster. Renders
          ONLY when the engine says `available`; every refusal reason (untrusted cluster, too few
          publishers, template genre, cross-language, disabled) renders nothing at all. No claim
          here is derived from article text: `textClaims` is false at this tier, and the standing
          caveat says so rather than implying the article omitted anything. */}
      {result.coverageComparison?.available && (
        <CoverageComparisonCard data={result.coverageComparison} />
      )}

      {/* AI summary + bias analysis — rendered ONLY when the engine has a cached artifact for
          this article (docs/ARTICLE_INSIGHTS.md). No placeholder, no loading state: absence
          means not generated yet, and the page is complete without it. */}
      {result.insights && (
        <SectionCard title={t("analyze.insights.title")}>
          <div className="space-y-3">
            <p className="text-sm leading-relaxed">{result.insights.summary}</p>
            {result.insights.bias && (
              <div className="space-y-2 border-t border-border/60 pt-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t("analyze.insights.biasTitle")}
                </p>
                <Row label={t("analyze.insights.framing")}>
                  <span className="text-sm">{result.insights.bias.framing}</span>
                </Row>
                <Row label={t("analyze.insights.tone")}>
                  <span className="text-sm">{result.insights.bias.tone}</span>
                </Row>
                {result.insights.bias.loadedLanguage.length > 0 && (
                  <Row label={t("analyze.insights.loaded")}>
                    <span className="flex flex-wrap gap-1.5">
                      {result.insights.bias.loadedLanguage.map((phrase) => (
                        <Badge key={phrase} variant="outline" className="font-normal">
                          &ldquo;{phrase}&rdquo;
                        </Badge>
                      ))}
                    </span>
                  </Row>
                )}
                <Row label={t("analyze.insights.omissions")}>
                  <span className="text-sm">{result.insights.bias.omissions}</span>
                </Row>
                <Row label={t("analyze.insights.viewpoint")}>
                  <span className="text-sm">{result.insights.bias.viewpoint}</span>
                </Row>
              </div>
            )}
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Sparkles className="h-3 w-3" aria-hidden />
              {t("analyze.insights.disclaimer")}
            </p>
          </div>
        </SectionCard>
      )}

      {/* Story context — membership only when catalog-backed; otherwise advisory or nothing. */}
      {view.story && (
        <SectionCard title={t("analyze.story.title")}>
          {view.story.kind === "member" ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">{t("analyze.story.memberIntro")}</p>
              <SpectrumBar distribution={view.story.distribution} />
              {view.story.missingViewpoints.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <span className="text-xs text-muted-foreground">{t("analyze.story.missingViewpoints")}</span>
                  {view.story.missingViewpoints.map((b: LeanBucket) => (
                    <Badge key={b} variant={b}>
                      {t(`filter.${b}`)}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">{t("analyze.story.noMissing")}</p>
              )}
            </div>
          ) : view.story.kind === "similar" ? (
            <p className="text-sm text-muted-foreground">{t("analyze.story.similar")}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("analyze.story.none")}</p>
          )}
        </SectionCard>
      )}

      {/* A3/A4 — reader-relative "For you": present only when the engine enriched (measured
          reader). Anonymous / non-measured input maps every section to null → nothing renders. */}
      {(view.recommendation || view.explanation || view.personal) && <ForYouCard view={view} />}

      <NotesPanel notes={view.notes} />

      {/* Zero-write invariant, surfaced to the reader. */}
      <p className="text-xs text-muted-foreground">{t("analyze.disclaimer")}</p>
    </div>
  );
}

/**
 * The A3 "For you" section: the reader-relative verdict + explanation. The verdict headline and
 * reason statements come from the pure mapper (localized, closed vocabulary — never a raw signal);
 * the explanation SENTENCE reuses the recommendation-card renderer verbatim (`presentRecommendation`
 * for the readerFact / contribution parts, `localizeExplanation` as the fallback), so there is one
 * explanation vocabulary product-wide and no duplicated logic.
 */
function ForYouCard({ view }: { view: ReturnType<typeof analysisPresentation> }) {
  const { t } = useTranslation();
  const rec = view.recommendation;
  return (
    <SectionCard title={t("analyze.rec.title")}>
      <div className="space-y-3">
        {rec ? (
          <Badge variant={rec.wouldBroaden ? "positive" : "secondary"} className="gap-1">
            <Sparkles className="h-3 w-3" aria-hidden />
            {t(rec.headlineKey)}
          </Badge>
        ) : null}

        {view.explanation ? <ExplanationLines explanation={view.explanation} /> : null}

        {rec && rec.reasons.length > 0 ? (
          <ul className="space-y-1">
            {rec.reasons.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-positive" aria-hidden />
                <span>{t(r.key, r.params)}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {/* A4 — "You and this article": the reader-standing facts (each line only when present). */}
        {view.personal ? <PersonalStanding personal={view.personal} /> : null}

        {/* A3.3 — "Read next": the reader's next pick. Nothing renders when there is no honest
            pick (null / malformed). The explanation reuses the SAME renderer as above. */}
        {rec?.next ? <NextArticleBlock next={rec.next} /> : null}
      </div>
    </SectionCard>
  );
}

/** One standing fact line: a small leading icon + a localized sentence (the reasons-list idiom). */
function Fact({ icon: Icon, children }: { icon: LucideIcon; children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-2 text-xs text-muted-foreground">
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}

/**
 * A4.2 — the reader-standing block: verbatim engine facts rendered compactly. Already-read (with
 * a locale date when known), the familiar-publisher standing, the reader's measured topic share
 * (the SAME `rec.reader.top_topic_share` sentence the recommendation cards use), their political
 * reading mix (the shared SpectrumBar), and their own coverage of this story. Sub-blocks the
 * mapper nulled render nothing.
 */
function PersonalStanding({
  personal,
}: {
  personal: NonNullable<ReturnType<typeof analysisPresentation>["personal"]>;
}) {
  const { t, formatDate } = useTranslation();
  const story = personal.story;
  return (
    <div className="space-y-2 border-t pt-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("analyze.personal.title")}
      </p>

      {personal.alreadyRead ? (
        <Fact icon={History}>
          {personal.alreadyRead.at
            ? t("analyze.personal.alreadyReadOn", {
                date: formatDate(personal.alreadyRead.at, {
                  year: "numeric", month: "short", day: "numeric",
                }),
              })
            : t("analyze.personal.alreadyRead")}
        </Fact>
      ) : null}

      {personal.familiarPublisher ? (
        <Fact icon={Building2}>
          {t("analyze.personal.familiarPublisher", { publisher: personal.familiarPublisher.name })}
        </Fact>
      ) : null}

      {personal.topicShare ? (
        <Fact icon={BarChart3}>
          {t("rec.reader.top_topic_share", personal.topicShare)}
        </Fact>
      ) : null}

      {personal.viewpoint ? (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">{t("analyze.personal.readingMix")}</p>
          <SpectrumBar distribution={personal.viewpoint.shares} />
          {personal.viewpoint.addsMissing ? (
            <p className="flex items-start gap-2 text-xs font-medium text-positive">
              <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{t("analyze.personal.addsMissing")}</span>
            </p>
          ) : null}
        </div>
      ) : null}

      {story ? (
        <div className="space-y-1.5">
          <Fact icon={Layers}>{t("analyze.personal.storyRead", { count: story.readCount })}</Fact>
          {story.bucketsRead.length > 0 || story.addsBucket ? (
            <div className="flex flex-wrap items-center gap-1.5 pl-5">
              {story.bucketsRead.map((b) => (
                <Badge key={b} variant={b}>
                  {t(`filter.${b}`)}
                </Badge>
              ))}
              {story.addsBucket ? (
                <>
                  <span className="text-xs text-muted-foreground">
                    {t("analyze.personal.storyAdds")}
                  </span>
                  <Badge variant={story.addsBucket}>{t(`filter.${story.addsBucket}`)}</Badge>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The ONE explanation renderer for this card, reused by the analyzed article and the next pick:
 * `presentRecommendation` for the readerFact / contribution parts (the recommendation-card
 * pattern), `localizeExplanation` as the validated-sentence fallback.
 */
function ExplanationLines({
  explanation,
  compact = false,
}: {
  explanation: NonNullable<ReturnType<typeof analysisPresentation>["explanation"]>;
  compact?: boolean;
}) {
  const { t, localizeExplanation } = useTranslation();
  const pres = presentRecommendation(explanation);
  const lead = compact ? "text-xs font-medium leading-snug" : "text-sm font-medium leading-snug";
  if (pres.reader || pres.contribution) {
    return (
      <div>
        {pres.reader ? <p className={lead}>{t(pres.reader.key, pres.reader.params)}</p> : null}
        {pres.contribution ? (
          <p className={pres.reader ? "mt-0.5 text-xs text-muted-foreground" : lead}>
            {t(pres.contribution.key, pres.contribution.params)}
          </p>
        ) : null}
      </div>
    );
  }
  return <p className={compact ? "text-xs text-muted-foreground" : "text-sm text-muted-foreground"}>{localizeExplanation(explanation)}</p>;
}

/** The "Read next" block: headline + publisher + lean + source label + explanation + Read button. */
function NextArticleBlock({
  next,
}: {
  next: NonNullable<NonNullable<ReturnType<typeof analysisPresentation>["recommendation"]>["next"]>;
}) {
  const { t } = useTranslation();
  const a = next.article;
  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("analyze.next.title")}
        </p>
        <span className="text-xs text-muted-foreground">{t(next.labelKey)}</span>
      </div>
      <p className="text-sm font-medium leading-snug">{a.headline}</p>
      <div className="flex flex-wrap items-center gap-2">
        <PublisherBadge name={a.publisher} lean={a.publisherLean} logo={a.publisherLogo} />
        {typeof a.lean === "number" ? <LeanBadge lean={a.lean} bucket={a.leanBucket} /> : null}
      </div>
      {next.explanation ? <ExplanationLines explanation={next.explanation} compact /> : null}
      <ReadArticleButton article={a} openedFrom="analyze" className="mt-1" />
    </div>
  );
}

/**
 * Coverage comparison, tier L0 (docs/COVERAGE_COMPARISON_DESIGN.md §10 sketch A).
 *
 * Every line is a count with its own evidence: the outlets a finding came from are listed and
 * linkable, so the reader checks rather than trusts. The card deliberately does NOT say the
 * article "omitted" anything — L0 examines no article text, and the footer states that plainly
 * (design §5.1/§9.1). "Unique to this article" sits beside the coverage context so the comparison
 * is balanced rather than a deficit report (§5.5).
 */
function CoverageComparisonCard({
  data,
}: {
  data: Extract<NonNullable<AnalysisResult["coverageComparison"]>, { available: true }>;
}) {
  const { t } = useTranslation();
  const hasContext = data.reportedElsewhere.length > 0;
  const hasUnique = data.uniqueHere.length > 0;
  if (!hasContext && !hasUnique && !data.missingViewpoints.length) return null;

  return (
    <SectionCard title={t("analyze.coverage.title")}>
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          {t("analyze.coverage.scope")
            .replace("{outlets}", String(data.outlets))
            .replace("{articles}", String(data.articles))}
        </p>

        {data.timing && (
          <Row label={t("analyze.coverage.timing")}>
            <span className="text-sm">
              {data.timing.isFirstReport
                ? t("analyze.coverage.timingFirst")
                : t("analyze.coverage.timingLater")
                    .replace("{position}", String(data.timing.position))
                    .replace("{of}", String(data.timing.of))
                    .replace("{hours}", String(data.timing.hoursAfterFirst))}
            </span>
          </Row>
        )}

        {hasContext && (
          <div className="space-y-2 border-t border-border/60 pt-3">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("analyze.coverage.contextTitle")}
            </p>
            {data.reportedElsewhere.map((f) => (
              <FindingRow key={f.key} f={f} />
            ))}
          </div>
        )}

        {hasUnique && (
          <div className="space-y-2 border-t border-border/60 pt-3">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("analyze.coverage.uniqueTitle")}
            </p>
            {data.uniqueHere.map((f) => (
              <FindingRow key={f.key} f={f} />
            ))}
          </div>
        )}

        {data.missingViewpoints.length > 0 && (
          <Row label={t("analyze.coverage.missingViewpoints")}>
            <span className="flex flex-wrap gap-1.5">
              {data.missingViewpoints.map((b) => (
                <Badge key={b} variant={b}>
                  {t(`filter.${b}`)}
                </Badge>
              ))}
            </span>
          </Row>
        )}

        <p className="flex items-start gap-1.5 border-t border-border/60 pt-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          {t("analyze.coverage.caveat")}
        </p>
      </div>
    </SectionCard>
  );
}

/** One finding: its label, its count, and the outlets the count came from. */
function FindingRow({ f }: { f: CoverageFinding }) {
  const { t } = useTranslation();
  return (
    <div className="text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span>
          {f.label}
          {f.countries?.length ? ` (${f.countries.join(", ")})` : null}
        </span>
        {f.support > 0 && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {t("analyze.coverage.support")
              .replace("{support}", String(f.support))
              .replace("{of}", String(f.of))}
          </span>
        )}
      </div>
      {f.evidence.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
          {f.evidence.map((e) => (
            <li key={`${e.url}`} className="text-xs text-muted-foreground">
              {e.url ? (
                <a
                  href={e.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-foreground hover:underline"
                >
                  {e.publisher}
                </a>
              ) : (
                e.publisher
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="flex flex-wrap items-center justify-end gap-1.5">{children}</span>
    </div>
  );
}

/** Localized notes + a "Technical note" fallback for anything the mapper didn't recognize. */
function NotesPanel({ notes }: { notes: ReturnType<typeof analysisPresentation>["notes"] }) {
  const { t } = useTranslation();
  if (notes.length === 0) return null;
  return (
    <ul className={cn("space-y-1.5 rounded-lg border bg-muted/30 px-4 py-3")}>
      {notes.map((n, i) => (
        <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {n.kind === "known" ? (
              t(n.key)
            ) : (
              <>
                <span className="font-medium">{t("analyze.note.technical")}:</span> {n.text}
              </>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}
