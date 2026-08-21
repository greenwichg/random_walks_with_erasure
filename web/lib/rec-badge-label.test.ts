import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

/**
 * The cross-perspective card's badge and its button are ONE thought — "Other side" / "Read the
 * other side" — and they live in two different keys, in five files.
 *
 * That is the whole risk. Update one and not the other and the card reads incoherently in that
 * language ("Otro lado" … "Leer otra perspectiva"), which renders perfectly, breaks no test, and
 * is invisible to anyone who does not read that language. `check-i18n` cannot catch it either: both
 * keys are present, non-empty, and have matching placeholders.
 *
 * The badge replaced "Bridging" — internal strategy vocabulary (RWE-B) that had leaked onto a card
 * a reader sees. The KEYS deliberately keep that vocabulary (`rec.strategy.rwe-b`,
 * `rec.cta.perspective`): a key names a SLOT, and the slot really is "the RWE-B strategy's badge".
 * Only the words a reader sees changed.
 */

const ROOT = join(import.meta.dirname, "..");
const MESSAGES = join(ROOT, "messages");
const LANGS = readdirSync(MESSAGES).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5));

const catalog = (lang: string): Record<string, string> =>
  JSON.parse(readFileSync(join(MESSAGES, `${lang}.json`), "utf8"));

test("the CTA restates the badge, in every language", () => {
  // The check that survives translation: whatever a language calls the badge, its button has to
  // contain that same phrase. It holds for all five without forcing a wording — "Andere Seite" /
  // "Andere Seite lesen" puts the verb last, "Lire l'autre bord" puts it first, and both pass.
  for (const lang of LANGS) {
    const c = catalog(lang);
    const badge = c["rec.strategy.rwe-b"];
    const cta = c["rec.cta.perspective"];
    assert.ok(badge?.trim(), `${lang}: the bridging badge is missing`);
    assert.ok(cta?.trim(), `${lang}: the bridging CTA is missing`);
    assert.ok(
      cta.toLowerCase().includes(badge.toLowerCase()),
      `${lang}: the badge says "${badge}" and the button says "${cta}" — one was updated ` +
        `and the other was not, so the card names the same idea two different ways`,
    );
  }
});

test("internal strategy vocabulary stays out of the reader's card", () => {
  // "Bridging" / "Puente" / "Passerelle" / "Brücke" / "Ponte" described the ALGORITHM (RWE-B), not
  // anything a reader could act on. The words are still correct internally and are still used in
  // code comments and Python docstrings; they must not come back to the badge.
  const JARGON = ["bridging", "puente", "passerelle", "brücke", "ponte"];
  for (const lang of LANGS) {
    const badge = catalog(lang)["rec.strategy.rwe-b"].toLowerCase();
    for (const word of JARGON) {
      assert.notEqual(badge, word, `${lang}: the badge is internal jargon again (${word})`);
    }
  }
});

test("the badge stays short enough for a badge", () => {
  // It renders as <Badge> + a 12px icon, in a flex row WITH the publication date beside it, inside
  // a card marked min-w-0 because it was overflowing mobile by ~49px (recommendation-card.tsx).
  // Its siblings — Discovery / For you / Same story — are 7-10 characters in English; the ceiling
  // here is generous enough for German compounds and still refuses a sentence.
  for (const lang of LANGS) {
    const badge = catalog(lang)["rec.strategy.rwe-b"];
    assert.ok(
      badge.length <= 16,
      `${lang}: badge "${badge}" is ${badge.length} chars — too long to sit beside the date`,
    );
    assert.ok(!badge.includes("."), `${lang}: a badge is a label, not a sentence`);
  }
});

test("the badge does not duplicate the explanation line under it", () => {
  // The card has three slots and they must do three jobs: the badge names the KIND of
  // recommendation, `explanation.bridge` gives the political specifics, the button is the action.
  // "Another Perspective" was rejected as the badge for exactly this reason — it would have made
  // the card say "perspective" three times in about 40 vertical pixels.
  for (const lang of LANGS) {
    const c = catalog(lang);
    const badge = c["rec.strategy.rwe-b"].toLowerCase().replace(/[.]/g, "");
    const why = c["explanation.bridge"].toLowerCase();
    assert.ok(
      !why.includes(badge),
      `${lang}: the badge (${c["rec.strategy.rwe-b"]}) is repeated verbatim in the explanation ` +
        `line below it — one of the two slots is not earning its space`,
    );
  }
});
