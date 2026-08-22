/**
 * What the mobile Recommendations screen does, driven headlessly against the real stack.
 *
 * There is no simulator here — no `adb`, no `xcrun` — so the screen itself cannot be rendered. What
 * CAN be proved, and is what actually matters, is that the path the screen depends on works end to
 * end with a bearer token: the shared API client reaches the real routes, the shared logic shapes
 * the real payloads, and every explanation resolves to a real sentence from the shared catalogs.
 *
 * The modules exercised below are the SAME imports `mobile/app/index.tsx` and
 * `mobile/components/recommendation-card.tsx` make. Nothing here is a mobile-flavoured copy: if one
 * of these assertions fails, the screen is broken, and if the screen needed a rule this file cannot
 * reach, that rule was in the wrong package.
 *
 * What this does NOT cover, and must be checked on a device: Google's native sign-in, the keystore,
 * and how any of it looks. `docs/MOBILE_PHASE3_PLAN.md` carries that checklist.
 */
import { test, expect } from "../fixtures";
import { engineGet, mintApiToken, mintSessionCookie, seedReads } from "../helpers";
import { WEB_URL } from "../constants";

import type { Recommendation, Settings } from "@ih/core/domain/types";
import { partitionByCountryMatch } from "@ih/core/logic/country-partition";
import { countryName } from "@ih/core/logic/countries";
import { presentRecommendation } from "@ih/core/logic/rec-presentation";
import { makeT } from "@ih/core/i18n/core";
import en from "@ih/core/i18n/messages/en.json";
import es from "@ih/core/i18n/messages/es.json";

/** Exactly what `mobile/lib/api.ts` configures the shared client to do, in plain fetch terms. */
async function asMobile<T>(path: string, token: string): Promise<{ status: number; data: T }> {
  const res = await fetch(`${WEB_URL}${path}`, { headers: { authorization: `Bearer ${token}` } });
  return { status: res.status, data: (await res.json()) as T };
}

const t = makeT(en as Record<string, string>, en as Record<string, string>);

