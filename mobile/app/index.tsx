import { useQuery } from "@tanstack/react-query";
import { Link } from "expo-router";
import { useMemo } from "react";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View, useColorScheme } from "react-native";

import type { Recommendation, Settings } from "@ih/core/domain/types";
import { services } from "@ih/core/api/services";
import { partitionByCountryMatch } from "@ih/core/logic/country-partition";
import { countryName } from "@ih/core/logic/countries";

import { RecommendationCard } from "@/components/recommendation-card";
import { dark, light, radius, space, type as typeScale } from "@/design/tokens";
import { deviceLang, translatorFor } from "@/lib/i18n";
import { currentToken } from "@/lib/session";

/**
 * Recommendations — the first real screen.
 *
 * Everything with a decision in it comes from `@ih/core`:
 *
 *   services.recommendations()   the same typed call the web makes, over the same axios instance
 *   partitionByCountryMatch      the For You country split — matched cards, then backfill
 *   countryName                  the localized country label
 *   presentRecommendation        (inside the card) what each card may claim
 *
 * The only thing this file decides is layout. That is the test of whether the Phase 2 split worked:
 * if the screen needed its own copy of any rule, the rule was in the wrong package.
 */
export default function RecommendationsScreen() {
  const scheme = useColorScheme();
  const palette = scheme === "dark" ? dark : light;
  const lang = deviceLang();
  const t = useMemo(() => translatorFor(lang), [lang]);
  const signedIn = currentToken() !== null;

  const feed = useQuery({
    queryKey: ["recommendations"],
    queryFn: () => services.recommendations(),
    enabled: signedIn,
  });

  // Settings carry three of the four things this screen has to honour: `interests` (Interest
  // Intensity), `recommendationCountry` (the For You country) and `politicalOpenness` (which the
  // engine maps to the RWE-B epsilon behind Political Viewpoint Diversity). All three are applied
  // ENGINE-SIDE — the feed arrives already ranked by them. What the screen needs them for is
  // saying so, which is the difference between a personalised feed and one a reader can see is
  // personalised.
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => services.settings() as Promise<Settings>,
    enabled: signedIn,
  });

  if (!signedIn) return <SignedOut palette={palette} />;

  if (feed.isLoading) {
    return (
      <View style={[styles.centre, { backgroundColor: palette.background }]}>
        <ActivityIndicator color={palette.primary} />
      </View>
    );
  }

  if (feed.isError) {
    return (
      <View style={[styles.centre, { backgroundColor: palette.background }]}>
        <Text style={[typeScale.body, { color: palette.mutedForeground }]}>
          {t("states.error.body")}
        </Text>
      </View>
    );
  }

  const all = (feed.data ?? []) as Recommendation[];
  const country = settings.data?.recommendationCountry ?? null;

  // `partitionByCountryMatch` returns ONE ordered list plus the index where backfill begins —
  // `{ ordered, firstBackfill }`, with `-1` meaning there is no boundary to draw (no country
  // selected, or the country filled every slot). An earlier draft of this screen assumed
  // `{ matched, backfill }` and had to invent the ordering itself; reading the real signature is
  // both correct and less code, and the ordering stays the one place the web reads it from.
  const { ordered, firstBackfill } = partitionByCountryMatch(all);

  const rows: Array<{ kind: "card"; rec: Recommendation } | { kind: "divider" }> = [];
  ordered.forEach((rec, i) => {
    if (i === firstBackfill) rows.push({ kind: "divider" });
    rows.push({ kind: "card", rec });
  });

  return (
    <FlatList
      style={{ backgroundColor: palette.background }}
      contentContainerStyle={styles.list}
      data={rows}
      keyExtractor={(row, i) => (row.kind === "card" ? row.rec.article.id : `divider-${i}`)}
      ListHeaderComponent={
        <Header palette={palette} country={country} settings={settings.data} lang={lang} />
      }
      ListEmptyComponent={<EmptyFeed palette={palette} t={t} />}
      ItemSeparatorComponent={() => <View style={{ height: space.md }} />}
      renderItem={({ item }) =>
        item.kind === "divider" ? (
          // The web's exact wording, keyed the same way: `rec.backfill.after` when a country is
          // selected (it interpolates the country name), `rec.backfill.generic` otherwise. An
          // earlier draft invented `rec.country.backfill`, which no catalog defines — the divider
          // would have read "rec.country.backfill" on screen, because makeT's last fallback is the
          // key itself. lib/catalog-keys.test.ts now fails on that class of mistake.
          <Text style={[typeScale.caption, { color: palette.mutedForeground, marginTop: space.sm }]}>
            {country
              ? t("rec.backfill.after", { country: countryName(country, lang) })
              : t("rec.backfill.generic")}
          </Text>
        ) : (
          <RecommendationCard rec={item.rec} palette={palette} t={t} lang={lang} />
        )
      }
    />
  );
}

