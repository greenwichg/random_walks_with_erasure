#!/usr/bin/env python3
"""outlet_search.py — the IH outlet index: our own news-source discovery engine.

The searchable half of the "own Web Search API" design (`docs`-level review, 2026-08-31): a
domain-level index of news outlets, queryable by geography/language/free text, built from data
that is either already ours or openly licensed — and NEVER from a news API. The SerpAPI-shaped
facade in ``api_fastapi`` serves it; ``source_web``'s existing adapter consumes that facade with
zero application changes, which is the whole point of wearing SerpAPI's shape.

## Where rows come from, and the independence invariant

Every row records its evidence sources. The core index is rows from:

  ``exhaust``    the admission table + outlet registry — hosts this deployment has already seen,
                 with the language/country/publisher evidence discovery recorded (M10/M11).
  ``wikidata``   news organisations with a country/language/official-website statement (CC0).
  ``wikipedia``  the external links of "List of newspapers in {Country}" pages — the same lists
                 the directory channel imports by hand, fetched by title instead.
  ``cc``         Common Crawl domain ranks, as a PROMINENCE prior on rows that already exist
                 (bulk file supplied by the operator; this module never downloads it).

SerpAPI results never write this index. The facade may TOP UP a thin response with a live SerpAPI
call, and those results flow onward as discovery candidates exactly as they always have — but the
index itself must pass its coverage bars with the external provider dark, or "our own search"
is a label rather than a property.

## Fetchers are injected, and the network default is None

Same structural safety as `source_web.discover` and `source_validation.validate`, inherited for
the same reason: this sandbox cannot reach Wikidata, production can, and a module that fetched by
default would make the ToS/licensing question depend on who happens to import it. ``build``
without fetchers ingests the exhaust only.

    python examples/outlet_search.py build --exhaust --db "$RWE_DB_URL"       # offline
    python examples/outlet_search.py build --wikidata --wikipedia             # network (the box)
    python examples/outlet_search.py measure --db "$RWE_DB_URL"               # Phase 0 bar
    python examples/outlet_search.py query --country KE
    python examples/outlet_search.py query "kenya local newspaper"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: Coverage bar for Phase 0 / the switch-off drill: the share of corpus gaps the index must offer
#: candidates for, and how many candidates count as "offered". Pre-registered in the design review;
#: change them there first, here second.
MEASURE_MIN_CANDIDATES = 3
MEASURE_MIN_GAP_SHARE = 0.60

_USER_AGENT = "HiddenView-Research/0.1 (+https://hidden-view.com/crawler)"

#: Second-level labels under which a two-label cut is not the registrable domain ("the-star.co.ke"
#: must not collapse to "co.ke"). A deliberate HEURISTIC standing in for the Public Suffix List:
#: it covers the news-relevant ccTLD conventions; a PSL snapshot replaces it if this ever
#: misgroups a real outlet pair. Kept tiny on purpose — the failure mode of an incomplete list is
#: two rows for one outlet, which dedup downstream tolerates; the failure mode of a wrong list is
#: two outlets merged, which nothing tolerates.
_CC_SLDS = frozenset({"co", "com", "net", "org", "ac", "gov", "go", "or", "ne", "edu", "mil"})


def registrable_domain(host: str) -> str:
    parts = [p for p in (host or "").lower().strip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in _CC_SLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def canonical_host(url_or_host: str) -> str:
    """A URL or bare host -> the host the index keys on (www/m/amp prefixes stripped)."""
    h = (url_or_host or "").strip().lower()
    if "//" in h:
        h = urllib.parse.urlsplit(h).hostname or ""
    h = h.strip(".").rstrip("/")
    for prefix in ("www.", "m.", "amp."):
        if h.startswith(prefix) and h.count(".") >= 2:
            h = h[len(prefix):]
    return h


def index_path() -> str:
    """Where the index lives. Beside the engine DB by default, because that directory is the one
    the deployment already persists and backs up; ``RWE_OUTLET_INDEX_DB`` overrides."""
    explicit = os.environ.get("RWE_OUTLET_INDEX_DB", "").strip()
    if explicit:
        return explicit
    db_url = os.environ.get("RWE_DB_URL", "")
    m = re.search(r"sqlite:///+(.+)$", db_url)
    base = os.path.dirname("/" + m.group(1).lstrip("/")) if m else "."
    return os.path.join(base, "outlet_index.db")


def open_index(path: "str | None" = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or index_path())
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hosts(
            host TEXT PRIMARY KEY, domain TEXT, name TEXT,
            country TEXT, language TEXT,
            evidence TEXT DEFAULT '[]',          -- JSON list of {source, detail?}
            prominence REAL DEFAULT 0.0,         -- log-scale prior (CC rank / observed volume)
            first_seen TEXT, last_seen TEXT);
        CREATE INDEX IF NOT EXISTS idx_hosts_geo ON hosts(country, language);
        CREATE INDEX IF NOT EXISTS idx_hosts_domain ON hosts(domain);
        CREATE VIRTUAL TABLE IF NOT EXISTS hosts_fts USING fts5(name, aka, host UNINDEXED);
        """)
    return con


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def upsert(con, host, *, name="", country="", language="", source="", detail="",
           prominence=None) -> bool:
    """Merge one observation into the index; True when the row is new.

    Evidence accumulates as a set of sources (corroboration is a ranking feature, so a source is
    recorded once however often it re-offers the host). Name/geo fill blanks and never overwrite —
    the first source to KNOW something wins over a later one guessing differently, and a
    disagreement is visible in the evidence list rather than silently resolved."""
    host = canonical_host(host)
    if not host or "." not in host:
        return False
    row = con.execute("SELECT * FROM hosts WHERE host=?", (host,)).fetchone()
    ev = json.loads(row["evidence"]) if row else []
    if source and not any(e.get("source") == source and e.get("detail") == detail for e in ev):
        ev.append({"source": source, **({"detail": detail} if detail else {})})
    if row is None:
        con.execute(
            "INSERT INTO hosts(host, domain, name, country, language, evidence, prominence,"
            " first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
            (host, registrable_domain(host), name or host, (country or "").upper(),
             (language or "").lower(), json.dumps(ev), float(prominence or 0.0), _now(), _now()))
        con.execute("INSERT INTO hosts_fts(name, aka, host) VALUES(?,?,?)",
                    (name or host, host, host))
        return True
    con.execute(
        "UPDATE hosts SET name=CASE WHEN name='' OR name=host THEN ? ELSE name END,"
        " country=CASE WHEN country='' THEN ? ELSE country END,"
        " language=CASE WHEN language='' THEN ? ELSE language END,"
        " evidence=?, prominence=MAX(prominence, ?), last_seen=? WHERE host=?",
        (name or row["name"], (country or "").upper(), (language or "").lower(),
         json.dumps(ev), float(prominence or 0.0), _now(), host))
    if name and (row["name"] == row["host"] or not row["name"]):
        con.execute("DELETE FROM hosts_fts WHERE host=?", (host,))
        con.execute("INSERT INTO hosts_fts(name, aka, host) VALUES(?,?,?)", (name, host, host))
    return False


