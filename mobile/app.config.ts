import type { ExpoConfig } from "expo/config";

/**
 * The Expo config, resolved at build time from the environment.
 *
 * This replaced a static `app.json` for one reason: the OAuth client ids and the API host differ per
 * environment and must not be committed. They are not secrets — a native OAuth client has no
 * confidential half, which is why the ID-token exchange works at all — but "not a secret" is not the
 * same as "belongs in git", and a repository that carries one deployment's identifiers is one where
 * the next deployment gets them by accident.
 *
 * Values come from `EXPO_PUBLIC_*` variables, which Expo loads from `mobile/.env` (gitignored) for
 * local runs and which EAS supplies per profile for cloud builds. `mobile/.env.example` documents
 * every one of them.
 *
 * Nothing here is a credential. The only credential this app ever holds is the Hidden View bearer
 * token, minted at runtime by `POST /api/auth/mobile` and written to the platform keystore.
 */

const APP_ID = "com.hiddenview.app";

/**
 * The EAS project this app builds under. Hardcoded, unlike everything else in `extra`.
 *
 * It has to be: `eas` commands read it out of the resolved config to know which project they are
 * talking to, and they evaluate `app.config.ts` WITHOUT loading `mobile/.env`, so a value read from
 * the environment is simply absent when EAS looks for it. The first version read it from
 * `EXPO_PUBLIC_EAS_PROJECT_ID` and every EAS command failed with "Cannot automatically write to
 * dynamic config" — EAS trying to link a project it could not see was already linked.
 *
 * Safe to commit, and different in kind from the other values here: it is not a per-deployment
 * setting, it identifies one Expo project, and it is already public — it appears in the project's
 * own URL, expo.dev/accounts/saierram/projects/hidden-view. The environment can still override it
 * for a fork that builds under its own EAS account.
 */
const EAS_PROJECT_ID = "93fea076-950c-4f98-9029-0210219a6a36";

/** Empty string rather than `undefined`, so a missing value is reported by `verify-config`, not by a crash. */
const env = (name: string): string => (process.env[name] ?? "").trim();

const config: ExpoConfig = {
  name: "Hidden View",
  slug: "hidden-view",
  version: "0.1.0",
  orientation: "portrait",
  scheme: "hiddenview",
  userInterfaceStyle: "automatic",
  newArchEnabled: true,
  ios: {
    supportsTablet: true,
    bundleIdentifier: APP_ID,
  },
  android: {
    package: APP_ID,
    edgeToEdgeEnabled: true,
  },
  plugins: ["expo-router", "expo-secure-store", "expo-web-browser"],
  experiments: { typedRoutes: true },
  extra: {
    apiBaseUrl: env("EXPO_PUBLIC_API_BASE_URL"),
    googleIosClientId: env("EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID"),
    googleAndroidClientId: env("EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID"),
    googleWebClientId: env("EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID"),
    /** Which build profile produced this binary — shown on the diagnostics row so a tester can say. */
    buildProfile: env("EXPO_PUBLIC_BUILD_PROFILE") || "local",
    eas: { projectId: env("EXPO_PUBLIC_EAS_PROJECT_ID") || EAS_PROJECT_ID },
  },
};

export default config;
