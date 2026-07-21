# Chrome Web Store submission kit — InfoDiet extension

Everything to paste into the developer dashboard, prepared from the approved release review
and pre-submission audit. Placeholders in `<ANGLE_BRACKETS>` must be filled by the operator
before submitting. The only remaining code change is the host-permission narrowing recorded in §0.

- **Item**: InfoDiet — Information Health
- **Version**: 0.2.0
- **Package**: `extension/dist/infodiet-extension-0.2.0.zip` (build with `bash extension/package.sh`)
- **Category**: News & Weather · **Language**: English
- **Visibility recommendation**: Unlisted for the beta (real store install flow, no public discovery)

---

## 0. Host-permission scope (release decision)

**Decision (v0.2.0 — "capture any article"):** the extension captures genuine articles from **any**
publisher, so the article detector must be able to run on any HTTPS page. It therefore declares the
broad capture host permission **`https://*/*` as OPTIONAL** — **nothing is granted at install**
(install-time permissions are only `storage` + `scripting`). Capture is enabled by an explicit user
action in Options (`chrome.permissions.request`), and only then is the detector registered
(`chrome.scripting.registerContentScripts`, HTTPS-only, top-frame, ISOLATED world) with a short
`excludeMatches` list for mainstream webmail/office. The Hidden View **sync** origin
(`https://hidden-view.com/*`, `https://*.hidden-view.com/*`, plus localhost for dev) remains a
separate optional grant requested when the user saves their app URL.

**Why this satisfies least-privilege** (*Use of Permissions* policy): the broad host permission is the
**narrowest permission that achieves the single purpose** (capturing articles from any publisher — a
publisher allowlist cannot cover unknown/independent/new sites). It is **optional and opt-in**, so a
user who does not enable capture grants no host access; the detector reads **standard metadata only**
(OpenGraph / JSON-LD / headline / URL), **never article body text**, and only sends on a positive
article classification; it does not run at install and does not run on the excluded origins.

**Update-migration note:** because the broad permission is optional (not install-time), Chrome does
**not** force existing users to re-accept on update; capture simply stays off until opted in.

