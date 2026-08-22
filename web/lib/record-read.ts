/**
 * In-app read recorder — the primary reading source (Commit 14). **The web's transport half.**
 *
 * When a reader clicks Read anywhere in the app, we record the read into the ONE canonical pipeline
 * (`POST /api/me/reads`, session-authenticated) *before* opening the publisher. Because the publisher
 * opens in a new tab, the current page stays alive — but we still prefer `navigator.sendBeacon`, which
 * is non-blocking and survives even a same-tab navigation, and fall back to a `keepalive` fetch.
 *
 * `readSource: "app"` and `openedFrom` are additive metadata; the engine never branches on them — it
 * just attributes the same reads table the browser extension writes to. Requires a signed-in session
 * (cookies are sent same-origin); anonymous browsing simply records nothing.
 *
 * What the request SAYS is `@ih/core/logic/record-read`; what sends it is here. `sendBeacon` has no
 * React Native equivalent and cannot be replaced by a plain fetch on the web — surviving the
 * navigation is the entire point — so the transport is the half that could not be shared. The
 * payload could, and the field that most needed sharing is `timeZone`: a client that quietly stopped
 * sending it would break streaks rather than fail.
 */
import {
  READS_ENDPOINT,
  isRecordableUrl,
  readsPayload,
  type RecordReadInput,
} from "@ih/core/logic/record-read";

export type { RecordReadInput };

/** The browser's IANA zone (e.g. "Asia/Kolkata"), or undefined where Intl cannot answer. */
function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** A coarse, privacy-light device hint (mobile vs desktop) — additive metadata only. */
function deviceHint(): string | undefined {
  if (typeof navigator === "undefined") return undefined;
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? "mobile" : "desktop";
}

/** Fire a single read into the canonical pipeline. Returns true if it was dispatched. */
export function recordRead(input: RecordReadInput): boolean {
  if (!isRecordableUrl(input.url)) return false;
  const payload = readsPayload({
    ...input,
    device: input.device ?? deviceHint(),
    timeZone: input.timeZone ?? browserTimeZone(),
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
