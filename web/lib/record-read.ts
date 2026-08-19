/**
 * In-app read recorder — the primary reading source (Commit 14).
 *
 * When a reader clicks Read anywhere in the app, we record the read into the ONE canonical pipeline
 * (`POST /api/me/reads`, session-authenticated) *before* opening the publisher. Because the publisher
 * opens in a new tab, the current page stays alive — but we still prefer `navigator.sendBeacon`, which
 * is non-blocking and survives even a same-tab navigation, and fall back to a `keepalive` fetch.
 *
 * `readSource: "app"` and `openedFrom` are additive metadata; the engine never branches on them — it
 * just attributes the same reads table the browser extension writes to. Requires a signed-in session
 * (cookies are sent same-origin); anonymous browsing simply records nothing.
 */
export interface RecordReadInput {
  url: string;
  title?: string;
  description?: string;
  /** The in-app surface: recommendations | discover | stories | search | saved | ai-coach. */
  openedFrom?: string;
  device?: string;
}

const READS_ENDPOINT = "/api/me/reads";

/** The browser's IANA zone (e.g. "Asia/Kolkata"), or undefined where Intl cannot answer. */
function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** Fire a single read into the canonical pipeline. Returns true if it was dispatched. */
export function recordRead(input: RecordReadInput): boolean {
  if (!input.url || !/^https?:\/\//i.test(input.url)) return false;
  const payload = JSON.stringify({
    reads: [
      {
        url: input.url,
        title: input.title ?? "",
        description: input.description ?? "",
        readSource: "app",
        openedFrom: input.openedFrom,
        device: input.device ?? deviceHint(),
        // The reader's IANA zone. A streak counts DAYS, and a day is local — without this the
        // engine files a 02:00 read under the UTC day that ended two hours earlier and breaks a
        // streak that never broke. Sent here because it needs no extra round trip and no settings
        // screen: the engine stores it only when it changes. Undefined on a browser with no Intl
        // (the engine then buckets by UTC, exactly as before).
        timeZone: browserTimeZone(),
      },
    ],
  });

  // Preferred: sendBeacon (non-blocking, survives navigation). A JSON Blob keeps the content-type so
  // the route parses it identically to a fetch body.
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    try {
      if (navigator.sendBeacon(READS_ENDPOINT, new Blob([payload], { type: "application/json" }))) {
        return true;
      }
    } catch {
      /* fall through to keepalive fetch */
    }
  }

  // Fallback: keepalive fetch (also survives the click / a same-tab navigation).
  try {
    void fetch(READS_ENDPOINT, {
      method: "POST",
      body: payload,
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      credentials: "same-origin",
    });
    return true;
  } catch {
    return false;
  }
}

/** A coarse, privacy-light device hint (mobile vs desktop) — additive metadata only. */
function deviceHint(): string | undefined {
  if (typeof navigator === "undefined") return undefined;
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? "mobile" : "desktop";
}
