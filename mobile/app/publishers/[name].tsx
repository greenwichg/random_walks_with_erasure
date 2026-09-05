import { useLocalSearchParams } from "expo-router";
import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { PublisherAbout, PublisherProfile } from "@ih/core/domain/types";
import { countryName } from "@ih/core/logic/countries";

import { Screen } from "@/components/layout/screen";
import { LeanBadge } from "@/components/shared/article-badges";
import { ArticleRow } from "@/components/shared/article-row";
import { BarList, type BarItem } from "@/components/shared/bar-list";
import { CountryBadge } from "@/components/shared/country-badge";
import { FactualityBadge } from "@/components/shared/factuality-badge";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { SectionCard } from "@/components/shared/section-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { usePublisher } from "@/lib/hooks";
import { emotionColor, OWNERSHIP_LABEL_KEY, ownershipColor } from "@/lib/meta";
import { navigate, openExternal } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const EMOTIONS = ["fear", "outrage", "analysis", "positive", "neutral"] as const;
const ABOUT_ROWS = ["founded", "headquarters"] as const;
const OWNERSHIP_DATE: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

/**
 * Publisher Intelligence — the profile of ONE publisher: curated registry facts + counted catalog
 * facts + its recent articles. Modules the engine omitted simply don't render; an unrated outlet
 * shows "Not rated" (L2.2), never a fabricated Center.
 */
export default function PublisherScreen() {
  const params = useLocalSearchParams<{ name: string }>();
  const name = params.name ?? "";
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = usePublisher(name);
  const notFound = (error as { status?: number } | null)?.status === 404;

  return (
    <Screen>
      {isLoading && (
        <View accessibilityElementsHidden>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 16, marginBottom: 24 }}>
            <Skeleton width={48} height={48} />
            <View style={{ gap: 8, flex: 1 }}>
              <Skeleton height={28} width="60%" />
              <Skeleton height={16} width="90%" />
            </View>
          </View>
          <View style={{ gap: 20 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} height={192} />
            ))}
          </View>
        </View>
      )}

      {notFound && (
        <EmptyState icon="building" title={t("publishers.notFound.title")} description={t("publishers.notFound.body")} style={{ marginTop: 32 }} />
      )}
      {isError && !notFound && <ErrorState onRetry={() => void refetch()} />}

      {data && <Profile profile={data} />}
    </Screen>
  );
}

