/**
 * BA1 — Beta access control (allowlist). A pure, testable gate that decides whether a given email may
 * authenticate during the closed beta. Enforced in the NextAuth `signIn` callback (`lib/auth.ts`), so a
 * non-approved user never gets a session — and therefore never an engine account. This is a **launch
 * operation**, not a product feature: it touches authentication only, and changes nothing in the
 * recommendation engine, analytics (PA1), observability (OBS1), or business logic.
 *
 * Configuration (operator-editable — see docs/BETA_ACCESS_CONTROL.md):
 *   BETA_ALLOWLIST        comma / newline / semicolon separated approved emails and/or `@domain` entries
 *                         (`#` starts a comment; case-insensitive; whitespace trimmed)
 *   BETA_ALLOWLIST_FILE   optional path to a file of the same format (appended to BETA_ALLOWLIST), so a
 *                         long list can be edited without a redeploy
 *   BETA_ACCESS_ENABLED   `1`/`0` to force the gate on/off; defaults to ON in production (RWE_ENV)
 *
 * Fail-closed: when the gate is enabled but the allowlist is empty, **everyone is denied** (a
 * misconfigured deploy stays private rather than accidentally opening) — the caller logs a loud warning.
 * When the gate is disabled (local dev), everyone is allowed, so development stays zero-config.
 */
import { readFileSync } from "node:fs";

export interface AllowEntry {
  kind: "email" | "domain";
  value: string;
}

export type AccessReason =
  | "disabled" // gate off (dev) → allow
  | "allowlisted"
  | "no_email"
  | "empty_allowlist" // gate on but nothing configured → deny all (fail-closed)
  | "not_allowlisted";

export interface AccessResult {
  allowed: boolean;
  reason: AccessReason;
}

type Env = Record<string, string | undefined>;

/** Parse a raw allowlist string into normalized entries. Splits on comma/newline/semicolon, lowercases,
 *  trims, skips blanks and `#` comments, and classifies `@domain.com` as a domain entry. */
export function parseAllowlist(raw: string | null | undefined): AllowEntry[] {
  if (!raw) return [];
  return raw
    .split(/[\n,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0 && !s.startsWith("#"))
    .map<AllowEntry>((s) => (s.startsWith("@") ? { kind: "domain", value: s.slice(1) } : { kind: "email", value: s }))
    .filter((e) => e.value.length > 0);
}

/** Whether `email` matches any allowlist entry (exact email, or its domain via an `@domain` entry). */
export function matches(email: string, entries: AllowEntry[]): boolean {
  const e = email.trim().toLowerCase();
  const at = e.lastIndexOf("@");
  if (at <= 0 || at === e.length - 1) return false; // not a plausible address
  const domain = e.slice(at + 1);
  return entries.some((x) => (x.kind === "email" ? x.value === e : x.value === domain));
}

/** Load the allowlist from BETA_ALLOWLIST (+ BETA_ALLOWLIST_FILE, appended). A missing file is ignored. */
export function loadAllowlist(env: Env = process.env): AllowEntry[] {
  const parts: string[] = [];
  if (env.BETA_ALLOWLIST) parts.push(env.BETA_ALLOWLIST);
  if (env.BETA_ALLOWLIST_FILE) {
    try {
      parts.push(readFileSync(env.BETA_ALLOWLIST_FILE, "utf8"));
    } catch {
      /* missing/unreadable file → ignore; the env var (if any) still applies */
    }
  }
  return parseAllowlist(parts.join("\n"));
}

/** Whether the beta gate is active. Explicit BETA_ACCESS_ENABLED wins; otherwise ON in production. */
export function betaAccessEnabled(env: Env = process.env): boolean {
  const flag = env.BETA_ACCESS_ENABLED;
  if (flag != null && flag.trim() !== "") {
    return ["1", "true", "yes", "on"].includes(flag.trim().toLowerCase());
  }
  return env.RWE_ENV === "production" || env.RWE_ENV === "prod";
}

/**
 * The access decision for `email`. Allowed when the gate is disabled (dev), or when the gate is enabled
 * and the email matches the configured allowlist. Denied (fail-closed) when enabled with an empty
 * allowlist, a missing email, or no match.
 */
export function isEmailAllowed(email: string | null | undefined, env: Env = process.env): AccessResult {
  if (!betaAccessEnabled(env)) return { allowed: true, reason: "disabled" };
  if (!email) return { allowed: false, reason: "no_email" };
  const entries = loadAllowlist(env);
  if (entries.length === 0) return { allowed: false, reason: "empty_allowlist" };
  return matches(email, entries)
    ? { allowed: true, reason: "allowlisted" }
    : { allowed: false, reason: "not_allowlisted" };
}
