# Event-identity rubric — the labeling spec for the same-event verifier (V1)

**Status: draft v1, 2026-08-17 — the ground-truth definition the V1 golden-pairs benchmark is
labeled against and the verifier prompt encodes.** Rubric changes bump `prompt_version`, which
re-keys the verdict store; labels and verdicts always carry the version they were made under.
The product-semantics calls below (marked ⚖) are editorial decisions, not model decisions —
ratify or edit them before labeling begins.

## The question

Given two articles (headline, dek, published time, extracted entities/countries when present —
**never the publisher**, agnosticism by construction): do they report **the same news event**?

**Same event** = the same occurrence: the same actors (who) doing/undergoing the same thing
(what) at the same instance in time (when) and place (where). Not the same topic, beat, series,
franchise, or person.

Labels: `same_event` / `different_event` / `uncertain`. `uncertain` is for pairs genuinely
underdetermined from the provided text alone — not for pairs the labeler finds tedious. The
verifier's `uncertain` is fail-closed (the deterministic baseline stands), so an over-broad
`uncertain` in labeling hides exactly the cases the verifier exists to resolve.

## Rules, each with its production receipt

1. **An article's event is what it REPORTS, not what it mentions.** "Vishwanath and Sons box
   office Day 2: Suriya's film trails behind Jana Nayagan" is a Vishwanath and Sons article;
   the Jana Nayagan mention is a comparison. Comparative headlines, background references, and
   "unlike X last year" clauses never make two events one. → label `different_event`.
2. **A continuing occurrence is ONE event across its updates.** Day-2 and day-3 collections of
   the *same* film's run; the election-day arc of one race; successive casualty counts of one
   disaster. Same occurrence, newer numbers. → `same_event`.
3. **Recurring series instances are DIFFERENT events.** The daily gold price (Antam up Monday,
   down Tuesday), market wraps, weekly charts: nothing connects two instances but the series
   template. Different days of a recurring series → `different_event`. (The boundary with rule
   2: a film's run is one occurrence unfolding; a daily price is a new measurement each day.)
3b. **Different instances of the same competition/fixture are different events** — Australia's
   first gold and Ireland's first men's gold at the same championships are two events; one
   match reported by many outlets is one.
4. **Same person/organization does not mean same event.** Mangione's arraignment and a Mangione
   sighting; Trump's rally and Trump's court filing; one company's earnings and its layoffs.
   The who slot matching while the what/when slots differ → `different_event`.
   ⚖ **Family rule (ratify):** an event and coverage published *in direct reaction to it* —
   obituaries, retrospectives, photo galleries, "what we know" explainers about that specific
   occurrence — belong to the SAME story (the Hayden Panettiere death + "life in photos"
   retrospective; consistent with X5b's adopted joining of the Mangione court family).
   Reaction to the event = same family; a later, separately-triggered development with its own
   what/when gets its own story.
5. **Wording, framing, and language are irrelevant.** "Mass shooting reported at Seattle
   Center" / "gunfire erupts near Seattle" → `same_event`. A Japanese and an English report of
   the same certification-database listing → `same_event`. Translation or paraphrase never
   separates; shared vocabulary never joins (the box-office and announcement templates).
6. **Numbers and ordinals are identity anchors when they name the instance.** "Day 2" vs
   "day 21" of *different* films, different quarters, different match days → different.
   Conflicting figures for the same occurrence (early counts vs updated counts) → same.
7. **Place disagreement on the same template is decisive.** "Human remains found" in
   Scarborough vs at Palomar Mountain; a shooting in Lexington vs Portland → `different_event`
   regardless of token overlap.

## Labeling protocol

Label from exactly the verifier's inputs (headline + dek + time + entities/countries where
present), blind to publisher and to how production currently clusters the pair. One labeler,
this rubric, rubric version recorded per label; pairs the labeler cannot decide from the text
get `uncertain`, and the benchmark reports them separately (they are the verifier's permitted
`uncertain` budget, not errors). Target set: ~300–500 pairs — every recorded exhibit below,
plus deterministically sampled intra-story pairs, near-misses, and random negatives,
stratified across the five requirement classes.

## The recorded exhibits, pre-labeled per this rubric (test cases, never rules)

| pair | label | rule |
|---|---|---|
| X-Men D23 (Radio Pacific ↔ Forbes) | same_event | 5 |
| X-Men ↔ The Paper / Paper ↔ Mirzapur / DJI ↔ Mirzapur | different_event | 5 (template) |
| Batwara ↔ Vishwanath (both edges) | different_event | 5, 6 |
| Vishwanath ↔ Jana Nayagan (comparative) | different_event | 1 |
| Same-film day-2 ↔ day-3 (Batwara pair) | same_event | 2 |
| Antam gold price up ↔ down | different_event | 3 |
| Athletics: Australia's ↔ Ireland's first gold | different_event | 3b |
| Toronto ↔ Palomar human remains | different_event | 7 |
| Lexington ↔ Portland shootings | different_event | 7 |
| Garmin CIRQA (JA ↔ EN) | uncertain → different_event (band vs ring; decide from text) | 5, 4 |
| UK eclipse timing ↔ Netherlands eclipse videos | ⚖ same_event (one eclipse, reactive angles) | 4-family |
| Seattle Center shooting paraphrase pair | same_event | 5 |
| Hayden death ↔ "life in photos" retrospective | ⚖ same_event | 4-family |
| Zhu Rongji death ↔ legacy ↔ flags half-mast | same_event | 4-family |
| Ronaldo wedding pairs | same_event | 2 |
