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

function loadCorpus(argv) {
  if (argv[0] === "--url" && argv[1]) return [["", argv[1]]];
  if (argv[0]) {
    const path = resolve(process.cwd(), argv[0]);
    return readFileSync(path, "utf8").split(/\r?\n/).map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"))
      .map((l) => {
        const parts = l.split(/\t+/);
        return parts.length > 1 ? [parts[0].toLowerCase(), parts.slice(1).join("\t")] : ["", parts[0]];
      });
  }
  console.error("(no corpus file given — running the built-in SEED; pass a TSV of '<accept|reject>\\t<url>' to verify your own)\n");
  return SEED;
}

const pad = (v, n) => String(v).padEnd(n).slice(0, n);

(async () => {
  const corpus = loadCorpus(process.argv.slice(2));
  console.log(pad("expect", 8), pad("got", 8), pad("signal", 16), "url");
  console.log("-".repeat(100));
  let fails = 0, fetchErrors = 0, accepted = 0;
  for (const [expect, url] of corpus) {
    const r = await fetchHtml(url);
    if (r.error) {
      fetchErrors++;
      console.log(pad(expect || "—", 8), pad("ERR", 8), pad(r.error, 16), url);
      continue;
    }
    const v = classifyPage(signalsFrom(r.html));
    const got = v.article ? "accept" : "reject";
    if (v.article) accepted++;
    const mark = expect ? (got === expect ? "✓" : "✗ MISMATCH") : "";
    if (expect && got !== expect) fails++;
    console.log(pad(expect || "—", 8), pad(got, 8), pad(v.signal, 16), `${url}  ${mark}`);
  }
  console.log("-".repeat(100));
  console.log(`total=${corpus.length}  accepted=${accepted}  fetch-errors=${fetchErrors}  labelled-mismatches=${fails}`);
  if (fetchErrors) console.log("note: fetch errors are network/paywall/bot-wall issues, NOT detector rejections.");
  process.exit(fails > 0 ? 1 : 0);
})();
