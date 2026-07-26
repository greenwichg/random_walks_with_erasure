import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "What InfoDiet and its browser extension collect (article URLs and standard page metadata on the sites you read, only after you connect an account and enable capture), what they never collect, and your controls.",
};

/**
 * The public privacy policy — the page the Chrome Web Store listing links to. Deliberately a
 * static, English-only server component (a legal document has one canonical text; it is not run
 * through the i18n catalogs). Source of record: docs/PRIVACY_POLICY.md — keep the two in sync.
 * Public by design: this route is outside the auth middleware matcher.
 */
export default function PrivacyPage() {
  return (
    <div className="min-h-screen">
      <header className="glass sticky top-0 z-20 flex h-16 items-center border-b px-4 lg:px-8">
        <span className="text-sm font-semibold">InfoDiet — Information Health</span>
      </header>
      <main className="mx-auto w-full max-w-2xl px-4 py-10 lg:py-14">
        <h1 className="text-2xl font-semibold tracking-tight">InfoDiet Privacy Policy</h1>
        <p className="mt-2 text-sm text-muted-foreground">Last updated: July 22, 2026</p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed">
          <p>
            InfoDiet (&ldquo;we&rdquo;, &ldquo;our&rdquo;) is an Information Health service: it scores how
            diverse, calm, and cross-cutting your news reading is, from articles you choose to record. This
            policy covers the InfoDiet web application and the <strong>InfoDiet — Information Health</strong>{" "}
            browser extension. It is written to match what the software actually does — nothing more.
          </p>

          <Section title="The short version">
            <ul className="list-disc space-y-2 pl-5">
              <li>
                The browser extension records <strong>only the URL and standard page metadata</strong> of news
                articles you open — never article text, and never the content of pages that aren&rsquo;t
                articles.
              </li>
              <li>
                It runs on <strong>no page and sends nothing anywhere</strong> until you connect it to your own
                InfoDiet account and turn on capture. Turning capture off stops it immediately.
              </li>
              <li>
                Your data is used for exactly one purpose: your own Information Health report and
                recommendations. <strong>We do not sell personal data and show no third-party advertising.</strong>
              </li>
            </ul>
          </Section>

          <Section title="What the browser extension collects">
            <p>
              Once you connect the extension and enable capture, then — and only when — you open a page that is
              an <em>article</em>, the extension records:
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>the article&rsquo;s URL,</li>
              <li>
                standard page metadata already present in the page&rsquo;s head: title, description,
                preview-image link, publisher/site name, publication date, author byline (when present), and
                the page&rsquo;s declared language,
              </li>
              <li>the time you opened it.</li>
            </ul>
            <p className="mt-2">
              Article detection uses standard, publisher-declared metadata only: the page&rsquo;s OpenGraph type
              (<code>og:type</code>), JSON-LD article types, and an article-publication-time tag alongside a
              headline. Pages that do not identify themselves as articles — home, section, and search pages, and
              product, video, and app pages — are not recorded.
            </p>
          </Section>

          <Section title="Running on the pages you read">
            <p>
              To detect articles on whatever news site or blog you choose, the extension asks — when you click{" "}
              <strong>Enable capture</strong> — for permission to run on the websites you visit. On each page you
              open it reads only the standard metadata above to decide whether the page is an article, and it
              records and sends data <strong>only</strong> when the page is an article. It never reads the body
              text of an article, and never reads the content of a non-article page. As an added safeguard, the
              detector never runs on a short list of sensitive sites (major webmail and office suites) even
              after you enable capture. Turning capture off removes this access and stops the detector on every
              page.
            </p>
          </Section>

          <Section title="What the extension never collects">
            <ul className="list-disc space-y-1 pl-5">
              <li>Article body text.</li>
              <li>The content of pages that aren&rsquo;t articles.</li>
              <li>Passwords, form input, cookies, or other page data.</li>
              <li>
                Networked analytics or tracking of any kind: the extension contains no trackers and makes no
                requests to anyone other than the InfoDiet app address you configure. It keeps an anonymous
                local count of detection outcomes (which signal matched, or why a page was skipped) that stays
                in your browser and is never sent anywhere.
              </li>
            </ul>
          </Section>

          <Section title="When collection starts">
            <p>
              The extension is <strong>inert until you set it up</strong>. Out of the box it stores nothing,
              runs on no page, and sends nothing. You turn it on in the extension&rsquo;s Options page:
            </p>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>enter your InfoDiet app address,</li>
              <li>
                paste an API token generated in your InfoDiet account (Settings → Connect browser extension),
                and approve access to that address so your reads can be sent to it,
              </li>
              <li>
                click <strong>Enable capture</strong> and approve access to the sites you read, so articles can
                be detected.
              </li>
            </ol>
            <p className="mt-2">
              Completing this setup is how you consent to the collection described above. Turning off capture,
              removing the token, or uninstalling the extension withdraws that consent.
            </p>
          </Section>

          <Section title="API tokens">
            <ul className="list-disc space-y-1 pl-5">
              <li>Tokens are created in your InfoDiet account while signed in, and shown to you once.</li>
              <li>
                The extension stores your token in your browser&rsquo;s local extension storage and sends it
                as an <code>Authorization: Bearer</code> header, only to the app address you configured.
              </li>
              <li>
                The InfoDiet server stores only a SHA-256 <em>hash</em> of each token — never the token
                itself — so a database leak cannot yield a usable token.
              </li>
              <li>
                You can revoke any token at any time in InfoDiet → Settings. A revoked token stops working
                immediately.
              </li>
            </ul>
          </Section>

          <Section title="Where your data is stored">
            <p>
              <strong>In your browser (extension storage):</strong> the app address and API token you
              configured, a short-lived duplicate-suppression cache of recently recorded article URLs, and an
              anonymous local count of detection outcomes. The dedupe cache is session-scoped and bounded:
              entries expire after 6 hours, the cache is capped in size, and it is cleared when your browser
              session ends. None of this local data is ever sent anywhere; uninstalling the extension deletes
              all of it.
            </p>
            <p className="mt-2">
              <strong>On the InfoDiet server (your account):</strong> your recorded reads (the article data
              listed above, tagged with the source they came from, e.g. &ldquo;extension&rdquo; or
              &ldquo;app&rdquo;), your account profile from sign-in (name and email from your Google account),
              your settings, API-token hashes, and the reports and recommendations derived from your reads.
            </p>
          </Section>

          <Section title="How data is transmitted">
            <p>
              The extension transmits reads <strong>only to the InfoDiet app address you configure</strong> —
              never to third parties. Use an HTTPS address for any remote deployment; a plain-HTTP address is
              possible only for local, self-hosted testing addresses you choose yourself (such as{" "}
              <code>http://localhost</code>).
            </p>
          </Section>

          <Section title="Retention">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                Browser-side: the token and app address remain until you change them, clear them, or
                uninstall the extension; the duplicate-suppression cache expires on its own as described
                above.
              </li>
              <li>
                Server-side: your reads, settings, and derived reports are retained while your account
                exists, so your Information Health history stays available to you. Revoked tokens stop
                working immediately; we retain only their hashes.
              </li>
              <li>
                You can request deletion of your account data at any time using the contact below, and we
                will delete it. (Self-serve account deletion is not yet built into the product.)
              </li>
            </ul>
          </Section>

          <Section title="Your controls">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                <strong>Disconnect:</strong> turn off capture, revoke the token in InfoDiet → Settings, or
                clear it in the extension&rsquo;s Options — recording stops immediately.
              </li>
              <li>
                <strong>Uninstall:</strong> removes everything the extension stored in your browser.
              </li>
              <li>
                <strong>Delete:</strong> email us to have your account data deleted.
              </li>
            </ul>
          </Section>

          <Section title="No sale of data, no advertising">
            <p>
              We do not sell, rent, or trade personal data. We do not share it with third parties for their
              own purposes. InfoDiet shows no third-party advertising. Your reading data is used solely to
              provide InfoDiet&rsquo;s own features to you: your report, recommendations, guidance, and
              notifications.
            </p>
          </Section>

          <Section title="Changes to this policy">
            <p>
              If this policy changes, the &ldquo;Last updated&rdquo; date above changes with it, and the
              current version is always available at this page.
            </p>
          </Section>

          <Section title="Contact">
            <p>
              Questions, or a data-deletion request:{" "}
              <a className="font-medium text-primary hover:underline" href="mailto:yerram.saisanath@gmail.com">
                yerram.saisanath@gmail.com
              </a>
            </p>
          </Section>
        </div>
      </main>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-base font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}
