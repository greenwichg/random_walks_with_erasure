import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "About the Hidden View crawler",
  description:
    "What Hidden View's automated agents fetch from publishers, what they never fetch, how often, and how to make them stop.",
};

/**
 * The page our User-Agent strings point at.
 *
 * Every automated request Hidden View makes carries `(+https://hidden-view.com/crawler)`. Before
 * this page existed that URL 404'd, and the RSS poller's agent pointed at a documentation site
 * belonging to another organisation entirely — so a publisher trying to find out who was polling
 * their newsroom was sent to the wrong company (F2 of the M7 Stage 2 audit).
 *
 * Everything stated here is checked against the code rather than aspirational. If the behaviour
 * changes, this page changes with it: an agent string pointing at a page that describes something
 * we no longer do is worse than no page at all.
 *
 * Public by design: `/crawler` is outside the auth middleware matcher in `web/middleware.ts`.
 */
export default function CrawlerPage() {
  return (
    <div className="min-h-screen">
      <header className="glass sticky top-0 z-20 flex h-16 items-center border-b px-4 lg:px-8">
        <span className="text-sm font-semibold">Hidden View</span>
      </header>
      <main className="mx-auto w-full max-w-2xl px-4 py-10 lg:py-14">
        <h1 className="text-2xl font-semibold tracking-tight">About our crawler</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          For publishers, editors, and anyone reading their server logs.
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed">
          <section>
            <p>
              Hidden View helps people understand and balance their news diet. To do that we read
              the feeds newsrooms publish, so we can show readers which outlets covered a story and
              which did not. We link people to your site to read the article itself.
            </p>
          </section>

          <section>
            <h2 className="text-base font-semibold">Who we are in your logs</h2>
            <ul className="mt-3 space-y-1 font-mono text-xs">
              <li>HiddenView-RSS/0.1 (+https://hidden-view.com/crawler)</li>
              <li>HiddenView-Crawler/0.1 (+https://hidden-view.com/crawler)</li>
              <li>HiddenView-Robots/0.1 (+https://hidden-view.com/crawler)</li>
            </ul>
            <p className="mt-3">
              Every automated request we make identifies itself this way. If you see traffic
              claiming to be Hidden View without one of these agents, it is not us.
            </p>
          </section>

          <section>
            <h2 className="text-base font-semibold">What we fetch</h2>
            <ul className="mt-3 list-disc space-y-1 pl-5">
              <li>
                <code>robots.txt</code>, before anything else.
              </li>
              <li>RSS and Atom feeds you publish.</li>
              <li>
                A site&rsquo;s home page, once, when we are looking for the{" "}
                <code>&lt;link rel=&quot;alternate&quot;&gt;</code> that advertises a feed.
              </li>
            </ul>
            <h2 className="mt-6 text-base font-semibold">What we never fetch</h2>
            <p className="mt-3">
              <strong>Article pages.</strong> We do not crawl, download, or store the text of your
              articles from your site. We keep the headline, link, publication time, and the summary
              your feed itself provides — and we link readers to you.
            </p>
          </section>

          <section>
            <h2 className="text-base font-semibold">How often</h2>
            <p className="mt-3">
              A feed is polled at most once every two minutes, and in practice far less: the
              interval widens automatically toward six hours for feeds that update rarely, and we
              send <code>If-None-Match</code> and <code>If-Modified-Since</code> so an unchanged
              feed costs you a <code>304</code> and no body. We read your <code>robots.txt</code>{" "}
              at most once a day and cache it, as RFC 9309 asks.
            </p>
            <p className="mt-3">
              To show your mark beside your articles we fetch your homepage once, read the icon it
              declares (<code>&lt;link rel=&quot;icon&quot;&gt;</code>, the Apple touch icon, a web-app
              manifest), and verify at most four of them. That fetch obeys the same{" "}
              <code>robots.txt</code> rules and the same per-host pause as everything else, and its
              verdict — including &ldquo;nothing usable&rdquo; — is kept for weeks, so it is not
              repeated. We never store the image itself, only its address.
            </p>
          </section>

          <section>
            <h2 className="text-base font-semibold">How to stop us</h2>
            <p className="mt-3">
              Disallow us in <code>robots.txt</code> and we stop. You can name us specifically:
            </p>
            <pre className="mt-3 overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">
              {`User-agent: HiddenView-RSS\nDisallow: /`}
            </pre>
            <p className="mt-3">
              We check before each fetch and honour an explicit <code>Disallow</code>. If your{" "}
              <code>robots.txt</code> is temporarily unreachable we do not treat that as permission
              to do anything new — we keep to the last policy we successfully read.
            </p>
            <p className="mt-3">
              You can also just ask. Email{" "}
              <a
                className="font-medium text-primary hover:underline"
                href="mailto:yerram.saisanath@gmail.com"
              >
                yerram.saisanath@gmail.com
              </a>{" "}
              and we will remove your outlet. We would rather hear from you than be blocked, but
              blocking works and needs no reply from us.
            </p>
          </section>

          <section>
            <h2 className="text-base font-semibold">Not for training models</h2>
            <p className="mt-3">
              We do not use your content to train machine-learning models, and we do not sell or
              redistribute it. It is used to tell a reader which outlets covered a story, and to
              link them to yours.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
