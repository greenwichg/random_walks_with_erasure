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
  limit?: number;
};   // type alias, not interface: the implicit index signature keeps it Record-assignable

export const discoverKey = (filters?: DiscoverFilters) =>
  [
    "discover",
    filters?.topic ?? "all",
    filters?.publisher ?? "all",
    filters?.lean ?? "all",
    filters?.country ?? "all",
    filters?.limit ?? "default",
  ] as const;
