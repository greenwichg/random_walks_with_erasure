/**
 * Both credentials, on every `/api/me/*` route, against the real stack.
 *
 * The unit suite (`lib/auth-decision.test.ts`) proves the decision is right. This proves it is
 * actually WIRED — that each route calls it, passes the resolved headers to the engine, and that the
 * engine attributes the call to the user the token names. Those are different claims, and the second
 * is the one that was only ever true of `/api/me/reads` before Phase 1.
 *
 * Every token here is minted by the engine and stored as a hash, exactly as a production token is;
 * revocation deletes the row, exactly as the settings UI does. Nothing is stubbed.
 *
 * The matrix below is the contract. A route added to `/api/me/` without a row in it is caught by
 * `lib/api-auth-guard.test.ts` in `npm test`, long before this suite runs — the guard checks that
 * the check exists, this spec checks that it works.
 */
import { test, expect } from "../fixtures";
import { mintApiToken, mintSessionCookie, revokeApiToken, seedReads, engineGet, createEngineUser } from "../helpers";
import { WEB_URL } from "../constants";

/**
 * What a route does with a caller who presents nothing.
 *
 *   required      401. The route refused an anonymous caller before Phase 1 and still does.
 *   optional      the route serves anonymous callers (an empty list, a null offer, or the engine's
 *                 own refusal surfacing as a 503) and must go on serving them unchanged.
 *   session-only  401, and a *valid* bearer token gets 403 — token management is not reachable with
 *                 a token (see app/api/me/tokens/route.ts).
 */
type Policy = "required" | "optional" | "session-only";

interface Route {
  method: "GET" | "POST" | "DELETE";
  path: string;
  policy: Policy;
  body?: unknown;
}

const ROUTES: Route[] = [
  { method: "GET", path: "/api/me", policy: "optional" },
  { method: "GET", path: "/api/me/continuation?url=https%3A%2F%2Fexample.com%2Fa", policy: "optional" },
  { method: "GET", path: "/api/me/geography", policy: "optional" },
  { method: "GET", path: "/api/me/notifications", policy: "optional" },
  { method: "POST", path: "/api/me/onboarding", policy: "optional", body: { outlets: [] } },

  { method: "POST", path: "/api/me/notifications/1/seen", policy: "required", body: {} },
  { method: "POST", path: "/api/me/reads", policy: "required", body: { reads: [] } },
  { method: "GET", path: "/api/me/recommendations/feedback", policy: "required" },
  {
    method: "POST",
    path: "/api/me/recommendations/feedback",
    policy: "required",
    body: { articleId: "https://example.com/a", feedback: "like" },
  },
  {
    method: "POST",
    path: "/api/me/recommendations/opened",
    policy: "required",
    body: { articleId: "https://example.com/a" },
  },
  { method: "GET", path: "/api/me/saved", policy: "required" },
  {
    method: "POST",
    path: "/api/me/saved",
    policy: "required",
    body: { articleId: "https://example.com/a" },
  },
  {
    method: "DELETE",
    path: "/api/me/saved?articleId=https%3A%2F%2Fexample.com%2Fa",
    policy: "required",
  },

  { method: "GET", path: "/api/me/tokens", policy: "session-only" },
  { method: "POST", path: "/api/me/tokens", policy: "session-only", body: { label: "probe" } },
  { method: "DELETE", path: "/api/me/tokens/999999", policy: "session-only" },

  // Phase 3a — the Recommendations path, outside /api/me/ and per-reader anyway. These are what the
  // Expo app reads, and until they were generalised a valid token got the demo reader's feed.
  { method: "GET", path: "/api/recommendations", policy: "optional" },
  { method: "GET", path: "/api/recommendations/explain", policy: "optional" },
  { method: "GET", path: "/api/settings", policy: "required" },
  { method: "POST", path: "/api/settings", policy: "required", body: { readingGoalMinutes: 20 } },
];

/** Every guarded handler is represented. Pinned so the matrix cannot quietly shrink. */
const EXPECTED_HANDLERS = 20;

type Credential = { cookie?: string; bearer?: string };

