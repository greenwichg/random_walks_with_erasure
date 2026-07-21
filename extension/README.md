# InfoDiet — Information Health (browser extension)

A deliberately tiny Manifest V3 extension that records **only the URL and standard page metadata**
(title, description, image link, publisher, publication date) of the news articles and blog posts you
open — on **any** site, once you enable capture — and syncs them to your InfoDiet account. This powers
your real **Measured Information Health Report** and feeds each article you discover into the shared
catalog as a first-class `FeedArticle` (born *provisional*; promoted once a feed re-discovers it or
more readers corroborate it). All scoring, classification, and recommendation happen in the backend —
the extension contains none of it. An unknown publisher is still ingested (it contributes to Topic,
Emotion, and Register); it receives **no** political lean and is not recommended.

> **Privacy.** InfoDiet records the URL and standard page metadata (title, description, image link,
> publisher, publication date, language/author when present) of the article pages you open **once you
> enable capture**. It never reads article body text, passwords, cookies, forms, the content of
> non-article pages, or a short list of excluded sites (Gmail, Google Docs, …). Detection activity is
> an anonymous local tally that never leaves the browser. Your token lives only in this browser;
> revoke it any time from InfoDiet → Settings. See `docs/PRIVACY_POLICY.md`.

## Permissions (opt-in, least-privilege)

| Permission | Why |
|---|---|
| `storage` | Store your app URL + token, a short-lived local de-dup cache (session-only), and an anonymous local detection tally. |
| `scripting` | Register the article detector **only after you enable capture**; unregister it if you turn capture off. |
| Capture host permission `https://*/*` (**optional**) | Requested at your click ("Enable capture"). Lets the detector run on the article pages you open on any site — standard metadata only, top frame only, excluding mainstream webmail/office. Nothing at install. |
| Sync host permission (your InfoDiet origin) (**optional**) | Requested when you save your app URL, so the read-sync POST can reach it. |

It does **not** request `tabs`, `cookies`, `history`, or `webNavigation`, and it never fetches the
news sites themselves — it only reads standard metadata already in the page.

## How it works

```
content.js (any HTTPS page, once capture is on)   classifyPage(): article? via og:type / JSON-LD /
   → { url, title, description, image,            article:published_time  (standard metadata only —
       siteName, publishedAt, author,              no page text, no scoring); non-article pages send
       language, observedAt, detectSignal }        only an anonymous outcome label (no URL)
background.js (service worker)                    de-dup locally (6h TTL) + anonymous detection tally
   → POST {appUrl}/api/me/reads                   Authorization: Bearer <token>
web tier (Next.js)                                resolves the token → forwards to the engine
engine /api/me/reads                              scores + dedups → your reading history ← one pipeline
```

The detector is **registered dynamically** after you grant capture (`chrome.scripting`), stays in
lock-step with the permission (`permissions.onAdded/onRemoved`, reconciled on startup), and persists
across browser restarts. The extension talks **only** to your InfoDiet web app, never to the engine.

## Article detection (precision-first, metadata-only)

`classifyPage` (in `common.js`) accepts a page as an article when it declares one via standard signals
— `og:type=article`, an Article-family JSON-LD `@type` (incl. Substack/blog `BlogPosting`), or a
corroborated `article:published_time` + headline — and hard-rejects explicit non-article `og:type`s
(website/product/video/…). It never inspects article body text. See
`docs/EXTENSION_ARTICLE_CAPTURE.md` for the architecture, the compatibility report, and the documented
limitation (pages that publish **no** standard metadata can't be detected; use the app's paste-URL
flow).

## Install (unpacked, for the beta)

1. `chrome://extensions` → **Developer mode** → **Load unpacked** → select this `extension/` folder.
   (Install-time permissions are only `storage` + `scripting` — no page access yet.)
2. In InfoDiet: **Settings → Connect browser extension → Generate token**, and copy it.
3. Extension **Options**: set your **InfoDiet app URL** + **API token** → **Save** (grants that one
   sync origin) → **Test connection**.
4. Click **Enable capture** and approve the prompt.
5. Open any news article or blog post; the badge flashes ✓ when a read is recorded.

## Tests

Pure logic (article detection + regression corpus, URL normalization, de-dup) is unit-tested:

```
node --test extension/common.test.js
```

## Pre-rollout detector verification (live URLs)

Before shipping a detector change, verify it against **real, live** pages (CI/sandbox egress is
network-restricted, so this is run manually on an open network). It reuses the shipping `classifyPage`:

```
node extension/tools/verify-detector.mjs                 # built-in seed
node extension/tools/verify-detector.mjs my-urls.tsv     # "<accept|reject>\t<url>" per line
node extension/tools/verify-detector.mjs --url https://example.com/story
```

## End-to-end verification

The full path (token → configure → enable capture → read → ingest → coverage → measured report) is
verified against the running stack: generate a token, configure the extension, enable capture, open
≥ 5 articles (any publisher), and the report flips from **Initial Estimate** to **Measured** once
coverage reaches the threshold.
