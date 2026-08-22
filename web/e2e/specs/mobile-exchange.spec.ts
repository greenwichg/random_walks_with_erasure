/**
 * `POST /api/auth/mobile` against the real route.
 *
 * The happy path needs a Google-signed ID token, which cannot be produced in a test — so what is
 * driven here is every way the endpoint must REFUSE, which is the half that matters. This is the
 * second door into Hidden View: the first one has a closed-beta gate and the whole value of that
 * gate is that there is no way around it.
 *
 * `lib/mobile-exchange.test.ts` covers the decision itself — audience, verified email, allowlist,
 * ordering — with injected probes. This covers the wiring: that the route exists, fails closed,
 * says the right status, and never echoes anything back.
 */
import { test, expect } from "../fixtures";
import { WEB_URL } from "../constants";

const ENDPOINT = `${WEB_URL}/api/auth/mobile`;

async function post(body: unknown): Promise<{ status: number; json: Record<string, unknown> }> {
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : {} };
}

test.describe("mobile sign-in exchange", () => {
  test("a request with no ID token is refused, and nothing is minted", async () => {
    const { status, json } = await post({ provider: "google" });
    expect(status).toBe(400);
    expect((json.error as { code: string }).code).toBe("missing-token");
    expect(JSON.stringify(json)).not.toContain("token\":\"ih");
  });

  test("an unknown provider is refused", async () => {
    const { status, json } = await post({ provider: "facebook", idToken: "x.y.z" });
    expect(status).toBe(400);
    expect((json.error as { code: string }).code).toBe("unsupported-provider");
  });

  test("a deployment with no client IDs configured mints nothing — it fails CLOSED", async () => {
    // The e2e web server sets no GOOGLE_IOS_CLIENT_ID / GOOGLE_ANDROID_CLIENT_ID / GOOGLE_CLIENT_ID,
    // which is exactly the state of a deployment where somebody has enabled mobile sign-in in the
    // code but not in the environment. The alternative — accepting any audience "until it is
    // configured" — is how a placeholder ships. 500, because this is the operator's mistake, not
    // the caller's.
    const { status, json } = await post({ provider: "google", idToken: "header.payload.signature" });
    expect(status).toBe(500);
    expect((json.error as { code: string }).code).toBe("not-configured");
  });

  test("the response never contains the submitted token", async () => {
    // A refusal that echoed the ID token back would put a live Google credential into every error
    // log and crash reporter between the phone and here.
    const secret = "eyJhbGciOiJSUzI1NiJ9.SUBMITTED_ID_TOKEN.sig";
    const res = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider: "google", idToken: secret }),
    });
    expect(await res.text()).not.toContain("SUBMITTED_ID_TOKEN");
  });

  test("the error envelope matches the rest of the API", async () => {
    // Same `{ error: { code, message } }` shape every other route returns, so a mobile client has
    // one error parser rather than a special case for sign-in.
    const { json } = await post({ provider: "google" });
    const error = json.error as { code?: unknown; message?: unknown };
    expect(typeof error.code).toBe("string");
    expect(typeof error.message).toBe("string");
    expect((error.message as string).length).toBeGreaterThan(10);
  });

  test("GET is not a way in", async () => {
    // Next returns 405 for a method the route does not export. Pinned because a sign-in endpoint
    // reachable by GET is one that can be triggered by a link.
    const res = await fetch(ENDPOINT);
    expect([404, 405]).toContain(res.status);
  });
});
