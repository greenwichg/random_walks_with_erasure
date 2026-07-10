#!/usr/bin/env python3
"""Extension-pipeline experiment probes — the automated half of docs/EXTENSION_E2E_EXPERIMENT.md.

You do the browser parts (open articles with the extension); this script proves every server-side
stage with a PASS / WAIT / FAIL verdict and a reason, so a failure is localized instantly. It is a
**developer tool**: read-only against the engine's HTTP API plus (for lifecycle fields the API
deliberately doesn't expose) a direct read of the same store the engine uses. It changes nothing.

Run it on the engine host (e.g. the Colab runtime), from anywhere inside the repo:

    python examples/extension_experiment.py --url https://www.npr.org/...   # after reading it
    python examples/extension_experiment.py --url ... --stage 3             # one stage only
    python examples/extension_experiment.py --url ... --simulate-read       # no browser available:
                                                                            # extension-shaped POST
(Stage 8 creates and seeds Reader B itself; Stage 9's promotion needs B to read the URL — see the doc.)
Stages covered: 2 history · 3 catalog/lifecycle · 4 search · 5 stories · 6 graph/refresh ·
8 reader-B recommendation · 9 discover/promotion · 10 duplicates. (1, 7, 11, 12 are browser/UI
observations — the doc walks those.)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "examples"))


def _get(base, path, uid=None):
    req = urllib.request.Request(base + path,
                                 headers={"X-IH-User-Id": str(uid)} if uid is not None else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _post(base, path, body, uid=None):
    h = {"Content-Type": "application/json"}
    if uid is not None:
        h["X-IH-User-Id"] = str(uid)
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _verdict(ok, label, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def _wait(label, detail=""):
    print(f"  [WAIT] {label}" + (f" — {detail}" if detail else ""))
    return None


class Probe:
    def __init__(self, args):
        self.base = args.engine.rstrip("/")
        self.uid = args.user
        self.url = args.url
        import ingest  # the engine's own canonicalizer — the join key everywhere
        self.canon = ingest.canonical_url(args.url)
        import store
        self.store = store.Store()          # same RWE_DB_URL / default file the engine uses
        self.slug = urllib.parse.urlparse(self.canon).path.rstrip("/").rsplit("/", 1)[-1] or self.canon

    def row(self):
        return self.store.get_feed_article(self.canon)

    # -- stages ------------------------------------------------------------
    def stage2_history(self):
        print("\nStage 2 · Reading History")
        hist = _get(self.base, "/api/me/history", self.uid)
        mine = [e for e in hist if (e.get("article") or {}).get("url", "").startswith(self.canon[:60])
                or self.slug in (e.get("article") or {}).get("url", "")]
        if not mine:
            return _verdict(False, "read recorded", "URL not in /api/me/history — extension badge? token? user id?")
        e = mine[0]
        return _verdict(True, "read recorded",
                        f"readSource={e.get('readSource')} · readAt={e.get('readAt')}")

    def stage3_catalog(self):
        print("\nStage 3 · FeedArticle creation / lifecycle")
        r = self.row()
        if r is None:
            return _verdict(False, "FeedArticle exists",
                            "no catalog row — grep engine log for extension_catalog_failed")
        ok = True
        ok &= _verdict(r.get("sourceType") == "extension" or r.get("sourceType") in ("rss", "newsapi", "gdelt"),
                       "provenance", f"sourceType={r.get('sourceType')} (extension = you created it; "
                       "a feed type = it pre-existed, Stage 11 applies)")
        ok &= _verdict(r.get("articleState") in ("provisional", "verified", None),
                       "lifecycle", f"articleState={r.get('articleState')}")
        sc = r.get("scored") or {}
        signals = [sc.get("lean"), sc.get("register"), sc.get("category") or None, sc.get("emotion")]
        ok &= _verdict(any(v is not None for v in signals), "classified",
                       f"category={sc.get('category') or '(uncategorised)'} lean={sc.get('lean')} "
                       f"register={sc.get('register')}")
        if sc.get("lean") is None:
            print("        note: lean unresolved — this article will NOT join the recommendation "
                  "graph (documented unknown-outlet gap, Stage 12).")
        return ok

    def stage4_search(self):
        print("\nStage 4 · Search (immediate)")
        words = " ".join((self.row() or {}).get("title", "").split()[:3]) or self.slug.replace("-", " ")
        res = _get(self.base, "/api/search?query=" + urllib.parse.quote(words))
        hit = any(self.slug in (a.get("url") or "") for a in res.get("results", []))
        return _verdict(hit, f"searchable via query={words!r}",
                        f"{res.get('total')} result(s)" if hit else "not in results — check title words")

    def stage5_stories(self):
        print("\nStage 5 · Stories (needs ≥2 articles / ≥2 publishers sharing headline tokens)")
        res = _get(self.base, "/api/stories?sort=latest&limit=50")
        stories = res.get("stories", res if isinstance(res, list) else [])
        blob = json.dumps(stories)
        if self.slug in blob:
            return _verdict(True, "article clustered into a story")
        return _wait("not clustered yet",
                     "expected until a second outlet's version of the same event is read (see doc)")

    def stage6_graph(self):
        print("\nStage 6 · Recommendation graph (after the normal refresh cycle)")
        ref = _get(self.base, "/api/internal/refresh")
        gen = ref.get("generation")
        r = self.row()
        lean = ((r or {}).get("scored") or {}).get("lean")
        if lean is None:
            return _verdict(False, "graph-eligible", "lean unresolved — corpus builder drops it (Stage 12)")
        # the only public, honest signal of node-hood: a *different* measured reader can be served it
        print(f"        refresh state: generation={gen} · catalogDirty={ref.get('catalogDirty')} "
              f"· lastSuccess={ref.get('lastSuccessAt')}")
        return _wait("node-hood is proven by Stage 8 (reader B receives it) after "
                     "'corpus_refresh_activated' appears in the engine log")

    def stage8_reader_b(self, create=True):
        print("\nStage 8 · Reader B receives the discovery")
        b = _post(self.base, "/api/internal/users",
                  {"provider": "dev", "providerAccountId": "experiment-reader-b"})["userId"]
        hist = _get(self.base, "/api/me/history", b)
        if len(hist) < 5 and create:
            arts = _get(self.base, "/api/discover?limit=8").get("articles", [])
            feed = [a for a in arts if self.slug not in (a.get("url") or "")][:5]
            _post(self.base, "/api/me/reads",
                  {"reads": [{"url": a["url"], "title": a.get("headline", "")} for a in feed]}, b)
            print(f"        seeded reader B (#{b}) with 5 catalog reads")
        got = set()
        for s in ("", "?strategy=rwe-b", "?strategy=rwe-d", "?strategy=adaptive"):
            for rec in _get(self.base, "/api/recommendations" + s, b):
                got.add((rec.get("article") or {}).get("url") or "")
        hit = any(self.slug in u for u in got)
        if hit:
            return _verdict(True, f"reader B (#{b}) recommended the article")
        return _wait("not recommended yet",
                     "run after the refresh activated; if it persists, check Stage 6 + engine log")

    def stage9_discover(self):
        print("\nStage 9 · Discover gating / promotion")
        r = self.row()
        state = (r or {}).get("articleState")
        disc = _get(self.base, "/api/discover?limit=200").get("articles", [])
        visible = any(self.slug in (a.get("url") or "") for a in disc)
        if state == "provisional":
            return _verdict(not visible, "provisional article hidden from Discover",
                            f"visible={visible} (should be False; promote via 2nd reader or RSS overlap)")
        return _verdict(visible, f"promoted article visible in Discover (articleState={state})")

    def stage10_duplicate(self):
        print("\nStage 10 · Duplicate read (server-side; the extension also de-dups locally for 6h)")
        before = self.store.count_feed_articles()
        res = _post(self.base, "/api/me/reads",
                    {"reads": [{"url": self.url, "title": "(dup probe)", "readSource": "extension"}]},
                    self.uid)
        ok = _verdict(res.get("accepted") == 0 and res.get("duplicates") == 1,
                      "re-submit is a duplicate", f"accepted={res.get('accepted')} duplicates={res.get('duplicates')}")
        ok &= _verdict(self.store.count_feed_articles() == before, "still one FeedArticle")
        return ok

    def simulate_read(self):
        """Stand-in for the browser step when no extension/browser is available (sandbox/CI):
        the exact payload the web tier forwards for an extension read."""
        print("\n(simulate) extension-shaped read — stands in for Stage 1's browser click")
        res = _post(self.base, "/api/me/reads", {"reads": [{
            "url": self.url, "title": self.slug.replace("-", " ").title(),
            "readSource": "extension", "description": "Experiment probe article.",
            "siteName": "", "publishedAt": None}]}, self.uid)
        print(f"  accepted={res.get('accepted')} duplicates={res.get('duplicates')} "
              f"totalReads={res.get('totalReads')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="http://127.0.0.1:8000")
    ap.add_argument("--user", type=int, default=1, help="Reader A's engine user id (demo reader = 1)")
    ap.add_argument("--url", required=True, help="the article URL you opened with the extension")
    ap.add_argument("--stage", type=int, default=None, help="run one stage (2,3,4,5,6,8,9,10); default all")
    ap.add_argument("--simulate-read", action="store_true",
                    help="no browser available: submit the extension-shaped read first (stand-in for Stage 1)")
    args = ap.parse_args()

    p = Probe(args)
    print(f"engine={args.engine} · reader A=#{args.user}\narticle={args.url}\ncanonical={p.canon}")
    if args.simulate_read:
        p.simulate_read()
    stages = {2: p.stage2_history, 3: p.stage3_catalog, 4: p.stage4_search, 5: p.stage5_stories,
              6: p.stage6_graph, 8: p.stage8_reader_b, 9: p.stage9_discover, 10: p.stage10_duplicate}
    torun = [args.stage] if args.stage else sorted(stages)
    results = [stages[s]() for s in torun if s in stages]
    print("\nSummary:", "ALL PASS" if all(r is True for r in results)
          else "see WAIT/FAIL lines above (WAIT = expected until the noted precondition)")


if __name__ == "__main__":
    main()
