import Constants from "expo-constants";

/**
 * The app's public configuration, from `app.json`'s `expo.extra`.
 *
 * Everything here is a public identifier. OAuth **client ids** are not secrets — the confidential
 * half of an OAuth client is what a web server holds, and native clients have none, which is the
 * reason the ID-token exchange in `lib/auth.ts` is the standard shape rather than a workaround.
 * The one credential this app ever holds is the Hidden View bearer token, and that is minted at
 * runtime and lives in the keystore, never here.
 *
 * `apiBaseUrl` has no same-origin default the way the web does: a native app has no origin, so it
 * must be told where the deployment is.
 */

interface Extra {
  apiBaseUrl?: string;
  googleIosClientId?: string;
  googleAndroidClientId?: string;
  googleWebClientId?: string;
  /** Which EAS profile produced this binary — `development`, `preview`, `production`, or `local`. */
  buildProfile?: string;
}

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

export const config = {
  /** Trailing slash stripped, because every caller appends `/api/...` and `//api` 404s. */
  apiBaseUrl: (extra.apiBaseUrl ?? "").replace(/\/+$/, ""),
  googleIosClientId: extra.googleIosClientId ?? "",
  googleAndroidClientId: extra.googleAndroidClientId ?? "",
  googleWebClientId: extra.googleWebClientId ?? "",
  // Shown on the account row so a tester reporting a result can say WHICH binary produced it. Three
  // builds of the same app pointed at different hosts look identical on a home screen.
  buildProfile: extra.buildProfile ?? "local",
} as const;

/**
 * What is missing, if anything — so the app can say so on screen instead of failing at the first
 * request with a network error that points nowhere.
 *
 * A development build pointed at `localhost` from a physical device is the classic version of this:
 * the phone's localhost is the phone, not the laptop, and the symptom is a timeout rather than a
 * message about configuration.
 */
export function configProblems(): string[] {
  const problems: string[] = [];
  if (!config.apiBaseUrl) {
    problems.push("apiBaseUrl is not set in app.json → expo.extra.");
  } else if (/localhost|127\.0\.0\.1/.test(config.apiBaseUrl)) {
    problems.push(
      "apiBaseUrl points at localhost. That resolves to the PHONE on a real device — use the " +
        "machine's LAN address (or a tunnel) when running outside the simulator.",
    );
  }
  if (!config.googleIosClientId && !config.googleAndroidClientId && !config.googleWebClientId) {
    problems.push(
      "No Google client id is set. Create one per platform in Google Cloud Console and put the " +
        "ids in app.json → expo.extra; the server needs the matching GOOGLE_IOS_CLIENT_ID / " +
        "GOOGLE_ANDROID_CLIENT_ID or it will answer 500 not-configured.",
    );
  }
  return problems;
}
