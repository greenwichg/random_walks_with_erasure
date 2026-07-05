/**
 * Content script (supported news sites only). Its entire job: decide whether the current page
 * is an article and, if so, hand the service worker the URL + title. It reads *only* standard
 * metadata (OpenGraph / JSON-LD / <h1> / <article>) and the page URL — never article text,
 * form fields, cookies, or anything else. No scoring or classification happens here.
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
    hasArticleTag: !!document.querySelector("article"),
    hasHeadline: !!document.querySelector("h1"),
  };

  // `isArticlePage` comes from common.js (injected just before this file).
  if (!isArticlePage(signals)) return;

  const canonical = document.querySelector('link[rel="canonical"]');
  const url = (canonical && canonical.href) || location.href;
  const title = (metaContent('meta[property="og:title"]') || document.title || "").trim();
  // og:description / meta description — a one-line abstract that lets the backend enrich
  // register + emotion far better than the headline alone. Still standard metadata, no body text.
  const description = (metaContent('meta[property="og:description"]') ||
                       metaContent('meta[name="description"]') || "").trim();

  chrome.runtime.sendMessage({
    type: "article",
    url,
    description,
    title,
    observedAt: new Date().toISOString(),
  });
})();