async function call(route: Route, credential: Credential): Promise<{ status: number; body: string }> {
  const headers: Record<string, string> = {};
  if (credential.cookie) headers.cookie = credential.cookie;
  if (credential.bearer) headers.authorization = `Bearer ${credential.bearer}`;
  if (route.body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(`${WEB_URL}${route.path}`, {
    method: route.method,
    headers,
    ...(route.body !== undefined ? { body: JSON.stringify(route.body) } : {}),
  });
  return { status: res.status, body: (await res.text()).slice(0, 200) };
}

const name = (route: Route) => `${route.method} ${route.path.split("?")[0]}`;

/** An authenticated call may 200, 400, 404 or 503 — it may not be refused for WHO it is. */
function expectAuthenticated(status: number, route: Route, via: string) {
  expect(
    [401, 403].includes(status),
    `${name(route)} refused a valid ${via} with ${status}`,
  ).toBe(false);
}

test.describe("the route/auth matrix", () => {
  test("the matrix covers every handler under /api/me", () => {
    // A row silently dropped from ROUTES would make this suite pass by testing less. The guard test
    // counts the handlers in the tree; this pins the same number here, so the two must be edited
    // together or one of them fails.
    expect(ROUTES.length).toBe(EXPECTED_HANDLERS);
  });

  test("a valid session cookie authenticates every route", async ({ uid }) => {
    const cookie = await mintSessionCookie(uid);
    for (const route of ROUTES) {
      const { status } = await call(route, { cookie: `${cookie.name}=${cookie.value}` });
      expectAuthenticated(status, route, "session");
    }
  });

  test("a valid bearer token authenticates every route that accepts one", async ({ uid }) => {
    // The claim Phase 1 exists to make true. Before it, exactly one of these rows passed.
    const { token } = await mintApiToken(uid);
    for (const route of ROUTES) {
      const { status } = await call(route, { bearer: token });
      if (route.policy === "session-only") {
        expect(status, `${name(route)} must refuse a bearer token`).toBe(403);
      } else {
        expectAuthenticated(status, route, "bearer token");
      }
    }
  });

  test("an invalid bearer token is refused by every route", async () => {
    for (const route of ROUTES) {
      const { status } = await call(route, { bearer: "not-a-token-that-was-ever-issued" });
      expect(status, `${name(route)} accepted a bogus token`).toBe(
        route.policy === "session-only" ? 403 : 401,
      );
    }
  });

  test("a REVOKED token is refused by every route", async ({ uid }) => {
    // The case that separates a real credential check from a string check. The token was valid a
    // moment ago and is syntactically indistinguishable from one that still is.
    const { id, token } = await mintApiToken(uid, "to-be-revoked");
    const before = await call(ROUTES.find((r) => r.path === "/api/me/saved" && r.method === "GET")!, {
      bearer: token,
    });
    expect(before.status, "the token must work before it is revoked").not.toBe(401);

    await revokeApiToken(uid, id);

    for (const route of ROUTES) {
      const { status } = await call(route, { bearer: token });
      expect(status, `${name(route)} still accepts a revoked token`).toBe(
        route.policy === "session-only" ? 403 : 401,
      );
    }
  });

  test("no credentials: the routes that refused before still refuse, and only those", async () => {
    // Both halves matter. A missing 401 is a hole; a NEW 401 is a regression in an anonymous-tolerant
    // route (the header bell's empty inbox, the continuation strip's null offer).
    for (const route of ROUTES) {
      const { status } = await call(route, {});
      if (route.policy === "optional") {
        expect(status, `${name(route)} newly refuses an anonymous caller`).not.toBe(401);
        expect(status, `${name(route)} newly forbids an anonymous caller`).not.toBe(403);
      } else {
        expect(status, `${name(route)} does not refuse an anonymous caller`).toBe(401);
      }
    }
  });
});

test.describe("the token actually names the user", () => {
  test("a bearer token reads THAT reader's data, not the demo reader's", async ({ uid }) => {
    // "Not 401" is a weak claim on its own: a route could accept the token and then serve an
    // unattributed answer, which is worse than refusing — the client cannot tell. So this asserts
    // the payload belongs to the token's owner.
    await seedReads(uid, 3, "bearer-attribution");
    const { token } = await mintApiToken(uid);

    const res = await fetch(`${WEB_URL}/api/me`, { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    const me = (await res.json()) as { reads?: number };
    expect(me.reads, "the bearer call was not attributed to the token's owner").toBe(3);
  });

  test("a write made with a bearer token lands on that account", async ({ uid }) => {
    const { token } = await mintApiToken(uid);
    const articleId = `https://example.com/bearer-write/${uid}`;

    const save = await fetch(`${WEB_URL}/api/me/saved`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ articleId }),
    });
    expect(save.status).toBe(200);

    // Read it back from the ENGINE directly, so the assertion does not depend on the same route.
    const saved = await engineGet<Array<{ articleId: string }>>(uid, "/api/me/saved");
    expect(saved.map((s) => s.articleId)).toContain(articleId);
  });

  test("one reader's token cannot read another reader's data", async ({ uid }) => {
    const other = await createEngineUser("bearer-isolation");
    await seedReads(other, 4, "isolation");
    const { token } = await mintApiToken(uid); // uid has no reads

    const res = await fetch(`${WEB_URL}/api/me`, { headers: { authorization: `Bearer ${token}` } });
    const me = (await res.json()) as { reads?: number };
    expect(me.reads ?? 0, "a token resolved to the wrong account").not.toBe(4);
  });

  test("the session wins when a request carries both credentials", async ({ uid }) => {
    // A mobile web view can send a cookie and an Authorization header on the same request. Whichever
    // the server picks decides who a write is attributed to, so the choice is pinned rather than left
    // to whichever branch happens to run first after a refactor.
    const other = await createEngineUser("both-credentials");
    await seedReads(uid, 2, "session-half");
    await seedReads(other, 6, "bearer-half");

    const cookie = await mintSessionCookie(uid);
    const { token } = await mintApiToken(other);

    const res = await fetch(`${WEB_URL}/api/me`, {
      headers: { cookie: `${cookie.name}=${cookie.value}`, authorization: `Bearer ${token}` },
    });
    const me = (await res.json()) as { reads?: number };
    expect(me.reads, "the bearer token overrode the session").toBe(2);
  });
});

