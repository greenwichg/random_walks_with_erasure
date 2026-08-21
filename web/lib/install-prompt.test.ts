import { test } from "node:test";
import assert from "node:assert/strict";
import {
  installOffer,
  isIosSafari,
  isIosInAppBrowser,
  readDismissed,
  writeDismissed,
  DISMISS_DAYS,
  DISMISS_KEY,
  type InstallFacts,
  type InstallOffer,
} from "./install-prompt.ts";

/**
 * The install banner has one job and three ways to embarrass the product: offering to install an
 * app that is already installed, re-appearing after it was dismissed, and never appearing at all
 * because a storage read threw in private mode. All three are decided here, away from the effect
 * that gathers the facts, so all three are reachable by a test.
 */

const DAY = 24 * 60 * 60 * 1000;
const NOW = 1_800_000_000_000;
const facts = (over: Partial<InstallFacts> = {}): InstallFacts => ({
  installed: false,
  nativePromptReady: true,
  iosSafari: false,
  iosInAppBrowser: false,
  dismissedAt: null,
  now: NOW,
  ...over,
});

test("an installed app is never asked to install itself", () => {
  // Beats every other signal, including a live beforeinstallprompt: some browsers keep firing it
  // for an installed app, and a banner offering what the reader already did reads as the app not
  // knowing its own state.
  assert.equal(installOffer(facts({ installed: true })), "hidden");
  assert.equal(installOffer(facts({ installed: true, nativePromptReady: true })), "hidden");
  assert.equal(installOffer(facts({ installed: true, iosSafari: true })), "hidden");
});

test("the native prompt is used whenever the browser offers one", () => {
  assert.equal(installOffer(facts()), "native");
});

test("iOS Safari gets instructions, because WebKit has no programmatic install", () => {
  assert.equal(installOffer(facts({ nativePromptReady: false, iosSafari: true })), "ios-instructions");
});

test("a browser with no install path is shown nothing", () => {
  // Desktop Firefox has neither beforeinstallprompt nor an Add-to-Home-Screen menu. A banner the
  // reader cannot act on is pure noise.
  assert.equal(installOffer(facts({ nativePromptReady: false, iosSafari: false })), "hidden");
});

test("a dismissal is honoured for the full window, and expires after it", () => {
  const dismissed = (ageDays: number) =>
    installOffer(facts({ dismissedAt: NOW - ageDays * DAY }));
  assert.equal(dismissed(0), "hidden", "dismissed just now");
  assert.equal(dismissed(DISMISS_DAYS - 1), "hidden", "still inside the window");
  assert.equal(dismissed(DISMISS_DAYS), "native", "the boundary is exclusive — offered again");
  assert.equal(dismissed(DISMISS_DAYS + 60), "native", "long expired");
});

test("a dismissal timestamped in the future is treated as expired, not trusted", () => {
  // A clock that moved backwards — a timezone change, a corrected system clock — would otherwise
  // silence the banner for as long as the skew lasted, with nothing to explain it.
  assert.equal(installOffer(facts({ dismissedAt: NOW + 90 * DAY })), "native");
});

test("unreadable storage offers the banner rather than hiding it", () => {
  // Safari private mode throws on localStorage. Failing closed here would mean the app is never
  // installable in private mode — silently, and only for those readers.
  const throwing = {
    getItem() {
      throw new Error("SecurityError");
    },
  };
  assert.equal(readDismissed(throwing), null);
  assert.equal(installOffer(facts({ dismissedAt: readDismissed(throwing) })), "native");
});

test("writing a dismissal never throws, even when storage refuses", () => {
  assert.doesNotThrow(() =>
    writeDismissed(NOW, {
      setItem() {
        throw new Error("QuotaExceededError");
      },
    }),
  );
});

test("a dismissal round-trips through real storage semantics", () => {
  const store = new Map<string, string>();
  const fake = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  };
  writeDismissed(NOW, fake);
  assert.equal(store.get(DISMISS_KEY), String(NOW));
  assert.equal(readDismissed(fake), NOW);
});

