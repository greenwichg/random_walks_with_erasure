# Phase 3b — real-device testing

Everything needed to get Hidden View running on a physical iPhone and Android phone, and the seven
checks that decide whether Phase 4 may begin.

**Nothing here is done yet.** The configuration and the build profiles exist; the console work, the
builds and the device runs are yours, and the results are what this document is waiting for.

Settled already:

| | |
|---|---|
| iOS bundle identifier | `com.hiddenview.app` |
| Android package name | `com.hiddenview.app` (same string, deliberately) |
| API base URL | `https://hidden-view.com`, all profiles |
| Build route | EAS Build (cloud) |

## Step 1 — Google Cloud Console

Two new OAuth clients. The existing `GOOGLE_CLIENT_ID` is a **Web** client that NextAuth uses; a
native app cannot use it, and a native ID token's `aud` claim carries the *native* client id.

Go to **console.cloud.google.com → APIs & Services → Credentials**, in the same project the web
client lives in.

### 1a. The iOS client

**Create credentials → OAuth client ID → Application type: iOS.**

| Field | Value |
|---|---|
| Name | `Hidden View iOS` |
| Bundle ID | `com.hiddenview.app` |

There is no client secret — native OAuth clients do not have one, which is why the whole ID-token
exchange works without shipping anything confidential in the app.

Copy the client id. It looks like `1234567890-abcdef….apps.googleusercontent.com`.

### 1b. The Android client — and the SHA-1

**Create credentials → OAuth client ID → Application type: Android.**

| Field | Value |
|---|---|
| Name | `Hidden View Android` |
| Package name | `com.hiddenview.app` |
| SHA-1 certificate fingerprint | see below |

**This is the step that stalls the work.** Android ties an OAuth client to the *certificate the APK
was signed with*, and a missing or wrong fingerprint fails sign-in on the device with a bare
`DEVELOPER_ERROR` that never mentions certificates, packages or fingerprints.

EAS manages the keystore, so the fingerprint comes from it:

```bash
cd mobile
npx eas-cli login          # once
npx eas-cli init           # once — creates the EAS project, writes the project id
npx eas-cli credentials    # → Android → production → Keystore → view
```

The output includes `SHA1 Fingerprint`. Paste that into the Android client.

**Register it once per keystore you will sign with.** The `development` and `preview` profiles above
both use the EAS-managed keystore, so one fingerprint covers both. If you ever build locally, that
debug keystore has a *different* SHA-1 and needs its own entry on the same client — Google allows
several.

### 1c. The consent screen

**APIs & Services → OAuth consent screen.**

While it is in **Testing**, only email addresses listed under *Test users* can sign in, capped at
100. That is a **second gate, independent of Hidden View's own `BETA_ALLOWLIST`** — a tester missing
from either one is refused, and the two refusals look different:

| Missing from | What the tester sees |
|---|---|
| Google test users | Google's own screen, before the app is involved |
| `BETA_ALLOWLIST` | Hidden View's message: *"Hidden View is in a closed beta and this account is not on the list yet."* |

Add your own address to both.

Scopes are `openid`, `email`, `profile` — all non-sensitive, so no Google verification review is
needed.

## Step 2 — configure the app and the server

### The app

```bash
cp mobile/.env.example mobile/.env      # .env is gitignored
```

Fill in `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` and `EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID`. Then:

```bash
npm run verify:config --workspace @ih/mobile
```

It **prints no values** — every one is reported as present/absent plus a shape check. It catches the
three failures that otherwise cost a twenty-minute cloud build each: an unset id, a `localhost` base
URL (which on a device resolves to the *phone*), and an id pasted into the wrong slot.

### The server

The two client ids also go on the deployment, because the server checks a token's `aud` against
them. On the box:

```bash
sudo tee -a /opt/ih/deploy/.env >/dev/null <<'EOF'
GOOGLE_IOS_CLIENT_ID=<the iOS client id>
GOOGLE_ANDROID_CLIENT_ID=<the Android client id>
EOF
cd /opt/ih && . deploy/ops/_compose.sh && dc up -d web
```

`docker-compose.yml` already forwards both, actively rather than in a commented block — a variable
compose does not forward is invisible to the process no matter what `.env` says, which is how
`RWE_EMAIL_REPLY_TO` once shipped configured and absent from every message.

Confirm the server sees them **without printing them**:

```bash
cd /opt/ih && . deploy/ops/_compose.sh && \
  dc exec -T web sh -c 'for v in GOOGLE_IOS_CLIENT_ID GOOGLE_ANDROID_CLIENT_ID; do
    eval "n=\${#$v}"; echo "$v: ${n} characters"; done'
```

