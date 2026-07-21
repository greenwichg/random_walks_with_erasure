/**
 * Service worker: the only part that talks to the network. It receives `{url, title,
 * observedAt}` from the content script, de-duplicates locally for a short TTL, and POSTs to the
 * InfoDiet web app's existing `/api/me/reads` with the per-user Bearer token. It performs no
 * scoring — the backend is the single source of truth and the only place reads are interpreted.
 */
importScripts("common.js");

const DEDUPE_TTL_MS = 6 * 60 * 60 * 1000; // re-send the same article at most once per 6h
const DEDUPE_KEY = "dedupe";

/** Human-readable cause for each sync failure — logged to the console so a failure is never silent. */
const REASON_HELP = {
  "bad-token": "API token is invalid or expired — open InfoDiet, regenerate a token, and update Options.",
  "wrong-url": "reached a server that isn't InfoDiet (no /api/me/reads) — check the app URL in Options.",
  "unavailable": "the InfoDiet engine is up but returned an error — try again shortly.",
  "unreachable": "couldn't reach the app URL — is it running? (a Colab tunnel URL changes each session).",
};

/** Stored config: { appUrl, token }. */
async function getConfig() {
  const { appUrl, token } = await chrome.storage.local.get(["appUrl", "token"]);
  return { appUrl: (appUrl || "").replace(/\/+$/, ""), token: token || "" };
}

function readsEndpoint(appUrl) {
  return `${appUrl}/api/me/reads`;
}

/** POST a batch of reads through the web tier with the Bearer token. Returns { ok, status, body }. */
async function postReads(appUrl, token, reads) {
  const res = await fetch(readsEndpoint(appUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ reads }),
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  return { ok: res.ok, status: res.status, body };
}

async function flashBadge(text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
  } catch {
    /* action badge is best-effort */
  }
}

// --- Capture permission + dynamic content-script registration (Option B) ---------------------- //
// The article detector is registered ONLY after the user grants the capture host permission
// (`CAPTURE_ORIGIN`, https-only) — nothing runs on any page until then. Registration persists across
// browser restarts; the permission events + startup reconcile keep it in lock-step with the grant.
const DETECTOR_ID = "ih-article-detector";

/** Whether the user has granted the broad capture host permission. */
async function hasCapture() {
  try {
    return await chrome.permissions.contains({ origins: [CAPTURE_ORIGIN] });
  } catch {
    return false;
  }
}

/** Register the detector across all HTTPS pages (minus the sensitive-origin exclude list). Idempotent. */
async function registerDetector() {
  try {
    const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [DETECTOR_ID] });
    if (existing && existing.length) return;
    await chrome.scripting.registerContentScripts([{
      id: DETECTOR_ID,
      matches: CAPTURE_MATCHES,          // ["https://*/*"] — from common.js
      excludeMatches: CAPTURE_EXCLUDES,  // mainstream webmail/office (defence-in-depth)
      js: ["common.js", "content.js"],
      runAt: "document_idle",
      allFrames: false,                  // top document only — no ad/embed iframes
      persistAcrossSessions: true,       // survives browser restart automatically
      world: "ISOLATED",
    }]);
  } catch (e) {
    console.warn("[InfoDiet] could not register the article detector:", e && e.message);
  }
}

/** Remove the detector registration (on revoke). Safe if it isn't registered. */
async function unregisterDetector() {
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [DETECTOR_ID] });
  } catch {
    /* nothing registered */
  }
}

/** Bring the registration in line with the live permission (grant → register, revoke → unregister),
 *  then refresh the toolbar state. Runs on grant/revoke, on startup, and on install/update — so a
 *  persisted registration and the actual permission can never drift. */
async function reconcileDetector() {
  if (await hasCapture()) await registerDetector();
  else await unregisterDetector();
  await refreshConfigBadge();
}

// --- Anonymous detection telemetry (local aggregate only) ------------------------------------- //
// A per-outcome counter histogram in local storage: which signal accepted a page, or why one was
// rejected. NO URL, NO per-page timing leaves the browser — it is a local aggregate the Options page
// reads. Signal/reason are the closed-set labels from common.classifyPage.
const STATS_KEY = "detectStats";
async function bumpDetectStat(outcome, signal) {
  if (!signal) return;
  try {
    const store = await chrome.storage.local.get(STATS_KEY);
    const stats = store[STATS_KEY] || {};
    const key = `${outcome}:${signal}`;   // e.g. "accept:og:type", "reject:no-signal"
    stats[key] = (stats[key] || 0) + 1;
    await chrome.storage.local.set({ [STATS_KEY]: stats });
  } catch {
    /* telemetry is best-effort and must never affect capture */
  }
}

/**
 * Reflect connection + capture status on the toolbar icon (persistent badge + tooltip), so the
 * extension always states what's needed instead of silently doing nothing. Three states:
 *   "!"  needs configuration (app URL + token)
 *   "○"  connected but capture not yet enabled (grant the host permission in Options)
 *   ""   fully configured and capturing
 */
