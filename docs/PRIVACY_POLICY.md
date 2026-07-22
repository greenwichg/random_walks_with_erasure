# InfoDiet Privacy Policy

**Last updated: July 22, 2026**

InfoDiet ("we", "our") is an Information Health service: it scores how diverse, calm, and
cross-cutting your news reading is, from articles you choose to record. This policy covers the
InfoDiet web application and the **InfoDiet — Information Health** browser extension. It is
written to match what the software actually does — nothing more.

The canonical public copy of this policy is served by the InfoDiet app at `/privacy`; this
document is its source.

## The short version

- The browser extension records **only the URL and standard page metadata** of news articles you
  open — never article text, and never the content of pages that aren't articles.
- It runs on **no page and sends nothing anywhere** until you connect it to your own InfoDiet
  account and turn on capture. Turning capture off stops it immediately.
- Your data is used for exactly one purpose: your own Information Health report and
  recommendations. **We do not sell personal data and show no third-party advertising.**

## What the browser extension collects

Once you connect the extension and enable capture, then — and only when — you open a page that is
an **article**, the extension records:

- the article's URL,
- standard page metadata already present in the page's head: title, description, preview-image
  link, publisher/site name, publication date, author byline (when present), and the page's
  declared language,
- the time you opened it.

Article detection uses standard, publisher-declared metadata only: the page's OpenGraph type
(`og:type`), JSON-LD article types, and an article-publication-time tag alongside a headline.
Pages that do not identify themselves as articles — home, section, and search pages, and product,
video, and app pages — are not recorded.

## Running on the pages you read

To detect articles on whatever news site or blog you choose, the extension asks — when you click
**Enable capture** — for permission to run on the websites you visit. On each page you open it
reads only the standard metadata above to decide whether the page is an article, and it records
and sends data **only** when the page is an article. It never reads the body text of an article,
and never reads the content of a non-article page. As an added safeguard, the detector never runs
on a short list of sensitive sites (major webmail and office suites) even after you enable capture.
Turning capture off removes this access and stops the detector on every page.

## What the extension never collects

- Article body text.
- The content of pages that aren't articles.
- Passwords, form input, cookies, or other page data.
- Networked analytics or tracking of any kind: the extension contains no trackers and makes no
  requests to anyone other than the InfoDiet app address you configure. (It keeps an anonymous
  local count of detection outcomes — which signal matched, or why a page was skipped — that stays
  in your browser and is never sent anywhere.)

## When collection starts

The extension is **inert until you set it up**. Out of the box it stores nothing, runs on no page,
and sends nothing. You turn it on in the extension's Options page:

1. enter your InfoDiet app address,
2. paste an API token generated in your InfoDiet account (Settings → Connect browser extension),
   and approve access to that address so your reads can be sent to it,
3. click **Enable capture** and approve access to the sites you read, so articles can be detected.

Completing this setup is how you consent to the collection described above. Turning off capture,
removing the token, or uninstalling the extension withdraws that consent.

## API tokens

- Tokens are created in your InfoDiet account while signed in, and shown to you **once**.
- The extension stores your token in your browser's local extension storage and sends it as an
  `Authorization: Bearer` header, only to the app address you configured.
- The InfoDiet server stores **only a SHA-256 hash** of each token — never the token itself — so
  a database leak cannot yield a usable token.
- You can revoke any token at any time in InfoDiet → Settings. A revoked token stops working
  immediately.

## Where your data is stored

**In your browser (extension storage):** the app address and API token you configured, a
short-lived duplicate-suppression cache of recently recorded article URLs, and an anonymous local
count of detection outcomes. The dedupe cache is session-scoped and bounded: entries expire after
6 hours, the cache is capped in size, and it is cleared when your browser session ends. None of
this local data is ever sent anywhere; uninstalling the extension deletes all of it.

**On the InfoDiet server (your account):** your recorded reads (the article data listed above,
tagged with the source they came from, e.g. "extension" or "app"), your account profile from
sign-in (name and email from your Google account), your settings, API-token hashes, and the
reports and recommendations derived from your reads.

## How data is transmitted

The extension transmits reads **only to the InfoDiet app address you configure** — never to third
parties. Use an HTTPS address for any remote deployment; a plain-HTTP address is possible only
for local, self-hosted testing addresses you choose yourself (such as `http://localhost`).

## Retention

- Browser-side: the token and app address remain until you change them, clear them, or uninstall
  the extension; the duplicate-suppression cache expires on its own as described above.
- Server-side: your reads, settings, and derived reports are retained while your account exists,
  so your Information Health history stays available to you. Revoked tokens stop working
  immediately; we retain only their hashes.
- You can request deletion of your account data at any time using the contact below, and we will
  delete it. (Self-serve account deletion is not yet built into the product.)

## Your controls

- **Disconnect:** turn off capture, revoke the token in InfoDiet → Settings, or clear it in the
  extension's Options — recording stops immediately.
- **Uninstall:** removes everything the extension stored in your browser.
- **Delete:** email us to have your account data deleted.

## No sale of data, no advertising

We do not sell, rent, or trade personal data. We do not share it with third parties for their own
purposes. InfoDiet shows no third-party advertising. Your reading data is used solely to provide
InfoDiet's own features to you: your report, recommendations, coaching, and notifications.

## Changes to this policy

If this policy changes, the "Last updated" date above changes with it, and the current version is
always available at the InfoDiet app's `/privacy` page.

## Contact

Questions, or a data-deletion request: **yerram.saisanath@gmail.com**
