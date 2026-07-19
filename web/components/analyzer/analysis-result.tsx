"use client";

import * as React from "react";
import { AlertCircle, Building2, ExternalLink, Info, Layers, Globe } from "lucide-react";
import type { AnalysisResult, LeanBucket } from "@/types/domain";
import { analysisPresentation } from "@/lib/analysis-presentation";
import { Badge } from "@/components/ui/badge";
import { SectionCard } from "@/components/shared/section-card";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LeanBadge, PublisherBadge } from "@/components/shared/article-badges";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Renders one ANALYSIS CONTRACT v1 result, honestly. Everything shown is derived through
 * `analysisPresentation` (the pure mapper): provenance is always explicit, an unknown outlet shows
 * "unknown" (never a guessed lean), `register` / `confidence` are deferred, and the reader-relative
 * sections (recommendation / explanation / personal) are never rendered. Known backend notes are
 * localized; an unrecognized note is preserved under a "Technical note" label rather than dropped.
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

      <NotesPanel notes={view.notes} />

      {/* Zero-write invariant, surfaced to the reader. */}
      <p className="text-xs text-muted-foreground">{t("analyze.disclaimer")}</p>
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
