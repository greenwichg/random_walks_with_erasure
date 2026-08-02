/**
 * Unit tests for the web-tier env validator. Run with: node --test web/lib/env-validation.test.mjs
 * No build step or test runner needed (matches the extension's node --test).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { validateEnv, isProduction, betaGateEnabled, countAllowlistEntries } from "./env-validation.mjs";

// A complete, valid production environment — the baseline the negative cases mutate. Includes an
// allowlist because production's gate defaults ON: a "complete" prod env without one would boot
// into fail-closed deny-everyone, which is exactly the state the validator now warns about.
const PROD_OK = {
  RWE_ENV: "production",
  NEXTAUTH_SECRET: "s".repeat(32),
  RWE_INTERNAL_SECRET: "i".repeat(32),
  RWE_BACKEND_URL: "https://engine.internal",
  GOOGLE_CLIENT_ID: "id",
  GOOGLE_CLIENT_SECRET: "secret",
  NEXTAUTH_URL: "https://app.example.com",
  BETA_ALLOWLIST: "owner@example.com",
};

test("isProduction only trips on RWE_ENV, never NODE_ENV (Colab safety)", () => {
  assert.equal(isProduction({ RWE_ENV: "production" }), true);
  assert.equal(isProduction({ RWE_ENV: "prod" }), true);
  assert.equal(isProduction({ NODE_ENV: "production" }), false); // Colab serves a prod build, not prod
  assert.equal(isProduction({}), false);
});

test("a complete production env is valid — no errors AND no warnings", () => {
  const r = validateEnv(PROD_OK);
  assert.equal(r.production, true);
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.warnings, []);
});

test("missing NEXTAUTH_SECRET is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, NEXTAUTH_SECRET: "" });
  assert.ok(r.errors.some((e) => e.includes("NEXTAUTH_SECRET")));
});

test("missing RWE_INTERNAL_SECRET is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, RWE_INTERNAL_SECRET: undefined });
  assert.ok(r.errors.some((e) => e.includes("RWE_INTERNAL_SECRET")));
});

test("missing RWE_BACKEND_URL is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, RWE_BACKEND_URL: "" });
  assert.ok(r.errors.some((e) => e.includes("RWE_BACKEND_URL")));
});

test("half-configured Google OAuth is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, GOOGLE_CLIENT_SECRET: "" });
  assert.ok(r.errors.some((e) => e.includes("half-configured")));
});

test("no sign-in method configured is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, GOOGLE_CLIENT_ID: "", GOOGLE_CLIENT_SECRET: "" });
  assert.ok(r.errors.some((e) => e.includes("No sign-in method")));
});

test("OAuth enabled without NEXTAUTH_URL is fatal in production", () => {
  const r = validateEnv({ ...PROD_OK, NEXTAUTH_URL: "" });
  assert.ok(r.errors.some((e) => e.includes("NEXTAUTH_URL")));
});

test("invalid RWE_BACKEND_URL is fatal in production, advisory in dev", () => {
  assert.ok(validateEnv({ ...PROD_OK, RWE_BACKEND_URL: "not a url" }).errors.some((e) => e.includes("valid URL")));
  const dev = validateEnv({ RWE_BACKEND_URL: "not a url" });
  assert.deepEqual(dev.errors, []);
  assert.ok(dev.warnings.some((w) => w.includes("valid URL")));
});

test("development is never fatal — zero-config local dev and Colab keep working", () => {
  assert.deepEqual(validateEnv({}).errors, []); // nothing set
  assert.deepEqual(validateEnv({ NODE_ENV: "production" }).errors, []); // Colab prod build, no RWE_ENV
  const partial = validateEnv({ GOOGLE_CLIENT_ID: "id" }); // half OAuth in dev
  assert.deepEqual(partial.errors, []);
  assert.ok(partial.warnings.length > 0); // ...but warned
});

// ---------------------------------------------------------------------------- BA1 gate warnings
// The two silent misconfigurations that carried the 2026-07-29 (unmounted file) and 2026-08-02
// (dead duplicate .env line) incidents. Warnings, never errors: fail-closed-empty is a legitimate
// emergency posture, and refusing to boot would turn "revoke everyone" into an outage.

test("gate enabled with zero entries anywhere warns fail-closed deny-everyone", () => {
  const r = validateEnv({ ...PROD_OK, BETA_ALLOWLIST: "" });
  assert.deepEqual(r.errors, [], "must stay bootable");
  assert.ok(r.warnings.some((w) => w.includes("ZERO entries") && w.includes("fail-closed")));
});

test("gate explicitly disabled means no beta warnings, even with zero entries", () => {
  const r = validateEnv({ ...PROD_OK, BETA_ALLOWLIST: "", BETA_ACCESS_ENABLED: "0" });
  assert.deepEqual(r.warnings, []);
});

test("the gate defaults OFF outside production and ON inside — betaGateEnabled parity", () => {
  assert.equal(betaGateEnabled({}), false);
  assert.equal(betaGateEnabled({ RWE_ENV: "production" }), true);
  assert.equal(betaGateEnabled({ BETA_ACCESS_ENABLED: "1" }), true); // explicit flag wins in dev
  assert.equal(betaGateEnabled({ RWE_ENV: "production", BETA_ACCESS_ENABLED: "0" }), false);
});

test("dev with the gate explicitly on gets the same zero-entries warning", () => {
  const r = validateEnv({ BETA_ACCESS_ENABLED: "1" });
  assert.deepEqual(r.errors, []);
  assert.ok(r.warnings.some((w) => w.includes("ZERO entries")));
});

test("an unreadable allowlist file warns loudly and names the path", () => {
  const boom = () => {
    const err = new Error("ENOENT");
    err.code = "ENOENT";
    throw err;
  };
  const r = validateEnv(
    { ...PROD_OK, BETA_ALLOWLIST: "", BETA_ALLOWLIST_FILE: "/app/data/allowlist.txt" },
    { readFileSync: boom },
  );
  assert.ok(r.warnings.some((w) => w.includes("/app/data/allowlist.txt") && w.includes("ENOENT")));
});

test("a readable file with entries satisfies the gate — no warnings", () => {
  const r = validateEnv(
    { ...PROD_OK, BETA_ALLOWLIST: "", BETA_ALLOWLIST_FILE: "/app/data/allowlist.txt" },
    { readFileSync: () => "tester@example.com\n" },
  );
  assert.deepEqual(r.warnings, []);
});

test("a set-but-empty BETA_ALLOWLIST_FILE means NO file — never a read attempt", () => {
  // ${BETA_ALLOWLIST_FILE:-} in compose renders exactly this when deploy/.env lacks the key.
  const boom = () => {
    throw new Error("must not be called");
  };
  const r = validateEnv({ ...PROD_OK, BETA_ALLOWLIST: "", BETA_ALLOWLIST_FILE: "" }, { readFileSync: boom });
  assert.ok(r.warnings.some((w) => w.includes("ZERO entries")), "still warns about the empty list");
  assert.ok(!r.warnings.some((w) => w.includes("cannot be read")), "but not about an unread file");
});

test("with no injected reader the real filesystem is used (the production path)", () => {
  // validateEnv acquires fs via process.getBuiltinModule at call time — the only form that both
  // reads the real file on the Node server and survives being bundled into the edge build, where
  // any fs import statement fails the compile. This pins the uninjected path against a real file.
  const dir = mkdtempSync(`${tmpdir()}/envv-`);
  writeFileSync(`${dir}/allow.txt`, "tester@example.com\n");
  const r = validateEnv({ ...PROD_OK, BETA_ALLOWLIST: "", BETA_ALLOWLIST_FILE: `${dir}/allow.txt` });
  assert.deepEqual(r.warnings, []);
});

test("countAllowlistEntries agrees with the TS/Python parsers on the shared parity fixture", () => {
  const fixture = JSON.parse(
    readFileSync(new URL("../../tests/fixtures/beta_allowlist_parity.json", import.meta.url), "utf8"),
  );
  for (const c of fixture.parse) {
    assert.equal(countAllowlistEntries(c.raw), c.entries.length, c.why);
  }
});
