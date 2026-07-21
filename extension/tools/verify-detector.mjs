#!/usr/bin/env node
/**
 * verify-detector — empirically verify the article detector against REAL, LIVE pages before a
 * production rollout. It fetches each URL, extracts exactly the signals the content script reads
 * (og:type / JSON-LD @type / article:published_time / <h1> presence — standard metadata only, never
 * body text), runs the SHIPPING detector (`classifyPage` imported from ../common.js), and reports the
 * decision + signal + expected-match, with a precision/recall summary.
 *
 * This is intentionally NOT part of CI: CI/sandbox egress is network-restricted, and detection depends
 * on live publisher markup. Run it on a machine with open internet before shipping a detector change.
 *
 * Usage:
 *   node extension/tools/verify-detector.mjs                 # run the built-in seed corpus
 *   node extension/tools/verify-detector.mjs urls.tsv        # custom corpus: "<expect>\t<url>" per line
 *                                                            #   <expect> = accept | reject (or omit)
 *   node extension/tools/verify-detector.mjs --url https://example.com/story   # a single URL
 *
 * Exit code is non-zero if any labelled expectation fails, so it can gate a release.
 */
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { classifyPage } = require("../common.js"); // the SAME detector the extension ships
const HERE = dirname(fileURLToPath(import.meta.url));

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
           "Chrome/120.0.0.0 Safari/537.36";
const TIMEOUT_MS = 20000;

// A small, diverse seed corpus. Article URLs age (paywalls, archival) — refresh as needed, or pass
// your own file. Homepages/section pages are stable negatives. `expect` is the desired outcome.
const SEED = [
  ["accept", "https://www.theguardian.com/international"],           // NOTE: replace with a live ARTICLE url
  ["accept", "https://apnews.com"],                                  //   (these are placeholders you should
  ["accept", "https://www.bbc.com/news"],                           //    swap for current article URLs)
  ["reject", "https://www.google.com/"],
  ["reject", "https://www.youtube.com/"],
  ["reject", "https://www.amazon.com/"],
];

// ---- HTML signal extraction (mirrors content.js: standard metadata only, no body text) ---------- //
function metaContent(html, prop) {
  // match <meta property="X" content="Y"> or <meta name="X" content="Y"> (attr order-independent)
  const re = new RegExp(
    `<meta[^>]*(?:property|name)=["']${prop.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["'][^>]*>`, "i");
  const tag = (html.match(re) || [])[0];
  if (!tag) return null;
  const m = tag.match(/content=["']([^"']*)["']/i);
  return m ? m[1] : null;
}

function jsonLdTypes(html) {
  const types = [];
  const re = /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) {
    let data;
    try { data = JSON.parse(m[1].trim()); } catch { continue; }
    const nodes = Array.isArray(data) ? data : data && data["@graph"] ? data["@graph"] : [data];
    for (const node of nodes) {
      const t = node && node["@type"];
      if (Array.isArray(t)) types.push(...t);
      else if (t) types.push(t);
    }
  }
  return types;
}

function signalsFrom(html) {
  return {
    ogType: metaContent(html, "og:type"),
    ldTypes: jsonLdTypes(html),
    hasArticlePublishedTime: !!metaContent(html, "article:published_time"),
    hasHeadline: /<h1[\s>]/i.test(html),
  };
}

async function fetchHtml(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { redirect: "follow", signal: ctrl.signal, headers: { "User-Agent": UA } });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return { html: await res.text(), finalUrl: res.url };
  } catch (e) {
    return { error: (e && e.name === "AbortError") ? "timeout" : (e && e.message) || "fetch failed" };
  } finally {
    clearTimeout(timer);
  }
}

