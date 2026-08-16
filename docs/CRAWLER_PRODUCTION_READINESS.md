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

## 4a. Classification rubric

Turn the five answers into exactly one verdict per publisher. Record the clause you relied on —
a verdict without a quotation is not reviewable by the next person.

| Verdict | When | Consequence |
|---|---|---|
| **DO NOT CRAWL** | ToS prohibits automated access or crawling (Q1 = yes), **or** prohibits commercial use of content/metadata (Q2 = yes), **or** the publisher has already refused us technically (403, robots block) | Remove from config. Add to the excluded list with the reason, which is pinned by a test. Do not seek permission unless the business decides to — a published prohibition is an answer. |
| **NEEDS PERMISSION** | Terms are silent, ambiguous, or arguably restrictive; **or** no ToS exists at all; **or** a licensing/syndication contact exists (Q4) and terms don't clearly permit | Do not crawl. Write to the contact describing the §1 narrowing. Reclassify on their reply. |
| **ALLOW** | Terms are readable and clearly permit automated access to public pages **and** do not restrict the §1 use, **and** our attribution satisfies Q3 | Eligible for the staged rollout. Still behind `RWE_CRAWL_ENABLED`, still one publisher at a time. |

Three rules for applying it:

- **Silence is NEEDS PERMISSION, never ALLOW.** An absent prohibition is not a grant. This is the
  same fail-closed reading the robots gate takes, and the reason the crawler skips a publisher whose
  robots.txt it cannot read.
- **A technical refusal outranks a textual permission.** A 403 is the enforcing mechanism speaking;
  a permissive ToS is the advisory one. That is why The Times of Israel is already excluded despite
  robots.txt allowing us.
- **Robots-layer PASS is not an input to this table.** All seven already passed it. It establishes
  only that we are technically permitted to fetch, which is a different question from whether we are
  permitted to use what we fetch.

### Current state of the review

| Publisher | Robots layer (verified) | ToS layer | Verdict |
|---|---|---|---|
| Kyiv Independent | PASS — readable, allows us, no AI-bot block | **not read** | **pending** |
| SCMP | PASS — plus `Crawl-delay: 10s`, honoured | **not read** | **pending** |
| The Straits Times | PASS — plus `Crawl-delay: 10s`, honoured | **not read** | **pending** |
| Dawn | PASS | **not read** | **pending** |
| Daily Maverick | PASS | **not read** | **pending** |
| Premium Times | PASS | **not read** | **pending** |
| Clarín | PASS | **not read** | **pending** |

No publisher has been classified. The robots column is measured evidence from live runs; the ToS
column is empty because nobody has opened the documents yet.

**A guessed verdict is worse than an empty one here.** A wrong DO NOT CRAWL costs a publisher we
could have used. A wrong ALLOW authorises crawling someone who forbade it, in a product that
displays their name — and it would be recorded as a completed review, so nobody would look again.
That asymmetry is why this table stays blank until the documents are read.

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

### Does it need optimising before production? Yes — and the deciding measurement does not exist yet

The cost is real and bounded: 6 fetches (the full per-publisher budget), ~20,000 sitemap entries
transferred and parsed, 3.5% kept, and ~60 seconds of mandated waiting per cycle from honouring
their 10s `Crawl-delay` six times. SCMP is the only publisher where the budget binds completely.

Two candidate optimisations, and **neither can be chosen on the evidence we have**:

- **Cut `max_fetches` for SCMP.** If most of the 690 recent URLs come from the first one or two
  children, dropping to 2 removes 4 fetches and ~13,000 parsed entries at no cost.
- **Early-exit once a child yields nothing recent.** Children are sorted newest-first by `<lastmod>`,
  so in principle once one falls entirely outside the window the rest are older. **This reasoning is
  not safe as stated**: `lastmod` is when the sitemap *file* changed, not the age of its newest
  article, so a regenerated archive can carry a recent `lastmod` over old contents. Sorted order is
  a good heuristic for which child to try first; it is not proof of monotonic content age.

Both turn on the same missing number: **recent yield per child**. The report aggregates across
children, so we cannot currently tell whether SCMP's 690 are concentrated in one child or spread
across all six. Instrumenting per-child yield is a small change to `CrawlReport`, and it is the
prerequisite for choosing between the two.

**Sequence:** ask SCMP about a news sitemap first (§5 option 1) — a positive answer makes both
optimisations moot, and the question is already going in the permission letter. Instrument per-child
yield only if the answer is no. Do not tune `max_fetches` by guesswork; a blind cut could drop the
child that carries most of the recent articles and would look like the publisher going quiet.

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
