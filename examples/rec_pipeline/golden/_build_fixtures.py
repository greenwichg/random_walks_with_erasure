"""Author the 9 scenario golden fixtures for the Recommendation Validation Pipeline (21d Phase 1).
Each scenario maps to an explanation type so a failure names the type, not a persona."""
import json
import pathlib

GD = pathlib.Path(__file__).resolve().parent
GD.mkdir(parents=True, exist_ok=True)

EMO = {"fear": 0.1, "outrage": 0.1, "analysis": 0.5, "positive": 0.1, "neutral": 0.2}


def base(n_per=4):
    outlets = [("The Guardian", -1.5, "Politics"), ("NPR", -1.0, "Health"),
               ("Reuters", 0.0, "Business"), ("Associated Press", 0.0, "World"),
               ("Fox News", 1.6, "Politics"), ("The Wall Street Journal", 0.9, "Business")]
    arts = []
    for pub, lean, topic in outlets:
        dom = pub.lower().replace(" ", "")
        for k in range(n_per):
            arts.append({"url": f"https://{dom}.example.com/base/{topic.lower()}-{k}",
                         "publisher": pub, "title": f"{pub} on {topic.lower()}, dispatch {k}",
                         "publishedAt": "2026-07-01T00:00:00+00:00",
                         "scored": {"outlet": pub, "category": topic, "lean": lean,
                                    "political": topic == "Politics",
                                    "title": f"{pub} on {topic.lower()} {k}", "register": 0.8,
                                    "emotion": EMO}})
    return arts


def story(slug, tokens, publishers, topic="Technology", lean_by=None, when=None):
    lean_by, when = lean_by or {}, when or {}
    out = []
    for pub in publishers:
        dom = pub.lower().replace(" ", "")
        out.append({"url": f"https://{dom}.example.com/story/{slug}", "publisher": pub,
                    "title": tokens.capitalize(),
                    "publishedAt": when.get(pub, "2026-07-09T09:00:00+00:00"),
                    "scored": {"outlet": pub, "category": topic, "lean": lean_by.get(pub, 0.0),
                               "political": topic == "Politics", "title": tokens.capitalize(),
                               "register": 0.8, "emotion": EMO}})
    return out


def novel(url, publisher, topic, lean, title):
    return {"url": url, "publisher": publisher, "title": title,
            "publishedAt": "2026-07-05T00:00:00+00:00",
            "scored": {"outlet": publisher, "category": topic, "lean": lean,
                       "political": topic == "Politics", "title": title,
                       "register": 0.8, "emotion": EMO}}


def reader(reads, uid="reader-under-test", under_test=True):
    return {"id": uid, "underTest": under_test,
            "reads": [{"url": u, "publisher": p} for (u, p) in reads]}


# a stable 5-read baseline history from the base catalog (measured threshold = 5)
BASE_READS = [("https://npr.example.com/base/health-0", "NPR"),
              ("https://reuters.example.com/base/business-0", "Reuters"),
              ("https://theguardian.example.com/base/politics-0", "The Guardian"),
              ("https://associatedpress.example.com/base/world-0", "Associated Press"),
              ("https://thewallstreetjournal.example.com/base/business-1", "The Wall Street Journal")]

FIX = {}

# 1 · same_story — read Fox's article; the CNN sibling is story_match/same_event
FIX["same_story"] = {
    "description": "Reader read the Fox News article of a two-publisher story; the CNN sibling must "
                   "resolve to story_match/same_event (same cluster, different publisher).",
    "expected": {"targetType": "story_match", "targetVariant": "same_event"},
    "catalog": base() + story("fusion", "fusion milestone reached", ["Fox News", "CNN"],
                              lean_by={"Fox News": 1.4, "CNN": -0.8}),
    "readers": [reader([("https://foxnews.example.com/story/fusion", "Fox News")] + BASE_READS[:4])],
    "target": "https://cnn.example.com/story/fusion"}

