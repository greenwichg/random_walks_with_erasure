import { test } from "node:test";
import assert from "node:assert/strict";
import {
  installOffer,
  isIosSafari,
  readDismissed,
  writeDismissed,
  DISMISS_DAYS,
  DISMISS_KEY,
  type InstallFacts,
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

test("iOS detection covers iPadOS and excludes browsers that cannot install", () => {
  const SAFARI_IPHONE =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1";
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
