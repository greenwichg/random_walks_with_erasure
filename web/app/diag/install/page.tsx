import type { Metadata } from "next";
import { InstallDiagnostic } from "@/components/diag/install-diagnostic";

/**
 * TEMPORARY — delete this route once the iOS in-app-browser detection is settled.
 *
 * Why it exists: production logs proved the phone's user agent is
 *
 *   Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15
 *   (KHTML, like Gecko) Version/26.6 Mobile/15E148 Safari/604.1
 *
 * — a string with `Version/` present and no in-app marker of any kind, i.e. shaped exactly like
 * Mobile Safari. No user-agent test can separate that from Safari, so the next detection signal
 * has to be a runtime capability probe. This page measures the candidate probes on the real
 * devices instead of me guessing which one works.
 *
 * Public on purpose: an in-app WKWebView has its own cookie jar and does not carry the Safari
 * session, so anything behind the auth matcher would be untestable in the browser we care about.
 * It reads nothing and writes nothing — it only reports what the browser says about itself.
 */
export const metadata: Metadata = {
  title: "Install diagnostic",
  // Temporary and uninteresting to anyone else; keep it out of search results.
  robots: { index: false, follow: false },
};

export default function InstallDiagnosticPage() {
  return <InstallDiagnostic />;
}