Two non-zero lengths. If either is `0`, the exchange endpoint answers `500 not-configured` and mints
nothing — which is the correct fail-closed behaviour, and is covered by
`e2e/specs/mobile-exchange.spec.ts`.

## Step 3 — build

### 3a. Give EAS the variables

**A cloud build cannot see `mobile/.env` — it is gitignored, which is the point.** The values have
to be registered with EAS once, or the build succeeds, installs, and shows **NOT CONFIGURED** on the
sign-in screen. That is a twenty-minute round trip for a missing variable, so do this first.

```bash
cd mobile
npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://hidden-view.com --environment preview \
  --visibility plaintext --scope project --non-interactive
npx eas-cli@latest env:set --name EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID \
  --value <the iOS client id> --environment preview \
  --visibility plaintext --scope project --non-interactive
npx eas-cli@latest env:set --name EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID \
  --value <the Android client id> --environment preview \
  --visibility plaintext --scope project --non-interactive
```

`env:set`, not `env:create` — the latter still runs but prints a deprecation notice.

`--visibility plaintext` is correct here and only here: these are public identifiers, and marking
them secret would hide them from the build logs you need to debug with. **Nothing else about this
app goes into EAS** — the bearer token is minted at runtime, and no secret exists to register.

`--environment preview` names an *EAS environment*, which is a different thing from a *build
profile* that happens to share the name. A build gets an environment's variables only because its
profile says `"environment": "preview"` — see below. Registering variables in `preview` and then
building `--profile production` gives you a binary with no configuration in it.

The EAS **project id** is hardcoded in `app.config.ts`, not read from `.env`. EAS evaluates that
config without loading `.env`, so a project id set only there is invisible to every `eas` command —
which fails as *"Cannot automatically write to dynamic config at: app.config.ts"*, EAS trying to
write in an id that was already present but unreachable.

### 3b. About the build profiles

`mobile/eas.json` carries no explanatory comments, because it cannot: JSON has no comment syntax, and
EAS validates the file against a strict schema that rejects `"//"` keys outright — an attempt to
document it inline failed `eas init` with *"build.// must be of type object"*. So the reasoning lives
here instead.

| Profile | What it is for |
|---|---|
| `development` | needs the Metro bundler attached; for iterating, not for a device test |
| `preview` | **the one to use.** A standalone binary — TestFlight on iOS, a directly installable APK on Android |
| `production` | store builds. Nothing in Phase 3 submits, and `eas submit` is deliberately not configured |

Every profile carries `"environment"`, naming the EAS environment whose variables the build reads.
It is spelled out on all three rather than left to a default, because the failure mode is silent:
the build succeeds, installs, and shows **NOT CONFIGURED** on the sign-in screen, with nothing in
the log saying a variable was looked for and not found.

`cli.version` is `>= 22.0.0` — the version the `environment` field was verified against. An older
CLI validates `eas.json` against an older schema, and an unrecognised key there is a parse error
about the file rather than a message about the CLI.

Two things deliberately absent from that file:

- **No `channel` on any profile.** Channels route over-the-air updates and require `expo-updates`,
  which is not installed. Declaring one fails the build with a message about update channels rather
  than about the missing package.
- **No client ids or API URL in the `env` blocks** — only `EXPO_PUBLIC_BUILD_PROFILE`. Those values
  are gitignored, and putting them in `eas.json` would put them straight back into git. They are
  registered with EAS in step 3a instead. (A profile's `env` block wins over the EAS environment for
  the same key, so the two are kept to disjoint sets of names.)

### 3c. Build

```bash
npx eas-cli build --profile preview --platform ios
npx eas-cli build --profile preview --platform android
```

**`preview`, not `development`.** A development build needs the Metro bundler attached; `preview` is
a standalone binary, which is what "real-device testing" means. iOS lands in TestFlight (or installs
directly if the device UDID is registered); Android produces an APK you can install directly.

**Not Expo Go.** `expo-secure-store`'s keystore access and a native Google sign-in bound to this
bundle id do not work under Expo Go — running there would appear to test items 2, 3 and 4 and would
test none of them.

iOS needs an Apple Developer Program membership ($99/yr) for a device build or TestFlight. Android
needs nothing beyond the EAS account.

## Step 4 — the seven checks

Run these on **both** platforms. Phase 4 does not start until both pass.

The account row at the top of the Recommendations screen shows the build profile, the API host, and
a keystore indicator. It reports whether a token exists, **never its value** — a screen that displays
a credential is a screen that ends up in a screenshot.

