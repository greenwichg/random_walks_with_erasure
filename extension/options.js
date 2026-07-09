/**
 * Options page logic: store the app URL + token, request host permission for exactly the
 * configured origin (nothing broader), and run a connection test through the service worker.
 * No reading data or scoring here — this only manages the connection.
 */
const $ = (id) => document.getElementById(id);
const statusEl = $("status");

function showStatus(kind, html) {
  statusEl.className = `status show ${kind}`;
  statusEl.innerHTML = html;
}

/** Host-permission match pattern for an app URL's origin (port-agnostic, per Chrome rules). */
function originPattern(appUrl) {
  const u = new URL(appUrl);
  return `${u.protocol}//${u.hostname}/*`;
}

async function load() {
  const { appUrl, token } = await chrome.storage.local.get(["appUrl", "token"]);
  if (appUrl) $("appUrl").value = appUrl;
  if (token) $("token").value = token;
}

async function save() {
  const appUrl = $("appUrl").value.trim();
  const token = $("token").value.trim();
  if (!appUrl || !token) {
    showStatus("err", "Enter both the app URL and your API token.");
    return;
  }
  let pattern;
  try {
    pattern = originPattern(appUrl);
  } catch {
    showStatus("err", "That app URL doesn’t look valid. Example: <code>http://localhost:3000</code>");
    return;
  }

  // Ask for access to exactly this origin (this click is the required user gesture).
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) {
    showStatus("err", `InfoDiet needs permission to reach <code>${pattern}</code> to sync your reads.`);
    return;
  }

  await chrome.storage.local.set({ appUrl, token });
  showStatus("ok", "Saved. Now open a supported news article, or hit “Test connection”.");
}

async function test() {
  showStatus("info", "Testing…");
  let res;
  try {
    res = await chrome.runtime.sendMessage({ type: "test" });
  } catch {
    showStatus("err", "Couldn’t reach the extension service worker. Try reloading the extension.");
    return;
  }
  if (res && res.ok) {
    const c = res.coverage || {};
    const cov =
      typeof c.totalReads === "number"
        ? ` You have <strong>${c.totalReads}</strong> read(s); a measured report unlocks at <strong>${c.threshold}</strong>` +
          (c.sufficient ? " — <strong>reached ✓</strong>." : ".")
        : "";
    showStatus("ok", `Connected ✓${cov}`);
    return;
  }
  const reason = (res && res.reason) || "unknown";
  const messages = {
    "no-url": "Set the app URL first.",
    "no-token": "Paste your API token first.",
    "bad-url": "That app URL doesn’t look valid. Example: <code>http://localhost:3000</code>",
    "bad-token":
      "Authentication failed — your API token is invalid or expired. Open InfoDiet → Settings, regenerate a token, and paste it here.",
    "wrong-url":
      "Reached a server, but not InfoDiet (no <code>/api/me/reads</code>). Double-check the app URL — in Colab it changes every session.",
    "unavailable": "The InfoDiet engine is running but returned an error. Try again shortly.",
    unreachable:
      "Couldn’t reach the app URL. Check it’s running and the address is correct (a Colab tunnel URL changes each session).",
  };
  showStatus("err", messages[reason] || `Connection failed (${reason}).`);
}

$("save").addEventListener("click", save);
$("test").addEventListener("click", test);
document.addEventListener("DOMContentLoaded", load);
