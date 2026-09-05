import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { Article, EmotionShare, Lean, LeanBucket, Register } from "@ih/core/domain/types";
import { leanBucket, leanLabelKey } from "@ih/core/logic/political";

import { Badge } from "@/components/ui/badge";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { emotionColor } from "@/lib/meta";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { PublisherLogo } from "./publisher-logo";

/** Political viewpoint pill, coloured by bucket. Unknown lean → "Unknown", never Center (L2.2). */
export function LeanBadge({ lean, bucket }: { lean?: Lean | null; bucket?: LeanBucket | null }) {
  const { t } = useTranslation();
  if (lean == null) return <Badge variant="secondary">{t("lean.unknown")}</Badge>;
  return <Badge variant={bucket ?? leanBucket(lean)}>{t(leanLabelKey(lean))}</Badge>;
}

/** Publisher with its own house-lean dot and an optional logo. The name opens the profile. */
export function PublisherBadge({
  name,
  lean,
  logo,
  logoFallbacks,
}: {
  name: string;
  lean?: Lean | null;
  logo?: string;
  logoFallbacks?: string[];
}) {
  const { palette } = useTheme();
  const bucket = lean == null ? null : leanBucket(lean);
  return (
    <View style={styles.publisher}>
      <PublisherLogo logo={logo} fallbacks={logoFallbacks} sizePx={14} style={{ borderRadius: 3 }} />
      <Pressable accessibilityRole="link" onPress={() => navigate(`/publishers/${encodeURIComponent(name)}`)} hitSlop={6}>
        <Txt size={12} weight="500" muted>
          {name}
        </Txt>
      </Pressable>
      {bucket && <View style={[styles.dot, { backgroundColor: palette[bucket] }]} />}
    </View>
  );
}

/** Confidence pill (top-2 softmax margin from the backend). */
export function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const variant = value >= 0.75 ? "positive" : value >= 0.5 ? "caution" : "secondary";
  return (
    <Badge variant={variant} icon="gauge">
      {`${pct}%`}
    </Badge>
  );
}

/** Dominant-emotion pill. */
export function EmotionBadge({ emotion, dominant }: { emotion: EmotionShare; dominant?: keyof EmotionShare }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const key =
    dominant ??
    (Object.keys(emotion) as (keyof EmotionShare)[]).reduce((a, b) => (emotion[a] >= emotion[b] ? a : b));
  const color = emotionColor(key, palette);
  return (
    <View style={[styles.emotion, { backgroundColor: alpha(color, 0.12) }]}>
      <Txt size={12} weight="500" color={color} lineHeight={16}>
        {t(`emotion.${key}`)}
      </Txt>
    </View>
  );
}

/** Reporting vs opinion pill. */
export function RegisterBadge({ register }: { register: Register }) {
  const { t } = useTranslation();
  const icon = register === "opinion" ? "quote" : "file-text";
  return (
    <Badge variant={register === "reporting" ? "positive" : "secondary"} icon={icon}>
      {t(`register.${register}`)}
    </Badge>
  );
}

/** A compact row of an article's key attributes. Absent signals render nothing (L2.2). */
export function ArticleAttributes({ article }: { article: Article }) {
  return (
    <View style={styles.attributes}>
      <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      {article.register && <RegisterBadge register={article.register} />}
      {article.emotion && <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion ?? undefined} />}
      {article.confidence != null && <ConfidenceBadge value={article.confidence} />}
    </View>
  );
}

const styles = StyleSheet.create({
  publisher: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 6, height: 6, borderRadius: radius.pill },
  emotion: { borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2, alignSelf: "flex-start" },
  attributes: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 },
});
