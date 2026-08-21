import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

/**
 * The manifest is the file that decides whether the app is installable at all, and every way it
 * can be wrong is silent: the browser simply declines to offer installation, with no error the app
 * can see. So the fields are asserted here rather than discovered by someone wondering why the
 * install button never appeared.
 *
 * It also carries two colours that CANNOT be media-queried — a manifest has one
 * `background_color` and one `theme_color` for both themes — so they are checked against the app's
 * real tokens instead of being left at whatever the favicon generator produced. Before this, the
 * manifest said `#ffffff` while the dark app paints `#131416`, which is a white flash on every
 * cold launch.
 */

const PUBLIC = join(import.meta.dirname, "..", "public");
const manifest = JSON.parse(readFileSync(join(PUBLIC, "site.webmanifest"), "utf8"));

test("the fields a browser requires before it will offer installation", () => {
  assert.equal(manifest.name, "Hidden View");
  assert.ok(manifest.short_name?.length, "short_name is the home-screen label");
  assert.ok(manifest.short_name.length <= 12, "short_name is truncated by launchers past ~12 chars");
  assert.equal(manifest.display, "standalone");
  assert.ok(manifest.start_url, "an absent start_url defaults to the manifest URL — be explicit");
  assert.ok(manifest.description?.length, "shown in the richer install dialog");
});

test("start_url is inside scope, or the installed app opens in a browser tab", () => {
  assert.ok(manifest.scope, "scope must be explicit");
  const scope = new URL(manifest.scope, "https://hidden-view.com");
  const start = new URL(manifest.start_url, "https://hidden-view.com");
  assert.ok(
    start.pathname.startsWith(scope.pathname),
    `start_url ${start.pathname} is outside scope ${scope.pathname}`,
  );
});

test("id is set, so the app's identity survives a start_url change", () => {
  // Without `id`, identity is derived from start_url — change it later and the browser treats the
  // result as a DIFFERENT app, leaving the old one installed and orphaned.
  assert.equal(manifest.id, "/");
});

test("icons: 192 and 512 are present, and at least one is maskable", () => {
  const sizes = new Set(manifest.icons.map((i: { sizes: string }) => i.sizes));
  assert.ok(sizes.has("192x192"), "192x192 is required by Chromium");
  assert.ok(sizes.has("512x512"), "512x512 is required by Chromium");
  const maskable = manifest.icons.filter((i: { purpose?: string }) =>
    (i.purpose ?? "").split(/\s+/).includes("maskable"),
  );
  assert.ok(
    maskable.length >= 1,
    "without a maskable icon Android shrinks the icon inside a white circle",
  );
  assert.ok(
    maskable.some((i: { sizes: string }) => i.sizes === "512x512"),
    "the maskable icon should be the large one",
  );
});

test("every file the manifest references actually exists", () => {
  // A manifest pointing at a missing icon is worse than one with fewer icons: the browser 404s it
  // and may decline installability entirely.
  const refs = [
    ...manifest.icons.map((i: { src: string }) => i.src),
    ...(manifest.screenshots ?? []).map((s: { src: string }) => s.src),
  ];
  for (const src of refs) {
    assert.ok(existsSync(join(PUBLIC, src)), `${src} is referenced but not present in public/`);
  }
});

test("the colours match the app, not the favicon generator's defaults", () => {
  // A manifest has ONE of each for both themes. `#131416` is the dark `--background`
  // (hsl 220 7% 8%) the app actually paints, and the app defaults to `system` on a product whose
  // screenshots are dark; `#463acb` is the brand purple already used as the icon fill.
  assert.equal(manifest.background_color, "#131416");
  assert.equal(manifest.theme_color, "#463acb");
  assert.notEqual(manifest.background_color, "#ffffff", "a white splash flashes before a dark app");
});

test("the offline route the worker precaches is a real page", () => {
  const offline = join(import.meta.dirname, "..", "app", "offline", "page.tsx");
  assert.ok(existsSync(offline), "sw.js precaches /offline — it must exist or install() 404s");
});
