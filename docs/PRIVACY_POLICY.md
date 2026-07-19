# InfoDiet Privacy Policy

**Last updated: July 19, 2026**

InfoDiet ("we", "our") is an Information Health service: it scores how diverse, calm, and
cross-cutting your news reading is, from articles you choose to record. This policy covers the
InfoDiet web application and the **InfoDiet — Information Health** browser extension. It is
written to match what the software actually does — nothing more.

The canonical public copy of this policy is served by the InfoDiet app at `/privacy`; this
document is its source.

## The short version

- The browser extension records **only the URL and standard page metadata** of news articles you
  open **on a fixed list of supported news sites** — never article text, never your general
  browsing history.
- It sends **nothing anywhere** until you explicitly connect it to your own InfoDiet account with
  an API token.
- Your data is used for exactly one purpose: your own Information Health report and
  recommendations. **We do not sell personal data and show no third-party advertising.**

## What the browser extension collects

When — and only when — you open a page that is an **article** on one of the supported news sites
listed in the extension's manifest, the extension records:

- the article's URL,
- standard page metadata already present in the page's head: title, description, preview-image
  link, publisher/site name, publication date, author byline (when present), and the page's
  declared language,
- the time you opened it.

Article detection uses standard signals only (OpenGraph tags, JSON-LD types, and the page's
`<article>`/headline structure). Section and front pages are not recorded.

## What the extension never collects

- Article body text.
- Your browsing history, or any activity on sites outside the supported list.
- Passwords, form input, cookies, or other page data.
- Analytics or telemetry of any kind: the extension contains no trackers and makes no requests to
  anyone other than the InfoDiet app address you configure.

## When collection starts

The extension is **inert until you connect it**. Out of the box it stores nothing and sends
nothing. Collection begins only after you complete all three setup steps in the extension's
Options page:

1. enter your InfoDiet app address,
2. paste an API token generated in your InfoDiet account (Settings → Connect browser extension),
3. approve the browser permission for exactly that one address.

Completing this setup is how you consent to the collection described above. Removing the token
(or uninstalling the extension) withdraws that consent.

## API tokens

- Tokens are created in your InfoDiet account while signed in, and shown to you **once**.
- The extension stores your token in your browser's local extension storage and sends it as an
  `Authorization: Bearer` header, only to the app address you configured.
- The InfoDiet server stores **only a SHA-256 hash** of each token — never the token itself — so
  a database leak cannot yield a usable token.
- You can revoke any token at any time in InfoDiet → Settings. A revoked token stops working
  immediately.

## Where your data is stored

**In your browser (extension storage):** the app address and API token you configured, plus a
short-lived duplicate-suppression cache of recently recorded article URLs. That cache is
session-scoped and bounded: entries expire after 6 hours, the cache is capped in size, and it is
cleared when your browser session ends. Uninstalling the extension deletes all of its local data.

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

- **Disconnect:** revoke the token in InfoDiet → Settings, or clear it in the extension's
  Options — recording stops immediately.
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
