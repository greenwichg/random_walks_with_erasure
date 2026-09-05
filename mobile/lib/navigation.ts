import { router, type Href } from "expo-router";
import * as WebBrowser from "expo-web-browser";

import { config } from "./config.ts";

/**
 * Web hrefs → native navigation.
 *
 * The mobile-web components link with web paths (`/stories?topic=…`, `/publishers/NPR`,
 * `/stories/st_1`), and the ported components keep those strings so the two trees read alike. The
 * app's routes are the web's paths, one for one (see `app/`), so most hrefs are a straight push;
 * the surfaces this build does not carry open the same page on the web in the in-app browser —
 * the same account, on the same deployment — rather than a dead row.
 */
const NATIVE: ReadonlyArray<[RegExp, (m: RegExpMatchArray, q: Record<string, string>) => Href]> = [
  [/^\/$/, () => "/" as Href],
  [/^\/recommendations$/, () => "/recommendations" as Href],
  [/^\/search$/, (_m, q) => ({ pathname: "/search", params: q }) as Href],
  [/^\/stories\/([^/?]+)$/, (m) => ({ pathname: "/stories/[id]", params: { id: decodeURIComponent(m[1]!) } }) as Href],
  [/^\/stories$/, (_m, q) => ({ pathname: "/stories", params: q }) as Href],
  [/^\/publishers\/([^/?]+)$/, (m) => ({ pathname: "/publishers/[name]", params: { name: decodeURIComponent(m[1]!) } }) as Href],
  [/^\/settings$/, () => "/settings" as Href],
  [/^\/alerts$/, () => "/alerts" as Href],
  [/^\/saved$/, () => "/saved" as Href],
];

/** `?a=1&b=2` → `{ a: "1", b: "2" }`. */
export function parseQuery(search: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!search) return out;
  for (const pair of search.replace(/^\?/, "").split("&")) {
    if (!pair) continue;
    const [k, v = ""] = pair.split("=");
    out[decodeURIComponent(k!)] = decodeURIComponent(v.replace(/\+/g, " "));
  }
  return out;
}

/** The native route for a web href, or null when the app has no screen for it. */
export function routeFor(href: string): Href | null {
  const [path, search = ""] = href.split("?");
  const q = parseQuery(search);
  for (const [re, make] of NATIVE) {
    const m = path!.match(re);
    if (m) return make(m, q);
  }
  return null;
}

/** Open the web page for a path this app does not carry, in the in-app browser. */
export function openOnWeb(path: string): void {
  if (!config.apiBaseUrl) return;
  void WebBrowser.openBrowserAsync(`${config.apiBaseUrl}${path}`).catch(() => {});
}

/** Open an absolute publisher URL in the in-app browser — the phone's "new tab". */
export function openExternal(url: string): void {
  void WebBrowser.openBrowserAsync(url).catch(() => {});
}

/** Push the href's screen (a new history entry, as a web link would). */
export function navigate(href: string): void {
  const route = routeFor(href);
  if (route) router.push(route);
  else openOnWeb(href);
}

/**
 * The tab bar's move: go to the destination without stacking a second copy of a screen the
 * reader is already on — `router.navigate` reuses an existing entry and pushes otherwise.
 */
export function navigateTab(href: string): void {
  const route = routeFor(href);
  if (route) router.navigate(route);
  else openOnWeb(href);
}

/** The story page's "Back to stories": the stack's own back when there is one, else the index. */
export function back(fallback = "/stories"): void {
  if (router.canGoBack()) router.back();
  else navigate(fallback);
}
