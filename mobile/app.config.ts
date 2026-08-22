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
    eas: { projectId: env("EXPO_PUBLIC_EAS_PROJECT_ID") || undefined },
  },
};

export default config;
