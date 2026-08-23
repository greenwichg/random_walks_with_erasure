import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import type { Recommendation } from "@ih/core/domain/types";
import { presentRecommendation } from "@ih/core/logic/rec-presentation";
import { countryName } from "@ih/core/logic/countries";
import type { TFunction } from "@ih/core/i18n/core";

import { leanColor, radius, space, type as typeScale, type Palette } from "@/design/tokens";

/** The same mapping the web uses, so both platforms name a strategy identically. */
const STRATEGY_LABEL_KEY: Record<Recommendation["strategy"], string> = {
  "rwe-b": "rec.strategy.rwe-b",
  "rwe-d": "rec.strategy.rwe-d",
  adaptive: "rec.strategy.adaptive",
  story: "rec.strategy.story",
  emerging: "rec.strategy.emerging",
  blindspot: "rec.strategy.blindspot",
};

/**
 * One recommendation, rendered natively.
 *
 * **Every claim on this card comes from `@ih/core`, and none of the logic is reimplemented here.**
 * `presentRecommendation` decides what the card may say — it returns catalog KEYS and typed
 * parameter refs, never rendered strings — and `TFunction` (also from core, built over the same five
 * catalogs the web ships) turns them into a sentence. So a card's explanation reads identically on
 * both platforms because it is literally the same function over the same catalog, not because two
 * implementations were kept in step.
 *
 * That is the split working as designed: the web renders those keys into `<span>`s, this renders
 * them into `<Text>`, and the only difference between the two files is which elements they use.
 */
export function RecommendationCard({
  rec,
  palette,
  t,
  lang,
  onOpen,
}: {
  rec: Recommendation;
  palette: Palette;
  t: TFunction;
  lang: string;
  onOpen?: (rec: Recommendation) => void;
}) {
  const presentation = presentRecommendation(rec.explanation);
  const lean = leanColor(rec.article.leanBucket, palette);

  // The reader fact is the bold slot when it exists; the contribution takes over when it does not.
  // Both are `PartRef`s — a catalog key plus its params — so neither can carry a sentence the
  // resolver did not sanction.
  const primary = presentation.reader ?? presentation.contribution;
  const secondary = presentation.reader ? presentation.contribution : null;

  const open = () => {
    onOpen?.(rec);
    if (rec.article.url) void Linking.openURL(rec.article.url);
  };

  return (
    <Pressable
      onPress={open}
      accessibilityRole="button"
      accessibilityLabel={rec.article.headline}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: palette.card, borderColor: palette.border },
        pressed && { opacity: 0.7 },
      ]}
    >
      <View style={styles.metaRow}>
        <Text style={[typeScale.label, { color: palette.mutedForeground }]} numberOfLines={1}>
          {rec.article.publisher.toUpperCase()}
        </Text>
        {/* No lean chip when the outlet is unrated. Rendering one as Center would be a fabricated
            claim about a publisher's politics — the same rule the web follows (L2.2). */}
        {lean ? (
          <View style={[styles.leanDot, { backgroundColor: lean }]} accessibilityElementsHidden />
        ) : null}
        {rec.countryMatch === false ? (
          <Text style={[typeScale.label, { color: palette.mutedForeground }]}>
            {countryName(rec.article.country ?? "", lang)}
          </Text>
        ) : null}
      </View>

      <Text style={[typeScale.headline, { color: palette.foreground }]} numberOfLines={3}>
        {rec.article.headline}
      </Text>

      {presentation.claimKey ? (
        <Text style={[typeScale.caption, styles.claim, { color: palette.primary }]}>
          {t(presentation.claimKey)}
        </Text>
      ) : null}

      {primary ? (
        <Text style={[typeScale.body, styles.explanation, { color: palette.foreground }]}>
          {t(primary.key, primary.params)}
        </Text>
      ) : null}

      {secondary ? (
        <Text style={[typeScale.caption, { color: palette.mutedForeground }]}>
          {t(secondary.key, secondary.params)}
        </Text>
      ) : null}

      {/* The strategy badge, keyed exactly as the web keys it. The first draft of this file invented
          `rec.badge.crossCutting`, which does not exist in any catalog — it would have rendered the
          key itself on screen, since `makeT`'s last fallback is the key rather than a blank. Reading
          the web's `STRATEGY_LABEL_KEY` is what a shared catalog is for. */}
      <View
        style={[
          styles.badge,
          { borderColor: rec.crossCutting ? palette.right : palette.border },
        ]}
      >
        <Text
          style={[
            typeScale.label,
            { color: rec.crossCutting ? palette.right : palette.mutedForeground },
          ]}
        >
          {t(STRATEGY_LABEL_KEY[rec.strategy])}
        </Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.lg,
    padding: space.lg,
    gap: space.sm,
  },
  metaRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
  leanDot: { width: 8, height: 8, borderRadius: radius.pill },
  claim: { marginTop: space.xs },
  explanation: { marginTop: space.xs },
  badge: {
    alignSelf: "flex-start",
    marginTop: space.xs,
    paddingHorizontal: space.sm,
    paddingVertical: space.xs,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
  },
});
