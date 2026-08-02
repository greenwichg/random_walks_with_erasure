/**
 * Startup environment validation for the web tier — pure logic, no side effects, so it can be
 * unit-tested (node --test) and called from `instrumentation.ts` at server boot.
 *
 * Philosophy (matches the engine): production is the cross-tier switch `RWE_ENV=production` (NOT
 * `NODE_ENV`, because the Colab demo serves a production Next build yet must stay zero-config). In
 * production the app refuses to boot with an incomplete configuration; outside production nothing
 * is fatal (local dev and Colab keep working with zero setup) and only advisory warnings are shown.
 */

// NO fs import — deliberately. This .mjs is bundled into instrumentation's EDGE build as well
// (the register() hook guards with an early `return`, which webpack's dependency scan does not
// dead-code-eliminate), where any fs import statement fails the compile: `node:fs` as an unhandled
// scheme, bare `fs` as unresolvable. `process.getBuiltinModule` is a runtime property access —
// invisible to the bundler, real fs on the Node server where validateEnv actually runs, undefined
// on edge where it never does.
const nodeReadFileSync = () => globalThis.process?.getBuiltinModule?.("fs")?.readFileSync;

/** @param {Record<string, string | undefined>} env */
export function isProduction(env) {
  const v = (env.RWE_ENV || "").trim().toLowerCase();
  return v === "production" || v === "prod";
}

const truthy = (v) => typeof v === "string" && v.trim() !== "";

/** Port of `betaAccessEnabled` (lib/beta-access.ts): explicit flag wins, else ON in production. */
export function betaGateEnabled(env) {
  const flag = env.BETA_ACCESS_ENABLED;
  if (flag != null && flag.trim() !== "") {
    return ["1", "true", "yes", "on"].includes(flag.trim().toLowerCase());
  }
  return isProduction(env);
}

/**
 * How many entries `parseAllowlist` (lib/beta-access.ts) would yield from `raw`. Counting is
 * re-implemented rather than imported because this module must load in bare node with no
 * type-stripping (it also runs under `node --test` directly); the parity fixture that guards the
 * TS↔Python parser pair guards this count too (see env-validation.test.mjs), so drift fails a build.
 * @param {string | null | undefined} raw
 */
export function countAllowlistEntries(raw) {
  if (!raw) return 0;
  return raw
    .split(/[\n,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0 && !s.startsWith("#"))
    .map((s) => (s.startsWith("@") ? s.slice(1) : s))
    .filter((s) => s.length > 0).length;
}

/**
 * Validate the environment. Returns errors (fatal in production) and warnings (advisory).
 * @param {Record<string, string | undefined>} env
 * @param {{ readFileSync?: (path: string, enc: string) => string }} [opts] test seam for file I/O
 * @returns {{ production: boolean, errors: string[], warnings: string[] }}
 */
export function validateEnv(env, opts = {}) {
  const production = isProduction(env);
  const errors = [];
  const warnings = [];

  const googleId = truthy(env.GOOGLE_CLIENT_ID);
  const googleSecret = truthy(env.GOOGLE_CLIENT_SECRET);

  // RWE_BACKEND_URL must be a valid URL wherever it is set (fatal in prod, advisory in dev).
  if (truthy(env.RWE_BACKEND_URL)) {
    try {
      new URL(env.RWE_BACKEND_URL);
    } catch {
      (production ? errors : warnings).push(
        `RWE_BACKEND_URL is not a valid URL: ${env.RWE_BACKEND_URL}`,
      );
    }
  }

  if (production) {
    if (!truthy(env.NEXTAUTH_SECRET))
      errors.push("NEXTAUTH_SECRET is required in production — it signs the session JWTs; without it sign-in throws at runtime. Generate one with `openssl rand -base64 32`.");
    if (!truthy(env.RWE_INTERNAL_SECRET))
      errors.push("RWE_INTERNAL_SECRET is required in production — it must match the engine's, which rejects unsigned per-user calls in production. Set the same value on both services.");
    if (!truthy(env.RWE_BACKEND_URL))
      errors.push("RWE_BACKEND_URL is required in production — the engine origin the proxy calls (e.g. https://engine.internal).");

    // Sign-in method: the dev demo-login is disabled in production, so Google OAuth is the only way in.
    if (googleId && googleSecret) {
      if (!truthy(env.NEXTAUTH_URL))
        errors.push("NEXTAUTH_URL is required in production when Google OAuth is enabled — it builds the OAuth callback URLs (e.g. https://app.example.com).");
    } else if (googleId || googleSecret) {
      errors.push("Google OAuth is only half-configured — set BOTH GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (or neither).");
    } else {
      errors.push("No sign-in method is configured for production — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (the dev demo-login is disabled in production).");
    }
  } else {
    // Development / Colab: never fatal; surface a half-configured OAuth pair as a warning only.
    if (googleId !== googleSecret)
      warnings.push("Only one of GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET is set — Google sign-in will fail. Set both, or neither (dev demo-login).");
  }

  // BA1 — the beta gate's two silent-misconfiguration states, WARNINGS even in production. Both are
  // deliberate: an enabled gate with zero entries is the designed fail-closed posture (a legitimate
  // emergency "close the beta" state — refusing to boot would turn revoking access into an outage),
  // and an unreadable allowlist file is swallowed by the gate BY DESIGN so a broken file can never
  // break sign-in. That very swallowing is what let the missing-mount (2026-07-29) and dead-env-line
  // (2026-08-02) incidents run silent: nothing anywhere said the effective allowlist was not what
  // the operator believed. Boot is the one moment to say it.
  if (betaGateEnabled(env)) {
    let fileEntries = 0;
    const file = env.BETA_ALLOWLIST_FILE;
    if (truthy(file)) {
      try {
        const read = opts.readFileSync ?? nodeReadFileSync();
        if (!read) throw Object.assign(new Error("fs unavailable"), { code: "fs_unavailable" });
        fileEntries = countAllowlistEntries(read(file, "utf8"));
      } catch (err) {
        const code = err && typeof err === "object" && "code" in err ? String(err.code) : "unreadable";
        warnings.push(
          `BETA_ALLOWLIST_FILE is set (${file}) but cannot be read from this container (${code}) — ` +
            "the gate ignores unreadable files by design, so every grant written there does NOTHING. " +
            "Check the volume mount and the path (see docs/BETA_ACCESS_CONTROL.md, troubleshooting).",
        );
      }
    }
    if (fileEntries + countAllowlistEntries(env.BETA_ALLOWLIST) === 0) {
      warnings.push(
        "the beta gate is ENABLED and the effective allowlist has ZERO entries — every sign-in " +
          "will be denied (fail-closed). If that is not intended: populate BETA_ALLOWLIST (ONE " +
          "line in deploy/.env — duplicate keys keep only the last) or BETA_ALLOWLIST_FILE.",
      );
    }
  }

  return { production, errors, warnings };
}

/**
 * Format a report for console output. Returned as lines so the caller decides how to emit them.
 * @param {{ production: boolean, errors: string[], warnings: string[] }} report
 * @returns {{ warnLines: string[], errorLines: string[] }}
 */
export function formatReport(report) {
  return {
    warnLines: report.warnings.map((w) => `[env] warning: ${w}`),
    errorLines: report.errors.map((e) => `  ✗ ${e}`),
  };
}
