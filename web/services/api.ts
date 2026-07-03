import axios, { type AxiosInstance } from "axios";

/**
 * The single HTTP client for the whole app.
 *
 * Base URL is env-driven (`NEXT_PUBLIC_API_BASE_URL`). When empty (the default),
 * requests hit the co-located Next.js mock API routes under `/api/*`. To switch
 * to the real Python backend, set that env var to its origin — no other change
 * is needed anywhere in the app. Every `services/*.ts` module talks only to this
 * client, so the transport, auth, and error handling live in exactly one place.
 */
export const api: AxiosInstance = axios.create({
  baseURL: `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api`,
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

// Central place to attach auth tokens once the backend has auth.
api.interceptors.request.use((config) => {
  // e.g. const token = getToken(); if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
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
