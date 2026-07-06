/** Unit tests for the security-headers/CSP builder. Run: node --test web/lib/security-headers.test.mjs */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildCsp, securityHeaders, isProduction } from "./security-headers.mjs";

const val = (headers, key) => headers.find((h) => h.key === key)?.value;

test("CSP: strict directives, Next- and OAuth-compatible", () => {
  const csp = buildCsp({ dev: false });
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /frame-ancestors 'none'/); // anti-clickjacking
  assert.match(csp, /object-src 'none'/);
  assert.match(csp, /script-src 'self' 'unsafe-inline'/); // Next inline hydration
  assert.match(csp, /connect-src 'self'/);
  assert.doesNotMatch(csp, /unsafe-eval/); // never in production
  assert.doesNotMatch(csp, /ws:/);
});

test("CSP: dev relaxations for next dev (HMR + Fast Refresh)", () => {
  const csp = buildCsp({ dev: true });
  assert.match(csp, /unsafe-eval/);
  assert.match(csp, /ws:/);
});

test("CSP: apiBase folds into connect-src; override wins verbatim", () => {
  assert.match(buildCsp({ apiBase: "https://api.example.com" }), /connect-src 'self' https:\/\/api\.example\.com/);
  assert.equal(buildCsp({ override: "default-src 'none'" }), "default-src 'none'");
});

test("page headers: full hardening set, CSP first", () => {
  const { page } = securityHeaders({ NODE_ENV: "production" });
  assert.equal(page[0].key, "Content-Security-Policy");
  for (const k of ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
                   "Permissions-Policy", "Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy"]) {
    assert.ok(val(page, k), `missing ${k}`);
  }
  assert.equal(val(page, "X-Frame-Options"), "DENY");
  assert.equal(val(page, "X-Content-Type-Options"), "nosniff");
});

test("HSTS baked into production builds (NODE_ENV), absent in dev", () => {
  // headers() is evaluated at build time, so NODE_ENV is the reliable gate.
  assert.ok(val(securityHeaders({ NODE_ENV: "production" }).page, "Strict-Transport-Security"));
  assert.equal(val(securityHeaders({ NODE_ENV: "development" }).page, "Strict-Transport-Security"), undefined);
  assert.equal(isProduction({ NODE_ENV: "production" }), true);
  assert.equal(isProduction({ NODE_ENV: "development" }), false);
});

test("API headers: no-store + no-sniff, and NO Cross-Origin-Resource-Policy (extension-safe)", () => {
  const { api } = securityHeaders({});
  assert.equal(val(api, "Cache-Control"), "no-store");
  assert.equal(val(api, "X-Content-Type-Options"), "nosniff");
  assert.equal(val(api, "Cross-Origin-Resource-Policy"), undefined); // must not block the extension
});

test("RWE_DISABLE_CSP removes only the CSP header (escape hatch)", () => {
  const { page, cspEnabled } = securityHeaders({ RWE_DISABLE_CSP: "1" });
  assert.equal(cspEnabled, false);
  assert.equal(val(page, "Content-Security-Policy"), undefined);
  assert.ok(val(page, "X-Frame-Options")); // other headers stay
});
