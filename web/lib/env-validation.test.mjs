/**
 * Unit tests for the web-tier env validator. Run with: node --test web/lib/env-validation.test.mjs
 * No build step or test runner needed (matches the extension's node --test).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { validateEnv, isProduction } from "./env-validation.mjs";

// A complete, valid production environment — the baseline the negative cases mutate.
const PROD_OK = {
  RWE_ENV: "production",
  NEXTAUTH_SECRET: "s".repeat(32),
  RWE_INTERNAL_SECRET: "i".repeat(32),
  RWE_BACKEND_URL: "https://engine.internal",
  GOOGLE_CLIENT_ID: "id",
  GOOGLE_CLIENT_SECRET: "secret",
  NEXTAUTH_URL: "https://app.example.com",
};

test("isProduction only trips on RWE_ENV, never NODE_ENV (Colab safety)", () => {
  assert.equal(isProduction({ RWE_ENV: "production" }), true);
  assert.equal(isProduction({ RWE_ENV: "prod" }), true);
  assert.equal(isProduction({ NODE_ENV: "production" }), false); // Colab serves a prod build, not prod
  assert.equal(isProduction({}), false);
});

test("a complete production env is valid", () => {
  const r = validateEnv(PROD_OK);
  assert.equal(r.production, true);
  assert.deepEqual(r.errors, []);
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