async function refreshConfigBadge() {
  const { appUrl, token } = await getConfig();
  const configured = configStatus({ appUrl, token }) === "ok";
  const capturing = await hasCapture();
  let title, color, text;
  if (!configured) {
    title = "InfoDiet — open Options and set your app URL and API token to start syncing";
    color = "#b45309"; text = "!";
  } else if (!capturing) {
    title = "InfoDiet — connected. Open Options and enable capture to sync the pages you read.";
    color = "#b45309"; text = "○";
  } else {
    title = "InfoDiet — syncing the articles you read"; color = "#15803d"; text = "";
  }
  try {
    await chrome.action.setTitle({ title });
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
  } catch {
    /* action API unavailable in this context */
  }
}

/** Record one observed article (deduped). Returns a short status string for logging/tests. */
async function recordArticle(article) {
  const normalized = normalizeReadUrl(article.url);
  if (!normalized) return "skipped:bad-url";

  const { appUrl, token } = await getConfig();
  if (configStatus({ appUrl, token }) !== "ok") {
    await refreshConfigBadge();        // persistent "!" + explanatory tooltip, not a silent no-op
    return "skipped:not-configured";
  }

  const now = Date.now();
  const store = await chrome.storage.session.get(DEDUPE_KEY);
  const cache = pruneCache(store[DEDUPE_KEY] || {}, now, DEDUPE_TTL_MS);
  if (!shouldSend(cache, normalized, now, DEDUPE_TTL_MS)) return "skipped:duplicate";

  try {
    const { ok, status } = await postReads(appUrl, token, [
      // Standard page metadata only; empty fields drop out of the JSON. The web tier stamps
      // readSource="extension" on this token-authenticated path, which is what lets the backend
      // also feed the article into the shared catalog (Commit 18).
      { url: article.url, title: article.title || "", description: article.description || "",
        image: article.image || undefined, siteName: article.siteName || undefined,
        publishedAt: article.publishedAt || undefined, language: article.language || undefined,
        author: article.author || undefined,
        observedAt: article.observedAt },
    ]);
    if (ok) {
      cache[normalized] = now;
      await chrome.storage.session.set({ [DEDUPE_KEY]: cache });
      await flashBadge("✓", "#15803d");
      return "sent";
    }
    const reason = readsErrorReason(status);
    console.warn(`[InfoDiet] read NOT recorded (HTTP ${status}): ${REASON_HELP[reason] || reason}`);
    await flashBadge(reason === "bad-token" ? "auth" : "err", "#b91c1c");
    return `error:${reason}`;
  } catch (e) {
    console.warn(`[InfoDiet] read NOT recorded: ${REASON_HELP.unreachable}`);
    await flashBadge("err", "#b91c1c");
    return "error:unreachable";
  }
}

/**
 * Connection test used by the Options page: POST an empty batch. A valid token + reachable app
 * returns coverage (accepted:0); an invalid token returns 401; anything else is a config error.
 */
async function testConnection() {
  const { appUrl, token } = await getConfig();
  const status = configStatus({ appUrl, token });
  if (status !== "ok") return { ok: false, reason: status };
  try {
    const { ok, status, body } = await postReads(appUrl, token, []);
    if (ok) return { ok: true, coverage: body };
    return { ok: false, reason: readsErrorReason(status), status };
  } catch {
    return { ok: false, reason: "unreachable" };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "article") {
    bumpDetectStat("accept", msg.detectSignal);   // anonymous: which signal detected this article
    recordArticle(msg).then((status) => sendResponse({ status }));
    return true; // async response
  }
  if (msg.type === "detect" && msg.article === false) {
    bumpDetectStat("reject", msg.signal);          // anonymous: why a page was not captured (no URL)
    return;                                        // fire-and-forget, no response
  }
  if (msg.type === "test") {
    testConnection().then(sendResponse);
    return true;
  }
  if (msg.type === "stats") {                      // Options page reads the local aggregate
    chrome.storage.local.get(STATS_KEY).then((s) => sendResponse(s[STATS_KEY] || {}));
    return true;
  }
});

// Keep the dynamic detector in lock-step with the capture permission: register on grant, unregister
// on revoke, and reconcile on browser startup (registration + grant both persist, but a policy/update
// edge could desync them).
chrome.permissions.onAdded.addListener(reconcileDetector);
chrome.permissions.onRemoved.addListener(reconcileDetector);
chrome.runtime.onStartup.addListener(reconcileDetector);

// The toolbar button's one job (there is no popup): open the Options page, where the
// connection status and configuration live.
chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

// On first install, open the setup page so configuration is never skipped; keep the toolbar
// indicator accurate on install/update and whenever the saved config changes.
chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === "install") {
    try {
      await chrome.runtime.openOptionsPage();
    } catch {
      /* options UI unavailable in this context */
    }
  }
  // Reconcile the detector with the current permission (registers it after an update if the user had
  // already granted capture) and refresh the badge.
  await reconcileDetector();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && ("appUrl" in changes || "token" in changes)) refreshConfigBadge();
});
