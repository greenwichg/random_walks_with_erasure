// Compatibility shim + the web's one piece of API configuration.
//
// The client moved to @ih/core/api/client so the Expo app can share it. What could NOT move is the
// base URL: it came from `process.env.NEXT_PUBLIC_API_BASE_URL`, a Next.js build-time substitution
// that does not exist on React Native. So the core client takes it as configuration, and the web
// supplies it here — on module load, exactly when the old code read it, producing the same instance
// with the same baseURL. Nothing about the requests the browser sends has changed.
import { configureApi } from "@ih/core/api/client";

configureApi({ baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "" });

export * from "@ih/core/api/client";
