/**
 * Content script (registered on any HTTPS page once the user enables capture). Its entire job:
 * decide whether the current page is a genuine article and, if so, hand the service worker the URL +
 * standard metadata. It reads *only* standard metadata (OpenGraph / JSON-LD / <h1> / the page URL) —
 * never article body text, form fields, cookies, or anything else. Non-article pages send only an
 * anonymous outcome label (no URL). No scoring or classification of content happens here.
 */
(() => {
  /** Collect JSON-LD `@type` values (handles arrays and `@graph`). */
  function jsonLdTypes() {
    const types = [];
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try {
        data = JSON.parse(el.textContent || "");
      } catch {
        continue;
      }
      const nodes = Array.isArray(data) ? data : data && data["@graph"] ? data["@graph"] : [data];
      for (const node of nodes) {
        const t = node && node["@type"];
        if (Array.isArray(t)) types.push(...t);
        else if (t) types.push(t);
      }
    }
    return types;
  }

  function metaContent(selector) {
    const el = document.querySelector(selector);
    return el ? el.getAttribute("content") : null;
  }

  const signals = {
    ogType: metaContent('meta[property="og:type"]'),
    ldTypes: jsonLdTypes(),
    hasArticlePublishedTime: !!(metaContent('meta[property="article:published_time"]') ||
                                metaContent('meta[name="article:published_time"]')),
    hasHeadline: !!document.querySelector("h1"),
  };

  // `classifyPage` comes from common.js (injected just before this file). It returns the decision and
  // a closed-set signal/reason label — no URL, no free text — used for anonymous detection telemetry.
  const verdict = classifyPage(signals);
  if (!verdict.article) {
    // Anonymous outcome only (why a page was NOT captured); the service worker keeps a local
    // aggregate counter. Never carries the URL or any page content.
    chrome.runtime.sendMessage({ type: "detect", article: false, signal: verdict.signal });
    return;
  }

  const canonical = document.querySelector('link[rel="canonical"]');
  const url = (canonical && canonical.href) || location.href;
  // Standard page metadata only (OpenGraph / meta tags — never article text): title + description
  // enrich scoring; image / site name / publication date / author / language let the backend make
  // the article a first-class catalog entry (Commit 18). `collectArticleMeta` lives in common.js.
  const meta = collectArticleMeta(metaContent, {
    docTitle: document.title,
    docLang: document.documentElement.lang,
  });

  chrome.runtime.sendMessage({
    type: "article",
    url,
    title: meta.title,
    description: meta.description,
    image: meta.image,
    siteName: meta.siteName,
    publishedAt: meta.publishedAt,
    author: meta.author,
    language: meta.language,
    observedAt: new Date().toISOString(),
    detectSignal: verdict.signal,   // closed-set label (og:type|jsonld|published_time) — anonymous telemetry
  });
})();
