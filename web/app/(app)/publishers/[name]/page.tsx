"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Building2, ExternalLink, Newspaper, Search } from "lucide-react";
import { usePublisher } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { countryFlag, countryName } from "@/lib/countries";
import type { PublisherAbout, PublisherProfile } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { BarList, type BarItem } from "@/components/shared/bar-list";
import { ArticleRow } from "@/components/shared/article-row";
import { CountryBadge } from "@/components/shared/country-badge";
import { LeanBadge } from "@/components/shared/article-badges";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { EMOTION_META } from "@/lib/metrics";

// Publisher Intelligence — the profile of ONE publisher: curated registry facts (identity, lean,
// locality) + counted catalog facts (volume, topics, event geography, tone-with-n) + its recent
// articles. Every number is a counted fact from /api/publishers/{name}; modules the engine omitted
// (below their signal floor) simply don't render — nothing is defaulted here. An unrated outlet
// shows "Not rated" (L2.2), never a fabricated Center. Reached from publisher names across the
// app — deliberately NOT a nav destination (the consolidation direction: context, not hubs).

const EMOTIONS = ["fear", "outrage", "analysis", "positive", "neutral"] as const;

// The About block's rows, in presentation order. `country` is rendered separately (it needs the
// locale's region name), so it is not listed here.
const ABOUT_ROWS = ["founded", "headquarters", "parent"] as const;

export default function PublisherPage() {
  const params = useParams<{ name: string }>();
  const name = decodeURIComponent(params.name ?? "");
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = usePublisher(name);
  const notFound = (error as { status?: number } | null)?.status === 404;

  return (
    <PageContainer>
      {isLoading && (
        <div>
          <div className="mb-6 flex items-center gap-4">
            <Skeleton className="h-12 w-12 rounded-lg" />
            <div className="space-y-2">
              <Skeleton className="h-7 w-56" />
              <Skeleton className="h-4 w-80" />
            </div>
          </div>
          <div className="grid gap-5 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-48 rounded-lg" />
            ))}
          </div>
        </div>
      )}

      {notFound && (
        <EmptyState
          icon={Building2}
          title={t("publishers.notFound.title")}
          description={t("publishers.notFound.body")}
          className="mt-8"
        />
      )}
      {isError && !notFound && <ErrorState onRetry={() => refetch()} />}

      {data && <Profile profile={data} />}
    </PageContainer>
  );
}