# --------------------------------------------------------------------------- ingesters
def ingest_exhaust(con, st, reg=None) -> dict:
    """The rows this deployment already knows: the admission table (every state — a rejected host
    is still a real outlet somebody may search for; its probe verdict is carried as evidence) and
    the outlet registry (curated names + countries). Offline: no request leaves this process."""
    import outlet_registry
    reg = reg or outlet_registry.default_registry()
    added = seen = 0
    for row in st.admission_rows(states=None, limit=0):
        seen += 1
        added += upsert(con, row["host"], name=(row.get("publisher") or ""),
                        language=row.get("language") or "",
                        source="exhaust", detail=f"admission:{row['state']}",
                        prominence=min(3.0, (row.get("articles") or 0) / 100.0))
    for o in reg.outlets():
        for dom in reg.domains(o.canonical) or []:
            seen += 1
            host = canonical_host(dom)
            if host:
                added += upsert(con, host, name=o.canonical or "",
                                country=(o.country or ""), source="registry")
    con.commit()
    return {"seen": seen, "added": added}


#: One SPARQL query per page, instance-of any of: newspaper, news website, broadcaster,
#: news agency, magazine — with an official website, and country / language where stated.
_WIKIDATA_SPARQL = """
SELECT ?site ?name ?cc ?lang WHERE {
  VALUES ?type { wd:Q11032 wd:Q1153191 wd:Q15265344 wd:Q192283 wd:Q41298 }
  ?outlet wdt:P31 ?type ; wdt:P856 ?site .
  OPTIONAL { ?outlet wdt:P17 ?c . ?c wdt:P297 ?cc . }
  OPTIONAL { ?outlet wdt:P407 ?l . ?l wdt:P218 ?lang . }
  OPTIONAL { ?outlet rdfs:label ?name FILTER(LANG(?name) = "en") }
}
LIMIT %(limit)d OFFSET %(offset)d
"""


