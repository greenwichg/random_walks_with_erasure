# Crawler Production Readiness — the seven candidate publishers

Status: **blocked on human review.** Every technical gate has been run and passed. What remains is
not engineering: somebody has to read seven terms-of-service documents and decide whether we may do
this. Nothing in this document was produced by crawling; it summarises runs already performed and
names what a person must now check.

Scope: Kyiv Independent 🇺🇦 · SCMP 🇭🇰 · The Straits Times 🇸🇬 · Dawn 🇵🇰 · Daily Maverick 🇿🇦 ·
Premium Times 🇳🇬 · Clarín 🇦🇷

---

## 1. What we would actually be doing

The review is only tractable if the ask is stated precisely. Reviewers asked "can we crawl X?"
answer a much bigger question than the one we need.

**Per publisher, per cycle we would:**

| | |
|---|---|
| Fetch | `robots.txt`, one sitemap (or one index + up to 6 children), sometimes one section page |
| Request volume | **1–6 requests**, honouring each publisher's stated `Crawl-delay` |
| Extract | canonical URL, headline, publication date |
| Store | those three fields |
| Display | the headline as a link to the publisher's own page, attributed to them |
| Derive | topic classification; story clustering by headline similarity; publisher-level political lean **from our own registry, not from their content** |

**We never:**

- fetch an article page — no body text is retrieved or stored, ever;
- reproduce more than the headline;
- pass a paywall, log in, or evade rate limits;
- ignore `robots.txt` (an unreadable one already fails closed and skips the publisher).

That narrowing matters to the review. The copyrighted asset is the article text, and we do not take
it. What we take is the metadata publishers emit specifically for machine consumption — the same
fields a search engine or news aggregator reads from the same files.

## 2. What we verified (no human needed)

Established by live runs of `verify_crawler_config.py` and `crawler.py plan`:

| Publisher | robots.txt | Blocks AI crawlers | Crawl-delay | Declared sitemap used | New/cycle |
|---|---|---|---|---|---|
| Kyiv Independent | readable, allows us | **no** | — | `/news-sitemap.xml` | 29 |
| SCMP | readable, allows us | **no** | **10s** | `/sitemap/sitemap.xml` | 645 |
| The Straits Times | readable, allows us | **no** | **10s** | `/googlenews.xml` | 547 |
| Dawn | readable, allows us | **no** | — | `/feeds/sitemap` | 100 |
| Daily Maverick | readable, allows us | **no** | — | `/sitemap_index.xml` | 149 |
| Premium Times | readable, allows us | **no** | — | `/sitemap_index.xml` | 116 |
| Clarín | readable, allows us | **no** | — | `/sitemaps/sitemap_google_news.xml` | 1,150 |

**None of the seven names ClaudeBot, anthropic-ai, GPTBot, CCBot or any other AI crawler in
robots.txt.** That is the material difference from the eleven publishers already excluded, eight of
which do. All sitemap URLs above were harvested from each publisher's own `robots.txt`, not guessed.

Totals: **2,736 new articles, 100% dated, 23 fetches** — a seven-day backlog on first contact, so
roughly **390/day** at steady state.

**robots.txt permission is not a licence.** It is a technical signal about crawling, published by
whoever administers the web server. It says nothing about copyright, contract, or commercial reuse.
That is exactly the gap section 3 exists to close.

## 3. What a human must review, per publisher

For each, open the ToS and answer the five questions in §4. The paths below are **conventional
locations, not verified** — I have not fetched any of them, and the automated ToS locator already
failed on one publisher (HuffPost) by trying seven conventional paths and hitting none. Treat them
as where to start looking, not as answers.

| Publisher | Start here | Also check | Note |
|---|---|---|---|
| **Kyiv Independent** | `kyivindependent.com/terms` · site footer | About / Ethics pages | Reader- and grant-funded independent outlet; may have no formal ToS at all, which is itself an answer worth recording |
| **SCMP** | `scmp.com/terms-conditions` · footer | Corporate/licensing pages | Confirm current corporate owner — parent-level terms may govern |
| **The Straits Times** | `straitstimes.com/terms-conditions` | Publisher group's terms; syndication/licensing page | Singapore publisher-group ownership; group terms likely govern, and they run a syndication business — a licensing contact probably exists |
| **Dawn** | `dawn.com/terms` · footer | Copyright/reprint page | Pakistan's oldest English daily; check for an explicit reprint policy |
| **Daily Maverick** | `dailymaverick.co.za/terms-conditions` | Republishing policy | South African independent; some outlets in this class publish an explicit Creative Commons or republishing policy — if so, that resolves the question directly |
| **Premium Times** | `premiumtimesng.com/terms` | Republishing / copyright page | Nigerian investigative non-profit; similar to above |
| **Clarín** | `clarin.com/terminos-y-condiciones` | Grupo Clarín corporate terms | Spanish-language; largest of the seven by volume (1,150/cycle) and the one most worth getting right |

