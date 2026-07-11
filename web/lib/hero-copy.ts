/**
 * Dashboard hero copy keyed by Information Health band — the single place that maps a health band
 * to its headline + supporting-text catalog keys, so the headline, status badge, and description
 * all follow the SAME thresholds as `scoreBand()` (lib/metrics). The caller passes
 * `scoreBand(score).label`; this returns the keys. Only the "Healthy" band gets the positive
 * headline — every other (or unknown) band degrades to a non-positive message, so a low score can
 * never render "looking healthy". Pure, no imports — runnable under `node --test`.
 */
export interface HeroCopyKeys {
  title: string;
  body: string;
}

export function heroCopyKeys(bandLabel: string): HeroCopyKeys {
  switch (bandLabel) {
    case "Healthy":
      return { title: "dashboard.hero.healthy.title", body: "dashboard.hero.healthy.body" };
    case "Fair":
      return { title: "dashboard.hero.fair.title", body: "dashboard.hero.fair.body" };
    default:
      // "Needs work" and any unknown/low band — conservative, never the positive headline.
      return { title: "dashboard.hero.needsWork.title", body: "dashboard.hero.needsWork.body" };
  }
}
