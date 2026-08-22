// Every branch of the native sign-in exchange, with no Google, no engine and no network.
//
// This is the second door into Hidden View. The first one — NextAuth's `signIn` callback — has a
// closed-beta gate on it, and the entire value of that gate is that there is no way around it. A
// second sign-in path is exactly how a gate stops being one, so the cases below are less about
// happy-path correctness than about the four ways this endpoint could quietly become an open door:
// an unchecked audience, an unverified email, a skipped allowlist, and a token minted before the
// account exists.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  exchange,
  messageForExchangeFailure,
  safeLabel,
  statusForExchangeFailure,
  type ExchangeProbes,
  type VerifiedIdentity,
} from "./mobile-exchange.ts";

const IOS = "ios-client.apps.googleusercontent.com";
const ANDROID = "android-client.apps.googleusercontent.com";
const SOMEONE_ELSE = "some-other-app.apps.googleusercontent.com";

const IDENTITY: VerifiedIdentity = {
  providerAccountId: "108461123456789012345",
  email: "reader@example.com",
  displayName: "A Reader",
  audience: IOS,
  emailVerified: true,
};

/** Probes that record what they were asked, so a test can assert what was NOT reached. */
function probes(over: Partial<ExchangeProbes> & { identity?: VerifiedIdentity | null } = {}) {
  const calls = { verify: 0, allowed: 0, upsert: 0, mint: 0 };
  const p: ExchangeProbes & { calls: typeof calls } = {
    calls,
    verify: async () => {
      calls.verify++;
      return over.identity === undefined ? IDENTITY : over.identity;
    },
    allowed: (email) => {
      calls.allowed++;
      return over.allowed ? over.allowed(email) : { allowed: true, reason: "allowlisted" };
    },
    upsert: async (identity) => {
      calls.upsert++;
      return over.upsert ? over.upsert(identity) : 4242;
    },
    mint: async (userId, label) => {
      calls.mint++;
      return over.mint ? over.mint(userId, label) : "ih_tok_plaintext";
    },
  };
  return p;
}

const AUD = { audiences: [IOS, ANDROID] };
const req = { provider: "google", idToken: "an.id.token", label: "Pixel 8" };

/* -- the happy path ---------------------------------------------------------------------------- */

test("a verified, allowlisted identity gets a token", async () => {
  const outcome = await exchange(req, probes(), AUD);
  assert.deepEqual(outcome, {
    ok: true,
    userId: 4242,
    token: "ih_tok_plaintext",
    email: "reader@example.com",
  });
});

test("the account is created BEFORE the credential, never the other way round", async () => {
  // A token minted for a user id that does not exist yet is a credential with nothing behind it.
  const order: string[] = [];
  const p = probes({
    upsert: async () => {
      order.push("upsert");
      return 7;
    },
    mint: async () => {
      order.push("mint");
      return "t";
    },
  });
  await exchange(req, p, AUD);
  assert.deepEqual(order, ["upsert", "mint"]);
});

test("the provider defaults to google, and the token is minted for the id the PROVIDER named", async () => {
  // The trust boundary in one assertion: the engine user comes from the verified `sub`, so a client
  // cannot ask to be signed in as somebody else by sending a user id alongside its token.
  let seen: VerifiedIdentity | null = null;
  const p = probes({
    upsert: async (identity) => {
      seen = identity;
      return 99;
    },
  });
  const outcome = await exchange({ idToken: "t" }, p, AUD);
  assert.equal(outcome.ok && outcome.userId, 99);
  assert.equal(seen!.providerAccountId, IDENTITY.providerAccountId);
});

/* -- the audience check ------------------------------------------------------------------------ */

test("a valid Google token minted for SOMEBODY ELSE'S app is refused", async () => {
  // The one that matters most. A Google ID token from any other developer's app is a perfectly
  // valid, correctly signed Google ID token. Without this check, any app in the world could sign
  // its users into Hidden View.
  const outcome = await exchange(req, probes({ identity: { ...IDENTITY, audience: SOMEONE_ELSE } }), AUD);
  assert.deepEqual(outcome, { ok: false, reason: "untrusted-audience", detail: SOMEONE_ELSE });
});

test("an untrusted audience is refused BEFORE the allowlist and before any account exists", async () => {
  const p = probes({ identity: { ...IDENTITY, audience: SOMEONE_ELSE } });
  await exchange(req, p, AUD);
  assert.equal(p.calls.allowed, 0, "the gate must not be consulted for a token we do not trust");
  assert.equal(p.calls.upsert, 0, "no engine account may be created");
  assert.equal(p.calls.mint, 0);
});

test("an empty audience list fails CLOSED — an unconfigured deployment mints nothing", async () => {
  // The alternative — "accept anything until it is configured" — is how a placeholder ships.
  const p = probes();
  const outcome = await exchange(req, p, { audiences: [] });
  assert.deepEqual(outcome, { ok: false, reason: "not-configured" });
  assert.equal(p.calls.verify, 0, "not even the signature check runs; there is nothing to trust it against");
});

test("each configured platform's audience is accepted", async () => {
  for (const aud of [IOS, ANDROID]) {
    const outcome = await exchange(req, probes({ identity: { ...IDENTITY, audience: aud } }), AUD);
    assert.equal(outcome.ok, true, `${aud} should be trusted`);
  }
});

/* -- the closed beta --------------------------------------------------------------------------- */

