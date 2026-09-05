import { postJson } from "@ih/core/api/client";
import { isRecordableUrl, readsPayload, type RecordReadInput } from "@ih/core/logic/record-read";

export type { RecordReadInput };

/**
 * In-app read recorder — **the native transport half.**
 *
 * What the request SAYS is `@ih/core/logic/record-read` (shared with the web, `timeZone`
 * included — the field that keeps streaks honest). What SENDS it differs by platform: the web
 * fires a `sendBeacon` as the tab leaves for the publisher; a phone opens the publisher in an
 * in-app browser and this process stays alive, so a plain authenticated POST through the shared
 * client is the whole transport. The bearer token rides on it like every other request, and
 * `readSource: "app"` (the payload's default) says which client this was.
 */

/** The device's IANA zone, or undefined where Intl cannot answer. */
function deviceTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** Fire a single read into the canonical pipeline. Resolves true if it was dispatched. */
export async function recordRead(input: RecordReadInput): Promise<boolean> {
  if (!isRecordableUrl(input.url)) return false;
  const payload = JSON.parse(
    readsPayload({
      ...input,
      device: input.device ?? "mobile",
      timeZone: input.timeZone ?? deviceTimeZone(),
    }),
  ) as unknown;
  try {
    // The shared client is rooted at `/api`, and READS_ENDPOINT is `/api/me/reads`.
    await postJson("/me/reads", payload);
    return true;
  } catch {
    // Offline, or a token the server no longer honours: the reader still gets their article, and
    // the next read tries again. A read that fails to record is not a reason to stop reading.
    return false;
  }
}
