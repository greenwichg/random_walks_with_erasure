"use client";

import * as React from "react";
import { reportError } from "@/lib/observability";

/**
 * Root error boundary — the last-resort fallback when an error escapes the root layout or providers.
 * Next.js renders this *in place of* the root layout, so it must supply its own <html>/<body> and cannot
 * rely on the app's theme, i18n, or component library. The copy is therefore plain English and the
 * styling is inline and self-contained, so it renders even if the app's CSS/providers failed to load.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    reportError(error, { digest: error.digest });
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
          background: "#fafafa",
          color: "#18181b",
        }}
      >
        <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "1.5rem" }}>
          <div style={{ maxWidth: "28rem", textAlign: "center" }}>
            <div style={{ fontSize: "2rem", marginBottom: "0.75rem" }} aria-hidden="true">
              ⚠️
            </div>
            <h1 style={{ fontSize: "1.25rem", fontWeight: 600, margin: "0 0 0.5rem" }}>
              Something went wrong
            </h1>
            <p style={{ color: "#71717a", margin: "0 0 1.5rem", fontSize: "0.9rem" }}>
              An unexpected error occurred. You can try again, or reload the page.
            </p>
            <button
              onClick={() => reset()}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "0.5rem",
                border: "1px solid #d4d4d8",
                background: "#ffffff",
                color: "#18181b",
                cursor: "pointer",
                fontSize: "0.9rem",
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