test.describe("the mobile Recommendations path", () => {
  test("a token reaches the feed, the settings, and the explain endpoint", async ({ uid }) => {
    // The three calls the screen makes on mount. Before Phase 3a the first returned somebody else's
    // feed and the second answered 401.
    await seedReads(uid, 10, "mobile-path");
    const { token } = await mintApiToken(uid, "ios app");

    const feed = await asMobile<Recommendation[]>("/api/recommendations", token);
    expect(feed.status).toBe(200);
    expect(Array.isArray(feed.data)).toBe(true);

    const settings = await asMobile<Settings>("/api/settings", token);
    expect(settings.status).toBe(200);
    expect(settings.data.interests, "Interest Intensity").toBeTruthy();
    expect(typeof settings.data.politicalOpenness).toBe("number");

    const explain = await asMobile<unknown>("/api/recommendations/explain", token);
    expect([200, 503]).toContain(explain.status); // 503 only if the engine declines; never 401
  });

  test("Interest Intensity set from the phone changes what the engine stores", async ({ uid }) => {
    // The screen reads `interests` to show which topics shaped the feed; the settings screen (later)
    // writes them. Both go through the same bearer path, and the engine is the source of truth.
    const { token } = await mintApiToken(uid);
    const before = await asMobile<Settings>("/api/settings", token);

    const save = await fetch(`${WEB_URL}/api/settings`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({
        interests: { ...before.data.interests, science: 9, sports: 1 },
        recommendationCountry: "GB",
        politicalOpenness: 80,
      }),
    });
    expect(save.status).toBe(200);

    // Read back from the ENGINE, so the assertion does not depend on the route that wrote it.
    const stored = await engineGet<Settings>(uid, "/api/me/settings");
    expect(stored.interests.science).toBe(9);
    expect(stored.interests.sports).toBe(1);
    expect(stored.recommendationCountry).toBe("GB");
    expect(stored.politicalOpenness).toBe(80);

    // And the header chips the screen derives from them: topics off the neutral 5, strongest first.
    const nudged = Object.entries(stored.interests)
      .filter(([, v]) => v !== 5)
      .sort((a, b) => b[1] - a[1])
      .map(([k]) => k);
    expect(nudged[0]).toBe("science");
    expect(nudged[nudged.length - 1]).toBe("sports");
  });

  test("the country preference partitions the feed the way the screen renders it", async ({ uid }) => {
    // `partitionByCountryMatch` is the shared function BOTH clients order the list with. Driving it
    // over the real payload proves the screen's ordering is the web's ordering.
    await seedReads(uid, 10, "mobile-country");
    const { token } = await mintApiToken(uid);
    const feed = await asMobile<Recommendation[]>("/api/recommendations", token);

    const { ordered, firstBackfill } = partitionByCountryMatch(feed.data);
    expect(ordered.length).toBe(feed.data.length);

    if (firstBackfill >= 0) {
      // Every card before the divider matched the country; none after it did.
      expect(ordered.slice(0, firstBackfill).every((r) => r.countryMatch !== false)).toBe(true);
      expect(ordered.slice(firstBackfill).every((r) => r.countryMatch === false)).toBe(true);
    }

    // The divider's own label, localized — the string the screen puts under the last matched card.
    expect(t("rec.backfill.after", { country: countryName("GB", "en") })).toContain("United Kingdom");
    expect(t("rec.backfill.generic")).not.toContain("{");
  });

  test("every explanation the engine sends resolves to a real sentence", async ({ uid }) => {
    // The failure this catches is the one that survives typecheck and lint: `presentRecommendation`
    // returns catalog KEYS, and `makeT`'s last fallback is the key itself — so a key the resolver
    // emits but the catalog lacks renders as "rec.reader.top_topic" on a reader's screen.
    await seedReads(uid, 10, "mobile-explain");
    const { token } = await mintApiToken(uid);
    const feed = await asMobile<Recommendation[]>("/api/recommendations", token);

    const catalog = en as Record<string, string>;
    for (const rec of feed.data) {
      const p = presentRecommendation(rec.explanation);
      for (const key of [p.claimKey, p.ctaKey, p.reader?.key, p.contribution?.key]) {
        if (!key) continue;
        expect(catalog[key], `${key} is not in the catalog — it would render as itself`).toBeTruthy();
        // And no unsubstituted placeholder survives interpolation.
        const parts = [p.reader, p.contribution].find((r) => r?.key === key);
        expect(t(key, parts?.params)).not.toMatch(/\{[a-z]+\}/i);
      }
    }
  });

  test("the phone's language changes the sentence, not the logic", async ({ uid }) => {
    // The i18n split: `@ih/core/i18n/core` resolves, and the platform only supplies WHICH language —
    // `<html lang>` on the web, the device locale on a phone (mobile/lib/i18n.ts). Same key, same
    // params, different catalog.
    const spanish = makeT(es as Record<string, string>, en as Record<string, string>);
    const key = "rec.strategy.rwe-b";
    expect(t(key)).toBe("Other side");
    expect(spanish(key)).toBeTruthy();
    expect(spanish(key)).not.toBe(t(key));

    // Country names localize through the same shared module the card uses.
    expect(countryName("GB", "en")).toBe("United Kingdom");
    expect(countryName("GB", "es")).not.toBe(countryName("GB", "en"));
    void uid;
  });

  test("a signed-out phone and a signed-in one are told apart by the token alone", async ({ uid }) => {
    // `mobile/app/index.tsx` gates on `currentToken() !== null`. This is the server half of that:
    // with no Authorization header the same call is the anonymous showcase; with one it is theirs.
    await seedReads(uid, 10, "mobile-gate");
    const { token } = await mintApiToken(uid);
    const cookie = await mintSessionCookie(uid);

    const ids = async (headers: Record<string, string>) =>
      ((await (await fetch(`${WEB_URL}/api/recommendations`, { headers })).json()) as Recommendation[])
        .map((r) => r.article.id);

    const anon = await ids({});
    const viaToken = await ids({ authorization: `Bearer ${token}` });
    const viaCookie = await ids({ cookie: `${cookie.name}=${cookie.value}` });

    expect(viaToken).toEqual(viaCookie);
    expect(viaToken).not.toEqual(anon);
  });
});
