/**
 * Copy the country flag SVGs into `public/flags/` at build time.
 *
 * WHY THESE ARE FILES AND NOT EMOJI: a flag emoji is a pair of Unicode regional-indicator letters,
 * and the platform is expected to draw a flag for the pair. **Windows ships no flag glyphs at
 * all**, so every Windows browser falls back to rendering the two letters — "US United States"
 * where macOS, iOS and Android show "🇺🇸 United States". Nothing is broken in our code; there is no
 * glyph to draw. The only fix that renders the same everywhere is to ship the artwork ourselves,
 * and the CSP (`font-src 'self' data:`) rules out pulling an emoji font from a CDN — deliberately,
 * and not worth widening for decoration. `img-src 'self'` already allows these.
 *
 * COPIED, NOT VENDORED: the set is ~2.7 MB across 271 files. Committing it would put that in every
 * clone and every diff for artwork nobody edits; `npm ci` already runs in the web image before
 * `npm run build`, so the files land in the image without living in git. `public/flags/` is
 * gitignored for the same reason `public/sw-data.js` is.
 *
 * flag-icons is MIT, so no attribution is required in the UI.
 */
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "..", "node_modules", "flag-icons", "flags", "4x3");
const OUT = join(HERE, "..", "public", "flags");

if (!existsSync(SRC)) {
  // Loud, not silent: a missing set means every chip renders nameless-but-for-the-text, and the
  // build that produced it would otherwise look completely successful.
  console.error(`build-flags: ${SRC} not found — is flag-icons installed? (npm ci)`);
  process.exit(1);
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
cpSync(SRC, OUT, { recursive: true });

const files = readdirSync(OUT).filter((f) => f.endsWith(".svg"));
const bytes = files.reduce((n, f) => n + statSync(join(OUT, f)).size, 0);
console.log(`build-flags: ${files.length} flags -> public/flags (${(bytes / 1024 / 1024).toFixed(1)} MB)`);