function Profile({ profile: p }: { profile: PublisherProfile }) {
  const { t, lang, formatCompact, formatDate } = useTranslation();
  const { palette } = useTheme();
  const total = p.articles.total;
  const day = (iso: string) => formatDate(iso, { dateStyle: "medium" });
  const enc = encodeURIComponent(p.name);

  const topicBars: BarItem[] = p.topics.map((x) => ({ label: x.label, value: total ? x.count / total : 0, count: x.count }));
  const countryBars: BarItem[] = p.eventCountries.map((x) => ({ label: countryName(x.label, lang), value: total ? x.count / total : 0, count: x.count }));
  const registerBars: BarItem[] = p.registers
    ? (["reporting", "opinion", "mixed"] as const).map((k) => ({
        label: t(`register.${k}`),
        value: p.registers!.n ? p.registers![k] / p.registers!.n : 0,
        count: p.registers![k],
      }))
    : [];
  const emotionBars: BarItem[] = p.emotion
    ? EMOTIONS.map((k) => ({ label: t(`emotion.${k}`), value: p.emotion![k], color: emotionColor(k, palette) }))
    : [];

  return (
    <View style={{ gap: 20 }}>
      <View style={styles.header}>
        <View style={[styles.logoBox, { borderColor: palette.border, backgroundColor: palette.card }]}>
          <PublisherLogo logo={p.publisherLogo} fallbacks={p.publisherLogoFallbacks} sizePx={36} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={styles.titleRow}>
            <Txt display weight="600" size={24} lineHeight={30} tight accessibilityRole="header">
              {p.name}
            </Txt>
            {p.rated ? <LeanBadge lean={p.lean} bucket={p.leanBucket} /> : <Badge variant="secondary">{t("publishers.notRated")}</Badge>}
            {p.factualityPublished && <FactualityBadge factuality={p.factuality} />}
            {p.registry?.country && <CountryBadge code={p.registry.country} size={14} />}
            {p.registry?.scope && <Badge variant="outline">{t(`local.scope.${p.registry.scope}`)}</Badge>}
          </View>
          <View style={styles.snapshot}>
            <Txt size={14} muted>
              {total === 1
                ? t("publishers.snapshot.articles.one", { n: formatCompact(total) })
                : t("publishers.snapshot.articles", { n: formatCompact(total) })}
            </Txt>
            {p.articles.firstSeen && p.articles.lastSeen && (
              <Txt size={14} muted>{`· ${t("publishers.snapshot.window", { from: day(p.articles.firstSeen), to: day(p.articles.lastSeen) })}`}</Txt>
            )}
            {typeof p.articles.perDay === "number" && <Txt size={14} muted>{`· ${t("publishers.snapshot.perDay", { n: p.articles.perDay })}`}</Txt>}
            {p.site && (
              <Pressable accessibilityRole="link" onPress={() => openExternal(p.site!)} style={styles.inline}>
                <Txt size={14} muted>· </Txt>
                <Txt size={14} weight="500" color={palette.primary}>
                  {t("publishers.visit")}
                </Txt>
                <Icon name="external-link" size={14} color={palette.primary} />
              </Pressable>
            )}
          </View>
          {!p.rated && (
            <Txt size={12} muted style={{ marginTop: 4 }}>
              {t("publishers.notRated.body")}
            </Txt>
          )}
        </View>
      </View>

      <OwnershipCard profile={p} />
      <AboutCard about={p.about} />

      {total === 0 ? (
        <EmptyState icon="newspaper" title={t("publishers.empty.title")} description={t("publishers.empty.body")} />
      ) : (
        <View style={{ gap: 20 }}>
          {topicBars.length > 0 && (
            <SectionCard title={t("publishers.topics.title")} info={t("publishers.topics.info")}>
              <BarList items={topicBars} />
            </SectionCard>
          )}

          {(countryBars.length > 0 || p.languages.length > 0) && (
            <SectionCard title={t("publishers.geography.title")} info={t("publishers.geography.info")}>
              {countryBars.length > 0 && <BarList items={countryBars} />}
              {p.languages.length > 0 && (
                <Txt size={12} muted style={{ marginTop: 16 }}>
                  {t("publishers.languages")}: {p.languages.map((l) => `${l.label} · ${l.count}`).join("  ")}
                </Txt>
              )}
            </SectionCard>
          )}

          {p.topicGaps && p.topicGaps.length > 0 && (
            <SectionCard title={t("publishers.gaps.title")} info={t("publishers.gaps.info")}>
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
              <Txt size={12} muted style={{ marginBottom: 12 }}>
                {t("publishers.co.caption", { n: p.coCoverage.sharedStories })}
              </Txt>
              <View style={{ gap: 8 }}>
                {p.coCoverage.publishers.map((c) => (
                  <View key={c.publisher} style={styles.coRow}>
                    <Pressable accessibilityRole="link" onPress={() => navigate(`/publishers/${encodeURIComponent(c.publisher)}`)} style={{ flex: 1, minWidth: 0 }}>
                      <Txt size={14} weight="500" numberOfLines={1}>
                        {c.publisher}
                      </Txt>
                    </Pressable>
                    <Txt size={14} muted tabular>
                      {c.stories === 1 ? t("publishers.co.stories.one", { n: c.stories }) : t("publishers.co.stories.other", { n: c.stories })}
                    </Txt>
                  </View>
                ))}
              </View>
            </SectionCard>
          )}

          {(registerBars.length > 0 || emotionBars.length > 0) && (
            <SectionCard title={t("publishers.tone.title")} info={t("publishers.tone.info")}>
              <View style={{ gap: 20 }}>
                {registerBars.length > 0 && (
                  <View>
                    <Txt size={12} weight="500" muted style={{ marginBottom: 8 }}>
                      {t("publishers.tone.registers")} · {t("publishers.tone.n", { n: p.registers!.n })}
                    </Txt>
                    <BarList items={registerBars} />
                  </View>
                )}
                {emotionBars.length > 0 && (
                  <View>
                    <Txt size={12} weight="500" muted style={{ marginBottom: 8 }}>
                      {t("publishers.tone.emotion")} · {t("publishers.tone.n", { n: p.emotion!.n })}
                    </Txt>
                    <BarList items={emotionBars} />
                  </View>
                )}
              </View>
            </SectionCard>
          )}

          <SectionCard
            title={t("publishers.recent.title")}
            action={
              <View style={styles.actions}>
                <Pressable accessibilityRole="link" onPress={() => navigate(`/search?publisher=${enc}`)} style={styles.inline}>
                  <Icon name="search" size={14} color={palette.primary} />
                  <Txt size={12} weight="500" color={palette.primary}>
                    {t("publishers.searchAll")}
                  </Txt>
                </Pressable>
                <Pressable accessibilityRole="link" onPress={() => navigate(`/stories?publisher=${enc}`)} style={styles.inline}>
                  <Icon name="newspaper" size={14} color={palette.primary} />
                  <Txt size={12} weight="500" color={palette.primary}>
                    {t("publishers.viewStories")}
                  </Txt>
                </Pressable>
              </View>
            }
          >
            <View style={{ gap: 12 }}>
              {p.recent.map((article) => (
                <ArticleRow key={article.id} article={article} />
              ))}
            </View>
          </SectionCard>
        </View>
      )}
    </View>
  );
}

