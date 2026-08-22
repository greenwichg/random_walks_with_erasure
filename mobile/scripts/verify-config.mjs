#!/usr/bin/env node
/**
 * Check the mobile build configuration. **Prints no value.**
 *
 *   npm run verify:config --workspace @ih/mobile
 *
 * Exists because every way this configuration can be wrong looks the same from the device: sign-in
 * fails, or the feed is empty, or a request times out. This separates them — and it separates them
 * BEFORE a twenty-minute cloud build, which is the difference between one round trip and four.
 *
 * The three failures it is really for:
 *
 *   an unset client id        → POST /api/auth/mobile answers 500 not-configured and mints nothing
 *   a localhost API base URL  → resolves to the PHONE on a real device; the symptom is a timeout
 *   a client id whose shape   → an Android id pasted into the iOS slot is accepted by everything
 *   is wrong for its slot        until Google rejects the token, with no mention of which id
 *
 * Nothing is echoed. Client ids are public identifiers, but this output ends up in terminals,
 * tickets and screenshots, and a tool that prints configuration teaches people to paste
 * configuration — which is how the SMTP password in this project's history reached a chat window.
 * Every value is reported as present/absent plus a shape check.
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const MOBILE = join(dirname(fileURLToPath(import.meta.url)), "..");
const OK = "OK  ";
const FAIL = "FAIL";
const WARN = "--  ";

/**
 * Load `mobile/.env` the way Expo does, without adding a dependency for six lines.
 *
 * Split on `\r?\n`, not `\n`. Git for Windows checks files out with CRLF by default, so a `.env`
 * created there ends every line with `\r` — and in JavaScript `\r` is a line terminator, which `.`
 * does not match and `$` does not sit after. The first version split on `\n` alone and matched
 * NOTHING in a perfectly good file, reporting every variable as unset. The user's configuration was
 * right and the tool was wrong, which is the worst way for a verifier to fail.
 */
