import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { PublisherProfile } from "@ih/core/domain/types";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { openExternal } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** Day precision: a rater's revision cadence is months. */
const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

/**
 * A rater's factuality verdict, shown with its attribution rather than as our own claim: never a
 * value without who issued it and when, never a level for an unrated outlet (explicit "not
 * rated"), never a paraphrase of the rater's own vocabulary. The link goes to the rater's listing.
 */
export function FactualityBadge({ factuality }: { factuality?: PublisherProfile["factuality"] }) {
  const { t, formatDate } = useTranslation();
  const { palette } = useTheme();

  if (!factuality) return <Badge variant="outline">{t("publishers.factuality.notRated")}</Badge>;

  const { value, source, asOf, ratingUrl } = factuality;
  const level = t(`publishers.factuality.level.${value}`);
  const sourceName = t(`publishers.factuality.source.${source}`);
  const date = formatDate(asOf, DATE_OPTS);

  return (
    <View style={styles.row}>
      <Badge variant="secondary">{t("publishers.factuality.value", { level })}</Badge>
      <Pressable
        accessibilityRole="link"
        accessibilityLabel={t("publishers.factuality.attribution.full", { level, source: sourceName, date })}
        onPress={() => openExternal(ratingUrl)}
        style={styles.link}
      >
        <Txt size={12} muted>
          {t("publishers.factuality.attribution", { source: sourceName, date })}
        </Txt>
        <Icon name="external-link" size={12} color={palette.mutedForeground} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 },
  link: { flexDirection: "row", alignItems: "center", gap: 4 },
});
