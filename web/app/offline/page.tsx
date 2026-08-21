import Link from "next/link";
import { WifiOff } from "lucide-react";

/**
 * The one page the service worker serves from cache, and the reason it may hold a cache at all.
 *
 * **Outside `(app)`, and static by construction.** It is precached at install time, so whatever it
 * renders is frozen until the next deploy — which rules out anything personal, anything fetched,
 * and anything translated at runtime (the reader's language lives in a cookie the cache does not
 * see). A static English page that is honest about being static beats a personalised one that
 * would be silently months stale.
 *
 * It is shown ONLY when a navigation has already failed against the network. Everything else —
 * `/api/*`, cross-origin, non-GET, sub-resources — never reaches the worker's `respondWith` at
 * all. See `lib/sw-fetch-policy.ts`.
 */
export const metadata = { title: "Offline" };

export default function OfflinePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <div className="rounded-2xl border bg-card p-8 text-center shadow-soft">
        <span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-muted text-muted-foreground">
          <WifiOff className="h-6 w-6" />
        </span>
        <h1 className="text-lg font-semibold tracking-tight">You&rsquo;re offline</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Hidden View needs a connection to show your reading. Nothing has been lost — reconnect and
          everything will be where you left it.
        </p>
        {/* Said plainly, because it is the question an offline reader actually has: the app does
            not hold a private copy of their news, so there is nothing to read here while offline. */}
        <p className="mt-3 text-xs text-muted-foreground">
          Articles and recommendations aren&rsquo;t stored on your device.
        </p>
        <Link
          href="/"
          className="mt-6 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Try again
        </Link>
      </div>
    </main>
  );
}
