import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { Article } from "@ih/core/domain/types";

import { LeanBadge, PublisherBadge } from "@/components/shared/article-badges";
import { CardImage } from "@/components/shared/card-image";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { Card } from "@/components/ui/card";
import { Txt } from "@/components/ui/text";
import { useTranslation } from "@/lib/i18n-context";

/**
 * A Discover article card — one live article: the always-occupied image slot, headline,
 * publisher · topic · time, summary, lean, and the shared Read + Save controls. Search and Saved
 * render it; the image slot is the same `CardImage` every story card fronts (`imageSuspect` treated
 * as absence).
 */
export function DiscoverCard({
  article,
  openedFrom = "discover",
  leanDot = true,
}: {
  article: Article;
  openedFrom?: string;
  leanDot?: boolean;
}) {
  const { timeAgo } = useTranslation();
  const when = timeAgo(article.publishedAt);
  return (
    <Card style={styles.card}>
      <CardImage src={article.image} suspect={article.imageSuspect} accessibilityLabel={article.headline} style={{ marginBottom: 12 }} />
      <Txt display weight="600" size={17} lineHeight={22} tight accessibilityRole="header">
        {article.headline}
      </Txt>
      <View style={styles.meta}>
        <PublisherBadge
          name={article.publisher}
          lean={leanDot ? article.publisherLean : null}
          logo={article.publisherLogo}
          logoFallbacks={article.publisherLogoFallbacks}
        />
        {article.topic ? <Txt size={12} weight="500" muted>{`· ${article.topic}`}</Txt> : null}
        {when ? <Txt size={12} muted>{`· ${when}`}</Txt> : null}
      </View>
      {article.description ? (
        <Txt size={14} muted numberOfLines={3} style={{ marginTop: 8 }}>
          {article.description}
        </Txt>
      ) : null}
      <View style={styles.badges}>
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      </View>
      <View style={styles.actions}>
        <ReadArticleButton article={article} openedFrom={openedFrom} />
        <SaveButton article={article} />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: { padding: 20 },
  meta: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: 8 },
  badges: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, marginTop: 12 },
  actions: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 16 },
});
