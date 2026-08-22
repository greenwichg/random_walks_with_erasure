import type { Settings } from "../domain/types.ts";

/**
 * The minimal settings PATCH — only the fields that differ between `base` (the server truth) and
 * `draft` (the reader's edits). A one-level nested group (e.g. `notifications`) is reduced to just
 * its changed sub-fields, which the engine's `normalize_settings` deep-merges. Pure, non-mutating,
 * and deterministic (keys emitted in `draft`'s own order); an unchanged pair yields `{}`, which the
 * caller treats as "nothing to save".
 *
 * Not React-aware and dependency-free (a type-only import) so it runs under `node --test`. Note it
 * is intentionally generic over the whole shape — the settings page separately excludes `theme`
 * (owned by its own instant write-through), so this helper carries no special cases.
 */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * The changed sub-fields of one nested group. Recurses through nested groups, so a change to a
 * single leaf ships as that single leaf: `notifications.categories.breaking.inApp` sends
 * `{notifications: {categories: {breaking: {inApp: false}}}}` rather than the whole 4x2 matrix.
 *
 * Minimality is not cosmetic here — it is what makes two clients safe to edit at once. The engine
 * deep-merges a patch over the reader's stored preferences, so a patch that restates unchanged
 * siblings would silently overwrite a change made on another device between this page's load and
 * its save.
 */
function diffGroup(
  base: Record<string, unknown>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(draft)) {
    const b = base[key];
    const d = draft[key];
    if (isPlainObject(b) && isPlainObject(d)) {
      const sub = diffGroup(b, d);
      if (Object.keys(sub).length > 0) out[key] = sub;
    } else if (!Object.is(b, d)) {
      out[key] = d;
    }
  }
  return out;
}

export function diffSettings(base: Settings, draft: Settings): Partial<Settings> {
  // Built as a loose record (indexed writes through a `keyof` variable don't narrow on Partial),
  // then cast once at the boundary — the values assigned are always the draft's own typed values.
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(draft) as (keyof Settings)[]) {
    const b = base[key];
    const d = draft[key];
    if (isPlainObject(b) && isPlainObject(d)) {
      const sub = diffGroup(b, d);
      if (Object.keys(sub).length > 0) out[key as string] = sub;
    } else if (Array.isArray(b) || Array.isArray(d)) {
      // Arrays (e.g. `locations`) diff by VALUE: a draft that rebuilds an identical array is not
      // a change, and a changed array ships whole (the engine replaces, not merges, lists).
      if (JSON.stringify(b ?? null) !== JSON.stringify(d ?? null)) out[key as string] = d;
    } else if (!Object.is(b, d)) {
      out[key as string] = d;
    }
  }
  return out as Partial<Settings>;
}

/** Whether a computed patch has anything to send (a shared, readable guard for the save flow). */
export function hasChanges(patch: Partial<Settings>): boolean {
  return Object.keys(patch).length > 0;
}
