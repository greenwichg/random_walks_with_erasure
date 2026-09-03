/**
 * Interest follow — the honest reading of a "+ / ✓" control on a topic.
 *
 * The reference mobile layout puts a follow toggle on every topic chip. Hidden View has exactly
 * one contract that can back that: `Settings.interests`, the eight Interest Intensity sliders
 * (1–10, 5 = neutral) the engine re-ranks recommendations with (`_interest_rerank`). They REORDER,
 * they never filter — so "following" a topic here means "surface more of it", which is what the
 * slider already means and what the settings copy already promises.
 *
 * The catalog's topics are open (whatever the corpus carries: Politics, World, Arts, …); the
 * interest keys are a closed set of eight. A topic outside them has nothing to nudge, so
 * `interestForTopic` returns null and the UI renders NO toggle rather than a control that would
 * silently do nothing — the same rule that keeps a Follow button off the publisher spotlight.
 *
 * No React, no imports: runs under `node --test`.
 */
import type { InterestIntensity } from "../domain/types.ts";

export const INTEREST_KEYS = [
  "business",
  "technology",
  "science",
  "health",
  "climate",
  "sports",
  "entertainment",
  "artsCulture",
] as const;

export type InterestKey = (typeof INTEREST_KEYS)[number];

/** The untouched feed. Settings' own default for every slider. */
export const INTEREST_NEUTRAL = 5;
/** What "follow" sets a slider to — a nudge up, not the maximum: the reader can still tune it
 *  precisely in Settings, and a follow that slammed the slider to 10 would overwrite that. */
export const INTEREST_FOLLOWED = 8;

/** Catalog topic labels that mean the same interest area. Matched on a normalised form, so
 *  "Arts & Culture", "arts-culture" and "ARTS" all land on `artsCulture`. */
const TOPIC_ALIASES: Record<string, InterestKey> = {
  business: "business",
  economy: "business",
  finance: "business",
  technology: "technology",
  tech: "technology",
  science: "science",
  health: "health",
  climate: "climate",
  environment: "climate",
  sports: "sports",
  sport: "sports",
  entertainment: "entertainment",
  arts: "artsCulture",
  culture: "artsCulture",
  artsculture: "artsCulture",
  artsandculture: "artsCulture",
  artsentertainment: "artsCulture",
};

const normalise = (topic: string) => topic.toLowerCase().replace(/[^a-z]/g, "");

/** The interest slider a catalog topic maps onto, or null when following it would do nothing. */
export function interestForTopic(topic: string | null | undefined): InterestKey | null {
  if (!topic) return null;
  return TOPIC_ALIASES[normalise(topic)] ?? null;
}

/** Whether an interest is currently boosted above neutral — the "✓" state. */
export function isFollowedInterest(interests: InterestIntensity | undefined, key: InterestKey): boolean {
  return (interests?.[key] ?? INTEREST_NEUTRAL) > INTEREST_NEUTRAL;
}

/**
 * The interests object after toggling one key. Following sets the nudge; UNfollowing returns the
 * slider to neutral rather than below it — "not following" is an absence of a boost, not a
 * suppression, and the engine never hides a topic either way.
 */
export function toggleInterest(
  interests: InterestIntensity,
  key: InterestKey,
): InterestIntensity {
  const following = isFollowedInterest(interests, key);
  return { ...interests, [key]: following ? INTEREST_NEUTRAL : INTEREST_FOLLOWED };
}
