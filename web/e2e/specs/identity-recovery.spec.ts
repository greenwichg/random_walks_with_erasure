import { test, expect } from "../fixtures";
import {
  createEngineIdentity,
  mintBrokenSessionCookie,
  decodeSessionToken,
  seedOnboarding,
} from "../helpers";

/**
 * Journey 10 — Session identity recovery (S4b).
 *
 * A session whose token carries no `engineUserId` cannot be attributed to anyone: every `/api/me/*`
 * call goes out anonymous and the engine answers 401, for the thirty-day life of that session. It
 * happens when the engine is unreachable during the few hundred milliseconds of sign-in, which
 * swallows the failure by design. `callbacks.jwt` repairs it from the claims the signed token
 * already carries — see docs/SESSION_IDENTITY_RECOVERY_DESIGN.md.
 *
 * WHAT THIS ADDS OVER `lib/session-recovery.test.ts`, which already drives the real NextAuth route:
 * the repair's **durable** half. Recovery runs inside `getServerSession`, and a server render cannot
 * set cookies — so the current request is served correctly while the token on disk is still broken.
 * What makes it stick is the `/api/auth/session` fetch `SessionProvider` issues on mount, which is a
 * response that CAN re-issue the cookie. Only a real browser has a SessionProvider, so only an e2e
 * test can show that the *next* request is fixed too. That is the whole reason this file exists.
 *
 * All three tests mint a broken session directly. There is no UI that produces one — reproducing it
 * "naturally" would mean killing the engine mid-sign-in, which the suite's shared web server makes
 * impossible without breaking every other spec.
 */
test.describe("Session identity recovery", () => {
  test("a broken session heals to the SAME engine account, durably", async ({ browser }) => {
    // An account that already exists and already has history. Recovery's job is to find its way back
    // to THIS user — the failure that matters is not "no id" but "a second, empty account".
    const { uid, providerAccountId } = await createEngineIdentity("recovery");
    await seedOnboarding(uid);

    const context = await browser.newContext();
    await context.addCookies([await mintBrokenSessionCookie({ provider: "google", providerAccountId })]);
    const page = await context.newPage();

    // The token on disk is broken before anything loads — assert the fixture, so a later pass cannot
    // be explained by having minted a healthy cookie by mistake.
    const before = await decodeSessionToken(
      (await context.cookies()).find((c) => c.name === "next-auth.session-token")!.value,
    );
    expect(before?.engineUserId, "the fixture must start broken").toBeUndefined();

    await page.goto("/");

    // (1) THE CURRENT REQUEST is already correct: next-auth builds the session object from the jwt
    // callback's return value, in-process, before anything is serialised.
    await expect
      .poll(async () => (await page.request.get("/api/auth/session").then((r) => r.json()))?.engineUserId, {
        message: "the session should resolve to the original engine user",
      })
      .toBe(uid);

    // (2) THE REPAIR IS DURABLE: SessionProvider's own /api/auth/session fetch is a response that can
    // set cookies, so the token in the jar is re-issued with the id. Without this step the reader is
    // repaired on every request and broken between them — which is what no unit test can show.
    await expect
      .poll(
        async () => {
          const c = (await context.cookies()).find((x) => x.name === "next-auth.session-token");
          return c ? (await decodeSessionToken(c.value))?.engineUserId : undefined;
        },
        { message: "the re-issued cookie should carry the recovered id" },
      )
      .toBe(uid);

    // (3) And the reader is genuinely attributed: a per-user surface loads rather than bouncing to
    // sign-in or answering 401.
    await expect(page).toHaveURL(/localhost:\d+\/(\?.*)?$/);
    const settings = await page.request.get("/api/settings");
    expect(settings.status(), "an attributed session reads its own settings").toBe(200);

    await context.close();
  });

  test("a token minted before the provider claims existed still heals, from `sub`", async ({
    browser,
  }) => {
    // Backward compatibility, and the reason recovery reads `sub` as a fallback: sessions issued
    // before `token.providerAccountId` existed are still valid for up to thirty days, and for Google
    // the `sub` IS the provider account id. If this regressed, every pre-existing broken session
    // would stay broken until its owner signed in again.
    const { uid, providerAccountId } = await createEngineIdentity("legacy");
    await seedOnboarding(uid);

    const context = await browser.newContext();
    await context.addCookies([await mintBrokenSessionCookie({ sub: providerAccountId })]); // no `provider`, no `providerAccountId`
    const page = await context.newPage();
    await page.goto("/");

    await expect
      .poll(async () => (await page.request.get("/api/auth/session").then((r) => r.json()))?.engineUserId, {
        message: "a legacy token should recover via its `sub` claim",
      })
      .toBe(uid);

    await context.close();
  });

  test("a non-Google broken session is never recovered — recovery refuses to guess", async ({
    browser,
  }) => {
    // The guard that keeps the `sub` fallback safe. For the credentials provider NextAuth sets
    // `providerAccountId` to the USER id, not the value the engine keys identities on — so treating a
    // non-Google `sub` as a provider account id would resolve to, or CREATE, the wrong account.
    // Refusing is the correct outcome even though it leaves the session unattributed.
    const context = await browser.newContext();
    await context.addCookies([
      await mintBrokenSessionCookie({ provider: "dev", sub: "12345", email: "dev@infodiet.local" }),
    ]);
    const page = await context.newPage();
    await page.goto("/");

    // Give recovery every chance to run (and to have re-issued a cookie) before concluding it didn't.
    await page.waitForTimeout(1500);
    const session = await page.request.get("/api/auth/session").then((r) => r.json());
    expect(session?.user, "the session itself is still valid").toBeTruthy();
    expect(session?.engineUserId, "but it must NOT have been attributed to anyone").toBeUndefined();

    const cookie = (await context.cookies()).find((c) => c.name === "next-auth.session-token");
    expect((await decodeSessionToken(cookie!.value))?.engineUserId).toBeUndefined();

    await context.close();
  });
});
