/**
 * Discover request identity — the one place the Discover filter set is named.
 *
 * `discoverKey` builds the React Query cache key for /api/discover. Every filter the service
 * sends MUST appear as a segment here: a filter missing from the key is the frozen-filter bug
 * (switching it would serve the previous selection's cached response). The companion test file
 * ratchets this with a `Required<DiscoverFilters>` sample — adding a field to the type fails
 * typecheck there until the sample (and therefore this key) names it.
 */
export type DiscoverFilters = {
  topic?: string;
  publisher?: string;
  lean?: string;
  /** EVENT geography (ISO 3166-1 alpha-2) — same semantics as the Stories country filter. */
  country?: string;
  /** Curated SOURCE type: "news" | "research" | "community" — articles from a publisher the outlet
   *  registry classifies that way. A publisher the registry does not carry matches no type. */
  type?: string;
  limit?: number;
};   // type alias, not interface: the implicit index signature keeps it Record-assignable

export const discoverKey = (filters?: DiscoverFilters) =>
  [
    "discover",
    filters?.topic ?? "all",
    filters?.publisher ?? "all",
    filters?.lean ?? "all",
    filters?.country ?? "all",
    filters?.type ?? "all",
    filters?.limit ?? "default",
  ] as const;

/**
 * The Discover wire params the /api/discover PROXY forwards to the engine — the third copy of the
 * request identity, alongside `discoverKey` above and the engine's own signature.
 *
 * Extracted from the route file and pinned here for the reason the Stories list already documents:
 * a param the proxy does not forward is silently dropped, and the filter then applies in the UI
 * while the engine is never told. `discoverKey` was ratcheted against `Required<DiscoverFilters>`
 * and this list was not, so that hole stayed open on this surface until the Type filter walked
 * into it. The companion test now pins BOTH against the same type.
 */
export const DISCOVER_WIRE_KEYS = [
  "topic",
  "publisher",
  "lean",
  "country",
  "type",
  "limit",
] as const;