# 2 · same_publisher — read Fox's article; the OTHER Fox sibling must NOT be story_match
FIX["same_publisher"] = {
    "description": "Reader read one Fox News article of a story; a SECOND Fox News article of the "
                   "same story must NOT say 'here's how Fox covered it' (publisher not different).",
    "expected": {"targetTypeNot": "story_match"},
    "catalog": base() + story("verdict", "supreme court verdict landmark", ["Fox News"],
                              topic="Politics", lean_by={"Fox News": 1.5})
               + [novel("https://foxnews.example.com/story/verdict-2", "Fox News", "Politics", 1.5,
                        "Supreme court verdict landmark")],
    "readers": [reader([("https://foxnews.example.com/story/verdict", "Fox News")] + BASE_READS[:4])],
    "target": "https://foxnews.example.com/story/verdict-2"}

# 3 · follow_up_story — read the earlier Fox article; CNN's much-later article is a follow-up
FIX["follow_up_story"] = {
    "description": "Reader read the early Fox News coverage; CNN's article published two days later "
                   "must resolve to story_match/follow_up ('latest update').",
    "expected": {"targetType": "story_match", "targetVariant": "follow_up"},
    "catalog": base() + story("probe", "mars probe touchdown confirmed", ["Fox News", "CNN"],
                              when={"Fox News": "2026-07-07T09:00:00+00:00",
                                    "CNN": "2026-07-09T18:00:00+00:00"}),
    "readers": [reader([("https://foxnews.example.com/story/probe", "Fox News")] + BASE_READS[:4])],
    "target": "https://cnn.example.com/story/probe"}

# 4 · new_publisher — a novel outlet the reader has never read → new_publisher
FIX["new_publisher"] = {
    "description": "An article from Bloomberg, an outlet absent from the reader's history, must "
                   "resolve to new_publisher ('you've never read Bloomberg before').",
    "expected": {"targetType": "new_publisher"},
    "catalog": base() + [novel("https://bloomberg.example.com/tech/chips", "Bloomberg",
                               "Technology", 0.2, "A new chip reshapes the data center")],
    "readers": [reader(BASE_READS)],
    "target": "https://bloomberg.example.com/tech/chips"}

# 5 · bridge — a right POLITICAL article to a left-leaning reader, familiar outlet → bridge.
# (Commit R1: the cross-cutting gate requires article-level political classification, so the
# target is authored as the politics article the scenario always described — the earlier
# "World" topic predates the gate and contradicted the fixture's own description.)
FIX["bridge"] = {
    "description": "A right-leaning Fox News politics article to a left-leaning reader who already "
                   "reads Fox (so not new_publisher) must resolve to bridge (cross-cutting).",
    "expected": {"targetType": "bridge"},
    "catalog": base() + [novel("https://foxnews.example.com/politics/bridge", "Fox News",
                               "Politics", 1.8, "A conservative view from the summit")],
    "readers": [reader([("https://theguardian.example.com/base/politics-0", "The Guardian"),
                        ("https://theguardian.example.com/base/politics-1", "The Guardian"),
                        ("https://npr.example.com/base/health-0", "NPR"),
                        ("https://foxnews.example.com/base/politics-0", "Fox News"),
                        ("https://foxnews.example.com/base/politics-1", "Fox News")])],
    "target": "https://foxnews.example.com/politics/bridge"}

# 6 · long_tail — a niche article surfaced by rwe-d; the target-rec strategy is rwe-d
FIX["long_tail"] = {
    "description": "A familiar-outlet, same-topic, non-cross article whose target-rec strategy is "
                   "rwe-d must resolve to long_tail (introduces a less-recommended source).",
    "expected": {"targetType": "long_tail"},
    "catalog": base() + [novel("https://reuters.example.com/tech/tail", "Reuters",
                               "Technology", 0.0, "A quiet corner of the chip supply chain")],
    "readers": [reader([("https://reuters.example.com/base/business-0", "Reuters"),
                        ("https://reuters.example.com/base/business-1", "Reuters"),
                        ("https://reuters.example.com/base/business-2", "Reuters"),
                        ("https://reuters.example.com/base/business-3", "Reuters"),
                        ("https://npr.example.com/base/health-0", "NPR")])],
    "target": "https://reuters.example.com/tech/tail",
    "targetStrategy": "rwe-d"}

