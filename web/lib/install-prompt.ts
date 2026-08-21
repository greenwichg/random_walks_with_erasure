/**
 * Whether to offer "Install Hidden View", as pure logic so it can be tested without a browser.
 *
 * The banner has exactly one job and three ways to get it wrong: showing it to someone who has
 * already installed (which reads as the app not knowing its own state), showing it again after
 * they dismissed it (nagging), and never showing it at all because a storage read threw. The
 * decision is therefore made here, from facts the component gathers, rather than inline in an
 * effect where none of it is reachable by a test.
 */

/** How long a dismissal is honoured. Long enough not to nag, short enough that a reader who
 *  changes their mind is offered again within a season. */
export const DISMISS_DAYS = 30;
export const DISMISS_KEY = "ih-install-dismissed";

export interface InstallFacts {
  /** The app is running as an installed app (display-mode standalone, or iOS `navigator.standalone`). */
  installed: boolean;
  /** The browser handed us a `beforeinstallprompt` event we can call `prompt()` on. */
  nativePromptReady: boolean;
  /** iOS Safari: no `beforeinstallprompt` exists, so the only route is Share → Add to Home Screen. */
  iosSafari: boolean;
  /** An iOS in-app browser (Facebook, Instagram, LinkedIn, …). WebKit, but a WKWebView whose share
   *  sheet has no "Add to Home Screen" at all — so the Safari instruction cannot be followed. */
  iosInAppBrowser: boolean;
  /** Epoch ms of the last dismissal, or null when never dismissed / unreadable. */
  dismissedAt: number | null;
  /** Now, injected so the expiry boundary is testable. */
  now: number;
}

export type InstallOffer = "native" | "ios-instructions" | "ios-open-in-safari" | "hidden";

export function installOffer(f: InstallFacts): InstallOffer {
  // 1. Already installed beats everything. Offering to install an installed app is the single most
  //    embarrassing state this component can reach, and it is also the easiest to get right.
  if (f.installed) return "hidden";

  // 2. A live dismissal. `dismissedAt` in the FUTURE is treated as expired rather than trusted:
  //    a clock that moved backwards (timezone change, a corrected system clock) would otherwise
  //    silence the banner for as long as the skew lasts.
  if (f.dismissedAt !== null) {
    const age = f.now - f.dismissedAt;
    if (age >= 0 && age < DISMISS_DAYS * 24 * 60 * 60 * 1000) return "hidden";
  }

  // 3. The native prompt, whenever the browser offers one. Never a hand-rolled installer.
  if (f.nativePromptReady) return "native";

  // 4. An iOS IN-APP browser, checked before Safari because it is the narrower case. A reader who
  //    followed a link from Facebook or LinkedIn is in a WKWebView: WebKit, iOS, and no
  //    "Add to Home Screen" anywhere in its share sheet. Telling them to use the Share menu is an
  //    instruction that cannot be followed, which is worse than saying nothing — so they are told
  //    the one thing that does work, which is to reopen the page in Safari.
  if (f.iosInAppBrowser) return "ios-open-in-safari";

  // 5. iOS Safari proper. No programmatic install exists, so instructions are the honest fallback
  //    — and ONLY here. Showing "use the Share menu" in a browser that has a real install button
  //    would be telling the reader to do the harder thing.
  if (f.iosSafari) return "ios-instructions";

  // 6. Everything else: silence. A desktop Firefox reader has no install path at all, and a banner
  //    they cannot act on is pure noise.
  return "hidden";
}

/** Read the stored dismissal. Never throws — Safari private mode throws on `localStorage` access,
 *  and a reader in private mode should still be offered the app, not hidden from it. */
export function readDismissed(storage?: Pick<Storage, "getItem">): number | null {
  try {
    const raw = (storage ?? window.localStorage).getItem(DISMISS_KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

/** Persist a dismissal. Silent on failure for the same reason: worst case the banner returns. */
export function writeDismissed(now: number, storage?: Pick<Storage, "setItem">): void {
  try {
    (storage ?? window.localStorage).setItem(DISMISS_KEY, String(now));
  } catch {
    /* private mode / quota — the dismissal simply does not persist */
  }
}

/** iOS at all. iPadOS reports itself as a Mac, hence the touch-point check. */
function isIos(ua: string, maxTouchPoints: number): boolean {
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1);
}

/** Third-party BROWSERS on iOS: CriOS = Chrome, FxiOS = Firefox, EdgiOS = Edge, OPiOS/OPT = Opera.
 *  WebKit underneath, but none of them can add to the home screen and none of them is an in-app
 *  browser, so they are neither case below and get no banner at all. */
const IOS_OTHER_BROWSER = /CriOS|FxiOS|EdgiOS|OPiOS|OPT\//;

/**
 * In-app browsers that name themselves. Each token is the marker that app appends to the WebKit
 * user agent:
 *
 *   FBAN / FBAV / FB_IAB / FBIOS   Facebook, Messenger
 *   Instagram                      Instagram
 *   LinkedInApp                    LinkedIn
 *   Line/                          LINE (guarded by a delimiter so it cannot match mid-token)
 *   Twitter                        "Twitter for iPhone"
 *   Snapchat, Pinterest            those apps
 *   MicroMessenger                 WeChat
 *   WhatsApp                       WhatsApp
 *   musical_ly / BytedanceWebview  TikTok
 *   GSA/                           the Google app
 */
const IOS_IN_APP =
  /FBAN|FBAV|FB_IAB|FBIOS|Instagram|LinkedInApp|(?:^|[\s;([])Line\/|Twitter|Snapchat|Pinterest|MicroMessenger|WhatsApp|musical_ly|BytedanceWebview|GSA\//;

/**
 * An iOS in-app browser — a WKWebView embedded in some other app.
 *
 * This matters because its share sheet has **no "Add to Home Screen" item at all**. Showing the
 * Safari instructions there is an instruction that cannot be followed, which is worse than showing
 * nothing; the one thing that does work is to reopen the page in Safari.
 *
 * Two signals, because the named list can never be complete:
 *
 * 1. **A known marker** (above). Robust even for apps like LINE that append their name to the
 *    otherwise-unmodified Safari user agent.
 * 2. **A missing `Version/` token.** Genuine Mobile Safari has always sent `Version/17.0` (or
 *    whichever); a plain WKWebView sends `…AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148`
 *    and no `Version/`. That is what catches the apps nobody enumerated. It also means **genuine
 *    Safari cannot be misclassified by this rule** — it always carries the token.
 *
 * What it cannot catch: `SFSafariViewController`, which apps also use to open links. That is real
 * Safari with a real Safari user agent, and its share sheet likewise omits Add to Home Screen — but
 * nothing in the UA distinguishes it, so it is out of reach of any detection of this shape.
 */
export function isIosInAppBrowser(ua: string, maxTouchPoints = 0): boolean {
  if (!isIos(ua, maxTouchPoints)) return false;
  if (IOS_OTHER_BROWSER.test(ua)) return false;
  return IOS_IN_APP.test(ua) || !/Version\//.test(ua);
}

/**
 * iOS Safari detection, kept narrow on purpose.
 *
 * Deliberately the complement of `isIosInAppBrowser` within iOS rather than an independent test:
 * the two must be mutually exclusive, or the offer would depend on which branch `installOffer`
 * happens to check first.
 */
export function isIosSafari(ua: string, maxTouchPoints = 0): boolean {
  if (!isIos(ua, maxTouchPoints)) return false;
  if (IOS_OTHER_BROWSER.test(ua)) return false;
  return !isIosInAppBrowser(ua, maxTouchPoints);
}
