#!/usr/bin/env python3
"""Local end-to-end test for the Stories Country filter (Location Intelligence Phase 2).

Seeds a throwaway SQLite catalog with articles whose PUBLISHER home and EVENT location
deliberately disagree, then asserts the whole chain end to end — so you can see the feature
work on your own machine before it goes anywhere near production. No GDELT network access is
required: event geography is seeded through the same resolver + side table the GKG enricher
writes to, so what is exercised below is the real code path, not a mock.

    # 1) seed + assert the engine chain (offline, ~2 s)
    python deploy/ops/local-location-test.py

    # 2) leave it running to click through the UI
    python deploy/ops/local-location-test.py --serve
    #    then, in two more terminals:
    #      RWE_DB_URL=sqlite:///$(pwd)/.local-location-test.db python examples/api_fastapi.py
    #      cd web && RWE_BACKEND_URL=http://127.0.0.1:8000 NEXTAUTH_SECRET=dev \\
    #        NEXTAUTH_URL=http://127.0.0.1:3000 RWE_DEV_LOGIN=1 npm run dev

The fixture is two stories that make the semantics unmistakable:

    "Senate passes the funding bill"  — reported by NPR (US), Fox (US), BBC (GB) — EVENT: US
    "Bushfires spread across NSW"     — reported by The Guardian (GB), CNN (US)  — EVENT: AU

So ``?country=US`` must return the Senate story (even though a GB outlet covers it),
``?country=AU`` the bushfire story (even though a US outlet covers it), and ``?country=GB``
NOTHING — GB is only a publisher home, and publisher homes are provenance, never a content
filter. Unfiltered ("All") always returns both.
"""
import argparse
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import location        # noqa: E402
import store as store_mod  # noqa: E402

DB_PATH = ROOT / ".local-location-test.db"

#: (url, publisher, lean, title, publisher_home, event_country)
FIXTURE = [
    ("https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after long debate", "US", "United States"),
    ("https://foxnews.com/a2", "Fox News", 1.5, "Senate passes funding bill averting a shutdown", "US", "United States"),
    ("https://bbc.co.uk/a3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown", "GB", "United States"),
    ("https://theguardian.com/b1", "The Guardian", -1.2, "Bushfires spread across New South Wales", "GB", "Australia"),
    ("https://cnn.com/b2", "CNN", -0.8, "Bushfires spread rapidly across New South Wales", "US", "Australia"),
]


def seed() -> store_mod.Store:
    DB_PATH.unlink(missing_ok=True)
    st = store_mod.Store(f"sqlite:///{DB_PATH}")
    for i, (url, pub, lean, title, home, event) in enumerate(FIXTURE):
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=pub, source_publisher=pub, title=title,
            description="context", body=None, published_at=f"2026-07-2{4 + i % 2}T10:00:00Z",
            source_feed="feed://local-location-test", country=home,
            scored={"article_id": url, "outlet": pub, "category": "Politics",
                    "lean": lean, "title": title})
        # The same call the GKG enricher makes — provider name in, canonical ISO row out.
        st.replace_article_event_locations(
            url, location.resolve_event_locations([{"country": event, "source": "gdelt-gkg"}]))
    return st


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f", want {want!r}"))
    return ok


def offline_checks(st) -> bool:
    print("\nStore layer (no engine needed)")
    ok = check("catalog size", st.count_feed_articles(), 5)
    facets = {f["country"]: f["articles"] for f in st.feed_article_country_facets()}
    ok &= check("country facets count the EVENT dimension", facets, {"US": 3, "AU": 2})
    ok &= check("GB (publisher home only) is absent from facets", "GB" in facets, False)
    ok &= check("BBC (GB publisher) is located US by its event",
                st.event_countries_for_urls(["https://bbc.co.uk/a3"]),
                {"https://bbc.co.uk/a3": ["US"]})
    ok &= check("search ?country=US matches by event, not publisher",
                st.search_feed_articles(country="US")[1], 3)
    ok &= check("search ?country=GB matches nothing (provenance never filters)",
                st.search_feed_articles(country="GB")[1], 0)

    sys.path.insert(0, str(ROOT / "examples"))
    import story_service                                    # noqa: E402
    print("\nStory layer")
    ok &= check("?country=US -> the Senate story", story_service.list_stories(st, country="US")["total"], 1)
    ok &= check("?country=AU -> the bushfire story", story_service.list_stories(st, country="AU")["total"], 1)
    ok &= check("?country=GB -> nothing", story_service.list_stories(st, country="GB")["total"], 0)
    ok &= check("no filter ('All') -> the whole feed", story_service.list_stories(st)["total"], 2)
    senate = next(s for s in story_service.cluster_from_store(st) if "Senate" in s["title"])
    ok &= check("story consensus event country", senate["primaryCountry"], "US")
    ok &= check("publisher homes preserved separately", senate["publisherCountries"], ["GB", "US"])
    return bool(ok)


def live_checks(base: str) -> bool:
    def get(path):
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return json.loads(r.read())
    print(f"\nLive engine at {base}")
    try:
        get("/api/health")
    except Exception as e:                      # not reachable: say so, don't dump a traceback
        print(f"  SKIP  engine not reachable ({e.__class__.__name__}). Start it with:")
        print(f"          RWE_DB_URL=sqlite:///{DB_PATH} python examples/api_fastapi.py")
        return False
    ok = check("/api/stories?country=US", get("/api/stories?country=US")["total"], 1)
    ok &= check("/api/stories?country=AU", get("/api/stories?country=AU")["total"], 1)
    ok &= check("/api/stories?country=GB", get("/api/stories?country=GB")["total"], 0)
    ok &= check("/api/stories (All)", get("/api/stories")["total"], 2)
    opts = [c["country"] for c in get("/api/places/countries") if c["articles"] > 0]
    ok &= check("country dropdown options (articles > 0)", opts, ["US", "AU"])
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true",
                    help="seed and keep the DB (prints the commands to run engine + web)")
    ap.add_argument("--live", metavar="BASE",
                    help="also assert against a running engine, e.g. http://127.0.0.1:8000")
    args = ap.parse_args()

    st = seed()
    print(f"seeded {DB_PATH}")
    ok = offline_checks(st)
    if args.live:
        ok = live_checks(args.live) and ok

    if args.serve:
        print(f"""
Run these in two terminals, then open http://127.0.0.1:3000/stories :

  RWE_DB_URL=sqlite:///{DB_PATH} python examples/api_fastapi.py

  cd web && RWE_BACKEND_URL=http://127.0.0.1:8000 NEXTAUTH_SECRET=dev \\
    NEXTAUTH_URL=http://127.0.0.1:3000 RWE_DEV_LOGIN=1 npm run dev

The Country dropdown offers United States and Australia. Pick United States: the Senate story
(covered by BBC, a GB outlet). Pick Australia: the bushfire story (covered by CNN, a US outlet).
Neither appears under a publisher's home country — that is the whole point.

Re-assert against the running engine with:
  python deploy/ops/local-location-test.py --live http://127.0.0.1:8000""")
    else:
        DB_PATH.unlink(missing_ok=True)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
