# InfoDiet — Information Health (browser extension)

A deliberately tiny Manifest V3 extension that records **only the URL and title** of supported
news articles you open, and syncs them to your InfoDiet account so your reading produces a real
**Measured Information Health Report**. All scoring, classification, and recommendation happen in
the backend — the extension contains none of it.

> **Privacy.** InfoDiet only records the URL and title of supported news articles you open. We do
> not read page contents, browsing history, passwords, or activity outside supported news sites.
> Your token lives only in this browser; revoke it any time from InfoDiet → Settings.

## Permissions (kept to the minimum)

| Permission | Why |
|---|---|
| `storage` | Store your app URL + token, and a short-lived local de-dup cache (session-only). |
| `optional_host_permissions` | Access to **only the InfoDiet app origin you configure**, requested at setup — nothing is granted until you save a URL. |
| content scripts on a **static news allowlist** | A small detector runs only on supported news sites (listed in `manifest.json`). |

It does **not** request `tabs`, `cookies`, `history`, `webNavigation`, or `<all_urls>`. It never
fetches the news sites themselves — it only reads standard metadata already in the page.

## How it works

```
content.js (allowlisted news page)      detect article via og:type / JSON-LD / <article>+<h1>
   → { url, title, observedAt }         (no page text, no scoring)
background.js (service worker)          de-dup locally (6h TTL, session storage)
   → POST {appUrl}/api/me/reads         Authorization: Bearer <token>
web tier (Next.js)                      resolves the token → forwards to the engine
engine /api/me/reads                    scores + dedups → your reading history  ← single pipeline
```

The extension talks **only** to your InfoDiet web app, never to the engine directly.

## Install (unpacked, for the beta)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select this `extension/` folder.
2. In InfoDiet: **Settings → Connect browser extension → Generate token**, and copy it.
3. Right-click the extension → **Options** (or `chrome://extensions` → Details → Extension options):
   - **InfoDiet app URL** — e.g. `http://localhost:3000` (dev) or your deployed app.
   - **API token** — paste the token.
   - **Save** (grants access to just that origin), then **Test connection**.
4. Open a supported news article; the badge flashes ✓ when a read is recorded.

## Supported sites

The allowlist in `manifest.json` (NYT, WaPo, WSJ, Fox, CNN, NBC, AP, Reuters, NPR, BBC, Guardian,
Politico, The Hill, USA Today, CNBC, Bloomberg, ABC, CBS, Al Jazeera, Axios, Vox, The Atlantic,
LA Times). Expanding it is a one-line manifest change per domain (a static host list is an MV3
requirement for content scripts).

## Tests

Pure logic (article detection, URL normalization, de-dup) is unit-tested:

```
node --test extension/common.test.js
```

## End-to-end verification

The full path (token → configure → read → ingest → coverage → measured report) is verified against
the running stack; see the C3.3 review notes. In short: generate a token in Settings, configure the
extension, open ≥ 5 supported articles, and the report flips from **Initial Estimate** to
**Measured** once coverage reaches the threshold.