test("a verified identity that is NOT on the allowlist gets no token", async () => {
  const p = probes({ allowed: () => ({ allowed: false, reason: "not_allowlisted" }) });
  const outcome = await exchange(req, p, AUD);
  assert.deepEqual(outcome, { ok: false, reason: "not-allowlisted", detail: "not_allowlisted" });
  assert.equal(p.calls.upsert, 0, "a denied reader must not get an engine account");
  assert.equal(p.calls.mint, 0);
});

test("a refused beta reader is 403, not 401 — the credential was fine", async () => {
  // 401 would send a native client into a re-authentication loop against a door that is not going
  // to open. 403 says: you are who you say you are, and you are still not on the list.
  assert.equal(statusForExchangeFailure("not-allowlisted"), 403);
});

test("an UNVERIFIED email never reaches the allowlist", async () => {
  // Without this, the closed beta is defeated by claiming somebody else's address: Google will mint
  // a token carrying an unverified `email` claim, and the allowlist matches on the address.
  const p = probes({ identity: { ...IDENTITY, emailVerified: false } });
  const outcome = await exchange(req, p, AUD);
  assert.deepEqual(outcome, { ok: false, reason: "unverified-email" });
  assert.equal(p.calls.allowed, 0);
});

/* -- bad input --------------------------------------------------------------------------------- */

test("a missing or non-string token is a 400, and verifies nothing", async () => {
  for (const bad of [undefined, "", "   ", 42, null, {}]) {
    const p = probes();
    const outcome = await exchange({ provider: "google", idToken: bad }, p, AUD);
    assert.deepEqual(outcome, { ok: false, reason: "missing-token" }, JSON.stringify(bad));
    assert.equal(p.calls.verify, 0);
  }
});

test("an unknown provider is refused before the token is even read", async () => {
  const p = probes();
  const outcome = await exchange({ provider: "facebook", idToken: "t" }, p, AUD);
  assert.deepEqual(outcome, { ok: false, reason: "unsupported-provider", detail: "facebook" });
  assert.equal(p.calls.verify, 0);
});

test("apple is not accepted YET — the list is the contract, not the comment", async () => {
  // Guideline 4.8 will require Sign in with Apple at review time. Adding it is one entry plus a
  // verifier; this pins that it has not silently been assumed to work.
  const outcome = await exchange({ provider: "apple", idToken: "t" }, probes(), AUD);
  assert.equal(outcome.ok === false && outcome.reason, "unsupported-provider");
});

test("a token that fails signature, issuer or expiry is refused", async () => {
  const outcome = await exchange(req, probes({ identity: null }), AUD);
  assert.deepEqual(outcome, { ok: false, reason: "invalid-token" });
});

test("a forged token and one for the wrong app tell the CLIENT the same thing", async () => {
  // Distinguishing them would tell an attacker whether they had guessed a real client id.
  assert.equal(
    messageForExchangeFailure("invalid-token"),
    messageForExchangeFailure("untrusted-audience"),
  );
  // The operator still gets the difference — the reason is what goes to the log.
  assert.notEqual(statusForExchangeFailure("not-allowlisted"), statusForExchangeFailure("invalid-token"));
});

/* -- the engine -------------------------------------------------------------------------------- */

test("an engine that cannot upsert is 503, and mints nothing", async () => {
  const p = probes({ upsert: async () => null });
  const outcome = await exchange(req, p, AUD);
  assert.deepEqual(outcome, { ok: false, reason: "engine-unavailable", detail: "upsert" });
  assert.equal(p.calls.mint, 0);
  assert.equal(statusForExchangeFailure("engine-unavailable"), 503);
});

test("an engine that cannot mint is 503, not a silent success", async () => {
  const outcome = await exchange(req, probes({ mint: async () => null }), AUD);
  assert.deepEqual(outcome, { ok: false, reason: "engine-unavailable", detail: "mint" });
});

/* -- the device label -------------------------------------------------------------------------- */

test("safeLabel keeps something a reader recognises and nothing they did not send", async () => {
  assert.equal(safeLabel("Pixel 8"), "Pixel 8");
  assert.equal(safeLabel("iPhone 15 Pro (personal)"), "iPhone 15 Pro (personal)");
  assert.equal(safeLabel(""), "Mobile app");
  assert.equal(safeLabel(undefined), "Mobile app");
  assert.equal(safeLabel(42), "Mobile app");
  // Parentheses SURVIVE, on purpose — "iPhone 15 Pro (personal)" is a label people actually write.
  // What the filter is for is the characters that change how a string is interpreted somewhere
  // else: angle brackets, quotes, backslashes, control characters. Those are gone.
  assert.equal(safeLabel("<script>alert(1)</script>"), "scriptalert(1)script");
  assert.equal(safeLabel(`a"b'c\\d<e>f`), "abcdef");
  assert.equal(safeLabel("x".repeat(200)).length, 40);
});

test("every failure reason has a status and a message — no reason falls through", async () => {
  const reasons = [
    "unsupported-provider", "missing-token", "invalid-token", "untrusted-audience",
    "unverified-email", "not-allowlisted", "engine-unavailable", "not-configured",
  ] as const;
  for (const r of reasons) {
    assert.ok(statusForExchangeFailure(r) >= 400, r);
    assert.ok(messageForExchangeFailure(r).length > 10, r);
  }
});
