// @ts-check
/**
 * The message format — a minimal ICU `plural` subset, in plain JavaScript.
 *
 * A catalog value may write:
 *
 *     "{stories, plural, one {# story} other {# stories}}"
 *
 * `#` renders the count. Branch labels are the CLDR categories (`zero one two few many other`) or
 * an exact `=N` match, which wins over the category. A missing category falls back to `other`.
 * Nothing else from ICU is supported (no `select`, no nested plurals, no number skeletons).
 *
 * Why a real message format and not `key.one` / `key.other` suffixes: several strings count TWO
 * things at once — "{stories} stories across {publishers} publishers" is the one that prompted
 * this — and a per-key suffix can only agree with one of them. Selection has to happen per
 * argument, inside the string, which is what ICU's syntax is for.
 *
 * WHY THIS FILE IS .js AND NOT .ts, when everything around it is TypeScript: a message format is a
 * parser, and this one has to run in three places — the web bundler, Metro, and `check-i18n.mjs`,
 * a bare Node script the Docker build runs before Next is ever invoked. The build image is Node 20,
 * which has no type stripping, so a `.ts` import from that script fails the image build outright
 * (ERR_UNKNOWN_FILE_EXTENSION). Keeping the parse in plain ESM is what lets the build gate and the
 * runtime share ONE implementation instead of two that can drift — and they did drift: the two
 * hand-rolled regexes that came before this both read `other {are}` as an argument named `are`.
 * `message-format.d.ts` is its type contract; `core.ts` re-exports what callers use.
 */

/** `Intl.PluralRules` is not free to construct, and `t` runs on every render. */
const RULES = new Map();

/**
 * @param {string} lang
 * @returns {Intl.PluralRules | null}
 */
function rulesFor(lang) {
  const hit = RULES.get(lang);
  if (hit) return hit;
  try {
    const made = new Intl.PluralRules(lang);
    RULES.set(lang, made);
    return made;
  } catch {
    return null; // an environment without the locale data: everything reads as `other`
  }
}

/**
 * The count an argument carries, or NaN when it isn't one.
 *
 * A call site may pass a number (preferred — `t("storyCard.sources", { n: 4 })`) or a string a
 * caller already formatted. A formatted string only fails to parse once it has been compacted
 * ("1.2K"), and every count large enough to compact is `other` in all five supported languages,
 * which is where NaN lands anyway.
 *
 * @param {unknown} value
 * @returns {number}
 */
function countOf(value) {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") return Number(value);
  return Number.NaN;
}

const PLURAL_HEAD = /^\{\s*(\w+)\s*,\s*plural\s*,/;
const BRANCH_LABEL = /(=\d+|zero|one|two|few|many|other)\s*\{/g;

/**
 * Index of the `}` closing a `{` that opened just before `from`, or -1 if the braces never balance.
 * @param {string} str
 * @param {number} from
 * @returns {number}
 */
function closeBrace(str, from) {
  let depth = 1;
  for (let j = from; j < str.length; j++) {
    if (str[j] === "{") depth++;
    else if (str[j] === "}" && --depth === 0) return j;
  }
  return -1;
}

/**
 * Split a plural body into its `label {text}` branches, honouring braces inside a branch.
 * @param {string} body
 * @returns {Map<string, string>}
 */
function branchesOf(body) {
  /** @type {Map<string, string>} */
  const out = new Map();
  BRANCH_LABEL.lastIndex = 0;
  let m;
  while ((m = BRANCH_LABEL.exec(body))) {
    const end = closeBrace(body, BRANCH_LABEL.lastIndex);
    if (end === -1) break; // unbalanced — stop rather than invent a branch
    out.set(m[1], body.slice(BRANCH_LABEL.lastIndex, end));
    BRANCH_LABEL.lastIndex = end + 1;
  }
  return out;
}

/**
 * The argument names a message needs: its plain `{name}` placeholders, plus the argument each
 * plural block selects on. The build gate and the catalog unit test both check that the five
 * languages agree on this set.
 *
 * A branch body is a message of its own, so it is scanned rather than skipped — that is how
 * `{n, plural, one {See # result for “{q}”} …}` reports `q`, and how `other {are}` does NOT report
 * an argument named `are`.
 *
 * @param {string} template
 * @returns {Set<string>}
 */
export function messageArgs(template) {
  /** @type {Set<string>} */
  const out = new Set();
  /** @param {string} str */
  const scan = (str) => {
    let plain = "";
    let i = 0;
    while (i < str.length) {
      const open = str.indexOf("{", i);
      if (open === -1) {
        plain += str.slice(i);
        break;
      }
      const head = PLURAL_HEAD.exec(str.slice(open));
      if (!head) {
        plain += str.slice(i, open + 1);
        i = open + 1;
        continue;
      }
      out.add(head[1]);
      const bodyStart = open + head[0].length;
      const end = closeBrace(str, bodyStart);
      if (end === -1) break;
      for (const branch of branchesOf(str.slice(bodyStart, end)).values()) scan(branch);
      plain += str.slice(i, open);
      i = end + 1;
    }
    for (const m of plain.matchAll(/\{(\w+)\}/g)) out.add(m[1]);
  };
  scan(template);
  return out;
}

/**
 * Expand every `{arg, plural, …}` block against `params`. Anything that is not a plural block —
 * including ordinary `{name}` placeholders and stray braces — is copied through untouched for the
 * substitution pass that follows.
 *
 * @param {string} template
 * @param {Record<string, unknown>} params
 * @param {string} lang
 * @param {(n: number) => string} formatNumber
 * @returns {string}
 */
export function expandPlurals(template, params, lang, formatNumber) {
  if (!template.includes(", plural,") && !template.includes(",plural,")) return template;
  let out = "";
  let i = 0;
  while (i < template.length) {
    const open = template.indexOf("{", i);
    if (open === -1) {
      out += template.slice(i);
      break;
    }
    const head = PLURAL_HEAD.exec(template.slice(open));
    if (!head) {
      out += template.slice(i, open + 1);
      i = open + 1;
      continue;
    }
    const j = closeBrace(template, open + head[0].length);
    if (j === -1) {
      // Unbalanced braces: leave the rest verbatim so the damage is visible and greppable
      // rather than silently swallowed.
      out += template.slice(i);
      break;
    }
    const arg = head[1];
    const branches = branchesOf(template.slice(open + head[0].length, j));
    const n = countOf(params[arg]);
    const category = Number.isNaN(n) ? "other" : (rulesFor(lang)?.select(n) ?? "other");
    const chosen =
      branches.get(`=${n}`) ?? branches.get(category) ?? branches.get("other") ?? template.slice(open, j + 1);
    const shown = Number.isNaN(n) ? String(params[arg] ?? "") : formatNumber(n);
    out += template.slice(i, open) + chosen.replace(/#/g, shown);
    i = j + 1;
  }
  return out;
}