# 7 · mixed_feed — a varied reader; the served feed must carry >= 2 distinct explanation types
FIX["mixed_feed"] = {
    "description": "A varied reader; the served feed must carry at least two DISTINCT explanation "
                   "types — the resolver differentiates, it doesn't collapse to one sentence.",
    "expected": {"minDistinctTypes": 2},
    "catalog": base(),
    "readers": [reader(BASE_READS)]}

# 8 · cold_start — a below-threshold reader; history priorities can't fire → no history claims
FIX["cold_start"] = {
    "description": "A reader with a single read (below the measured threshold): a novel article "
                   "must NOT claim story_match, topic_continuity, or a familiar-history fact.",
    "expected": {"targetTypeNot": "story_match"},
    "catalog": base() + [novel("https://bloomberg.example.com/markets/cold", "Bloomberg",
                               "Business", 0.2, "Markets open mixed")],
    "readers": [reader([("https://npr.example.com/base/health-0", "NPR")])],
    "target": "https://bloomberg.example.com/markets/cold"}

# 9 · story_follower — read TWO members of a story → story_match/following
FIX["story_follower"] = {
    "description": "Reader read two publishers' articles of the same story; a third publisher's "
                   "article must resolve to story_match/following ('you've been following this').",
    "expected": {"targetType": "story_match", "targetVariant": "following"},
    "catalog": base() + story("summit", "climate summit breakthrough deal",
                              ["Fox News", "CNN", "BBC"], topic="World",
                              lean_by={"Fox News": 1.3, "CNN": -0.7, "BBC": -0.3}),
    "readers": [reader([("https://foxnews.example.com/story/summit", "Fox News"),
                        ("https://cnn.example.com/story/summit", "CNN")] + BASE_READS[:3])],
    "target": "https://bbc.example.com/story/summit"}

def _dump(fx: dict) -> str:
    """Compact, reviewable JSON: top-level keys indented, but each catalog article and each read on
    a single line (matching the Metric Validation Pipeline's fixture style)."""
    def line(obj):
        return json.dumps(obj, separators=(", ", ": "))
    out = ["{"]
    out.append(f'  "name": {json.dumps(fx["name"])},')
    out.append(f'  "description": {json.dumps(fx["description"])},')
    out.append(f'  "expected": {line(fx["expected"])},')
    if "targetStrategy" in fx:
        out.append(f'  "targetStrategy": {json.dumps(fx["targetStrategy"])},')
    out.append('  "catalog": [')
    out.append(",\n".join("    " + line(a) for a in fx["catalog"]))
    out.append("  ],")
    out.append('  "readers": [')
    rblocks = []
    for r in fx["readers"]:
        rb = ["    {"]
        rb.append(f'      "id": {json.dumps(r["id"])}, "underTest": {json.dumps(r.get("underTest", False))},')
        rb.append('      "reads": [')
        rb.append(",\n".join("        " + line(x) for x in r["reads"]))
        rb.append("      ]")
        rb.append("    }")
        rblocks.append("\n".join(rb))
    out.append(",\n".join(rblocks))
    out.append("  ]" + ("," if "target" in fx else ""))
    if "target" in fx:
        out.append(f'  "target": {json.dumps(fx["target"])}')
    out.append("}")
    return "\n".join(out) + "\n"


for name, fx in FIX.items():
    fx = {"name": name, **fx}
    (GD / f"{name}.json").write_text(_dump(fx))
    print(f"wrote {name}.json  (catalog {len(fx['catalog'])}, readers {len(fx['readers'])})")