/** Who controls the outlet: the registry's sourced TYPE, and the About merge's OWNER name. */
function OwnershipCard({ profile: p }: { profile: PublisherProfile }) {
  const { t, formatDate } = useTranslation();
  const { palette } = useTheme();
  if (!p.registry && !p.about) return null;
  const type = p.ownership;
  const owner = p.about?.parent;
  const ownerSource = p.about?.sources?.parent;

  return (
    <SectionCard title={t("publishers.ownership.title")} info={t("publishers.ownership.info")}>
      <View style={{ gap: 12 }}>
        <View>
          <Txt size={12} uppercase tracking={0.5} muted>
            {t("publishers.ownership.type")}
          </Txt>
          {type ? (
            <View style={[styles.inlineWrap, { marginTop: 2 }]}>
              <View style={[styles.dot, { backgroundColor: ownershipColor(type.value, palette) }]} />
              <Txt size={14} weight="500">
                {t(OWNERSHIP_LABEL_KEY[type.value])}
              </Txt>
              <Txt size={12} muted>
                {t("publishers.ownership.asOf", {
                  source: t(`publishers.ownership.source.${type.source}`),
                  date: formatDate(type.asOf, OWNERSHIP_DATE),
                })}
              </Txt>
            </View>
          ) : (
            <Txt size={14} muted style={{ marginTop: 2 }}>
              {t("publishers.ownership.notClassified")}
            </Txt>
          )}
        </View>
        <View>
          <Txt size={12} uppercase tracking={0.5} muted>
            {t("publishers.ownership.owner")}
          </Txt>
          {owner ? (
            <View style={[styles.inlineWrap, { marginTop: 2 }]}>
              <Txt size={14} weight="500">
                {owner}
              </Txt>
              {ownerSource && (
                <Txt size={12} muted>
                  {t(`publishers.about.source.${ownerSource}`)}
                </Txt>
              )}
            </View>
          ) : (
            <Txt size={14} muted style={{ marginTop: 2 }}>
              {t("publishers.ownership.unknownOwner")}
            </Txt>
          )}
        </View>
      </View>
    </SectionCard>
  );
}