// TSV rows: "<accept|reject>\t<url>". A line `@ <category>` sets the category for the rows beneath it
// (so a large list can be organised into blocks); `#` lines are comments. Category is inherited.
function loadCorpus(argv) {
  if (argv[0] === "--url" && argv[1]) return [{ expect: "", url: argv[1], category: "single" }];
  if (!argv[0]) {
    console.error("(no corpus file — running the built-in SEED placeholders; pass a TSV to verify your own)\n");
    return SEED.map(([expect, url]) => ({ expect, url, category: "seed" }));
  }
  const path = resolve(process.cwd(), argv[0]);
  const rows = [];
  let category = "uncategorized";
  for (let line of readFileSync(path, "utf8").split(/\r?\n/)) {
    line = line.trim();
    if (!line) continue;
    if (line.startsWith("@")) { category = line.slice(1).trim() || "uncategorized"; continue; }
    if (line.startsWith("#")) continue;
    const parts = line.split(/\t+/);
    const expect = (parts.length > 1 ? parts[0] : "").toLowerCase();
    const url = parts.length > 1 ? parts.slice(1).join("\t") : parts[0];
    rows.push({ expect, url, category });
  }
  return rows;
}

const pad = (v, n) => String(v).padEnd(n).slice(0, n);

(async () => {
  const corpus = loadCorpus(process.argv.slice(2));
  const results = [];
  console.error(`fetching ${corpus.length} URLs sequentially (a few minutes for a large list) — ` +
                `progress: . ok  ! fetch-error  ✗ mismatch`);
  for (const row of corpus) {
    const r = await fetchHtml(row.url);
    let got, signal, status;
    if (r.error) { got = "ERR"; signal = r.error; status = "err"; }
    else {
      const v = classifyPage(signalsFrom(r.html));
      got = v.article ? "accept" : "reject"; signal = v.signal;
      status = row.expect ? (got === row.expect ? "ok" : "mismatch") : "unlabelled";
    }
    results.push({ ...row, got, signal, status });
    process.stderr.write(status === "mismatch" ? "✗" : status === "err" ? "!" : ".");
  }
  process.stderr.write("\n");

  const cats = [...new Set(results.map((r) => r.category))];
  for (const cat of cats) {
    const rows = results.filter((r) => r.category === cat);
    console.log(`\n### ${cat}  (${rows.length})`);
    console.log(pad("expect", 8), pad("got", 8), pad("signal", 16), "url");
    for (const r of rows) {
      const mark = r.status === "mismatch" ? "  ✗ MISMATCH" : r.status === "err" ? "  (fetch err — inconclusive)" : "";
      console.log(pad(r.expect || "—", 8), pad(r.got, 8), pad(r.signal, 16), `${r.url}${mark}`);
    }
    const acc = rows.filter((r) => r.got === "accept").length;
    const mm = rows.filter((r) => r.status === "mismatch").length;
    const er = rows.filter((r) => r.status === "err").length;
    console.log(`  → ${cat}: n=${rows.length} accepted=${acc} mismatches=${mm} fetch-errors=${er}`);
  }

  const mism = results.filter((r) => r.status === "mismatch");
  const errs = results.filter((r) => r.status === "err");
  console.log(`\n==== TOTAL n=${results.length}  accepted=${results.filter((r) => r.got === "accept").length}` +
              `  fetch-errors=${errs.length}  labelled-mismatches=${mism.length} ====`);
  if (mism.length) {
    console.log(`\nMISMATCHES — investigate every one (${mism.length}):`);
    for (const r of mism) console.log(`  [${r.category}] expected ${r.expect}, got ${r.got} via "${r.signal}"\n     ${r.url}`);
  }
  if (errs.length) {
    console.log(`\nFETCH ERRORS — inconclusive (bot-wall/paywall/network), NOT detector failures (${errs.length}):`);
    for (const r of errs) console.log(`  [${r.category}] ${r.signal}  ${r.url}`);
  }
  console.log(`\nPer-category review checklist:`);
  for (const cat of cats) {
    const rows = results.filter((r) => r.category === cat);
    const mm = rows.filter((r) => r.status === "mismatch").length;
    const er = rows.filter((r) => r.status === "err").length;
    const flag = mm ? "❌ MISMATCH — investigate"
      : (er === rows.length && rows.length ? "⚠ all fetch-errors — re-source these URLs" : "✅ clean");
    console.log(`  ${pad(cat, 18)} ${flag}  (n=${rows.length}, fetch-err=${er})`);
  }
  console.log(`\nExit non-zero if any labelled mismatch. Fetch errors do not fail the run.`);
  process.exit(mism.length > 0 ? 1 : 0);
})();
