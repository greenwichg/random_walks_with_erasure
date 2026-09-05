import * as React from "react";
import { StyleSheet, View } from "react-native";

import { countryName } from "@ih/core/logic/countries";

import { ArticleRow } from "@/components/shared/article-row";
import { SectionHeader } from "@/components/shared/section-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { useSearch, useSettings } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * "From your places" — the home page's location module. Reads the reader's own places from
 * settings (edition first, then the first followed country) and shows the located catalog's latest
 * coverage for that place. Graceful fallbacks, in order: no place → a setup pointer; no coverage →
 * the honest empty note; a request that FAILED → a failure note with a retry, never silence.
 */
export function LocalPulse() {
  const { t, lang } = useTranslation();
  const { palette } = useTheme();
  const settings = useSettings();
  const place =
    settings.data?.edition ??
    settings.data?.locations?.find((l) => l.level === "country")?.placeId ??
    null;

  const articles = useSearch({ country: place ?? undefined, sort: "newest", limit: 3 }, place != null);

  return (
    <Card>
      <SectionHeader
        title={place ? t("home.pulse.title", { place: countryName(place, lang) }) : t("home.pulse.setupTitle")}
        href={place ? `/stories?country=${encodeURIComponent(place)}` : "/stories"}
        actionLabel={t("home.viewAll")}
        style={{ marginBottom: 12 }}
      />

      {place == null && (
        <Txt size={12} muted lineHeight={18}>
          {t("home.pulse.setupBody")}{" "}
          <Txt size={12} weight="500" color={palette.primary} onPress={() => navigate("/settings")}>
            {t("nav.settings")}
          </Txt>
        </Txt>
      )}

      {place != null && articles.isError && (
        <View style={styles.errorRow}>
          <View style={styles.note}>
            <Icon name="alert-circle" size={14} color={palette.destructive} />
            <Txt size={12} muted>
              {t("states.error.body")}
            </Txt>
          </View>
          <Button variant="ghost" size="sm" onPress={() => void articles.refetch()}>
            {t("common.tryAgain")}
          </Button>
        </View>
      )}

      {place != null && articles.data && articles.data.results.length === 0 && (
        <View style={styles.note}>
          <Icon name="map-pin" size={14} color={palette.mutedForeground} />
          <Txt size={12} muted>
            {t("local.noArticles.body")}
          </Txt>
        </View>
      )}

      {place != null && articles.data && articles.data.results.length > 0 && (
        <View style={{ gap: 8 }}>
          {articles.data.results.map((a) => (
            <ArticleRow key={a.id} article={a} />
          ))}
        </View>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  errorRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  note: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
});