test.describe("token management is not reachable with a token", () => {
  test("a valid token cannot mint another token", async ({ uid }) => {
    // Privilege escalation, and the reason `SESSION_ONLY` exists: a minted token would survive the
    // revocation of the one that minted it, so a leaked credential could never be fully withdrawn.
    const { token } = await mintApiToken(uid);
    const res = await fetch(`${WEB_URL}/api/me/tokens`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ label: "escalation" }),
    });
    expect(res.status).toBe(403);

    // And nothing was created — the refusal is before the engine, not a rejected write.
    const tokens = await engineGet<Array<{ label: string }>>(uid, "/api/me/tokens");
    expect(tokens.map((t) => t.label)).not.toContain("escalation");
  });

  test("a valid token cannot revoke or list tokens", async ({ uid }) => {
    const { id, token } = await mintApiToken(uid);
    const list = await fetch(`${WEB_URL}/api/me/tokens`, {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(list.status, "a leaked token must not enumerate the reader's devices").toBe(403);

    const revoke = await fetch(`${WEB_URL}/api/me/tokens/${id}`, {
      method: "DELETE",
      headers: { authorization: `Bearer ${token}` },
    });
    expect(revoke.status).toBe(403);

    // Still there: the refusal did not merely return 403 after doing the work.
    const tokens = await engineGet<Array<{ id: number }>>(uid, "/api/me/tokens");
    expect(tokens.map((t) => t.id)).toContain(id);
  });

  test("the session path to token management still works", async ({ uid }) => {
    // The other half of the 403 above: session-only must mean session-ALLOWED, or the settings page
    // is broken and the matrix would not have noticed (it only asserts "not 401/403").
    const cookie = await mintSessionCookie(uid);
    const headers = { cookie: `${cookie.name}=${cookie.value}`, "content-type": "application/json" };

    const minted = await fetch(`${WEB_URL}/api/me/tokens`, {
      method: "POST",
      headers,
      body: JSON.stringify({ label: "via-session" }),
    });
    expect(minted.status).toBe(200);
    const { id, token } = (await minted.json()) as { id: number; token: string };
    expect(token, "the plaintext is returned exactly once, at mint").toBeTruthy();

    const listed = await fetch(`${WEB_URL}/api/me/tokens`, { headers });
    expect(listed.status).toBe(200);
    expect(((await listed.json()) as Array<{ id: number }>).map((t) => t.id)).toContain(id);

    const revoked = await fetch(`${WEB_URL}/api/me/tokens/${id}`, { method: "DELETE", headers });
    expect(revoked.status).toBe(200);

    // And the revoked token is dead on a route that DOES accept tokens — the loop closes.
    const after = await fetch(`${WEB_URL}/api/me/saved`, {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(after.status).toBe(401);
  });
});

test.describe("the web path is unchanged", () => {
  test("anonymous answers are the ones the app has always rendered", async () => {
    // Regression protection for the routes that tolerate anonymous callers. These exact bodies are
    // what the header bell and the continuation strip read, and turning either into a 401 would
    // break a signed-out render without any route "failing".
    const bell = await fetch(`${WEB_URL}/api/me/notifications`);
    expect(bell.status).toBe(200);
    expect(await bell.json()).toEqual([]);

    const strip = await fetch(`${WEB_URL}/api/me/continuation?url=https%3A%2F%2Fexample.com%2Fa`);
    expect(strip.status).toBe(200);
    expect(await strip.json()).toBeNull();
  });

  test("a signed-in reader's notifications and saves come back over the cookie", async ({ uid }) => {
    const cookie = await mintSessionCookie(uid);
    const headers = { cookie: `${cookie.name}=${cookie.value}`, "content-type": "application/json" };
    const articleId = `https://example.com/session-write/${uid}`;

    const save = await fetch(`${WEB_URL}/api/me/saved`, {
      method: "POST",
      headers,
      body: JSON.stringify({ articleId }),
    });
    expect(save.status).toBe(200);

    const list = await fetch(`${WEB_URL}/api/me/saved`, { headers });
    expect(list.status).toBe(200);
    expect(((await list.json()) as Array<{ articleId: string }>).map((s) => s.articleId)).toContain(
      articleId,
    );
  });

  test("a read recorded over a cookie is still stamped `app`, and over a token `extension`", async ({
    uid,
  }) => {
    // `readSource` is derived from HOW the request authenticated, and `/api/me/reads` was the one
    // route whose auth ladder moved into the shared helper. If the ladder's `via` were wrong, every
    // in-app read would be labelled as coming from the extension — a quiet corruption of the one
    // field that says where a reader's data came from.
    const cookie = await mintSessionCookie(uid);
    await fetch(`${WEB_URL}/api/me/reads`, {
      method: "POST",
      headers: { cookie: `${cookie.name}=${cookie.value}`, "content-type": "application/json" },
      body: JSON.stringify({ reads: [{ url: `https://example.com/src/app/${uid}`, title: "A" }] }),
    });

    const { token } = await mintApiToken(uid);
    await fetch(`${WEB_URL}/api/me/reads`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ reads: [{ url: `https://example.com/src/ext/${uid}`, title: "B" }] }),
    });

    // The URL lives on the nested article (types/domain.ts: HistoryEntry), not on the entry.
    const history = await engineGet<Array<{ article: { url: string }; readSource?: string }>>(
      uid,
      "/api/me/history",
    );
    const sourceOf = (suffix: string) =>
      history.find((h) => h.article?.url?.endsWith(suffix))?.readSource ?? null;
    expect(sourceOf(`/src/app/${uid}`)).toBe("app");
    expect(sourceOf(`/src/ext/${uid}`)).toBe("extension");
  });
});

