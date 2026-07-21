# Chrome Web Store submission kit — InfoDiet extension

Everything to paste into the developer dashboard, prepared from the approved release review
and pre-submission audit. Placeholders in `<ANGLE_BRACKETS>` must be filled by the operator
before submitting. The only remaining code change is the host-permission narrowing recorded in §0.

- **Item**: InfoDiet — Information Health
- **Version**: 0.1.0
- **Package**: `extension/dist/infodiet-extension-0.1.0.zip` (build with `bash extension/package.sh`)
- **Category**: News & Weather · **Language**: English
- **Visibility recommendation**: Unlisted for the beta (real store install flow, no public discovery)

---

## 0. Host-permission scope (release decision)

**Decision:** for the **initial release**, the extension's host permissions are intentionally
**scoped to hidden-view.com** (`https://hidden-view.com/*`, `https://*.hidden-view.com/*`, plus
`localhost`/`127.0.0.1` for development) rather than the broad `https://*/*`. Hidden View is a
single hosted service, so hidden-view.com is the only origin the extension needs, and scoping to it
complies with Chrome Web Store **least-privilege guidance**: the *Use of Permissions* policy requires
the narrowest permissions necessary and explicitly discourages requesting broad access to
"future-proof" features not yet implemented. This also keeps the item off the broad-host review
track, improving approval probability and review turnaround.

**Future flexibility:** if **self-hosted deployments** at arbitrary origins become a supported
feature later, broader host permissions can be reintroduced in a **future release** — the correct
time to add that breadth is the update that actually ships the feature, via a normal manifest change
and re-review.

