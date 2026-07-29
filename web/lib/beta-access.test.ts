// BA1 — beta access-control allowlist tests (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  parseAllowlist,
  matches,
  loadAllowlist,
  betaAccessEnabled,
  isEmailAllowed,
} from "./beta-access.ts";

test("parseAllowlist: splits, lowercases, trims, skips blanks + comments, classifies domains", () => {
  const raw = " Alice@Example.com , bob@example.com \n# a comment\n@acme.co ;\n\n  CAROL@x.io ";
  const e = parseAllowlist(raw);
  assert.deepEqual(e, [
    { kind: "email", value: "alice@example.com" },
    { kind: "email", value: "bob@example.com" },
    { kind: "domain", value: "acme.co" },
    { kind: "email", value: "carol@x.io" },
  ]);
  assert.deepEqual(parseAllowlist(""), []);
  assert.deepEqual(parseAllowlist(null), []);
});

test("matches: exact email (case-insensitive), domain entry, and rejects non-matches / bad input", () => {
  const entries = parseAllowlist("alice@example.com, @acme.co");
  assert.equal(matches("ALICE@example.com", entries), true); // exact, case-insensitive
  assert.equal(matches("  alice@example.com ", entries), true); // trimmed
  assert.equal(matches("someone@acme.co", entries), true); // domain match
  assert.equal(matches("someone@other.com", entries), false); // no match
  assert.equal(matches("notanemail", entries), false); // no @
  assert.equal(matches("@acme.co", entries), false); // empty local part
  assert.equal(matches("x@", entries), false); // empty domain
});

test("betaAccessEnabled: explicit flag wins; else ON in production, OFF in dev", () => {
  assert.equal(betaAccessEnabled({}), false); // dev default
  assert.equal(betaAccessEnabled({ RWE_ENV: "production" }), true);
  assert.equal(betaAccessEnabled({ RWE_ENV: "prod" }), true);
  assert.equal(betaAccessEnabled({ BETA_ACCESS_ENABLED: "1" }), true);
  assert.equal(betaAccessEnabled({ BETA_ACCESS_ENABLED: "on", RWE_ENV: "dev" }), true);
  assert.equal(betaAccessEnabled({ BETA_ACCESS_ENABLED: "0", RWE_ENV: "production" }), false); // force off
});

test("isEmailAllowed: disabled gate allows everyone (dev stays zero-config)", () => {
  const r = isEmailAllowed("anyone@anywhere.com", {}); // gate off
  assert.deepEqual(r, { allowed: true, reason: "disabled" });
});

test("isEmailAllowed: enabled + allowlisted email or domain → allowed", () => {
  const env = { BETA_ACCESS_ENABLED: "1", BETA_ALLOWLIST: "alice@example.com, @acme.co" };
  assert.equal(isEmailAllowed("alice@example.com", env).allowed, true);
  assert.equal(isEmailAllowed("someone@acme.co", env).reason, "allowlisted");
});

test("isEmailAllowed: enabled fail-closed cases → denied", () => {
  const on = { BETA_ACCESS_ENABLED: "1" };
  assert.deepEqual(isEmailAllowed("x@y.com", on), { allowed: false, reason: "empty_allowlist" }); // no list
  assert.deepEqual(isEmailAllowed(null, { ...on, BETA_ALLOWLIST: "a@b.com" }), { allowed: false, reason: "no_email" });
  assert.deepEqual(
    isEmailAllowed("nope@evil.com", { ...on, BETA_ALLOWLIST: "a@b.com" }),
    { allowed: false, reason: "not_allowlisted" },
  );
});

test("loadAllowlist: merges BETA_ALLOWLIST and BETA_ALLOWLIST_FILE; missing file is ignored", () => {
  const dir = mkdtempSync(join(tmpdir(), "ba1-"));
  const file = join(dir, "allow.txt");
  writeFileSync(file, "# team\nfromfile@example.com\n@team.dev\n");
  const entries = loadAllowlist({ BETA_ALLOWLIST: "fromenv@example.com", BETA_ALLOWLIST_FILE: file });
  const values = entries.map((e) => `${e.kind}:${e.value}`);
  assert.ok(values.includes("email:fromenv@example.com"));
  assert.ok(values.includes("email:fromfile@example.com"));
  assert.ok(values.includes("domain:team.dev"));
  // a missing file does not throw and does not lose the env entries
  const only = loadAllowlist({ BETA_ALLOWLIST: "keep@example.com", BETA_ALLOWLIST_FILE: join(dir, "nope.txt") });
  assert.deepEqual(only, [{ kind: "email", value: "keep@example.com" }]);
});

// ── Parity with the Python CLI ────────────────────────────────────────────────────────────────────
// `scripts/manage_users.py` grants and revokes beta access by editing BETA_ALLOWLIST_FILE, and to do
// that it re-implements this parser in Python. If the two drift, the CLI reports access that this
// gate does not grant — the worst failure available to a tool whose only job is to say who can get
// in. `tests/fixtures/beta_allowlist_parity.json` is read by BOTH suites, so a change to either
// implementation has to be made in both or a build goes red.
import { readFileSync as _readFileSync } from "node:fs";

const parity = JSON.parse(
  _readFileSync(new URL("../../tests/fixtures/beta_allowlist_parity.json", import.meta.url), "utf8"),
) as {
  parse: { why: string; raw: string; entries: string[] }[];
  matches: { why: string; email: string; entries: string[]; allowed: boolean }[];
};

const render = (e: { kind: string; value: string }) => (e.kind === "domain" ? `@${e.value}` : e.value);

test("parity: parseAllowlist agrees with scripts/manage_users.py on every shared case", () => {
  for (const c of parity.parse) {
    assert.deepEqual(parseAllowlist(c.raw).map(render), c.entries, c.why);
  }
});

test("parity: matches() agrees with scripts/manage_users.py on every shared case", () => {
  for (const c of parity.matches) {
    assert.equal(matches(c.email, parseAllowlist(c.entries.join("\n"))), c.allowed, c.why);
  }
});