/**
 * The article ids a feed offers, in order — the stable, meaningful shape of a recommendation feed.
 *
 * Not the raw body: it carries a per-request field that differs between two identical calls, so a
 * byte comparison is noise. Which articles a reader is offered is what personalisation decides.
 */
async function feedIds(authorization?: string, cookie?: string): Promise<string[]> {
  const headers: Record<string, string> = {};
  if (authorization) headers.authorization = authorization;
  if (cookie) headers.cookie = cookie;
  const res = await fetch(`${WEB_URL}/api/recommendations`, { headers });
  expect(res.status).toBe(200);
  return ((await res.json()) as Array<{ article: { id: string } }>).map((r) => r.article.id);
}

test.describe("Phase 3a — the mobile client gets its OWN feed", () => {
  test("a bearer token's recommendations DIFFER from the anonymous showcase feed", async ({ uid }) => {
    // The exit criterion for Phase 3a, and the defect it closes.
    //
    // Before this, a valid token on /api/recommendations returned output byte-identical to a
    // signed-out request: `engineAuthHeaders()` reads a cookie and nothing else, so the engine saw
    // an unattributed call and served the showcase feed. Not a 401 — a 200, with cards claiming to
    // bridge a reading diet that was not this reader's.
    //
    // Asserting "differs" rather than a specific payload because the feed is the recommender's to
    // decide; what must be true is that it is a DIFFERENT reader's answer.
    //
    // Compared by ARTICLE SEQUENCE, not by raw body, and the distinction is the difference between
    // a test that means something and one that passes by accident. The raw body carries a
    // per-request field that varies between two identical anonymous calls, so "the bytes differ"
    // is satisfied by calling the same endpoint twice — this assertion passed against a stale
    // bundle where nothing had been fixed, which is how it came to be written this way. Which
    // ARTICLES a reader is offered is both stable across calls and exactly what personalisation
    // changes. The determinism check below proves the first half rather than assuming it.
    await seedReads(uid, 8, "phase3a");
    const { token } = await mintApiToken(uid);

    const showcase = await feedIds();
    expect(
      await feedIds(),
      "the anonymous feed is not stable, so 'differs from anonymous' would prove nothing",
    ).toEqual(showcase);
    expect(showcase.length, "the showcase feed should have cards to be distinguishable").toBeGreaterThan(0);

    const mine = await feedIds(`Bearer ${token}`);
    expect(mine).not.toEqual(showcase);

    // This reader's own feed is EMPTY, and that is the right answer rather than a broken one. They
    // are past the measured threshold (12 reads, threshold 5, `sufficient: true` from /api/report),
    // so the engine builds their feed from their own corpus — and every read the fixture seeded
    // points at a synthetic `e2e.example` URL that is not in the recommendation catalog, so there is
    // nothing of theirs to recommend from.
    //
    // Which makes it the sharpest possible demonstration of what Phase 3a fixed. Before it, this
    // reader was handed 14 showcase cards, each captioned "this offers another political
    // perspective" — a claim about a reading diet that was not theirs. After it, they are handed
    // nothing, because the engine can finally tell they are signed in. An honest empty state in
    // place of a confident wrong one is the whole point.
    expect(mine).toEqual([]);
  });

  test("the feed a token gets is the same one that reader's session gets", async ({ uid }) => {
    // The other half: "different from anonymous" would also be satisfied by a feed for the WRONG
    // reader. This pins that the token and the cookie resolve to the same person.
    await seedReads(uid, 8, "phase3a-parity");
    const { token } = await mintApiToken(uid);
    const cookie = await mintSessionCookie(uid);

    const viaToken = await feedIds(`Bearer ${token}`);
    const viaCookie = await feedIds(undefined, `${cookie.name}=${cookie.value}`);
    expect(viaToken).toEqual(viaCookie);

    // And neither is the showcase. Without this, "the two agree" would also be satisfied by both
    // falling back to the anonymous feed — which is precisely the bug, passing as a green test.
    expect(viaToken).not.toEqual(await feedIds());
  });

  test("Interest Intensity and the country preference are readable and writable with a token", async ({
    uid,
  }) => {
    // Three of the four things the mobile Recommendations screen has to honour live in Settings:
    // `interests` (Interest Intensity), `recommendationCountry` (For You country), and
    // `politicalOpenness` (which maps to the RWE-B epsilon behind Political Viewpoint Diversity).
    // Until Phase 3a, /api/settings answered 401 to a token and a native client could honour none.
    const { token } = await mintApiToken(uid);
    const bearer = { authorization: `Bearer ${token}`, "content-type": "application/json" };

    const before = await fetch(`${WEB_URL}/api/settings`, { headers: bearer });
    expect(before.status).toBe(200);
    const settings = (await before.json()) as {
      interests?: Record<string, number>;
      politicalOpenness?: number;
    };
    expect(settings.interests, "Interest Intensity must be present").toBeTruthy();
    expect(typeof settings.politicalOpenness).toBe("number");

    const saved = await fetch(`${WEB_URL}/api/settings`, {
      method: "POST",
      headers: bearer,
      body: JSON.stringify({
        interests: { ...settings.interests, technology: 9 },
        recommendationCountry: "IN",
      }),
    });
    expect(saved.status).toBe(200);

    // Read it back through the ENGINE, so the assertion does not depend on the same route.
    const stored = await engineGet<{
      interests?: Record<string, number>;
      recommendationCountry?: string | null;
    }>(uid, "/api/me/settings");
    expect(stored.interests?.technology).toBe(9);
    expect(stored.recommendationCountry).toBe("IN");
  });

  test("a revoked token cannot read settings or the feed", async ({ uid }) => {
    const { id, token } = await mintApiToken(uid, "phase3a-revoke");
    await revokeApiToken(uid, id);
    for (const path of ["/api/settings", "/api/recommendations", "/api/recommendations/explain"]) {
      const res = await fetch(`${WEB_URL}${path}`, { headers: { authorization: `Bearer ${token}` } });
      expect(res.status, `${path} accepted a revoked token`).toBe(401);
    }
  });

  test("the signed-out landing feed is unchanged", async () => {
    // The regression that would matter to the WEB: /api/recommendations serves the showcase feed to
    // anonymous callers on purpose — that is what a visitor browsing the landing experience sees.
    // `optionalUser` had to leave it exactly as it was.
    const res = await fetch(`${WEB_URL}/api/recommendations`);
    expect(res.status).toBe(200);
    expect(Array.isArray(await res.json())).toBe(true);
  });
});
