"""Resolve MIND ``news_id`` -> original publisher by parsing the MSN article snapshot.

MIND ships MSN *aggregator* URLs with **no publisher field**, which blocks the
outlet-lean join and forces the lean axis onto the noisy text classifier
(``docs/RESULTS.md`` Limitation: outlet-lean). But MSN *aggregates* from real
publishers and usually credits them, and every MIND article carries its **real MSN URL
in the ``url`` column of ``news.tsv``** -- this tool fetches *that* URL, not one
reconstructed from the ``news_id`` (the two do not match: the id is ``N55528`` while the
URL is an ``.../ar-<MSN id>`` page). So the original outlet is often recoverable from the
page's ``<meta>`` / JSON-LD / canonical-link markup. This tool fetches each article's URL
(browser User-Agent + retries) and **parses the publisher out**, writing the same
``news_id<TAB>outlet`` source-map that
``examples/build_source_map.py`` produces -- which ``ingest_mind.py --source-map`` joins
to ``examples/data/outlet_lean.csv`` to populate the **high-confidence branch** of the
hybrid lean axis (``docs/HEALTH_REPORT_PLAN.md``: outlet rating vs. AI estimation).

**This is a spike.** Its job is to answer *"is that high-confidence branch even reachable
on MIND?"* -- i.e. do the snapshots (a) still fetch and (b) carry attribution, and (c) how
many resolved outlets are in a bias table. It **must run where the network is open**
(Colab): a restrictive network policy or Microsoft's 409 gating blocks the fetch (that is
the whole open question). The five-strategy **parser is unit-tested** on synthetic
snapshots; the fetch is best-effort. It prints per-strategy hit counts and lean-join
coverage -- the numbers that decide whether pursuing the branch is worth it.

**Result (2026-07-01):** run on real MIND-small in Colab, every snapshot returns **HTTP
409 (Conflict)** -- the account-wide Azure "public access is not permitted" gate that also
blocks the MIND dataset-blob download; a browser User-Agent does not bypass it (it is an
access-policy gate, not a bot block). So on MIND the outlet branch is a **confirmed dead
end** without Microsoft-issued credentials, and the AI-estimation branch with confidence
weighting (``health_report.py --confidence-csv``) is the axis MIND supports. The parser is
retained because it works on any *non-gated* multi-publisher catalog.

    python examples/resolve_msn_publisher.py --mind-dir MINDsmall_train --political-only \\
        --limit 300 --lean-csv examples/data/outlet_lean.csv --out source_map.tsv
    python examples/ingest_mind.py --mind-dir MINDsmall_train --source-map source_map.tsv \\
        --lean-csv examples/data/outlet_lean.csv --political-only --out mind_outlet.npz
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from rwe.mind import (DEFAULT_POLITICAL_TERMS, _is_political, _norm, _read_news,
                      load_lean_table)

MSN_TEMPLATE = "https://assets.msn.com/labs/mind/{}.html"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    re.I | re.S)
_PROV_NAME_RE = re.compile(r'"(?:providerName|sourceName|cp)"\s*:\s*"([^"]{2,60})"')
_PROV_OBJ_RE = re.compile(r'"provider"\s*:\s*\{[^{}]*?"name"\s*:\s*"([^"]{2,60})"', re.S)
_BYLINE_RE = re.compile(r"(?:Provided by|Courtesy of|Source:)\s+([A-Z][\w .&'-]{2,40})")


def _attrs(tag: str) -> dict:
    """Attribute dict for one tag, tolerant of single/double quotes and order."""
    return {k.lower(): (a or b) for k, a, b in _ATTR_RE.findall(tag)}


def _metas(html: str) -> dict:
    """``property``/``name`` -> ``content`` over all ``<meta>`` tags (lower-cased keys)."""
    out = {}
    for tag in _META_RE.findall(html):
        a = _attrs(tag)
        key = a.get("property") or a.get("name")
        if key and "content" in a:
            out[key.lower()] = a["content"]
    return out


def _clean(name: str) -> str:
    """Tidy a raw publisher string: unescape, collapse space, drop a trailing ' - MSN'."""
    name = _html.unescape(name or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s*[-|–—]\s*MSN\s*$", "", name, flags=re.I)
    return name.strip(" -|·\t")


def _name_of(x):
    """Pull a ``name`` out of a JSON-LD publisher value (str / dict / list)."""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return x.get("name")
    if isinstance(x, list):
        for e in x:
            n = _name_of(e)
            if n:
                return n
    return None


def _from_jsonld(html: str):
    """publisher / provider / sourceOrganization name from a JSON-LD block."""
    for blob in _LD_RE.findall(html):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict):
                for key in ("publisher", "provider", "sourceOrganization"):
                    n = _name_of(obj.get(key))
                    if n:
                        return n
    return None


def _from_site_name(html: str):
    m = _metas(html)
    return m.get("og:site_name") or m.get("article:publisher")


def _from_provider_json(html: str):
    """MSN embeds a big JSON blob; grab its providerName/sourceName/cp/provider.name."""
    m = _PROV_NAME_RE.search(html) or _PROV_OBJ_RE.search(html)
    return m.group(1) if m else None


def _from_canonical(html: str):
    """Host of a canonical/og:url link (a non-MSN domain reveals the origin outlet)."""
    hrefs = [(_attrs(t).get("rel", "").lower(), _attrs(t).get("href", ""))
             for t in _LINK_RE.findall(html)]
    cands = [h for rel, h in hrefs if rel == "canonical" and h]
    cands.append(_metas(html).get("og:url", ""))
    for href in cands:
        host = urlparse(href).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and "msn." not in host and "." in host:
            return host
    return None


def _from_byline(html: str):
    text = re.sub(r"<[^>]+>", " ", html)                 # strip tags for a text scan
    m = _BYLINE_RE.search(text)
    return m.group(1) if m else None


# ordered: name-yielding strategies first, domain/byline as weaker fallbacks
_STRATEGIES = [("jsonld", _from_jsonld), ("site_name", _from_site_name),
               ("provider_json", _from_provider_json), ("canonical", _from_canonical),
               ("byline", _from_byline)]


def parse_publisher(html: str):
    """(publisher, strategy) from a snapshot's HTML, or (None, None). Tries JSON-LD ->
    og:site_name -> MSN provider JSON -> canonical-host -> byline, first hit wins."""
    if not html:
        return None, None
    for name, fn in _STRATEGIES:
        try:
            got = fn(html)
        except Exception:
            got = None
        if got:
            cleaned = _clean(got)
            if cleaned:
                return cleaned, name
    return None, None


def fetch_snapshot(news_id: str, url: str = None, url_template: str = MSN_TEMPLATE,
                   timeout: float = 25, retries: int = 3, backoff: float = 2.0):
    """GET the article HTML with a browser User-Agent (the 409-gating fix), retrying
    transient failures with exponential backoff. Fetches ``url`` (the *real* news.tsv
    URL) when given, else ``url_template.format(news_id)``. Returns the HTML or ``None``."""
    target = url or url_template.format(news_id)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(target, headers={
                "User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    return None


def http_status(url: str, timeout: float = 20):
    """``(code, reason)`` for a single GET -- the diagnostic that turns a blanket
    ``fetched=0`` into *why* (404 wrong/removed vs 403/409 gated vs a network error)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, "ok"
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def resolve(items, fetch_fn=None, url_template: str = MSN_TEMPLATE, sleep: float = 0.0,
            log_every: int = 50):
    """Fetch + parse each item. ``items`` = iterable of ``news_id`` or ``(news_id, url)``
    pairs (URL = the real news.tsv one). ``fetch_fn(news_id, url) -> html`` is injectable
    for tests. Returns ``(resolved, strat_counts, n_fetched)``, ``resolved`` = ``{nid:
    publisher}``."""
    fetch_fn = fetch_fn or (lambda nid, url: fetch_snapshot(nid, url, url_template))
    items = list(items)
    resolved, strat_counts, n_fetched = {}, Counter(), 0
    for i, item in enumerate(items, 1):
        nid, url = item if isinstance(item, (tuple, list)) else (item, None)
        html = fetch_fn(nid, url)
        if html:
            n_fetched += 1
            pub, strat = parse_publisher(html)
            if pub:
                resolved[nid] = pub
                strat_counts[strat] += 1
        if sleep:
            time.sleep(sleep)
        if log_every and i % log_every == 0:
            print(f"  {i}/{len(items)}  fetched={n_fetched}  resolved={len(resolved)}")
    return resolved, strat_counts, n_fetched


