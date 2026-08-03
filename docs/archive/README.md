# Archive — the insight-derived Coverage Comparison roadmap

These documents describe an **AI-powered** roadmap that was designed, partially built, measured,
and then **removed from the milestone before any of it reached readers**. They are kept because the
reasoning and the production measurements are worth having if the idea is ever revisited — not
because any of it is live. **No code in the repository implements them.**

| document | what it is |
|---|---|
| `ARTICLE_INSIGHTS.md` | design for AI per-article summaries + framing analysis |
| `ARTICLE_INSIGHTS_VERIFICATION.md` | production verification of that feature while dormant |
| `OLLAMA_PROVIDER_VERIFICATION.md` | the local-provider adapter and its wire-protocol verification |
| `INSIGHTS_TIERING_DESIGN.md` | subscription-aware provider/model variants (design only) |
| `COVERAGE_COMPARISON_REVISED_DESIGN.md` | revision 2: comparison over AI-extracted facets |
| `COVERAGE_COMPARISON_DESIGN_REVIEW.md` | the pre-implementation review that reshaped revision 2 |
| `COVERAGE_COMPARISON_IMPLEMENTATION.md` | the build log, deviations, and the production results |

## What the measurements established

Worth reading before anyone proposes this again, because these were measured on the live catalog
rather than estimated:

- **Phase 0a passed** — 339 of 779 gated clusters could reach a comparable set (bar: 100). But the
  median gated cluster reached **2** support units, below the threshold, and that figure was an
  upper bound.
- **Throughput did not close** — 945 eligible clustered articles/day arriving against an 864/day
  worker ceiling.
- **Local inference was rejected on three counts** — it cannot co-reside with the app on a
  t3.medium (the attempt took the site down), 153 s/call is far below the required rate, and a
  3b-class model **ignored the closed vocabularies**, inventing frame keys and voice roles so that
  only two of six facet fields survived validation.
- **The syndication threshold (0.9) was well calibrated** — 6.6% of members folded, a quarter of
  clusters carried wire copy, largest group 11×.

## What is still live, and documented outside this folder

**Coverage Comparison tier L0** — `examples/coverage_comparison.py`. Deterministic counted facts
about an article's story cluster, no AI anywhere, deployed and rendering. Its design is
`docs/COVERAGE_COMPARISON_DESIGN.md` (§5.1 only; §5.2–§5.4 were retired) and its production
assessment is `docs/COVERAGE_COMPARISON_VALUE_EVALUATION.md`.
