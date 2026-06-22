# Talk Walkthrough — *Random Walks with Erasure* (WWW 2021)

A slide-by-slide companion to Bibek Paudel's Web Conference 2021 talk
*"Random Walks with Erasure: Diversifying Personalized Ranking"*
(Bibek Paudel, Stanford University; Abraham Bernstein, University of Zürich).

For each slide this records **what is on the slide**, the **speaker's notes**
(from the talk narration), and **→ In this project** — how that point maps to
this repository, so the slides double as an implementation checklist.

> **Status:** slides 1–5 of the deck (the introduction / motivation). Remaining
> slides — ideology detection, the RWE algorithm, diversification strategies and
> the experimental results — will be appended as they are added.

---

## Slide 1 — Title

**On the slide.** *Random Walks with Erasure: Diversifying Personalized Ranking.*
Bibek Paudel (Stanford University) · Abraham Bernstein (Universität Zürich) ·
Web Conference (WWW) 2021.

**Speaker notes.** Introduces the work on *diversifying personalized ranking
using random walks with erasure*, joint work between Stanford and the University
of Zürich.

**→ In this project.** The whole repository is an implementation of this paper:
the core method in `rwe/` plus two extensions and a beginner guide
([`GUIDE.md`](../GUIDE.md)) and technical reference ([`README.md`](../README.md)).

---

## Slide 2 — "In the beginning …" (2010–2013)

**On the slide.** A protest placard listing *"REVOLUTION TOOLS: AK-47 ✗,
Machete ✗, Twitter ✓, Facebook ✓"*, beside *The Economist*'s 2013 cover
**"The march of protest."** Caption: **2010–2013**.

**Key point.** A decade ago, new social networks and online media were widely
**praised as enablers of democracy and social cohesion** — tools of protest and
participation.

**Speaker notes.** "Ten years ago … new social networks and online media were
being praised as enablers of democracy and social cohesion."

---

## Slide 3 — "Then something went wrong …"

**On the slide.** *The Economist*'s 2017 cover **"Social media's threat to
democracy,"** beside Jaron Lanier's book **"Ten Arguments for Deleting Your
Social Media Accounts Right Now."**

**Key point.** Within a few years the **same technologies were criticised** for
**increasing polarization and harming society** — raising questions about the
diversity, fairness and accountability of the information these systems serve.

**Speaker notes.** "In a matter of few years something seemed to go wrong. The
same technologies were now criticized for increasing polarization and harming
the society."

---

## Slide 4 — "How to bridge the divide?"

**On the slide.** Two overlapping blue/red heads and a fraying blue rope about to
snap. Three bullets:

- **High political polarization** increases suspicion, hostility and incivility.
- The **quality of democracy depends on political participation**.
- Political understanding and participation may **increase with cross-cutting
  awareness and discussions**.

**Key point.** Polarization is harmful and self-reinforcing; **cross-cutting
awareness** — exposure to viewpoints from different sides — can increase
political understanding and participation. This is the normative motivation for
*bridging*.

**Speaker notes.** "High political polarization leads to increase in suspicion
and hostility … the quality of democracy depends on political participation …
cross-cutting awareness and cross-cutting discussions lead to increased
political understanding and participation. By cross-cutting awareness we mean
exposure to viewpoints from different sides and different perspectives."

**→ In this project.** This is precisely what the **RWE-B bridging strategy**
targets: `rwe/random_walk.py::RWEB` promotes reachable *opposite-side* content
(weak-tie "bridges") rather than reinforcing a user's own side. The two
extensions go further — `satisfaction.py` and `agent_sim.py` model how much
cross-cutting content a user will actually engage with, so the "dose" can be
tuned per user.

---

## Slide 5 — "Challenge of political content"

**On the slide.** A screenshot of **AllSides** showing one story framed *From the
Left / From the Right / From the Center*. Below it, two **Telegraph** articles
about Brexit: *"Nigel Farage: £350 million pledge to fund the NHS was 'a
mistake'"* and *"Britain remains a great country with a great future."*

**Key point.** The obvious fix — *mix content using the known political slant of
each outlet* (as AllSides does) — is **not enough**. Two articles **from the same
outlet** (The Telegraph) were shared by people with **opposite** positions on
Brexit. So **pre-existing labels / fixed outlet slants are unreliable**:
ideological positions are **specific to issues and events** and must be learned.

**Speaker notes.** "The solution seems really simple — we know the political
slant of different news outlets … we can just mix and recommend information from
all these outlets … but this approach suffers from a major problem. These two
articles from the same newspaper … were shared by people with radically
different positions about the Brexit referendum. This shows that pre-existing
labels or known slants are not enough; these positions can change based on the
issues and based on specific events, and we need to know them."

**→ In this project.** This is the motivation for learning positions rather than
hard-coding them. `rwe/ideology.py::IdeologyModel.fit(R, S)` estimates the
ideological position of every user, elite and piece of **content** *from the
event-specific endorsement (`R`) and content-share (`S`) graphs* — exactly the
"learn issue-specific positions" requirement this slide argues for. (The
Telegraph example here is Table 1 of the paper.)

---

*More slides to follow: ideology detection, the RWE algorithm and erasure
matrix, the long-tail and bridging diversification strategies, and the
experimental results (Results I–IV).*
