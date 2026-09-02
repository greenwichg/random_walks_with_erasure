"""verify_crawler_config.py — the pre-rollout verification gate for the crawl framework.

Answers, per configured publisher, the four questions that decide whether it may be crawled at all:

  1. **robots.txt** — does it exist, is it actually a policy, does it allow OUR user-agent on the
     discovery URLs we configured, and what ``Crawl-delay`` does it state?
  2. **Discovery URLs** — does each configured feed/sitemap/section URL exist, return the content
     type it should, and parse into entries?
  3. **Article patterns** — of the URLs actually discovered, what fraction match the configured
     ``article_pattern``? A pattern that matches nothing is a silent no-op; one that matches
     everything is not filtering.
  4. **Terms of service** — locates the ToS page and surfaces clauses containing automated-access
     language for a HUMAN to read. It does not decide anything: a regex has no opinion about
     contract law.

**Read-only and article-free.** It fetches robots.txt, the configured discovery documents, and ToS
pages. It never fetches an article, never touches the store, and never ingests. It obeys the same
robots gate and rate limiter the crawler does — a verification pass that ignored robots would be
the exact behaviour it exists to check for.

It reuses ``crawler.py``'s own policy, parsers, and config loader rather than reimplementing them,
so a green report means *that code* works against the real site — not that a second implementation
agrees with itself.

    python examples/verify_crawler_config.py                     # all publishers, human-readable
    python examples/verify_crawler_config.py --publisher NPR
    python examples/verify_crawler_config.py --json > report.json
    python examples/verify_crawler_config.py --skip-tos          # robots + discovery only

Exit code is 0 when every enabled publisher verified crawlable, 1 when any did not — so it can gate
a rollout step in CI.

**Run this from an environment with real outbound access.** It is the whole point of the script;
it cannot tell you anything from a sandbox whose egress proxy refuses the hosts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawler               # reuse: config, RobotsPolicy, RateLimiter, discovery parsers

#: Automated-access language worth a human's attention in a ToS. Deliberately broad: the cost of a
#: false positive is somebody reads a paragraph, and the cost of a false negative is a contract
#: breach nobody noticed.
_TOS_PATTERNS = [
    r"\bcrawl(?:er|ing)?\b", r"\bscrap(?:e|er|ing)\b", r"\bspider\b", r"\brobots?\b",
    r"\bautomated (?:access|means|tools?|systems?|devices?)\b", r"\bdata[- ]?min(?:e|ing)\b",
    r"\bbots?\b", r"\bmachine[- ]?learning\b", r"\btrain(?:ing)? (?:an? )?(?:AI|model)",
    r"\bartificial intelligence\b", r"\bsystematic(?:ally)? (?:extract|collect|download)",
    r"\btext and data mining\b", r"\bcommercial use\b",
]
_TOS_RE = re.compile("|".join(_TOS_PATTERNS), re.I)

#: Conventional ToS locations, tried in order. Publishers are inconsistent enough that guessing a
#: few paths beats requiring an operator to hand-configure five URLs before the first run.
_TOS_PATHS = ("/terms", "/terms-of-service", "/terms-of-use", "/legal/terms",
              "/help/terms-of-service", "/about/terms", "/tos")

#: AI/LLM crawler user-agents worth reporting even though they do not bind us. A publisher that
#: names and blocks them has a stated posture on automated ingestion, which is context for the ToS
#: question even when `User-agent: *` would let us through.
_AI_AGENTS = ("GPTBot", "CCBot", "ClaudeBot", "anthropic-ai", "Google-Extended",
              "PerplexityBot", "Bytespider", "Applebot-Extended")


@dataclass
class PublisherVerdict:
    publisher: str
    crawlable: Optional[bool] = None       # None = could not determine (the honest third state)
    robots_status: str = ""
    robots_url: str = ""
    crawl_delay: Optional[float] = None
    configured_interval: float = 0.0
    sitemaps_declared: list = field(default_factory=list)
    ai_agents_blocked: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    pattern_match_rate: Optional[float] = None
    pattern_sample_misses: list = field(default_factory=list)
    #: URLs the pattern ACCEPTED, from the live sitemap. These are the evidence an enabled config
    #: must carry (tests/test_crawl_adapter_wiring.py records them verbatim), so the verifier has
    #: to print them — a report that showed only the misses left the operator with nothing to
    #: record for AP and CNN on 2026-09-02.
    pattern_sample_hits: list = field(default_factory=list)
    tos_url: str = ""
    tos_clauses: list = field(default_factory=list)
    corrections: list = field(default_factory=list)
    blockers: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _host_allowed_any(host: str, domains) -> bool:
    """Whether ``host`` is one of the publisher's article domains (crawler's own dot-anchored rule)."""
    return bool(domains) and crawler._host_allowed(host, domains)


def _primary_host(hosts, domains) -> str:
    """The host whose robots.txt governs this publisher's ARTICLES.

    robots.txt is per-host, so the per-URL gate in ``crawler.RobotsPolicy`` is already correct
    whatever this returns; this picks the host the *summary* should describe — declared sitemaps,
    named-AI-bot posture, and where to look for the ToS.

    Ranking matters because the obvious readings are both wrong. Taking the first source host puts
    ``feeds.npr.org`` in the report, whose robots.txt is a feed endpoint's and need not resemble
    the newsroom's. Guessing ``www.`` + domain invents a host that need not exist — AP serves on
    the bare ``apnews.com``. So: prefer a host that IS a configured article domain (bare or
    ``www.``), then the shortest article host, and only then anything at all.
    """
    def rank(h: str) -> tuple:
        bare = h[4:] if h.startswith("www.") else h
        is_apex = any(bare == d.lower() for d in domains)
        return (0 if is_apex else 1, len(h), h)

    article = [h for h in hosts if _host_allowed_any(h, domains)]
    return (sorted(article, key=rank) or sorted(hosts, key=rank) or [""])[0]


def _fetch(url: str, timeout: float = 20.0) -> "tuple[Optional[str], str]":
    """``(body, error)`` — never raises, because one dead URL must not end the verification run."""
    try:
        return crawler._fetch_text(url, timeout=timeout), ""
    except Exception as e:
        detail = getattr(e, "code", None)
        return None, f"{type(e).__name__}{f' {detail}' if detail else ''}: {e}"


def _robots_text(host: str) -> "tuple[Optional[str], str, str]":
    url = f"https://{host}/robots.txt"
    body, err = _fetch(url)
    return body, url, err


def _declared_sitemaps(robots_body: str) -> "list[str]":
    """``Sitemap:`` directives — the publisher telling us where their sitemaps actually are.

    This is the single most valuable line in the file for us: a configured sitemap URL that 404s is
    usually not "they have no sitemap", it is "we guessed the path". Reporting what they declare
    turns a failed check into a specific correction.
    """
    out = []
    for line in (robots_body or "").splitlines():
        s = line.split("#", 1)[0].strip()
        if s.lower().startswith("sitemap:"):
            v = s.split(":", 1)[1].strip()
            if v:
                out.append(v)
    return out


def _blocked_ai_agents(robots_body: str) -> "list[str]":
    """Which named AI crawlers this robots.txt disallows outright (context, not a rule that binds us)."""
    blocked, current = [], None
    for line in (robots_body or "").splitlines():
        s = line.split("#", 1)[0].strip()
        low = s.lower()
        if low.startswith("user-agent:"):
            current = s.split(":", 1)[1].strip()
        elif low.startswith("disallow:") and current:
            path = s.split(":", 1)[1].strip()
            if path == "/" and any(current.lower() == a.lower() for a in _AI_AGENTS):
                blocked.append(current)
    return sorted(set(blocked))


def _verify_sources(cfg, policy, limiter, verdict: PublisherVerdict) -> "list[str]":
    """Fetch and parse each configured discovery URL. Returns the URLs discovered across all rungs."""
    discovered: "list[str]" = []
    for src in cfg.sources:
        row = {"kind": src.kind, "url": src.url}
        decision = policy.check(src.url)
        if not decision.allowed:
            row.update(ok=False, reason=f"robots: {decision.reason}")
            verdict.sources.append(row)
            continue
        limiter.wait(src.url, max(cfg.min_interval, decision.crawl_delay or 0.0))
        body, err = _fetch(src.url)
        if body is None:
            row.update(ok=False, reason=err)
            verdict.corrections.append(f"{src.kind} URL unreachable: {src.url} ({err})")
            verdict.sources.append(row)
            continue
        try:
            entries = crawler._DISCOVERY[src.kind](body, src.url)
        except Exception as e:
            row.update(ok=False, reason=f"parse failed: {type(e).__name__}: {e}")
            verdict.sources.append(row)
            continue
        urls = [e.url for e in entries]
        discovered.extend(urls)
        row.update(ok=bool(entries), entries=len(entries),
                   dated=sum(1 for e in entries if e.published_at),
                   titled=sum(1 for e in entries if (e.title or "").strip()))
        if not entries:
            row["reason"] = "parsed but yielded no entries"
            verdict.corrections.append(
                f"{src.kind} URL parsed to 0 entries: {src.url} — wrong document or wrong kind")
        verdict.sources.append(row)
    return discovered


def _verify_pattern(cfg, discovered: "list[str]", verdict: PublisherVerdict) -> None:
    """What fraction of really-discovered URLs the configured pattern accepts.

    Both extremes are failures worth naming. 0% means the crawler would silently ingest nothing
    while every gate reports healthy. 100% across a section page means the pattern is not
    filtering, and tag/author/index pages will land in the catalog as articles.
    """
    pattern = cfg.pattern
    if not discovered or pattern is None:
        return
    on_domain = [u for u in discovered
                 if crawler._host_allowed(urllib.parse.urlsplit(u).hostname or "", cfg.domains)]
    if not on_domain:
        verdict.corrections.append(
            "no discovered URL was on the configured domains — check `domains`")
        return
    hits = [u for u in on_domain if pattern.search(u)]
    verdict.pattern_match_rate = round(len(hits) / len(on_domain), 3)
    verdict.pattern_sample_misses = [u for u in on_domain if not pattern.search(u)][:5]
    verdict.pattern_sample_hits = hits[:3]
    if not hits:
        verdict.blockers.append(
            f"article_pattern {cfg.article_pattern!r} matched 0 of {len(on_domain)} discovered "
            f"URLs — the crawler would ingest nothing while every gate reported healthy")
    elif verdict.pattern_match_rate > 0.98 and len(on_domain) >= 20:
        verdict.corrections.append(
            f"article_pattern matched {verdict.pattern_match_rate:.0%} of URLs — it is probably "
            f"not filtering; confirm index/tag pages are excluded")


def _verify_tos(host: str, verdict: PublisherVerdict, limiter, policy) -> None:
    """Locate the ToS and surface automated-access clauses for a human. Decides nothing."""
    for path in _TOS_PATHS:
        url = f"https://{host}{path}"
        if not policy.check(url).allowed:
            continue
        limiter.wait(url)
        body, _err = _fetch(url)
        if not body or len(body) < 500:
            continue
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body,
                      flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        hits = []
        for sentence in re.split(r"(?<=[.;])\s+", text):
            if _TOS_RE.search(sentence) and 40 <= len(sentence) <= 400:
                hits.append(sentence.strip())
        if hits:
            verdict.tos_url = url
            verdict.tos_clauses = hits[:12]
            return
    verdict.corrections.append("could not locate a ToS page automatically — check by hand")


def verify(cfg, *, skip_tos: bool = False, policy=None, limiter=None) -> PublisherVerdict:
    v = PublisherVerdict(publisher=cfg.publisher, configured_interval=cfg.min_interval)
    policy = policy if policy is not None else crawler.RobotsPolicy()
    limiter = limiter if limiter is not None else crawler.RateLimiter(cfg.min_interval)

    # The robots host comes from a URL we actually intend to fetch, never from `www.` + domain.
    # robots.txt is per-HOST, and guessing the `www.` form gets it wrong in both directions: AP
    # serves on the bare `apnews.com`, so the guess would report an unreachable robots.txt and a
    # false NOT-CRAWLABLE for a publisher that is fine. A verifier's failures have to be the
    # publisher's, not its own.
    hosts = [h for h in (urllib.parse.urlsplit(s.url).hostname for s in cfg.sources) if h]
    primary = _primary_host(hosts, cfg.domains)
    if not primary:
        v.robots_status = "no discovery source to derive a host from"
        v.blockers.append("no sources configured")
        v.crawlable = False
        return v
    body, url, err = _robots_text(primary)
    v.robots_url = url
    if body is None:
        v.robots_status = f"unreachable ({err})"
        v.blockers.append("robots.txt could not be fetched — the gate fails closed, so this "
                          "publisher is NOT crawlable until it can be read")
        v.crawlable = False
        return v
    if not crawler._looks_like_robots(body):
        v.robots_status = "not a robots policy (no User-agent directive)"
        v.blockers.append("robots.txt returned 200 but is not a policy — fails closed")
        v.crawlable = False
        return v

    v.robots_status = "ok"
    v.sitemaps_declared = _declared_sitemaps(body)
    v.ai_agents_blocked = _blocked_ai_agents(body)

    for src in cfg.sources:
        d = policy.check(src.url)
        if d.crawl_delay is not None:
            v.crawl_delay = max(v.crawl_delay or 0.0, d.crawl_delay)
    if v.crawl_delay and v.crawl_delay > cfg.min_interval:
        v.corrections.append(
            f"publisher states Crawl-delay {v.crawl_delay}s; configured min_interval is "
            f"{cfg.min_interval}s — raise it so the config states the truth")

    discovered = _verify_sources(cfg, policy, limiter, v)
    _verify_pattern(cfg, discovered, v)

    if v.sitemaps_declared:
        configured = {s.url for s in cfg.sources}
        unused = [s for s in v.sitemaps_declared if s not in configured]
        failed_sitemaps = any(r["kind"] == "sitemap" and not r.get("ok") for r in v.sources)
        if unused and failed_sitemaps:
            v.corrections.append(
                "robots.txt declares sitemaps we are not using: " + ", ".join(unused[:5]))

    if not any(r.get("ok") for r in v.sources):
        v.blockers.append("no configured discovery URL yielded entries")
    if v.ai_agents_blocked:
        v.corrections.append(
            "publisher blocks named AI crawlers (" + ", ".join(v.ai_agents_blocked) +
            ") — `User-agent: *` may still permit us, but their stated posture is relevant to the "
            "ToS review and to whether we should ask before crawling")

    if not skip_tos and primary:
        _verify_tos(primary, v, limiter, policy)

    v.crawlable = not v.blockers
    return v


def _render(verdicts) -> str:
    lines = []
    for v in verdicts:
        mark = {True: "CRAWLABLE", False: "NOT CRAWLABLE", None: "UNDETERMINED"}[v.crawlable]
        lines.append(f"\n{'=' * 78}\n{v.publisher}  —  {mark}\n{'=' * 78}")
        lines.append(f"  robots.txt      {v.robots_status}  ({v.robots_url})")
        if v.crawl_delay is not None:
            lines.append(f"  Crawl-delay     {v.crawl_delay}s  (configured {v.configured_interval}s)")
        if v.sitemaps_declared:
            lines.append(f"  declares        {len(v.sitemaps_declared)} sitemap(s)")
            for s in v.sitemaps_declared[:5]:
                lines.append(f"                    {s}")
        if v.ai_agents_blocked:
            lines.append(f"  blocks AI UAs   {', '.join(v.ai_agents_blocked)}")
        for r in v.sources:
            state = "ok  " if r.get("ok") else "FAIL"
            extra = (f"{r.get('entries', 0)} entries, {r.get('dated', 0)} dated"
                     if r.get("ok") else r.get("reason", ""))
            lines.append(f"  [{state}] {r['kind']:<8} {r['url']}\n           {extra}")
        if v.pattern_match_rate is not None:
            lines.append(f"  pattern match   {v.pattern_match_rate:.0%}")
            for u in v.pattern_sample_hits:
                lines.append(f"                    hit:  {u}")
            for u in v.pattern_sample_misses:
                lines.append(f"                    miss: {u}")
        if v.tos_clauses:
            lines.append(f"  ToS             {v.tos_url}  ({len(v.tos_clauses)} clause(s) to read)")
            for c in v.tos_clauses[:3]:
                lines.append(f"                    \"{c[:150]}\"")
        for b in v.blockers:
            lines.append(f"  BLOCKER         {b}")
        for c in v.corrections:
            lines.append(f"  correction      {c}")
    ok = [v for v in verdicts if v.crawlable]
    lines.append(f"\n{'=' * 78}\n{len(ok)}/{len(verdicts)} publishers verified crawlable")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify crawler config against live publishers (read-only).")
    ap.add_argument("--publisher", help="limit to one configured publisher")
    ap.add_argument("--config", help="path to crawler_publishers.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-tos", action="store_true", help="robots + discovery only")
    args = ap.parse_args(argv)

    configs = crawler.load_config(args.config)
    if args.publisher:
        want = args.publisher.strip().lower()
        configs = [c for c in configs if c.publisher.lower() == want]
        if not configs:
            print(f"no configured publisher named {args.publisher!r}")
            return 2
    else:
        # A sweep verifies what RUNS. A NAMED publisher is verified whatever its switch says —
        # the whole point of naming one is to earn the evidence that flips `enabled`, and a
        # verifier that skipped disabled entries reported "0/0 verified" for exactly the three
        # configs that were waiting on it (production, 2026-09-02).
        configs = [c for c in configs if c.enabled]

    verdicts = [verify(c, skip_tos=args.skip_tos) for c in configs]
    print(json.dumps([v.as_dict() for v in verdicts], indent=2) if args.json else _render(verdicts))
    return 0 if all(v.crawlable for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