/** Enriched publisher facts. Curated values win; Wikipedia/Wikidata fill gaps; each row names its source. */
function AboutCard({ about }: { about?: PublisherAbout }) {
  const { t, lang, formatDate } = useTranslation();
  const { palette } = useTheme();
  if (!about) return null;
  const rows = ABOUT_ROWS.filter((k) => about[k]);
  const hasFacts = rows.length > 0 || Boolean(about.country) || Boolean(about.website);
  if (!hasFacts && !about.description) return null;
  const source = (field: keyof NonNullable<PublisherAbout["sources"]>) => {
    const s = about.sources?.[field];
    return s ? t(`publishers.about.source.${s}`) : null;
  };

  return (
    <SectionCard title={t("publishers.about.title")} info={t("publishers.about.info")}>
      {about.description && (
        <Txt size={14} muted lineHeight={22} style={{ marginBottom: 16 }}>
          {about.description}
        </Txt>
      )}
      {hasFacts && (
        <View style={{ gap: 12 }}>
          {rows.map((key) => (
            <View key={key}>
              <Txt size={12} uppercase tracking={0.5} muted>
                {t(`publishers.about.${key}`)}
              </Txt>
              <View style={[styles.inlineWrap, { marginTop: 2 }]}>
                <Txt size={14} weight="500">
                  {about[key]}
                </Txt>
                {source(key) && <Txt size={12} muted>{source(key)}</Txt>}
              </View>
            </View>
          ))}
          {about.country && (
            <View>
              <Txt size={12} uppercase tracking={0.5} muted>
                {t("publishers.about.country")}
              </Txt>
              <View style={[styles.inlineWrap, { marginTop: 2 }]}>
                <Txt size={14} weight="500">
                  {countryName(about.country, lang)}
                </Txt>
                {source("country") && <Txt size={12} muted>{source("country")}</Txt>}
              </View>
            </View>
          )}
          {about.website && (
            <View>
              <Txt size={12} uppercase tracking={0.5} muted>
                {t("publishers.about.website")}
              </Txt>
              <View style={[styles.inlineWrap, { marginTop: 2 }]}>
                <Pressable accessibilityRole="link" onPress={() => openExternal(about.website!)}>
                  <Txt size={14} weight="500" color={palette.primary} numberOfLines={1}>
                    {about.website.replace(/^https?:\/\//, "")}
                  </Txt>
                </Pressable>
                {source("website") && <Txt size={12} muted>{source("website")}</Txt>}
              </View>
            </View>
          )}
        </View>
      )}
      <View style={[styles.aboutFooter, { borderTopColor: palette.border }]}>
        {about.wikipediaUrl && (
          <Pressable accessibilityRole="link" onPress={() => openExternal(about.wikipediaUrl!)} style={styles.inline}>
            <Txt size={12} color={palette.primary}>
              {t("publishers.about.wikipedia")}
            </Txt>
            <Icon name="external-link" size={12} color={palette.primary} />
          </Pressable>
        )}
        {about.status === "ok" && about.refreshedAt && (
          <Txt size={12} muted>
            {t("publishers.about.refreshed", { when: formatDate(about.refreshedAt, { dateStyle: "medium" }) })}
          </Txt>
        )}
        {(about.status === "ambiguous" || about.status === "no_match") && (
          <Txt size={12} muted>
            {t("publishers.about.unmatched")}
          </Txt>
        )}
      </View>
    </SectionCard>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "flex-start", gap: 16 },
  logoBox: { width: 48, height: 48, borderRadius: radius.lg, borderWidth: StyleSheet.hairlineWidth, alignItems: "center", justifyContent: "center", padding: 6 },
  titleRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  snapshot: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 8, rowGap: 2, marginTop: 4 },
  inline: { flexDirection: "row", alignItems: "center", gap: 4 },
  inlineWrap: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 8, rowGap: 4 },
  dot: { width: 8, height: 8, borderRadius: radius.pill },
  coRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  actions: { flexDirection: "row", alignItems: "center", gap: 12 },
  aboutFooter: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 12, rowGap: 4, borderTopWidth: StyleSheet.hairlineWidth, marginTop: 16, paddingTop: 12 },
});