Sources: [Use of Permissions](https://developer.chrome.com/docs/webstore/program-policies/permissions) ·
[Review process](https://developer.chrome.com/docs/webstore/review-process).

> **Implementation status:** applied (v0.2.0). `extension/manifest.json` declares
> `optional_host_permissions` including `https://*/*` (capture) and hidden-view.com (sync); the static
> content-script allowlist is removed and the detector is registered dynamically after opt-in. See
> `docs/EXTENSION_ARTICLE_CAPTURE.md` for the full architecture, detector, and compatibility report.

---

## 1. Store listing copy

**Short description** (auto-filled from the manifest; identical string):

> Privately syncs the news articles you read to your InfoDiet account — URL and standard metadata only, once you enable capture.

**Detailed description** (paste into the listing):

> InfoDiet measures the health of your news diet — diversity, tone, and balance — from the
> articles you actually read. This extension is the connector: enable capture, open a news
> article or blog post on any site, and it records that read to your InfoDiet account.
>
> WHAT IT RECORDS: only the article's URL and standard page metadata (title, description,
> preview-image link, publisher, publication date, author and language when present) of the
> article pages you open once capture is enabled.
>
> WHAT IT NEVER TOUCHES: article text, passwords, cookies, forms, the content of non-article
> pages, or a short list of excluded sites (such as Gmail and Google Docs).
>
> INACTIVE UNTIL YOU CONNECT IT: the extension sends nothing and accesses no page until you
> paste an API token, grant access to your app's address, and enable capture — and you can turn
> capture off or revoke the token at any time.
>
> Requires an InfoDiet account and a running InfoDiet app. All scoring and analysis happen in
> your InfoDiet backend; the extension contains no tracking, analytics, or third-party
> services.
>
> Features:
> • One-time setup (app URL + token), then enable capture — after which it's fully automatic
> • Works on any publisher — major, regional, independent, Substack, Ghost, WordPress, Medium
> • Toolbar badge confirms each recorded read (✓) and explains every failure
> • Metadata-only capture; precise article detection from standard signals (OpenGraph / JSON-LD)
> • Local 6-hour de-duplication so repeat visits aren't re-sent
> • Capture is an explicit opt-in; nothing runs on any page until you turn it on
>
> Note: a page must publish standard article metadata (OpenGraph / JSON-LD) to be detected; a
> handful of minimal, hand-built sites that publish none can still be added via the app's
> paste-URL flow.
>
> Privacy policy: <PUBLIC_APP_URL>/privacy

---

## 2. Privacy tab answers

**Single purpose description:**

> Records the article pages the user opens on any site (URL and standard page metadata only),
> once the user enables capture, and syncs them to the user's own InfoDiet account, powering
> their Information Health report.

**Permission justifications:**

| Field | Justification text |
|---|---|
| `storage` | Stores the user's configured InfoDiet app URL and API token, a short-lived session-scoped duplicate-suppression cache of recently recorded article URLs, and an anonymous local detection tally (which metadata signal matched / why a page was skipped — no URLs). |
| `scripting` | Registers the article detector dynamically once the user enables capture (`chrome.scripting.registerContentScripts`), and unregisters it if they turn capture off. No script is registered until the user opts in. |
| Capture host permission (`https://*/*` — **optional**) | Requested at the user's explicit click in Options, never at install. It lets the article detector run on the article pages the user opens on any site. The detector reads **standard metadata only** (OpenGraph / JSON-LD / headline / URL), **never article body text, forms, or cookies**, modifies nothing, runs on the top frame only, and does not run on an excluded list (mainstream webmail/office). It only sends a read on a positive article classification. |
| Sync host permission (`https://hidden-view.com/*`, `https://*.hidden-view.com/*`, localhost — **optional**) | Requested when the user saves their app URL. The only network call is the read-sync POST to the user's Hidden View app; localhost/127.0.0.1 are for local development. |
| Remote code | **No** — all code ships in the package; the only network request is the read-sync POST to the user-configured origin. |

**Data usage questionnaire** — check exactly these:

| Category | Collected? | Note |
|---|---|---|
| Web history | **Yes** | URLs + titles of the article pages the user opens (any site), once they enable capture |
| Website content | **Yes** | Standard page metadata: title, description, preview-image link |
| Authentication information | **Yes** | The user's InfoDiet API token, stored locally and sent only to their configured server |
| PII / health / financial / personal communications / location / user activity | No | Not collected by the extension |

**Certifications** — all three apply truthfully: no sale of data; no transfers unrelated to the
single purpose; no use for creditworthiness/lending.

**Privacy policy URL:** `<PUBLIC_APP_URL>/privacy`

---

## 3. Review notes (paste into "Notes for reviewer")

> This extension is a companion to the InfoDiet web app. It records only the URL and standard
> page metadata of the article pages the user opens, and syncs them to the user's own InfoDiet
> account. It is inert until configured — no network requests and no data collection of any kind
> before setup, and no page access until the user enables capture.
>
> ABOUT HOST PERMISSIONS: both host permissions are OPTIONAL — nothing is granted at install
> (install-time permissions are only `storage` + `scripting`). (1) The **capture** permission
> `https://*/*` is requested at the user's explicit click ("Enable capture"); only then is the
> article detector registered. It reads standard metadata only (OpenGraph/JSON-LD/headline/URL),
> NEVER article body text, runs on the top frame only, excludes mainstream webmail/office, and
> sends a read only when the page positively classifies as an article. A publisher allowlist cannot
> satisfy the product (users read unknown/independent/new publishers), so broad-but-optional capture
> is the narrowest permission that meets the single purpose. (2) The **sync** permission
> (hidden-view.com + subdomains, plus localhost for dev) is requested when the user saves their app
> URL so the read-sync POST can reach it.
>
> NO REMOTE CODE. No analytics. The only endpoint contacted is `<origin>/api/me/reads` on the
> origin the user configured.
>
> TEST SETUP (~3 minutes, no sign-in needed — a pre-minted API token is provided):
> 1. Install the extension. The Options page opens automatically (also reachable by clicking
>    the toolbar icon).
> 2. Enter: InfoDiet app URL: `<DEMO_APP_URL>` · API token: `<DEMO_TOKEN>`
> 3. Click **Save** (Chrome prompts for access to that one sync origin), then **Test
>    connection** → shows "Connected ✓".
> 4. Click **Enable capture** and approve the prompt (this grants the article-detection permission).
> 5. Open any current news article or blog post on any site (e.g. reuters.com, apnews.com, or a
>    Substack post). The toolbar badge flashes ✓ — the read was recorded.
> 5. Optional: open `<DEMO_APP_URL>/history` signed in as the demo account
>    (`<DEMO_EMAIL>` / `<DEMO_ACCESS_NOTE>`) to see the recorded article appear.
>
> Privacy policy: `<PUBLIC_APP_URL>/privacy`

---

## 4. Reviewer test instructions (standalone copy, same steps)

1. Install → Options opens automatically. Install-time permissions are only `storage` + `scripting`
   (no host access, no page access).
2. App URL `<DEMO_APP_URL>`, token `<DEMO_TOKEN>` → Save → approve the one-origin sync permission
   prompt → Test connection → "Connected ✓".
3. Click **Enable capture** → approve the article-detection permission prompt.
4. Visit a current article on any news site or blog → toolbar badge flashes ✓. (Non-article pages,
   and excluded sites like Gmail/Docs, record nothing.)
5. Nothing else to configure; turning off capture or uninstalling removes access and local data.

---

## 5. Demo account checklist (operator, before submitting)

- [ ] Deploy the InfoDiet app (web + engine) at a public HTTPS URL → this is `<DEMO_APP_URL>`
      and `<PUBLIC_APP_URL>` (they may be the same instance).
- [ ] Confirm `<PUBLIC_APP_URL>/privacy` loads publicly (no sign-in).
- [ ] Sign in to the deployed app with a dedicated demo Google account (not a personal one).
- [ ] Settings → Connect browser extension → Generate token → record as `<DEMO_TOKEN>`
      (plaintext is shown once; mint a fresh one for review and revoke it after approval).
- [ ] Record a handful of reads so `/history` and the report aren't empty for the reviewer.
- [ ] Decide `<DEMO_EMAIL>` / `<DEMO_ACCESS_NOTE>` (how the reviewer may view the app
      signed-in, if at all — the extension test itself needs only URL + token).
- [ ] Fill every placeholder in sections 1–3, then submit.

---

## 6. Screenshot plan (capture at exact store resolutions — none exist yet)

At least one screenshot is mandatory; up to five allowed. Use **1280×800** (preferred) or
640×400, PNG or 24-bit JPEG, no alpha.

| # | Resolution | What to capture | Suggested filename |
|---|---|---|---|
| 1 | 1280×800 | Extension Options page in the connected state ("Connected ✓" status visible) | `cws-shot-1-options-1280x800.png` |
| 2 | 1280×800 | A news article open with the toolbar ✓ badge visible (OS-level window capture — the badge lives in the browser chrome, not the page) | `cws-shot-2-read-badge-1280x800.png` |
| 3 | 1280×800 | InfoDiet → Settings → Connect browser extension (token generation UI) | `cws-shot-3-connect-1280x800.png` |
| 4 | 1280×800 | The Measured report page powered by synced reads | `cws-shot-4-report-1280x800.png` |

Also required/optional listing art:

| Asset | Size | Status |
|---|---|---|
| Store icon | 128×128 | **Ready** — reuse `extension/icons/icon128.png` |
| Small promo tile | 440×280 | Recommended, not yet designed |
| Marquee promo | 1400×560 | Optional |

---

## 7. Dashboard sequence (once every placeholder above is filled)

1. Developer account ready: $5 registration paid, email verified, 2FA on, EU DSA
   trader/non-trader declaration made.
2. Dashboard → **New item** → upload `infodiet-extension-0.2.0.zip` → confirm no manifest
   warnings.
3. **Store listing** tab: category News & Weather, language English, detailed description
   (section 1), screenshots (section 6), store icon `icon128.png`, promo tile if made.
4. **Privacy** tab: single purpose, four justifications, remote code = No, data questionnaire,
   three certifications, privacy policy URL (section 2).
5. **Notes for reviewer**: paste section 3 with placeholders filled.
6. **Distribution**: visibility (Unlisted recommended for beta), regions, free.
7. **Submit for review**. The broad capture host permission is **optional and opt-in** (nothing is
   granted at install), the extension reads standard metadata only (no article body text) and
   contacts no endpoint but the user's configured origin — so the single-purpose justification is
   strong. Expect broad-host scrutiny given `https://*/*`; lead the reviewer notes with the opt-in +
   metadata-only + excluded-origins facts. Typical turnaround is a few days, up to a couple of weeks.
8. After approval: revoke the reviewer token; keep the demo instance up for re-reviews.
