/**
 * Deterministic test helpers for the E2E suite — the only "infrastructure" the suite adds. They do
 * NOT change product behavior; they set up isolated state and read it back:
 *
 *   - createEngineUser: mint a fresh, isolated engine user (a unique identity per test)
 *   - mintSessionCookie: encode the SAME NextAuth session JWT a completed sign-in would set, so a
 *     test starts already authenticated as that user (OAuth itself is external / un-automatable)
 *   - seedReads / seedOnboarding: put an account past a threshold via the canonical engine endpoints
 *   - engineGet / enginePost: attributed direct engine calls (dev trust) for setup + assertions
 *
 * Direct engine calls carry `X-IH-User-Id`; on localhost with no internal secret the engine trusts
 * it (the same dev posture the app uses), so no secret is needed.
 */
import { encode } from "next-auth/jwt";
import { ENGINE_URL, NEXTAUTH_SECRET } from "./constants";

export interface SessionCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  httpOnly: boolean;
  sameSite: "Lax";
}

let counter = 0;

/** Create a fresh, isolated engine user and return its stable engine id. */
export async function createEngineUser(tag = "e2e"): Promise<number> {
  const providerAccountId = `${tag}-${Date.now()}-${counter++}`;
  const res = await fetch(`${ENGINE_URL}/api/internal/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "google", providerAccountId }),
  });
  if (!res.ok) throw new Error(`createEngineUser failed: ${res.status} ${await res.text()}`);
  const { userId } = (await res.json()) as { userId: number };
  return userId;
}

/** Encode the NextAuth session cookie for `uid` (the identity a real sign-in would establish). */
export async function mintSessionCookie(uid: number): Promise<SessionCookie> {
  const token = await encode({
    token: { name: "E2E Reader", email: `e2e-${uid}@infodiet.local`, sub: String(uid), engineUserId: uid },
    secret: NEXTAUTH_SECRET,
  });
  return {
    name: "next-auth.session-token",
    value: token,
    domain: "localhost",
    path: "/",
    httpOnly: true,
    sameSite: "Lax",
  };
}

function engineHeaders(uid: number): Record<string, string> {
  return { "X-IH-User-Id": String(uid), "Content-Type": "application/json" };
}

/** Attributed engine GET (dev trust). Throws on any non-2xx. */
export async function engineGet<T = unknown>(uid: number, path: string): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, { headers: engineHeaders(uid) });
  if (!res.ok) throw new Error(`engineGet ${path} → ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

/** Attributed engine POST (dev trust). Throws on any non-2xx. */
export async function enginePost<T = unknown>(uid: number, path: string, body: unknown): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, {
    method: "POST",
    headers: engineHeaders(uid),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`enginePost ${path} → ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

/** Record `count` distinct reads through the canonical `/api/me/reads` pipeline (5 crosses Measured). */
export async function seedReads(uid: number, count: number, prefix = "seed"): Promise<void> {
  const reads = Array.from({ length: count }, (_, i) => ({
    url: `https://e2e.example/${prefix}/${uid}/${i}`,
    title: `Seeded read ${i}`,
    description: "E2E seeded read",
  }));
  await enginePost(uid, "/api/me/reads", { reads });
}

/** Save onboarding outlets so the report resolves to a genuine Estimate (not the demo fallback). */
export async function seedOnboarding(uid: number): Promise<void> {
  const outlets = await engineGet<Array<{ id: string }>>(uid, "/api/outlets");
  const ids = outlets.slice(0, 4).map((o) => o.id);
  await enginePost(uid, "/api/me/onboarding", { outlets: ids });
}

/** The user's recorded recommendation feedback, straight from the engine (assertion source of truth). */
export async function engineFeedback(uid: number): Promise<Array<{ articleId: string; feedback: string }>> {
  return engineGet(uid, "/api/me/recommendations/feedback");
}

/** The user's recommendation feed id-order, straight from the engine (the ranking reference). */
export async function engineRecommendationIds(uid: number): Promise<string[]> {
  const recs = await engineGet<Array<{ article: { id: string } }>>(uid, "/api/recommendations");
  return recs.map((r) => r.article.id);
}