test("garbage in storage reads as no dismissal", () => {
  const fake = { getItem: () => "not-a-number" };
  assert.equal(readDismissed(fake), null);
});

const SAFARI_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
const SAFARI_IPAD =
  "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

test("iOS detection covers iPadOS and excludes browsers that cannot install", () => {
  const CHROME_IOS =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120 Mobile/15E148";
  const FIREFOX_IOS =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 FxiOS/121 Mobile/15E148";
  const MAC_SAFARI =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15";

  assert.equal(isIosSafari(SAFARI_IPHONE), true);
  // Chrome and Firefox on iOS are WebKit too, but neither can add to the home screen — telling
  // them to use the Share menu would be an instruction that does not work.
  assert.equal(isIosSafari(CHROME_IOS), false);
  assert.equal(isIosSafari(FIREFOX_IOS), false);
  // A real Mac: Safari there has no Add to Home Screen, and it is not a touch device.
  assert.equal(isIosSafari(MAC_SAFARI, 0), false);
  // iPadOS reports itself as a Mac; the touch points are what give it away.
  assert.equal(isIosSafari(MAC_SAFARI, 5), true);
});

/**
 * iOS in-app browsers.
 *
 * The bug being fixed: every one of these reported `isIosSafari === true`, so a reader who opened
 * Hidden View from a Facebook or LinkedIn post was told "Share → Add to Home Screen" — in a share
 * sheet that has no Add to Home Screen item at all. An instruction that cannot be followed is worse
 * than no instruction, so these get "open in Safari" instead.
 *
 * `touchPoints` is 0 throughout: every one of these names iPhone/iPad outright.
 */
const IN_APP: [string, string][] = [
  [
    "Facebook",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/21A329 [FBAN/FBIOS;FBDV/iPhone14,2;FBMD/iPhone;FBSN/iOS;FBSV/17.0;FBID/phone;FBLC/en_US]",
  ],
  [
    "Instagram",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 302.0.0.23.113 (iPhone14,2; iOS 17_0; en_US; scale=3.00)",
  ],
  [
    "LinkedIn",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [LinkedInApp]",
  ],
  [
    // LINE is the interesting one: it appends its token to the OTHERWISE UNMODIFIED Safari user
    // agent, `Version/` and `Safari/` and all. Only the explicit marker catches it.
    "LINE",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 Line/13.13.0",
  ],
  [
    "Twitter",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Twitter for iPhone",
  ],
  [
    "Snapchat",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Snapchat/12.60.0.36 (like Safari/604.1)",
  ],
  [
    "WeChat",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.42(0x18002a2d)",
  ],
  [
    "TikTok",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 musical_ly_32.5.0 JsSdk/2.0 BytedanceWebview/d8a21c6",
  ],
  [
    "the Google app",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/295.0.556370809 Mobile/15E148 Safari/604.1",
  ],
  [
    // Nobody enumerated this one. A bare WKWebView omits the `Version/` token that genuine Mobile
    // Safari has always sent, which is what catches the apps not on any list.
    "an unnamed WKWebView",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
  ],
];

/** What the component does: sniff the user agent, then decide. Threaded through here so the tests
 *  cover the whole path rather than the two halves separately. */
const offerFor = (ua: string, touch = 0): InstallOffer =>
  installOffer(
    facts({
      nativePromptReady: false, // WebKit never fires beforeinstallprompt
      iosSafari: isIosSafari(ua, touch),
      iosInAppBrowser: isIosInAppBrowser(ua, touch),
    }),
  );

test("iOS in-app browsers are detected, and are not mistaken for Safari", () => {
  for (const [app, ua] of IN_APP) {
    assert.equal(isIosInAppBrowser(ua), true, `${app} should be an in-app browser`);
    // The half that was broken: each of these used to answer `true` here.
    assert.equal(isIosSafari(ua), false, `${app} must not be treated as Safari`);
    // And the offer that follows from it — the Share-menu instructions this used to produce were
    // an instruction the reader could not carry out.
    assert.equal(offerFor(ua), "ios-open-in-safari", `${app} should be told to open in Safari`);
  }
});