def lean_coverage(resolved: dict, lean_table: dict):
    """How well resolved publishers join the outlet-lean table (via ``_norm``).

    Returns ``(articles_in_table, unique_pubs_in_table, unmatched_pub_counts)`` -- the
    coverage that decides whether the outlet branch is worth wiring in."""
    in_table_articles = sum(1 for p in resolved.values() if _norm(p) in lean_table)
    unique = {p for p in resolved.values()}
    in_table_unique = sum(1 for p in unique if _norm(p) in lean_table)
    unmatched = Counter(p for p in resolved.values() if _norm(p) not in lean_table)
    return in_table_articles, in_table_unique, unmatched


def _political_news_items(mind_dir: str, political_only: bool):
    """``(news_id, url)`` pairs from ``news.tsv`` (optionally only political ones); the URL
    is the real MSN article URL the resolver fetches (not one built from the news_id)."""
    news = _read_news(Path(mind_dir) / "news.tsv")     # {nid: (cat, sub, title, url)}
    items = []
    for nid, (_, sub, title, url) in news.items():
        if not political_only or _is_political(sub, title, DEFAULT_POLITICAL_TERMS):
            items.append((nid, url))
    return items


def write_source_map(resolved: dict, out: str) -> int:
    """Write ``news_id<TAB>outlet`` rows (build_source_map.py's format)."""
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for nid, outlet in resolved.items():
            if nid and outlet:
                f.write(f"{nid}\t{outlet}\n")
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind-dir", required=True, help="directory with news.tsv")
    ap.add_argument("--political-only", action="store_true",
                    help="resolve only political articles (matches the lean pipeline)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the number of snapshots fetched (spike-friendly)")
    ap.add_argument("--out", default="source_map.tsv", help="news_id<TAB>outlet output")
    ap.add_argument("--lean-csv", default=None,
                    help="outlet-lean table to report join coverage against "
                         "(e.g. examples/data/outlet_lean.csv)")
    ap.add_argument("--url-template", default=MSN_TEMPLATE,
                    help="snapshot URL pattern ('{}' = news_id)")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between fetches (be nice)")
    args = ap.parse_args()

    items = _political_news_items(args.mind_dir, args.political_only)
    if args.limit:
        items = items[: args.limit]
    print(f"resolving {len(items)} MSN articles (political_only={args.political_only}) ...")
    if items:
        print(f"  first target URL: {items[0][1] or args.url_template.format(items[0][0])}")

    resolved, strat_counts, n_fetched = resolve(items, url_template=args.url_template,
                                                sleep=args.sleep)
    n = write_source_map(resolved, args.out)
    print(f"\nfetched {n_fetched}/{len(items)} articles; parsed a publisher for "
          f"{len(resolved)} -> wrote {n} rows to {args.out}")
    if n_fetched == 0 and items:
        first = items[0][1] or args.url_template.format(items[0][0])
        code, reason = http_status(first)
        print(f"NO articles fetched. Diagnostic GET of the first URL:\n  {first}\n"
              f"  -> HTTP {code} ({reason})")
        print("  404 => URL wrong/removed; 403/409 => gated (bot-blocked -- may need "
              "cookies or a non-datacenter IP); timeout/None => network. THIS is the "
              "reachability answer the spike returns.")
    if strat_counts:
        print("by parser strategy: " + ", ".join(f"{k}={v}" for k, v in strat_counts.most_common()))

    if args.lean_csv and resolved:
        table = load_lean_table(args.lean_csv)
        art, uniq, unmatched = lean_coverage(resolved, table)
        print(f"\nlean-join coverage vs {args.lean_csv}:")
        print(f"  {art}/{len(resolved)} resolved articles have an outlet in the lean table "
              f"({uniq} distinct outlets matched)")
        if unmatched:
            print("  top unmatched outlets (add these to the lean table to lift coverage): "
                  + ", ".join(f"{p}({c})" for p, c in unmatched.most_common(10)))
    print(f"\nnext: python examples/ingest_mind.py --mind-dir {args.mind_dir} "
          f"--source-map {args.out} --lean-csv examples/data/outlet_lean.csv --political-only")


if __name__ == "__main__":
    main()
