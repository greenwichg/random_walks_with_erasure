/**
 * The read-recording payload — what gets sent, decided once for every client.
 *
 * The *transport* is not here and cannot be. On the web a read is fired as the reader leaves for the
 * publisher, so it goes through `navigator.sendBeacon` (non-blocking, survives the navigation) with
 * a `keepalive` fetch behind it; `sendBeacon` has no React Native equivalent, and on a native client
 * there is no navigation to survive in the first place. Two different answers to "how do I get this
 * out of the process", one answer to "what does it say".
 *
 * So this module builds and validates; `web/lib/record-read.ts` and, later, the Expo app send. The
 * split matters because the payload is where the *meaning* is: `timeZone` in particular is load-
 * bearing, and a client that quietly stopped sending it would break streaks rather than fail.
 */

export interface RecordReadInput {
  url: string;
  title?: string;
  description?: string;
  /** Where the reader clicked from: recommendations | discover | stories | search | saved | ai-coach. */
  openedFrom?: string;
  /** Coarse device hint. Supplied by the caller — only the platform knows. */
  device?: string;
  /**
   * How the request authenticated, from the client's point of view. `"app"` for an in-app read on
   * any platform; the API stamps `"extension"` for a bearer caller that does not say otherwise
   * (see docs/API_AUTH_MATRIX.md), which is why a mobile client should send this explicitly.
   */
  readSource?: string;
  /** The reader's IANA zone. Supplied by the caller — see {@link readsPayload}. */
  timeZone?: string;
}

/** The endpoint every client posts to. Same path on web and mobile; only the origin differs. */
export const READS_ENDPOINT = "/api/me/reads";

/** A read is only worth sending if it names a real article. */
export function isRecordableUrl(url: string | undefined): boolean {
  return !!url && /^https?:\/\//i.test(url);
}

/**
 * The request body for one read.
 *
 * `timeZone` is the field to be careful about. A streak counts DAYS, and a day is local — without
 * it the engine files a 02:00 read under the UTC day that ended two hours earlier and breaks a
 * streak that never broke. It is passed in rather than read here because `Intl` is available on both
 * platforms but the *right* zone is not always the runtime's: a native app may know better. When it
 * is absent the engine buckets by UTC, which is the documented pre-`timeZone` behaviour.
 */
export function readsPayload(input: RecordReadInput): string {
  return JSON.stringify({
    reads: [
      {
        url: input.url,
        title: input.title ?? "",
        description: input.description ?? "",
        readSource: input.readSource ?? "app",
        openedFrom: input.openedFrom,
        device: input.device,
        timeZone: input.timeZone,
      },
    ],
  });
}
