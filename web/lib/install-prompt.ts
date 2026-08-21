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
  /** Epoch ms of the last dismissal, or null when never dismissed / unreadable. */
  dismissedAt: number | null;
  /** Now, injected so the expiry boundary is testable. */
  now: number;
}

export type InstallOffer = "native" | "ios-instructions" | "hidden";

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

  // 4. iOS has no programmatic install, so instructions are the honest fallback — and ONLY on iOS
  //    Safari. Showing "use the Share menu" in a browser that has a real install button would be
  //    telling the reader to do the harder thing.
  if (f.iosSafari) return "ios-instructions";

  // 5. Everything else: silence. A desktop Firefox reader has no install path at all, and a banner
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

/**
 * iOS Safari detection, kept narrow on purpose.
 *
 * Chrome and Firefox on iOS are WebKit too but cannot add to the home screen at all, so the
 * instructions would be wrong there. iPadOS reports itself as a Mac, hence the touch-point check.
 */
export function isIosSafari(ua: string, maxTouchPoints = 0): boolean {
  const ios = /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1);
  if (!ios) return false;
  // CriOS = Chrome, FxiOS = Firefox, EdgiOS = Edge, OPiOS/OPT = Opera — none can install.
  return !/CriOS|FxiOS|EdgiOS|OPiOS|OPT\//.test(ua);
}
