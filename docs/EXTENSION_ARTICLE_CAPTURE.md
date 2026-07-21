# Browser extension — capture any article (architecture, detector, limitation)

**Status:** implemented (extension v0.2.0). **Goal:** capture genuine articles from *any* publisher
— known or unknown, major or independent — determined by *"is this an extractable article?"*, never by
a publisher allowlist. Unknown publishers ingest and contribute to Topic / Emotion / Register; they
receive **no** political lean and are excluded from recommendations (unchanged three-dataset behavior).

## Why the backend needed no change

The engine was already publisher-agnostic. `/api/me/reads` gates only on `ingest.has_host` (a junk-URL
check, any real domain passes); `Scorer._resolve_outlet` maps an unknown outlet to `(domain, NaN lean)`
and still ingests; `rss_ingest.ingest_entries` (the same producer path an extension read feeds) counts
unknown-outlet articles but ingests them; the recommendation-corpus projection drops NaN-lean rows
(searchable ≠ recommendable) and the outlet registry is the only source of lean. So the **only**
publisher restriction in the whole system was the extension's content-script allowlist.

## The capture model (Option B — opt-in, dynamic registration)

- **Install-time permissions:** `storage`, `scripting` only. **No host access, no content script at
  install** — nothing runs on any page.
- **Capture is opt-in:** the Options page requests the `https://*/*` host permission at the user's click
  (`chrome.permissions.request`); on grant, the service worker registers the detector via
  `chrome.scripting.registerContentScripts` (HTTPS-only, `excludeMatches` for mainstream webmail/office,
  top frame only, ISOLATED world, `persistAcrossSessions`).
- **Lifecycle:** `permissions.onAdded/onRemoved` register/unregister; `onStartup`/`onInstalled`
  reconcile the registration with the live grant (both persist across restart; reconcile guards desync).
  Revoking capture (chrome://extensions or the Options "Turn off capture") unregisters the detector.
- **No install-time permission increase** ⇒ Chrome does not force existing users to re-accept on update;
  capture stays off until opted in.
- **Privacy:** metadata-only (OpenGraph / JSON-LD / `<h1>` / URL), never article body text; non-article
  pages send only an anonymous outcome label (no URL). See `PRIVACY_POLICY.md`.

## The detector (`common.js` `classifyPage`) — precision-first, metadata-only

Because capture is now web-wide, `classifyPage` is the **sole** guard separating "Should ingest"
(genuine articles) from "Should NOT ingest" (search / category / home / product / video / webmail /
dashboards / docs). Decision order:

1. `og:type == "article"` → **accept** (signal `og:type`) — the near-universal article signal.
2. `og:type` present & non-article (`website`/`profile`/`product`/`book`/`game`/`place`/`video.*`/`music.*`)
   → **reject** (`nonarticle-og`) — a publisher's explicit "not an article" overrides stray JSON-LD.
3. an Article-family JSON-LD `@type` (`*NewsArticle` subtree, or `article`/`blogposting`/`liveblogposting`/
   `report`/`reportagenewsarticle` — includes Substack/blog `BlogPosting`) → **accept** (`jsonld`).
4. `article:published_time` + a headline → **accept** (`published_time`) — a narrow corroborated fallback.
5. otherwise → **reject** (`no-signal`).

The old bare `<article>+<h1>` DOM heuristic is **removed** — web-wide it self-identifies on GitHub /
Wikipedia / Stack Overflow / marketing / section pages. We never read body text, so a page carrying no
standard article metadata is indistinguishable from a non-article (see Limitation).

## Compatibility report (detector validated across the requested classes)

Validated with platform-accurate signal profiles (og:type / JSON-LD are platform-emitted and
language-independent) across 92 named publisher/page profiles; see `extension/common.test.js` for the
in-repo regression corpus, and `extension/tools/verify-detector.mjs` to re-verify against live URLs.

| Class | Result |
|---|---|
| Major international, Substack, Ghost, Medium, WordPress+SEO, multilingual (fr/de/es/ar/jp/zh/it/sv/pt/en-IN) | ✅ 100% accepted (`og:type=article` / Article JSON-LD) |
| Regional / local newspapers (custom, Arc, TownNews/BLOX, Newspack) | ✅ accepted; miss only bare hand-rolled CMS |
| Independent blogs | ✅ accepted when they emit any article metadata (Jekyll+seo-tag, Hugo PaperMod, WP+Yoast); ⚠️ missed when bare |
| Negative controls (news homepages/sections/search/tag; Amazon, YouTube, X, Reddit, GitHub, Stack Overflow, Gmail/Docs, dashboards, marketing) | ✅ 100% rejected |

**Outcome: precision 100% (0 non-article leaks / 19), recall ~90% (66/73 genuine).** Against the same
corpus the *previous* detector (unleashed web-wide) would have leaked **13/19** non-articles via the
`<article>+<h1>` fallback (NYT homepage, section/search pages, Amazon, YouTube home & watch, X, Reddit,
GitHub, Stack Overflow, marketing) — the strengthening trades that for a small, precise recall gap.

## Documented limitation — metadata-less pages

The residual misses are pages that emit **no** `og:type`, **no** JSON-LD, and **no**
`article:published_time` — bare hand-rolled CMS and minimal / no-SEO indie blogs (e.g. Daring Fireball,
bare Hugo/Jekyll/Eleventy). To a metadata-only detector these are indistinguishable from arbitrary
pages; capturing them would require scoring **article body text**, which is deliberately out of scope
(it would break the extension's metadata-only privacy model). This is an **accepted limitation**: the
affected segment is small and shrinking (modern SEO tooling emits OpenGraph by default), and such
articles can still be captured via the in-app paste-URL flow.

## Platform limitations (Chrome / MV3) that cannot be fully eliminated

- **SPA in-place navigation** (pushState without a full load) is not captured — the content script runs
  once per document load. (Full navigations, redirects, background/middle-clicked tabs, refresh,
  duplicate tabs, and session restore *are* captured; each read is self-contained and URL-keyed, so
  concurrency and out-of-order completion are safe — see the Phase 3/4 audit.)
- **Late client-rendered metadata:** a page that injects OpenGraph/JSON-LD after `document_idle` may be
  missed on that load.
- **No automatic retry** of a failed sync: a failed POST is not cached, so re-opening the URL retries;
  a one-shot read that fails and is never reopened is lost.

These are inherent to a metadata-only, declarative-injection MV3 extension and are documented rather
than worked around.
