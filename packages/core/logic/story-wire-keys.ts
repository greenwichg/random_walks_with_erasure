/**
 * The Stories wire params the /api/stories PROXY forwards to the engine — the third copy of the
 * request identity (client cache key, proxy whitelist, engine signature). The client side is
 * ratcheted by request-params.test.ts; this list is pinned by the SAME test against
 * Required<StoryQuery>, so a new StoryQuery field that isn't forwarded here fails the suite
 * instead of being silently dropped (the M3 blindspot param was lost exactly this way —
 * filter chip applied, engine never told).
 */
export const STORY_WIRE_KEYS = [
  "topic",
  "publisher",
  "lean",
  "country",
  "blindspot",
  "type",
  "dateFrom",
  "dateTo",
  "sort",
  "limit",
  "offset",
  "debug",
] as const;
