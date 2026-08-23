import axios, { type AxiosInstance } from "axios";

/**
 * The single HTTP client for every Hidden View client.
 *
 * Every `api/services.ts` function talks only to this instance, so transport, base URL, auth and
 * error normalisation live in exactly one place — which is what makes one API layer serve the web
 * app, the Expo app and the browser extension rather than three.
 *
 * **Configured by the host, not by the environment.** The previous version read
 * `process.env.NEXT_PUBLIC_API_BASE_URL` at module load. That is a Next.js build-time substitution:
 * `process` does not exist on React Native, and `NEXT_PUBLIC_*` means nothing there. It was the one
 * genuine platform dependency hiding in this file, and the compiler found it the moment the module
 * moved to a tsconfig without Node types — which is the whole argument for that tsconfig.
 *
 * So the platform half is now injected. Each app calls {@link configureApi} once at startup:
 *
 *   web     configureApi({ baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "" })
 *   mobile  configureApi({ baseUrl: "https://hidden-view.com", getToken: () => secureStore.get() })
 *
 * Defaults are the web's current behaviour exactly — an empty base URL, so requests go to `/api/*`
 * on the same origin — so a host that never calls `configureApi` behaves as this file always did.
 */

/** Everything about the client that differs per platform. */
export interface ApiConfig {
  /**
   * Origin the API lives on. `""` (the default) means same-origin — correct for the web app, where
   * the Next route handlers are co-located. A native client has no origin of its own and must set
   * this to the deployment.
   */
  baseUrl: string;
  /**
   * Bearer token for this request, or `null` when there is none.
   *
   * Returns a value rather than being one, because a native client's token comes out of secure
   * storage and can change while the app is running (sign-in, sign-out, revocation). Reading it per
   * request is what lets that work without rebuilding the client.
   *
   * The web leaves this unset: the browser attaches the session cookie itself, and the whole point
   * of `docs/API_AUTH_MATRIX.md`'s session-first ladder is that a cookie is honoured before any
   * token. Setting it on web would be harmless but pointless.
   */
  getToken?: () => string | null | undefined;
}

const config: ApiConfig = { baseUrl: "" };

/** Point the client at a deployment and, on a native client, at its token source. Call once at startup. */
export function configureApi(next: Partial<ApiConfig>): void {
  if (next.baseUrl !== undefined) {
    config.baseUrl = next.baseUrl;
    api.defaults.baseURL = `${next.baseUrl}/api`;
  }
  if ("getToken" in next) config.getToken = next.getToken;
}

/** The current configuration — for a host that wants to assert what it wired up. */
export function apiConfig(): Readonly<ApiConfig> {
  return config;
}

export const api: AxiosInstance = axios.create({
  baseURL: "/api",
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

// The bearer header, for clients that hold a token. A no-op wherever `getToken` is unset (the web),
// which keeps the browser's request byte-identical to what it sent before this file was shared.
api.interceptors.request.use((request) => {
  const token = config.getToken?.();
  if (token) request.headers.Authorization = `Bearer ${token}`;
  return request;
});

// Normalise errors into a predictable shape for React Query + toasts.
export interface ApiError {
  status: number;
  message: string;
}

api.interceptors.response.use(
  (res) => res,
  (error) => {
    const apiError: ApiError = {
      status: error.response?.status ?? 0,
      message:
        error.response?.data?.message ??
        error.message ??
        "Something went wrong. Please try again.",
    };
    return Promise.reject(apiError);
  },
);

export async function getJson<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const { data } = await api.get<T>(url, { params });
  return data;
}

export async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const { data } = await api.post<T>(url, body);
  return data;
}

export async function deleteJson<T>(url: string, body?: unknown): Promise<T> {
  // axios carries a DELETE body via `data` — used by feedback removal (articleId + type).
  const { data } = await api.delete<T>(url, body === undefined ? undefined : { data: body });
  return data;
}