### 1. The app launches

Open it. You should see the signed-out screen: "Hidden View — A health check for your news diet" and
a **Sign in** button.

*If the sign-in screen shows a yellow **NOT CONFIGURED** box*, it is telling you exactly which value
is missing — that is `configProblems()`, and it is there so a misconfiguration says so instead of
failing on tap.

### 2. Google sign-in works

Tap **Sign in → Continue with Google**. The system browser opens (not an in-app web view — Google
rejects embedded web views for OAuth). Choose your account.

| Symptom | Cause |
|---|---|
| `DEVELOPER_ERROR` (Android) | the SHA-1 is missing or wrong on the Android client — step 1b |
| Google says the app is not verified / access blocked | your address is not under *Test users* — step 1c |
| Returns immediately, nothing happens | check the bundle id on the OAuth client matches `com.hiddenview.app` |

### 3. The ID token reaches `/api/auth/mobile`

Confirm from the server, since the phone shows only the result:

```bash
cd /opt/ih && . deploy/ops/_compose.sh && dc logs --since 10m web | grep mobile_exchange
```

One structured line per attempt. `{"event":"mobile_exchange","ok":true,"userId":N}` is the success.
A refusal names the reason and never contains either token:

| `reason` | Meaning |
|---|---|
| `not-configured` | the server has no client ids — step 2 |
| `untrusted-audience` | the app's client id is not one the server trusts; the two do not match |
| `unverified-email` | Google says the address is not verified |
| `not-allowlisted` | the account is not in `BETA_ALLOWLIST` |
| `invalid-token` | signature, issuer or expiry failed |

### 4. The bearer token is stored securely

On the Recommendations screen, tap the keystore line in the account row. It should read
**"Keystore: a token is stored"**.

That reads `expo-secure-store` — the iOS Keychain, the Android Keystore — not the in-memory cache,
which would say yes for a process that had never written anything down.

### 5. `/api/recommendations` returns your real feed

The feed renders. The header chips show your country, your Political Openness and your nudged
topics.

**Check it is yours, not the showcase feed.** Open `hidden-view.com/recommendations` in a browser
signed in as the same account and compare the headlines — they must match. Before Phase 3a a token
got output byte-identical to a *signed-out* request, which looked entirely convincing.

An **empty feed with "You're all caught up" is a valid pass** if the engine has nothing to recommend
you — it is the honest answer, and it is what a signed-in reader gets instead of somebody else's
cards. What would be a failure is a *full* feed that does not match the browser.

### 6. Settings read and write, including Country and Interest Intensity

The header chips are the read. For the write, change something on
`hidden-view.com/settings` — set the For You country, or move an Interest Intensity slider — then
pull to refresh on the phone. The chips must follow.

*(There is no mobile settings screen yet; that is Phase 4. What is being tested here is that the
bearer path reads and writes `/api/settings`, which the headless suite already proves and this
confirms on a device.)*

### 7. Sign-out removes the local token

Tap **Sign out** in the account row, confirm. You return to the signed-out screen. Tap the keystore
line: **"Keystore: no token stored"**.

Force-quit and relaunch. It must still be signed out — if it is not, the token survived in the
keystore and only the in-memory cache was cleared.

**Note what sign-out does *not* do:** it does not revoke the token server-side. `/api/me/tokens/:id`
is `SESSION_ONLY` and this token cannot reach it — a deliberate consequence of a token being unable
to revoke tokens, which is what stops a stolen one locking its owner out. Revoke from
**Settings → Extension tokens** on the web. Confirm from the token list that the device's token is
listed there under its platform label (`ios app` / `android app`).

## Reporting back

For each platform, the seven results plus:

- the `mobile_exchange` log lines from check 3 (they contain no credential);
- for check 5, whether the phone's headlines matched the browser's;
- anything the account row said.

Paste the output rather than summarising it — every wrong turn in this project's history was found
in output somebody pasted, and none in a summary.

## Known limits

- **No simulator in the development container** — no `adb`, no `xcrun`. Every check above needs your
  device; I verify from what you paste.
- **Sign in with Apple is not implemented.** App Store Guideline 4.8 will require it alongside Google
  at review time. It does not block device testing or TestFlight, and `/api/auth/mobile` already
  takes a provider discriminator so adding it is an argument rather than a second endpoint.
- **Push is not wired.** APNs/FCM needs its own registration endpoint; the existing
  `/api/push/subscriptions` takes a browser `PushSubscription` and cannot represent a device token.