/**
 * The header states what shaped this feed.
 *
 * Not decoration: a recommendation carries a claim about the reader's diet, and a reader who cannot
 * see which preferences produced it has no way to judge the claim. The web shows the same three.
 */
function Header({
  palette,
  country,
  settings,
  lang,
}: {
  palette: typeof light;
  country: string | null;
  settings?: Settings;
  lang: string;
}) {
  // The topics the reader pushed away from the neutral middle (5). All-5 is the untouched feed, so
  // listing those would say nothing.
  const nudged = Object.entries(settings?.interests ?? {})
    .filter(([, v]) => typeof v === "number" && v !== 5)
    .sort((a, b) => (b[1] as number) - (a[1] as number))
    .slice(0, 3)
    .map(([k]) => k);

  return (
    <View style={{ gap: space.sm, marginBottom: space.lg }}>
      <Text style={[typeScale.display, { color: palette.foreground }]}>For you</Text>
      <View style={styles.chips}>
        {country ? (
          <Chip palette={palette} label={countryName(country, lang)} />
        ) : (
          <Chip palette={palette} label="Global" />
        )}
        {settings?.politicalOpenness != null ? (
          <Chip palette={palette} label={`Openness ${settings.politicalOpenness}`} />
        ) : null}
        {nudged.map((topic) => (
          <Chip key={topic} palette={palette} label={topic} />
        ))}
      </View>
    </View>
  );
}

function Chip({ palette, label }: { palette: typeof light; label: string }) {
  return (
    <View style={[styles.chip, { borderColor: palette.border, backgroundColor: palette.muted }]}>
      <Text style={[typeScale.label, { color: palette.mutedForeground }]}>{label}</Text>
    </View>
  );
}

/**
 * An empty feed is a real answer, not an error.
 *
 * The engine returns `[]` to a signed-in reader it cannot yet recommend for, rather than handing
 * them the showcase feed — every card carries "this offers another political perspective", which is
 * a claim about a diet it has not measured. Saying so is more honest than filling the screen.
 */
function EmptyFeed({ palette, t }: { palette: typeof light; t: (k: string) => string }) {
  return (
    <View style={[styles.centre, { paddingVertical: space.xxl }]}>
      <Text style={[typeScale.title, { color: palette.foreground }]}>{t("rec.empty.title")}</Text>
      <Text
        style={[typeScale.body, { color: palette.mutedForeground, textAlign: "center", marginTop: space.sm }]}
      >
        {t("rec.empty.body")}
      </Text>
    </View>
  );
}

function SignedOut({ palette }: { palette: typeof light }) {
  return (
    <View style={[styles.centre, { backgroundColor: palette.background, gap: space.lg }]}>
      <Text style={[typeScale.display, { color: palette.foreground }]}>Hidden View</Text>
      <Text style={[typeScale.body, { color: palette.mutedForeground, textAlign: "center" }]}>
        A health check for your news diet.
      </Text>
      <Link href="/sign-in" asChild>
        <Pressable style={[styles.cta, { backgroundColor: palette.primary }]}>
          <Text style={[typeScale.headline, { color: palette.primaryForeground }]}>Sign in</Text>
        </Pressable>
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  list: { padding: space.lg, paddingBottom: space.xxl },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  chip: {
    paddingHorizontal: space.md,
    paddingVertical: space.xs,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
  },
  cta: { paddingHorizontal: space.xl, paddingVertical: space.md, borderRadius: radius.md },
});
