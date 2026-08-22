import { configureApi } from "@ih/core/api/client";

import { config } from "./config.ts";
import { currentToken } from "./session.ts";

/**
 * Point the shared API client at this deployment, and at the keystore.
 *
 * This is the entire mobile side of the API layer. Everything else — the 33 typed calls, the error
 * normalisation, the response shapes — is `@ih/core/api/services`, shared byte for byte with the web
 * app. The two injected values are exactly the two things that differ between a browser and a phone:
 *
 *   baseUrl   the web is same-origin and passes `""`; a native app has no origin of its own
 *   getToken  the web passes nothing, because the browser attaches a session cookie itself and the
 *             API resolves a session before it looks at any bearer token
 *
 * Read per request rather than captured once, because the token changes while the app is running —
 * sign-in, sign-out, revocation — and a client rebuilt on every change would drop in-flight work.
 */
export function initApi(): void {
  configureApi({
    baseUrl: config.apiBaseUrl,
    getToken: () => currentToken(),
  });
}