function loadEnv() {
  const path = join(MOBILE, ".env");
  if (!existsSync(path)) return { __missing: true };
  const out = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    if (/^\s*#/.test(line)) continue;
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)$/.exec(line);
    if (m) out[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

const fileEnv = loadEnv();
const fileMissing = fileEnv.__missing === true;
delete fileEnv.__missing;
const env = { ...fileEnv, ...process.env };
const problems = [];

// Tell the two states apart. "No .env at all" and "a .env this tool could not read" look identical
// in the report below — every variable reads as unset — and they need completely different fixes.
if (fileMissing) {
  console.log(`${WARN} mobile/.env                            not found — copy .env.example to .env\n`);
} else if (Object.keys(fileEnv).length === 0) {
  console.log(
    `${FAIL} mobile/.env                            found, but NO variables parsed out of it\n` +
      `     Check the file is really named .env and not .env.txt — Windows hides known\n` +
      `     extensions, so a file saved from Notepad's "Save as" can look right and not be.\n`,
  );
  problems.push("mobile/.env exists but no variables could be read from it.");
} else {
  console.log(`${OK}   mobile/.env                            read, ${Object.keys(fileEnv).length} variables\n`);
}

function report(mark, name, note) {
  console.log(`${mark} ${name.padEnd(38)} ${note}`);
}

/**
 * A Google OAuth client id looks like `<digits>-<hash>.apps.googleusercontent.com`.
 *
 * Checked because the ids for the three platforms are visually similar, live next to each other in
 * the console, and are copied by hand. One in the wrong slot is accepted by Expo, accepted by the
 * build, and rejected by Google at sign-in with a message that names neither slot.
 */
function checkClientId(name, { required }) {
  const value = (env[name] ?? "").trim();
  if (!value) {
    report(required ? FAIL : WARN, name, required ? "not set" : "not set (optional)");
    if (required) problems.push(`${name} is not set.`);
    return;
  }
  if (!/^\d+-[a-z0-9]+\.apps\.googleusercontent\.com$/.test(value)) {
    report(FAIL, name, "set, but not shaped like a Google OAuth client id");
    problems.push(
      `${name} does not look like a client id. Expected <digits>-<hash>.apps.googleusercontent.com ` +
        `— a client SECRET or a project number pasted here would look exactly like this.`,
    );
    return;
  }
  report(OK, name, `set, ${value.length} characters, correct shape`);
}

console.log("Hidden View mobile — build configuration (prints no values)\n");

// --- the API host ------------------------------------------------------------------------------
const base = (env.EXPO_PUBLIC_API_BASE_URL ?? "").trim();
if (!base) {
  report(FAIL, "EXPO_PUBLIC_API_BASE_URL", "not set");
  problems.push("EXPO_PUBLIC_API_BASE_URL is not set — a native app has no origin of its own.");
} else if (/localhost|127\.0\.0\.1/.test(base)) {
  report(FAIL, "EXPO_PUBLIC_API_BASE_URL", "points at localhost");
  problems.push(
    "EXPO_PUBLIC_API_BASE_URL points at localhost, which on a real device resolves to the PHONE. " +
      "Use the deployment, or your machine's LAN address for a dev build.",
  );
} else if (!/^https:\/\//.test(base)) {
  report(FAIL, "EXPO_PUBLIC_API_BASE_URL", "not https");
  problems.push(
    "EXPO_PUBLIC_API_BASE_URL is not https. iOS App Transport Security blocks cleartext by " +
      "default, and the bearer token would travel in the open.",
  );
} else {
  report(OK, "EXPO_PUBLIC_API_BASE_URL", `https, host ${new URL(base).host}`);
}

// --- the OAuth clients -------------------------------------------------------------------------
// At least one native id is required; both are required to test both platforms. The web id is
// optional and only helps a development build before the native clients exist.
checkClientId("EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID", { required: false });
checkClientId("EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID", { required: false });
checkClientId("EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID", { required: false });

const native = ["EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID", "EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID"].filter(
  (n) => (env[n] ?? "").trim(),
);
if (native.length === 0) {
  problems.push(
    "No native Google client id is set, so sign-in cannot be tested on either platform. " +
      "See mobile/.env.example for the console steps.",
  );
} else if (native.length === 1) {
  console.log(
    `\n${WARN} only ${native.length} of 2 native client ids is set — one platform cannot be tested yet.`,
  );
}

// --- the app identity --------------------------------------------------------------------------
// Read out of app.config.ts by pattern rather than by importing it: importing pulls in the Expo
// config machinery, and this script has to run before `npm install` has necessarily produced it.
const appConfig = readFileSync(join(MOBILE, "app.config.ts"), "utf8");
const appId = /const APP_ID = "([^"]+)"/.exec(appConfig)?.[1];
if (appId) {
  report(OK, "bundle id / package name", appId);
  console.log(
    `     the SAME string must be registered on BOTH Google OAuth clients, or sign-in fails\n` +
      `     on the device with a bare DEVELOPER_ERROR that never mentions the identifier`,
  );
} else {
  report(FAIL, "bundle id / package name", "could not be read from app.config.ts");
  problems.push("app.config.ts no longer declares APP_ID.");
}

// --- the server half ---------------------------------------------------------------------------
console.log(
  `\n${WARN} server side (checked on the deployment, not here):\n` +
    `     GOOGLE_IOS_CLIENT_ID and GOOGLE_ANDROID_CLIENT_ID must be set in deploy/.env and must\n` +
    `     MATCH the two above. The server trusts a token's \`aud\` against those values; if they\n` +
    `     are unset, POST /api/auth/mobile answers 500 not-configured and mints nothing.`,
);

console.log();
if (problems.length) {
  console.log("NOT READY:");
  problems.forEach((p, i) => console.log(`  ${i + 1}. ${p}`));
  console.log("\nSee mobile/.env.example and docs/MOBILE_DEVICE_TEST.md");
  process.exit(1);
}
console.log("READY — the build configuration is sound. Device testing still has to prove sign-in.");
