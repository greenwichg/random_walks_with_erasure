"""Tests for media enrichment (Commit 9) — media.py selection + RSS extraction + storage survival.

Proves image selection is centralised + deterministic, RSS/Atom media is parsed (never downloaded),
FeedArticle stores media metadata, the Story hero is picked by priority, publisher logos derive from
the publisher's own domain, and media survives re-polling + retention. Feeds without images still work.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import media                 # noqa: E402
import rss_ingest as ri      # noqa: E402
import store as store_mod    # noqa: E402
import discover              # noqa: E402


# --------------------------------------------------------------------------- #
# media.pick_best_image — choose among a feed item's media tags
# --------------------------------------------------------------------------- #
def test_pick_best_image_largest_wins():
    best = media.pick_best_image([
        {"url": "https://x.example/small.jpg", "width": 150, "height": 100, "mime": "image/jpeg", "source": "media:thumbnail"},
        {"url": "https://x.example/big.jpg", "width": 1200, "height": 800, "mime": "image/jpeg", "source": "media:content"},
    ])
    assert best["url"] == "https://x.example/big.jpg" and best["width"] == 1200


def test_pick_best_image_rejects_audio_and_relative():
    assert media.pick_best_image([{"url": "https://x.example/a.mp3", "mime": "audio/mpeg", "source": "enclosure"}]) is None
    assert media.pick_best_image([{"url": "/relative/x.jpg", "source": "enclosure"}]) is None
    assert media.pick_best_image([]) is None


def test_pick_best_image_by_extension_when_no_mime():
    best = media.pick_best_image([{"url": "https://x.example/pic.png", "source": "enclosure"}])
    assert best and best["url"].endswith("pic.png")


def test_pick_article_media_and_guard():
    row = {"image": "https://x.example/a.jpg", "imageWidth": 800, "imageHeight": 600,
           "imageMimeType": "image/jpeg", "imageSource": "media:content", "imageAttribution": "NPR"}
    m = media.pick_article_media(row)
    assert m["image"] == "https://x.example/a.jpg" and m["imageWidth"] == 800 and m["imageAttribution"] == "NPR"
    assert media.pick_article_media({"image": None})["image"] is None
    assert media.pick_article_media({"image": "ftp://x/a.jpg"})["image"] is None    # non-http guard


def test_pick_story_hero_priority():
    rep = {"image": "https://x.example/rep.jpg", "imageWidth": 400, "imageHeight": 300, "publishedAt": "2026-07-01"}
    big = {"image": "https://x.example/big.jpg", "imageWidth": 2000, "imageHeight": 1500, "publishedAt": "2026-07-02"}
    # representative wins even though `big` is larger
    assert media.pick_story_hero([rep, big], representative=rep)["image"] == "https://x.example/rep.jpg"
    # when the representative has no image, the highest-quality one wins
    rep0 = {"image": None, "publishedAt": "2026-07-01"}
    assert media.pick_story_hero([rep0, big], representative=rep0)["image"] == "https://x.example/big.jpg"
    # none when no article carries an image
    assert media.pick_story_hero([{"image": None}, {"image": None}]) is None


def test_pick_best_logo_leads_with_the_highest_resolution_site_icon():
    """`favicon.ico` is a 16-32px browser-chrome icon. Leading with it meant the publisher profile
    upscaled it 4.5x into a 36px box — a blurry mark on every outlet enrichment had not reached.
    The Apple touch icon is conventionally 180x180 at a predictable path, so it leads and the
    favicon becomes the last resort it always should have been."""
    logo = media.pick_best_logo("Fox News", "https://www.foxnews.com/politics/x")
    assert logo["publisherLogo"] == "https://www.foxnews.com/apple-touch-icon.png"
    assert logo["publisherLogoSource"] == "site-icon"
    assert logo["publisherLogoFallbacks"][-1] == "https://www.foxnews.com/favicon.ico", (
        "the favicon is still offered — it is a fallback now, not the first thing shown")
    assert media.pick_best_logo("Unknown", None)["publisherLogo"] is None


def test_the_fallback_chain_is_ordered_largest_first_and_never_empty_handed():
    """An Apple touch icon is a convention, not a guarantee. Every candidate can 404, so the
    client needs the whole chain — one URL meant a single miss dropped the outlet to a generic
    glyph even when it published a perfectly usable icon one step down."""
    chain = media.pick_best_logo("X", "https://ex.example/a")
    urls = [chain["publisherLogo"], *chain["publisherLogoFallbacks"]]
    assert urls == ["https://ex.example/apple-touch-icon.png",
                    "https://ex.example/apple-touch-icon-precomposed.png",
                    "https://ex.example/favicon.ico"]
    assert len(set(urls)) == len(urls), "a duplicate would be a wasted request on the way down"


def test_an_enriched_logo_still_wins_but_keeps_the_site_icons_beneath_it():
    """A Commons file is the outlet's actual mark at usable resolution, so it leads. It can also
    be renamed, and before the chain existed that 404 fell straight past every icon the publisher
    hosts to the glyph."""
    got = media.pick_best_logo("Fox News", "https://www.foxnews.com/x",
                               enriched=("https://commons.example/Fox.svg", "wikimedia"))
    assert got["publisherLogo"] == "https://commons.example/Fox.svg"
    assert got["publisherLogoSource"] == "wikimedia"
    assert got["publisherLogoFallbacks"][0] == "https://www.foxnews.com/apple-touch-icon.png"


def test_a_publisher_with_no_url_offers_nothing_rather_than_a_guess():
    """No host means no icon path can be constructed. An empty chain is the honest answer; the
    client renders the glyph, which is what "we have no mark for this outlet" looks like."""
    got = media.pick_best_logo("Nowhere Gazette", None)
    assert got["publisherLogo"] is None and got["publisherLogoFallbacks"] is None, (
        "absent, not an empty list — an empty list is a value the response model would emit on "
        "every article, inventing a field the service never produced")


# --------------------------------------------------------------------------- #
# media.pick_story_hero RANKED mode (RWE_STORY_HERO_GUARD) — docs/STORY_HERO_IMAGES.md.
# The legacy tests above are untouched on purpose: ranked=False must stay byte-identical.
# --------------------------------------------------------------------------- #
def test_image_identity_strips_query_and_lowercases_host_but_keeps_path_case():
    assert (media.image_identity("HTTPS://CDN.Example.com/Signed/Path.jpg?w=600#f")
            == "https://cdn.example.com/Signed/Path.jpg")
    # cache-busters must not let one placeholder wear a thousand identities
    assert (media.image_identity("https://x.example/ph.png?cb=1")
            == media.image_identity("https://x.example/ph.png?cb=2"))
    assert media.image_identity("/relative.jpg") == "" and media.image_identity(None) == ""


def test_hero_suspect_matches_the_measured_furniture_and_only_the_path():
    """Every token is receipted from the 2026-08-16 production reuse table (or _ICON_PATHS);
    the HOST is never token-matched, so a CDN with 'logo' in its name cannot condemn every
    photo it serves."""
    assert media.hero_suspect("https://media.spokesman.com/graphics/2020/08/sr_placeholder.png")
    assert media.hero_suspect(
        "https://www.taipeitimes.com/assets/images/TaipeiTimesLogo-1200X1200px_new.jpg")
    assert media.hero_suspect(
        "https://www.winnipegfreepress.com/wp-content/uploads/sites/2/2022/11/fb-og-image.png")
    assert media.hero_suspect("https://cdn.thestar.com.my/Themes/img/newTsol_logo_socmedia.png")
    assert media.hero_suspect("https://x.example/apple-touch-icon.png")
    assert media.hero_suspect("https://x.example/news/photo.jpg", 1200, 1200), \
        "exact-square declared dimensions are the social-logo shape"
    assert not media.hero_suspect("https://x.example/2026/08/scene.jpg", 1600, 900)
    assert not media.hero_suspect("https://logocdn.example/2026/08/scene.jpg")


def test_ranked_hero_a_real_photo_beats_the_fastest_filers_branding():
    """The defect the guard exists for: the earliest filer's house graphic used to win
    unconditionally because the representative was an override, not evidence."""
    rep = {"image": "https://wire.example/assets/site_logo.png",
           "publishedAt": "2026-08-01T00:00:00+00:00"}
    photo = {"image": "https://news.example/2026/08/scene.jpg",
             "publishedAt": "2026-08-01T05:00:00+00:00"}
    # legacy: the representative's logo wins (unchanged behaviour, pinned above)
    assert media.pick_story_hero([rep, photo], representative=rep)["image"] == rep["image"]
    # ranked: the suspect tier demotes the logo below the clean photo
    got = media.pick_story_hero([rep, photo], representative=rep, ranked=True)
    assert got["image"] == "https://news.example/2026/08/scene.jpg"


def test_ranked_hero_rejects_a_cross_story_reused_asset_by_identity():
    """`rejected` carries image IDENTITIES, so a resize query-string cannot smuggle a rejected
    placeholder back in."""
    reused = {"image": "https://cdn.example/promo/site-banner.png?resize=600",
              "publishedAt": "2026-08-01T00:00:00+00:00"}
    other = {"image": "https://news.example/real.jpg", "publishedAt": "2026-07-31T00:00:00+00:00"}
    rej = frozenset({media.image_identity("https://cdn.example/promo/site-banner.png")})
    got = media.pick_story_hero([reused, other], representative=reused, ranked=True, rejected=rej)
    assert got["image"] == "https://news.example/real.jpg"


def test_ranked_hero_returns_none_when_every_candidate_is_branding():
    """All-suspect (or all-rejected) means NO hero: the imageless card renders the coverage
    figure, a designed state — no hero is more honest than a masthead pretending to be news."""
    a = {"image": "https://x.example/img/masthead.png", "publishedAt": "2026-08-01T00:00:00+00:00"}
    b = {"image": "https://y.example/social.jpg", "imageWidth": 1200, "imageHeight": 1200,
         "publishedAt": "2026-08-02T00:00:00+00:00"}
    assert media.pick_story_hero([a, b], representative=a, ranked=True) is None
    rej = frozenset({media.image_identity("https://z.example/shared.jpg")})
    only = {"image": "https://z.example/shared.jpg", "publishedAt": "2026-08-01T00:00:00+00:00"}
    assert media.pick_story_hero([only], representative=only, ranked=True, rejected=rej) is None


def test_ranked_hero_representative_is_a_tiebreak_not_an_override():
    rep = {"image": "https://a.example/one.jpg", "publishedAt": "2026-08-01T00:00:00+00:00"}
    dims = {"image": "https://b.example/two.jpg", "imageWidth": 1600, "imageHeight": 900,
            "publishedAt": "2026-07-30T00:00:00+00:00"}
    # photo-shaped dimensions beat the representative...
    assert media.pick_story_hero([rep, dims], representative=rep, ranked=True)["image"] \
        == "https://b.example/two.jpg"
    # ...but between otherwise-equal candidates the representative still wins (determinism)
    peer = {"image": "https://c.example/three.jpg", "publishedAt": "2026-08-01T00:00:00+00:00"}
    assert media.pick_story_hero([peer, rep], representative=rep, ranked=True)["image"] \
        == "https://a.example/one.jpg"


def test_ranked_hero_consults_the_ingestion_sources_media_priority():
    """`store.SOURCE_PRIORITY` already ranks RSS media above GDELT's og:image — the og tier is
    exactly where site-wide fallback graphics arrive — and the ranked hero finally consults it."""
    og = {"image": "https://a.example/fallback.jpg", "imageSource": "gdelt",
          "publishedAt": "2026-08-02T00:00:00+00:00"}
    rss = {"image": "https://b.example/scene.jpg", "imageSource": "media:content",
           "publishedAt": "2026-08-01T00:00:00+00:00"}
    got = media.pick_story_hero([og, rss], representative=og, ranked=True)
    assert got["image"] == "https://b.example/scene.jpg", \
        "rss(100) outranks gdelt(60) even though the og arrival is newer and the representative"


# --------------------------------------------------------------------------- #
# RSS/Atom media extraction (parse only — never downloads)
# --------------------------------------------------------------------------- #
def test_rss_media_extraction_selects_best_and_rejects_audio():
    rss = (b"<?xml version='1.0'?><rss version='2.0' xmlns:media='http://search.yahoo.com/mrss/'>"
           b"<channel><title>C</title><item><title>T</title><link>https://n.example/1</link>"
           b"<description>d</description><pubDate>Wed, 02 Jul 2026 12:00:00 GMT</pubDate>"
           b"<media:content url='https://n.example/big.jpg' type='image/jpeg' width='1200' height='800' medium='image'/>"
           b"<media:thumbnail url='https://n.example/small.jpg' width='150' height='100'/>"
           b"<enclosure url='https://n.example/audio.mp3' type='audio/mpeg'/></item></channel></rss>")
    _, entries = ri.parse_feed(rss)
    e = entries[0]
    assert e.image == "https://n.example/big.jpg" and e.image_width == 1200 and e.image_source == "media:content"


def test_atom_image_link_extracted():
    atom = (b"<feed xmlns='http://www.w3.org/2005/Atom'><title>C</title><entry><title>T</title>"
            b"<link rel='alternate' href='https://a.example/1'/>"
            b"<link rel='enclosure' type='image/jpeg' href='https://a.example/hero.jpg'/>"
            b"<published>2026-07-02T12:00:00Z</published></entry></feed>")
    _, entries = ri.parse_feed(atom)
    assert entries[0].url == "https://a.example/1" and entries[0].image == "https://a.example/hero.jpg"


def test_feed_without_images_still_parses():
    rss = (b"<?xml version='1.0'?><rss version='2.0'><channel><title>C</title>"
           b"<item><title>T</title><link>https://n.example/1</link><description>d</description></item>"
           b"</channel></rss>")
    _, entries = ri.parse_feed(rss)
    assert len(entries) == 1 and entries[0].image is None      # graceful: no media, still an entry


# --------------------------------------------------------------------------- #
# Storage: media persists, survives re-poll + retention, and serialises through
# --------------------------------------------------------------------------- #
def _put(st, cu, *, image=None, w=None, h=None, mime=None, pub="NPR"):
    st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title="t",
                           description="", body=None, published_at="2026-07-05T10:00:00+00:00",
                           source_feed="f", scored={"article_id": cu, "outlet": pub, "lean": -1.0},
                           image=image, image_width=w, image_height=h, image_mime=mime,
                           image_source="media:content" if image else None,
                           image_attribution=pub if image else None)


def test_media_stored_and_serialised_with_logo():
    st = store_mod.Store("sqlite://")
    _put(st, "https://foxnews.com/a", image="https://foxnews.com/i.jpg", w=1000, h=700,
         mime="image/jpeg", pub="Fox News")
    row = st.get_feed_article("https://foxnews.com/a")
    assert row["image"] == "https://foxnews.com/i.jpg" and row["imageWidth"] == 1000
    a = discover.feed_article_to_article(st.list_feed_articles(limit=1)[0])
    assert a["image"] == "https://foxnews.com/i.jpg" and a["imageWidth"] == 1000
    assert a["publisherLogo"] == "https://foxnews.com/apple-touch-icon.png"
    assert "https://foxnews.com/favicon.ico" in a["publisherLogoFallbacks"]


def test_media_survives_repoll_and_backfills():
    st = store_mod.Store("sqlite://")
    _put(st, "https://n.example/a")                                   # first poll: no image
    _put(st, "https://n.example/a", image="https://n.example/late.jpg", w=800, h=600)   # later poll adds one
    assert st.get_feed_article("https://n.example/a")["image"] == "https://n.example/late.jpg"   # backfilled
    _put(st, "https://n.example/a")                                   # a later image-less poll doesn't wipe it
    assert st.get_feed_article("https://n.example/a")["image"] == "https://n.example/late.jpg"


def test_media_survives_retention_and_batched_lookup():
    st = store_mod.Store("sqlite://")
    _put(st, "https://n.example/keep", image="https://n.example/k.jpg", w=800, h=600)
    _put(st, "https://n.example/drop")                                # no image
    st.delete_feed_articles(["https://n.example/drop"])              # retention removes the other row
    assert st.count_feed_articles() == 1
    assert st.get_feed_article("https://n.example/keep")["image"] == "https://n.example/k.jpg"
    mm = st.feed_article_media(["https://n.example/keep", "https://n.example/missing"])
    assert mm["https://n.example/keep"]["image"] == "https://n.example/k.jpg" and "https://n.example/missing" not in mm


def test_media_imports_no_recommendation_algorithm():
    for banned in ("health_report", "rwe", "simulate_users", "personalize", "narrate_report",
                   "corpus_refresh", "api_server", "story_service"):
        assert not hasattr(media, banned), f"media must not import {banned}"
