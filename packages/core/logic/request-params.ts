/**
 * A request's wire identity — the ONE place a params object becomes the record we actually send.
 *
 * `services` uses this record as the query string and `queryKeys` embeds the same record in the
 * cache key, so fetch identity and cache identity cannot disagree. That is the invariant the
 * frozen-country-filter bug violated: `services.search` sent `country` while the hand-enumerated
 * key didn't include it, so switching countries changed the request but not the key — React Query
 * saw "same key, data already cached" and never refetched until a reload emptied the cache.
 * Deriving both sides from one function makes that class of drift impossible: a param that
 * reaches the URL is part of the key, always, including params added later.
 *
 * Dropped values (`undefined`/`null`/`""`/`"all"`) are the "unfiltered" spellings — identical to
 * how the services already cleaned outgoing requests, so wire behavior is unchanged. Caller
 * property order never matters: React Query hashes object key-parts with their keys sorted.
 * Pure and DOM-free (runs under `node --test`).
 */
export function requestParams(params: object): Record<string, string> {
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== "all") clean[k] = String(v);
  }
  return clean;
}
