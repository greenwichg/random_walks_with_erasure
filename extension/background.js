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

/**
 * Reflect connection-config status on the toolbar icon (persistent badge + tooltip), so an
 * unconfigured extension states what's wrong instead of silently doing nothing. Called on install,
 * when the config changes, and whenever a read is skipped for lack of configuration.
 */
async function refreshConfigBadge() {
  const { appUrl, token } = await getConfig();
  const ok = configStatus({ appUrl, token }) === "ok";
  try {
    await chrome.action.setTitle({
      title: ok
        ? "InfoDiet — syncing your reads"
        : "InfoDiet — open Options and set your app URL and API token to start syncing",
    });
    await chrome.action.setBadgeBackgroundColor({ color: ok ? "#15803d" : "#b45309" });
    await chrome.action.setBadgeText({ text: ok ? "" : "!" });
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
      { url: article.url, title: article.title || "", description: article.description || "",
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
    recordArticle(msg).then((status) => sendResponse({ status }));
    return true; // async response
  }
  if (msg.type === "test") {
    testConnection().then(sendResponse);
    return true;
  }
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
  await refreshConfigBadge();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && ("appUrl" in changes || "token" in changes)) refreshConfigBadge();
});