test("genuine iPhone and iPad Safari are unchanged", () => {
  // The constraint on this fix: real Safari still gets the Share → Add to Home Screen path. It is
  // safe by construction — every rule above keys off a token genuine Safari does not have, or off
  // the absence of `Version/`, which genuine Safari always sends.
  for (const [name, ua, touch] of [
    ["iPhone Safari", SAFARI_IPHONE, 0],
    ["iPad Safari", SAFARI_IPAD, 5],
    // iPadOS in desktop mode claims to be a Mac and is only distinguishable by touch points.
    [
      "iPadOS desktop mode",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      5,
    ],
  ] as [string, string, number][]) {
    assert.equal(isIosSafari(ua, touch), true, `${name} must still get instructions`);
    assert.equal(isIosInAppBrowser(ua, touch), false, `${name} is not an in-app browser`);
    assert.equal(offerFor(ua, touch), "ios-instructions", `${name} must still get instructions`);
  }
});

test("the two iOS predicates are mutually exclusive on every user agent", () => {
  // installOffer checks in-app first, but the offer must not DEPEND on that ordering: a user agent
  // that satisfied both would mean the two predicates disagree about what the browser is.
  const all: [string, number][] = [
    ...IN_APP.map(([, ua]) => [ua, 0] as [string, number]),
    [SAFARI_IPHONE, 0],
    [SAFARI_IPAD, 5],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120 Mobile/15E148", 0],
    ["Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36", 5],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36", 0],
  ];
  for (const [ua, touch] of all) {
    assert.equal(
      isIosSafari(ua, touch) && isIosInAppBrowser(ua, touch),
      false,
      `both predicates matched: ${ua}`,
    );
  }
});

test("non-iOS and third-party iOS browsers are not in-app browsers", () => {
  // Android's Facebook and Instagram browsers carry the same FBAN/Instagram tokens, but Android
  // has beforeinstallprompt — misfiring here would replace a real install button with advice to
  // open Safari, which does not exist on that device.
  const ANDROID_FB =
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UP1A) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/448.0.0.35.114;]";
  assert.equal(isIosInAppBrowser(ANDROID_FB, 5), false);
  assert.equal(
    isIosInAppBrowser(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    ),
    false,
  );
  // Chrome and Firefox on iOS are neither Safari nor an in-app browser: they are real browsers
  // that simply cannot install. They stay silent rather than gaining a banner from this change.
  for (const ua of [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120 Mobile/15E148",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 FxiOS/121 Mobile/15E148",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 EdgiOS/120 Mobile/15E148",
  ]) {
    assert.equal(isIosInAppBrowser(ua), false, ua);
    assert.equal(isIosSafari(ua), false, ua);
    assert.equal(offerFor(ua), "hidden", `${ua} gained a banner it did not have before`);
  }
});

test("an in-app browser is still outranked by installed, dismissed and the native prompt", () => {
  // The new branch is a fallback, not an override: none of the earlier rules may be weakened by it.
  const inApp = (over: Partial<InstallFacts> = {}) =>
    installOffer(facts({ nativePromptReady: false, iosInAppBrowser: true, ...over }));
  assert.equal(inApp(), "ios-open-in-safari");
  assert.equal(inApp({ installed: true }), "hidden");
  assert.equal(inApp({ dismissedAt: NOW - DAY }), "hidden");
  assert.equal(inApp({ nativePromptReady: true }), "native");
  // And when both iOS facts are somehow set, the narrower one wins — telling an in-app reader to
  // use a Share menu that has no Add to Home Screen is the failure this whole change is about.
  assert.equal(inApp({ iosSafari: true }), "ios-open-in-safari");
});