def _default_fetch_json(url: str, *, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ingest_wikidata(con, *, fetch_json=None, page: int = 5000, max_pages: int = 40) -> dict:
    """Wikidata news organisations (CC0). Paged so a 200k-binding result cannot OOM the box."""
    fetch = fetch_json or _default_fetch_json
    added = seen = 0
    for i in range(max_pages):
        q = _WIKIDATA_SPARQL % {"limit": page, "offset": i * page}
        url = ("https://query.wikidata.org/sparql?format=json&query="
               + urllib.parse.quote(q))
        rows = (fetch(url).get("results") or {}).get("bindings") or []
        for b in rows:
            seen += 1
            added += upsert(
                con, (b.get("site") or {}).get("value", ""),
                name=(b.get("name") or {}).get("value", ""),
                country=(b.get("cc") or {}).get("value", ""),
                language=(b.get("lang") or {}).get("value", ""),
                source="wikidata")
        con.commit()
        if len(rows) < page:
            break
        time.sleep(1.0)                      # politeness toward a free public endpoint
    return {"seen": seen, "added": added}


def ingest_wikipedia_lists(con, countries, *, fetch_json=None) -> dict:
    """The external links of "List of newspapers in {Country}" pages, country-stamped.

    The same registers the directory channel imports from pasted text — fetched by title. Links to
    platforms/encyclopaedias are dropped through `source_web.is_non_outlet`, the same gate the
    search channel applies, so a citation to Facebook never becomes an outlet row."""
    import location
    import source_web
    fetch = fetch_json or _default_fetch_json
    added = seen = pages = 0
    for cc in countries:
        name = location.country_name(cc)
        if not name:
            continue
        title = urllib.parse.quote(f"List_of_newspapers_in_{name.replace(' ', '_')}")
        url = (f"https://en.wikipedia.org/w/api.php?action=parse&page={title}"
               f"&format=json&prop=externallinks&redirects=1")
        try:
            links = ((fetch(url).get("parse") or {}).get("externallinks")) or []
        except Exception:
            continue                          # a country without the list page is not an error
        pages += 1
        for link in links:
            seen += 1
            host = canonical_host(link)
            if host and not source_web.is_non_outlet(host):
                added += upsert(con, host, country=cc, source="wikipedia",
                                detail=f"list:{cc}")
        con.commit()
        time.sleep(0.5)
    return {"pages": pages, "seen": seen, "added": added}


def ingest_cc_domains(con, path, *, add_missing: bool = False) -> dict:
    """Common Crawl domain-rank lines ("<rank> <reversed-or-plain domain>") as a prominence prior.

    Updates existing rows by default; ``add_missing`` also inserts unknown domains (Phase 3's
    breadth lever, behind a flag because rank alone says popular, not news)."""
    import math
    updated = added = seen = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            seen += 1
            rank, dom = parts[0], parts[1]
            if "." not in dom and "," in dom:     # CC web graph reverses labels: com,example
                dom = ".".join(reversed(dom.split(",")))
            try:
                prom = max(0.0, 9.0 - math.log10(max(1.0, float(rank))))
            except ValueError:
                continue
            host = canonical_host(dom)
            row = con.execute("SELECT host FROM hosts WHERE host=? OR domain=?",
                              (host, registrable_domain(host))).fetchone()
            if row is not None:
                con.execute("UPDATE hosts SET prominence=MAX(prominence,?), last_seen=? "
                            "WHERE host=?", (prom, _now(), row["host"]))
                updated += 1
            elif add_missing:
                added += upsert(con, host, source="cc", prominence=prom)
    con.commit()
    return {"seen": seen, "updated": updated, "added": added}


# --------------------------------------------------------------------------- feedback (Phase 4)
def feedback_weights(st) -> dict:
    """Probe outcomes per evidence source: {source: weight in [-1, 1]}.

    The measured version of "which evidence is worth believing": a source whose hosts validate
    gains, one whose hosts get rejected loses. Neutral (0) below three outcomes — two probes are
    an anecdote, and the ranker must not whipsaw on them."""
    outcomes: dict = {}
    try:
        rows = st.admission_rows(states=["validated", "admitted", "rejected"], limit=0)
    except Exception:
        return {}
    con = open_index()
    try:
        for r in rows:
            idx = con.execute("SELECT evidence FROM hosts WHERE host=?",
                              (canonical_host(r["host"]),)).fetchone()
            if idx is None:
                continue
            good = r["state"] in ("validated", "admitted")
            for e in json.loads(idx["evidence"]):
                s = e.get("source")
                if s:
                    won, total = outcomes.get(s, (0, 0))
                    outcomes[s] = (won + (1 if good else 0), total + 1)
    finally:
        con.close()
    return {s: round((2.0 * won / total) - 1.0, 3)
            for s, (won, total) in outcomes.items() if total >= 3}


# --------------------------------------------------------------------------- query + ranking
_TEMPLATE_PATTERNS = (
    re.compile(r"^local news websites in (?P<country>.+)$", re.I),
    re.compile(r"^(?P<country>.+?) newspapers online$", re.I),
    re.compile(r"^(?P<language>\S+) language news site (?P<country>.+)$", re.I),
    re.compile(r"^regional news outlets (?P<country>.+)$", re.I),
)


def plan_query(q: str) -> dict:
    """Free text -> {country?, language?, text?}. The gap templates round-trip EXACTLY because
    `source_web.queries` generates them from four fixed phrasings [that module's own data] — so
    the planner is a parser, not a guesser. Anything else stays free text for FTS."""
    import location
    q = " ".join((q or "").split())
    for pat in _TEMPLATE_PATTERNS:
        m = pat.match(q)
        if not m:
            continue
        g = m.groupdict()
        cc = location.normalize_country(g.get("country") or "")
        lang = location.normalize_language(g.get("language") or "") if g.get("language") else None
        if cc:
            return {"country": cc, **({"language": lang} if lang else {})}
    return {"text": q}


def query_index(con, plan: dict, *, count: int = 10, feedback: "dict | None" = None,
                registry=None) -> list:
    """Ranked candidate rows for one planned query.

    score = 3·geo match + 2·corroboration(capped 3) + prominence + feedback − 2·already-tracked.
    Tracked outlets are PENALISED, never hidden: the API stays a generic search surface, and the
    discovery pipeline applies its own tracked gate downstream exactly as it does for SerpAPI."""
    import outlet_registry
    registry = registry or outlet_registry.default_registry()
    feedback = feedback or {}
    if "country" in plan:
        sql = "SELECT * FROM hosts WHERE country=?"
        args: list = [plan["country"]]
        if plan.get("language"):
            sql += " AND (language=? OR language='')"
            args.append(plan["language"])
        rows = con.execute(sql, args).fetchall()
        geo = {r["host"]: (3.0 if plan.get("language") and r["language"] == plan["language"]
                           else 2.0) for r in rows}
    else:
        hits = con.execute(
            "SELECT host, bm25(hosts_fts) AS b FROM hosts_fts WHERE hosts_fts MATCH ? "
            "ORDER BY b LIMIT 400",
            (" OR ".join(re.findall(r"\w+", plan.get("text", ""))) or '""',)).fetchall()
        if not hits:
            return []
        by_host = {h["host"]: -float(h["b"]) for h in hits}
        marks = ",".join("?" for _ in by_host)
        rows = con.execute(f"SELECT * FROM hosts WHERE host IN ({marks})",
                           list(by_host)).fetchall()
        top = max(by_host.values()) or 1.0
        geo = {h: 3.0 * (s / top) for h, s in by_host.items()}
    scored = []
    for r in rows:
        ev = json.loads(r["evidence"])
        sources = {e.get("source") for e in ev if e.get("source")}
        score = (geo.get(r["host"], 0.0)
                 + 2.0 * min(len(sources), 3)
                 + float(r["prominence"])
                 + sum(feedback.get(s, 0.0) for s in sources))
        tracked = registry.resolve(r["host"]) is not None
        if tracked:
            score -= 2.0
        scored.append({"host": r["host"], "domain": r["domain"], "name": r["name"],
                       "country": r["country"], "language": r["language"],
                       "evidence": sorted(sources), "tracked": tracked,
                       "score": round(score, 3)})
    scored.sort(key=lambda x: (-x["score"], x["host"]))
    out, seen_domains = [], set()
    for row in scored:
        if row["domain"] in seen_domains:
            continue                          # one result per registrable domain
        seen_domains.add(row["domain"])
        out.append(row)
        if len(out) >= count:
            break
    return out


# --------------------------------------------------------------------------- Phase 0 measurement
def measure(con, st, *, floor: int = 5) -> dict:
    """The pre-registered coverage bar: for what share of corpus gaps does the index offer at
    least MEASURE_MIN_CANDIDATES untracked candidates? PASS/FAIL is printed AND returned, so the
    cron, a test, and an operator all read the same verdict."""
    import source_web
    gap_list = source_web.gaps(source_web.corpus_gap_counts(st), floor=floor)
    covered = 0
    detail = []
    for gap in gap_list:
        plan = {"country": gap["country"]}
        cands = [c for c in query_index(con, plan, count=MEASURE_MIN_CANDIDATES * 2)
                 if not c["tracked"]]
        ok = len(cands) >= MEASURE_MIN_CANDIDATES
        covered += ok
        detail.append({"gap": gap, "candidates": len(cands), "ok": ok})
    share = covered / len(gap_list) if gap_list else 0.0
    return {"gaps": len(gap_list), "covered": covered, "share": round(share, 3),
            "bar": MEASURE_MIN_GAP_SHARE, "pass": share >= MEASURE_MIN_GAP_SHARE,
            "detail": detail}


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", default=None, help="index db path (default: RWE_OUTLET_INDEX_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="ingest sources into the index")
    b.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    b.add_argument("--exhaust", action="store_true", help="admission table + registry (offline)")
    b.add_argument("--wikidata", action="store_true", help="Wikidata news orgs (network)")
    b.add_argument("--wikipedia", action="store_true",
                   help="'List of newspapers in X' pages for --countries or the current gaps")
    b.add_argument("--countries", default="", help="comma ISO codes for --wikipedia")
    b.add_argument("--cc", default="", help="Common Crawl domain-rank file (prominence prior)")
    b.add_argument("--cc-add-missing", action="store_true")
    q = sub.add_parser("query", help="rank candidates for a query")
    q.add_argument("text", nargs="?", default="")
    q.add_argument("--country", default="")
    q.add_argument("--language", default="")
    q.add_argument("--count", type=int, default=10)
    q.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    sub.add_parser("stats", help="row counts by source/country")
    m = sub.add_parser("measure", help="Phase 0 bar: index coverage of the corpus gaps")
    m.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    m.add_argument("--floor", type=int, default=5)
    args = ap.parse_args(argv)
    con = open_index(args.index)

    if args.cmd == "build":
        import store as store_mod
        if args.exhaust:
            st = store_mod.Store(args.db) if args.db else None
            if st is None:
                print("--exhaust needs --db")
                return 2
            print("exhaust  :", ingest_exhaust(con, st))
        if args.wikidata:
            print("wikidata :", ingest_wikidata(con))
        if args.wikipedia:
            countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
            if not countries and args.db:
                import source_web
                st = store_mod.Store(args.db)
                countries = [g["country"] for g in
                             source_web.gaps(source_web.corpus_gap_counts(st)) if g["country"]]
            print("wikipedia:", ingest_wikipedia_lists(con, countries))
        if args.cc:
            print("cc       :", ingest_cc_domains(con, args.cc, add_missing=args.cc_add_missing))
        return 0

    if args.cmd == "query":
        plan = ({"country": args.country.upper(),
                 **({"language": args.language.lower()} if args.language else {})}
                if args.country else plan_query(args.text))
        fb = {}
        if args.db:
            import store as store_mod
            fb = feedback_weights(store_mod.Store(args.db))
        for row in query_index(con, plan, count=args.count, feedback=fb):
            mark = "·tracked" if row["tracked"] else ""
            print(f"  {row['score']:>6.2f}  {row['host']:<32} {row['country'] or '--'} "
                  f"{row['language'] or '--'}  [{','.join(row['evidence'])}]{mark}  {row['name'][:40]}")
        return 0

    if args.cmd == "stats":
        total = con.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"]
        print(f"hosts: {total:,}")
        for r in con.execute("SELECT country, COUNT(*) c FROM hosts GROUP BY country "
                             "ORDER BY c DESC LIMIT 15"):
            print(f"  {r['country'] or '--'}  {r['c']:,}")
        return 0

    if args.cmd == "measure":
        import store as store_mod
        st = store_mod.Store(args.db)
        rep = measure(con, st, floor=args.floor)
        print(f"gaps {rep['gaps']}  covered {rep['covered']}  share {rep['share']:.0%}  "
              f"bar {rep['bar']:.0%}  ->  {'PASS' if rep['pass'] else 'FAIL'}")
        for d in rep["detail"][:20]:
            print(f"  {'ok ' if d['ok'] else 'THIN'}  {d['gap']['country']:<3} "
                  f"{d['gap']['language'] or '--':<3} outlets={d['gap']['outlets']} "
                  f"candidates={d['candidates']}")
        return 0 if rep["pass"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