Sources: [Use of Permissions](https://developer.chrome.com/docs/webstore/program-policies/permissions) ·
[Review process](https://developer.chrome.com/docs/webstore/review-process).

> **Implementation status:** this records the committed decision. The `optional_host_permissions`
> narrowing in `extension/manifest.json`, and the matching justification/reviewer-note updates in
> §2–§3 below, are applied as the Phase 1 engineering step; until then, §2–§3 still describe the
> broad-host case and are **superseded** by this decision.

---

## 1. Store listing copy

**Short description** (auto-filled from the manifest; identical string, 120 chars):

> Privately syncs the news articles you read on supported sites to your InfoDiet account — URL and standard metadata only.

**Detailed description** (paste into the listing):

> InfoDiet measures the health of your news diet — diversity, tone, and balance — from the
> articles you actually read. This extension is the connector: open a news article on a
> supported site, and it records that read to your InfoDiet account.
>
> WHAT IT RECORDS: only the article's URL and standard page metadata (title, description,
> preview-image link, publisher, publication date, author and language when present) of
> articles on supported news sites.
>
> WHAT IT NEVER TOUCHES: article text, browsing history, passwords, cookies, forms, or any
> site outside the supported list.
>
> INACTIVE UNTIL YOU CONNECT IT: the extension sends nothing until you paste an API token
> from your InfoDiet account and grant access to your app's address — and you can revoke the
> token from InfoDiet Settings at any time.
>
> Requires an InfoDiet account and a running InfoDiet app. All scoring and analysis happen in
> your InfoDiet backend; the extension contains no tracking, analytics, or third-party
> services.
>
> Features:
> • One-time setup (app URL + token), then fully automatic
> • Toolbar badge confirms each recorded read (✓) and explains every failure
> • Metadata-only capture; article detection from standard signals (OpenGraph / JSON-LD)
> • Local 6-hour de-duplication so repeat visits aren't re-sent
> • Permission granted only for the one server origin you configure
>
> Supported sites: NYT, Washington Post, WSJ, Fox News, CNN, NBC, AP, Reuters, NPR, BBC,
> The Guardian, Politico, The Hill, USA Today, CNBC, Bloomberg, ABC, CBS, Al Jazeera, Axios,
> Vox, The Atlantic, LA Times.
>
> Privacy policy: <PUBLIC_APP_URL>/privacy

---

## 2. Privacy tab answers

**Single purpose description:**

> Records the news articles the user opens on a fixed list of supported news sites (URL and
> standard page metadata only) and syncs them to the user's own InfoDiet account, powering
> their Information Health report.

**Permission justifications:**

| Field | Justification text |
|---|---|
| `storage` | Stores the user's configured InfoDiet app URL and API token, plus a short-lived, session-scoped duplicate-suppression cache of recently recorded article URLs. |
| Host permissions (`https://*/*`, localhost — all optional) | Declared optional-only; nothing is granted at install. The InfoDiet app origin is user-configured (users run their own instance), so at setup the extension requests access to exactly the one origin the user enters (`chrome.permissions.request` with that origin pattern). The broad pattern exists solely so that per-origin request can succeed for any user-chosen server. |
| Content scripts (24 news domains) | A small detector runs on the statically allowlisted news sites to decide whether the open page is an article (OpenGraph / JSON-LD / headline structure) and read standard head metadata. It reads no article text, no forms/cookies, and modifies nothing on the page. |
| Remote code | **No** — all code ships in the package; the only network request is the read-sync POST to the user-configured origin. |

**Data usage questionnaire** — check exactly these:

| Category | Collected? | Note |
|---|---|---|
| Web history | **Yes** | URLs + titles of article pages the user opens on supported news sites |
| Website content | **Yes** | Standard page metadata: title, description, preview-image link |
| Authentication information | **Yes** | The user's InfoDiet API token, stored locally and sent only to their configured server |
| PII / health / financial / personal communications / location / user activity | No | Not collected by the extension |

**Certifications** — all three apply truthfully: no sale of data; no transfers unrelated to the
single purpose; no use for creditworthiness/lending.

**Privacy policy URL:** `<PUBLIC_APP_URL>/privacy`

---

## 3. Review notes (paste into "Notes for reviewer")

> This extension is a companion to the InfoDiet web app. It records only the URL and standard
> page metadata of news articles the user opens on 24 statically allowlisted news domains, and
> syncs them to the user's own InfoDiet account. It is inert until configured — no network
> requests and no data collection of any kind before setup.
>
> ABOUT THE BROAD OPTIONAL HOST PERMISSION: users run their own InfoDiet instance, so the app
> origin is user-configured. `https://*/*` is declared optional-only so the runtime
> `chrome.permissions.request` for exactly the one origin the user enters can succeed; nothing
> is granted at install time.
>
> NO REMOTE CODE. No analytics. The only endpoint contacted is `<origin>/api/me/reads` on the
> origin the user configured.
>
> TEST SETUP (~3 minutes, no sign-in needed — a pre-minted API token is provided):
> 1. Install the extension. The Options page opens automatically (also reachable by clicking
>    the toolbar icon).
> 2. Enter: InfoDiet app URL: `<DEMO_APP_URL>` · API token: `<DEMO_TOKEN>`
> 3. Click **Save** (Chrome will prompt for access to that one origin), then **Test
>    connection** → shows "Connected ✓".
> 4. Open any current article on a supported site (e.g. reuters.com or apnews.com). The
>    toolbar badge flashes ✓ — the read was recorded.
> 5. Optional: open `<DEMO_APP_URL>/history` signed in as the demo account
>    (`<DEMO_EMAIL>` / `<DEMO_ACCESS_NOTE>`) to see the recorded article appear.
>
> Privacy policy: `<PUBLIC_APP_URL>/privacy`

---

## 4. Reviewer test instructions (standalone copy, same steps)

1. Install → Options opens automatically.
2. App URL `<DEMO_APP_URL>`, token `<DEMO_TOKEN>` → Save → approve the one-origin permission
   prompt → Test connection → "Connected ✓".
3. Visit a current article on any supported site → toolbar badge flashes ✓.
4. Nothing else to configure; uninstalling removes all local data.

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
| 2 | 1280×800 | A supported news article open with the toolbar ✓ badge visible (OS-level window capture — the badge lives in the browser chrome, not the page) | `cws-shot-2-read-badge-1280x800.png` |
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
2. Dashboard → **New item** → upload `infodiet-extension-0.1.0.zip` → confirm no manifest
   warnings.
3. **Store listing** tab: category News & Weather, language English, detailed description
   (section 1), screenshots (section 6), store icon `icon128.png`, promo tile if made.
4. **Privacy** tab: single purpose, four justifications, remote code = No, data questionnaire,
   three certifications, privacy policy URL (section 2).
5. **Notes for reviewer**: paste section 3 with placeholders filled.
6. **Distribution**: visibility (Unlisted recommended for beta), regions, free.
7. **Submit for review**. Expect the deeper review track (broad optional host permission);
   typical turnaround is days up to a couple of weeks.
8. After approval: revoke the reviewer token; keep the demo instance up for re-reviews.