function Profile({ profile: p }: { profile: PublisherProfile }) {
  const { t, lang, formatCompact, formatDate } = useTranslation();
  const [logoOk, setLogoOk] = React.useState(true);
  const total = p.articles.total;
  const day = (iso: string) => formatDate(iso, { dateStyle: "medium" });
  const enc = encodeURIComponent(p.name);

  const topicBars: BarItem[] = p.topics.map((x) => ({
    label: x.label,
    value: total ? x.count / total : 0,
    count: x.count,
  }));
  const countryBars: BarItem[] = p.eventCountries.map((x) => ({
    label: `${countryFlag(x.label)} ${countryName(x.label, lang)}`.trim(),
    value: total ? x.count / total : 0,
    count: x.count,
  }));
  const registerBars: BarItem[] = p.registers
    ? (["reporting", "opinion", "mixed"] as const).map((k) => ({
        label: t(`register.${k}`),
        value: p.registers!.n ? p.registers![k] / p.registers!.n : 0,
        count: p.registers![k],
      }))
    : [];
  const emotionBars: BarItem[] = p.emotion
    ? EMOTIONS.map((k) => ({
        label: t(`emotion.${k}`),
        value: p.emotion![k],
        color: EMOTION_META[k].color,
      }))
    : [];

  return (
    <>
      <header className="mb-6">
        <div className="flex items-start gap-4">
          {p.publisherLogo && logoOk ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={p.publisherLogo}
              alt=""
              width={48}
              height={48}
              onError={() => setLogoOk(false)}
              className="h-12 w-12 rounded-lg border bg-card object-contain p-1.5"
            />
          ) : (
            <div className="flex h-12 w-12 items-center justify-center rounded-lg border bg-card">
              <Building2 className="h-6 w-6 text-muted-foreground" />
            </div>
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{p.name}</h1>
              {p.rated ? (
                <LeanBadge lean={p.lean} bucket={p.leanBucket} />
              ) : (
                <Badge variant="secondary">{t("publishers.notRated")}</Badge>
              )}
              {p.registry?.country && <CountryBadge code={p.registry.country} className="text-sm" />}
              {p.registry?.scope && (
                <Badge variant="outline">{t(`local.scope.${p.registry.scope}`)}</Badge>
              )}
            </div>
            {/* The counted snapshot line — volume, observed window, cadence. Facts only. */}
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-muted-foreground">
              <span>
                {total === 1
                  ? t("publishers.snapshot.articles.one", { n: formatCompact(total) })
                  : t("publishers.snapshot.articles", { n: formatCompact(total) })}
              </span>
              {p.articles.firstSeen && p.articles.lastSeen && (
                <>
                  <span>·</span>
                  <span>
                    {t("publishers.snapshot.window", {
                      from: day(p.articles.firstSeen),
                      to: day(p.articles.lastSeen),
                    })}
                  </span>
                </>
              )}
              {typeof p.articles.perDay === "number" && (
                <>
                  <span>·</span>
                  <span>{t("publishers.snapshot.perDay", { n: p.articles.perDay })}</span>
                </>
              )}
              {p.site && (
                <>
                  <span>·</span>
                  <a
                    href={p.site}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                  >
                    {t("publishers.visit")}
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </>
              )}
            </p>
            {!p.rated && (
              <p className="mt-1 max-w-xl text-xs text-muted-foreground">{t("publishers.notRated.body")}</p>
            )}
          </div>
        </div>
      </header>

      <AboutCard about={p.about} />

      {total === 0 ? (
        <EmptyState
          icon={Newspaper}
          title={t("publishers.empty.title")}
          description={t("publishers.empty.body")}
        />
      ) : (
        <div className="grid gap-5 md:grid-cols-2">
          {topicBars.length > 0 && (
            <SectionCard title={t("publishers.topics.title")} info={t("publishers.topics.info")}>
              <BarList items={topicBars} />
            </SectionCard>
          )}

          {(countryBars.length > 0 || p.languages.length > 0) && (
            <SectionCard title={t("publishers.geography.title")} info={t("publishers.geography.info")}>
              {countryBars.length > 0 && <BarList items={countryBars} />}
              {p.languages.length > 0 && (
                <p className="mt-4 text-xs text-muted-foreground">
                  {t("publishers.languages")}:{" "}
                  {p.languages.map((l) => `${l.label} · ${l.count}`).join("  ")}
                </p>
              )}
            </SectionCard>
          )}

          {p.topicGaps && p.topicGaps.length > 0 && (
            <SectionCard title={t("publishers.gaps.title")} info={t("publishers.gaps.info")}>
              {/* Bar = the topic's weight in the CATALOG; the sublabel is this publisher's own
                  count — the gap is the distance between the two, shown as counted facts. */}
              <BarList
                items={p.topicGaps.map((g) => ({
                  label: g.label,
                  value: g.catalogShare,
                  count: g.catalogCount,
                  sublabel: t("publishers.gaps.them", { n: g.publisherCount }),
                }))}
              />
            </SectionCard>
          )}

          {p.coCoverage && (
            <SectionCard title={t("publishers.co.title")} info={t("publishers.co.info")}>
              <p className="mb-3 text-xs text-muted-foreground">
                {t("publishers.co.caption", { n: p.coCoverage.sharedStories })}
              </p>
              <ul className="space-y-2">
                {p.coCoverage.publishers.map((c) => (
                  <li key={c.publisher} className="flex items-center justify-between gap-3 text-sm">
                    <Link
                      href={`/publishers/${encodeURIComponent(c.publisher)}`}
                      className="min-w-0 truncate font-medium hover:text-primary hover:underline"
                    >
                      {c.publisher}
                    </Link>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {c.stories === 1
                        ? t("publishers.co.stories.one", { n: c.stories })
                        : t("publishers.co.stories.other", { n: c.stories })}
                    </span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}

          {(registerBars.length > 0 || emotionBars.length > 0) && (
            <SectionCard title={t("publishers.tone.title")} info={t("publishers.tone.info")}>
              <div className="space-y-5">
                {registerBars.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">
                      {t("publishers.tone.registers")} · {t("publishers.tone.n", { n: p.registers!.n })}
                    </p>
                    <BarList items={registerBars} />
                  </div>
                )}
                {emotionBars.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">
                      {t("publishers.tone.emotion")} · {t("publishers.tone.n", { n: p.emotion!.n })}
                    </p>
                    <BarList items={emotionBars} />
                  </div>
                )}
              </div>
            </SectionCard>
          )}

          <SectionCard
            title={t("publishers.recent.title")}
            className="md:col-span-2"
            action={
              <span className="flex items-center gap-3 text-xs font-medium">
                <Link
                  href={`/search?publisher=${enc}`}
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  <Search className="h-3.5 w-3.5" />
                  {t("publishers.searchAll")}
                </Link>
                <Link
                  href={`/stories?publisher=${enc}`}
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  <Newspaper className="h-3.5 w-3.5" />
                  {t("publishers.viewStories")}
                </Link>
              </span>
            }
          >
            <div className="space-y-3">
              {p.recent.map((article, i) => (
                <ArticleRow key={article.id} article={article} index={i} />
              ))}
            </div>
          </SectionCard>
        </div>
      )}
    </>
  );
}

// Enriched publisher facts. Curated registry values always win; Wikipedia/Wikidata only fills the
// gaps, and each row names its own source — so a founding year sourced from Wikidata never borrows
// the authority of a country somebody curated by hand. Renders nothing at all when there is nothing
// to show, which is the normal case for an outlet nobody has matched yet.
function AboutCard({ about }: { about?: PublisherAbout }) {
  const { t, lang, formatDate } = useTranslation();
  if (!about) return null;

  const rows = ABOUT_ROWS.filter((k) => about[k]);
  const hasFacts = rows.length > 0 || Boolean(about.country) || Boolean(about.website);
  // A recorded miss is worth saying once; a miss with nothing else to show is not worth a card.
  if (!hasFacts && !about.description) return null;

  const source = (field: keyof NonNullable<PublisherAbout["sources"]>) => {
    const s = about.sources?.[field];
    return s ? t(`publishers.about.source.${s}`) : null;
  };

  return (
    <SectionCard
      title={t("publishers.about.title")}
      info={t("publishers.about.info")}
      className="mb-5"
    >
      {about.description && (
        <p className="mb-4 max-w-3xl text-sm leading-relaxed text-muted-foreground">
          {about.description}
        </p>
      )}

      {hasFacts && (
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {rows.map((key) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                {t(`publishers.about.${key}`)}
              </dt>
              <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium">{about[key]}</span>
                {source(key) && (
                  <span className="text-xs text-muted-foreground">{source(key)}</span>
                )}
              </dd>
            </div>
          ))}

          {about.country && (
            <div className="min-w-0">
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                {t("publishers.about.country")}
              </dt>
              <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium">{countryName(about.country, lang)}</span>
                {source("country") && (
                  <span className="text-xs text-muted-foreground">{source("country")}</span>
                )}
              </dd>
            </div>
          )}

          {about.website && (
            <div className="min-w-0">
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                {t("publishers.about.website")}
              </dt>
              <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-sm">
                <a
                  href={about.website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="truncate font-medium text-primary hover:underline"
                >
                  {about.website.replace(/^https?:\/\//, "")}
                </a>
                {source("website") && (
                  <span className="text-xs text-muted-foreground">{source("website")}</span>
                )}
              </dd>
            </div>
          )}
        </dl>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
        {about.wikipediaUrl && (
          <a
            href={about.wikipediaUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            {t("publishers.about.wikipedia")}
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
        {about.status === "ok" && about.refreshedAt && (
          <span>
            {t("publishers.about.refreshed", {
              when: formatDate(about.refreshedAt, { dateStyle: "medium" }),
            })}
          </span>
        )}
        {/* An unconfirmed match is stated, not hidden: it explains why the block is thin and marks
            the outlet as one a human could resolve by hand. */}
        {(about.status === "ambiguous" || about.status === "no_match") && (
          <span>{t("publishers.about.unmatched")}</span>
        )}
      </div>
    </SectionCard>
  );
}