Ownership notes are **context to confirm, not established fact** — corporate structures change and
my information has a cutoff. Verify before relying on any of it.

## 4. The five questions

Ask these of every publisher. A "yes" to Q1 or Q2 stops that publisher regardless of robots.txt.

1. **Does the ToS prohibit automated access, crawling, or scraping** — in terms that bind us even
   though `robots.txt` permits our user-agent?
2. **Does it prohibit commercial use** of headlines, links, or metadata?
3. **Does it require attribution in a specific form**, and does our current display satisfy it?
   (We show the headline as a link to their page with the publisher named.)
4. **Is there a licensing or syndication contact** — i.e. is there a way to simply ask? For at least
   one of these publishers this is likely a routine commercial conversation rather than a legal
   dispute.
5. **Does any local press-publishers' right apply?** None of the seven is EU-based, so the EU DSM
   Article 15 right does not attach to these publishers — but Hidden View's own users may be in the
   EU, and that interaction is a lawyer's question, not mine.

**My read, offered as engineering input rather than legal advice:** taking headline + URL + date
from a file a publisher publishes for machines, and linking back to them, is materially what a news
aggregator does and is the weakest possible version of the copyright question. The risk is
concentrated in Q1 and Q2 — a blanket "no automated access" or "no commercial use" clause — not in
the content itself. Q4 is the cheapest path to certainty for any publisher whose terms are
ambiguous.

## 5. SCMP — works, but wasteful

SCMP declares **no news sitemap**. `robots.txt` names `/sitemap/sitemap.xml` and
`/sitemap/archives-0.xml`, both archives. Each cycle we therefore:

- fetch 6 documents (the per-publisher budget, fully consumed),
- discover **19,962** URLs,
- discard **19,273** as older than seven days,
- keep **690**.

It works — 645 new articles a cycle, the second-largest contributor — but reading an entire archive
to extract a week is disproportionate, and it burns SCMP's own bandwidth for output we throw away.
It is also the only publisher where our 10s `Crawl-delay` compliance times 6 fetches makes the cycle
noticeably slow.

Three options, in order of preference:

1. **Ask SCMP whether a news sitemap exists** that `robots.txt` does not declare. Most publishers of
   this size have one; the fix would be a one-line config change. This folds naturally into the §4
   Q4 conversation.
2. **Use the section page instead** — but it yields no dates, so the age filter correctly rejects
   all of it. Not viable without a different date source.
3. **Accept the cost** — bounded and functional, but hard to justify to the publisher if they ever
   ask what we are doing.

**Recommendation: option 1, bundled with the permission request.** Do not ship SCMP on option 3
without at least having asked.

## 6. The Japan Times — out of scope

Declares **no sitemap** in `robots.txt`. Its only usable rung is the section page, which states no
publication dates, so under `max_age_days` every one of its 61 discovered URLs is correctly excluded
and it yields **zero** candidates.

This is the age rule working as designed, not a defect. Undated articles are excluded because an
article whose age we cannot establish cannot be shown to be inside the clustering window.

**Excluded from the production candidate set.** Reconsider only if a dated source appears — a news
sitemap they publish but do not declare, or a working feed. Do not "fix" this by relaxing the age
rule for one publisher: that would readmit undated content everywhere and undo the filter's purpose.

## 7. Sign-off checklist

Nothing ships until every line is checked.

- [ ] ToS reviewed for all seven (§3, §4) — **the blocking item**
- [ ] Any publisher whose ToS prohibits automated access or commercial use is removed from the config
- [ ] Licensing/syndication contacts identified where terms are ambiguous
- [ ] SCMP news-sitemap question asked (§5)
- [ ] Attribution form confirmed adequate (§4 Q3)
- [ ] Decision recorded on whether to notify publishers proactively, even where terms permit
- [ ] `sources._request` promoted to a public helper (currently reused through a private name)
- [ ] One publisher wired behind `RWE_CRAWL_ENABLED`, default off, and watched for a week before widening
- [ ] Steady-state marginal value re-measured days after the first real ingest — every number here is a first-contact backlog, not a recurring rate

## 8. What is already settled

So the review does not relitigate work that is done:

- **robots.txt compliance** — fails closed on unreadable, unparseable, or non-policy responses;
  honours `Crawl-delay` upward only.
- **Eleven publishers already excluded** — eight for naming AI crawlers, two for answering 403
  despite robots permission, one for an unreadable robots.txt. Those exclusions are pinned by a test
  that fails if any is re-added.
- **No article bodies** — architecturally, not by configuration.
- **Recency** — `max_age_days: 7`; undated content excluded rather than assumed recent.
- **Volume** — 23 fetches per cycle for all seven combined.
- **Nothing is wired in** — no poller references the crawler; it runs only when a person invokes it.
