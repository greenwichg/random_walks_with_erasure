/**
 * `expo-secure-store` for the VALIDATION harness only.
 *
 * The app has no web target: `expo-secure-store`'s own web build is an empty module, and the
 * keystore is the iOS Keychain / Android Keystore. The parity harness (the screen-by-screen
 * comparison against the mobile web app; its report lands in docs/MOBILE_PARITY.md when the pass
 * completes) renders the app's real component tree through react-native-web in a headless browser,
 * beside the mobile web app, and it has to hold a bearer token to reach the signed-in screens. This is the smallest
 * stand-in that lets it: the same three async calls over `localStorage`, which the harness seeds
 * before the page loads. Metro substitutes it for `expo-secure-store` on the web platform only
 * (metro.config.js); no native bundle ever contains it.
 */
const store = () => {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
};

module.exports = {
  async getItemAsync(key) {
    const s = store();
    return s ? s.getItem(key) : null;
  },
  async setItemAsync(key, value) {
    store()?.setItem(key, value);
  },
  async deleteItemAsync(key) {
    store()?.removeItem(key);
  },
};
