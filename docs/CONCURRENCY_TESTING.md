# Why `tests/concurrency/` Exists

The mechanics — what each test pins, how to run it — are in
[`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §7. This is the part that is easy to
lose: **why the suite is shaped the way it is, and what to do when it goes red.**

---

## 1. These are assumption detectors, not regression tests

An ordinary regression test asserts something about *our* code: given this input, this function does
that. It fails when someone changes our code, and the fix is to change our code back or update the
test on purpose.

Most of `tests/concurrency/` asserts something about *somebody else's* code — SQLite's transaction
semantics, SQLAlchemy's session behaviour, the sqlite3 driver's idea of when a `BEGIN` is warranted.
Those tests fail when the **world** moves, not when we do. We cannot fix the world, and reverting our
diff will not help.

The distinction is worth keeping because the correct response is different:

| | Regression test goes red | Detector goes red |
|---|---|---|
| **What happened** | your change broke a behaviour we own | the substrate under an argument changed |
| **First question** | what did I just change? | what moved — Python, SQLite, SQLAlchemy, a pragma, a connect arg? |
| **Fix** | in the code | possibly in the *design*, or only in the documentation |
| **Silencing it** | sometimes right (the behaviour changed on purpose) | **never right on its own** — see §3 |

The suite protects an argument rather than a function. `IDENTITY_UPSERT_CONCURRENCY.md` §9 proves the
identity upsert correct *against premises*. A proof is only as good as its premises, and premises
written in prose rot silently. These tests are the premises made executable, so they rot loudly.

There is a specific reason this repository has such a thing. Twice while writing that proof,
measurement contradicted careful reasoning — once about whether an explicit `flush()` was load-bearing
(it wasn't), and once, more expensively, about whether a `SAVEPOINT` participates in its enclosing
transaction (it doesn't, and the entire algorithm had to be withdrawn). Both mistakes were made by
someone who had read the code closely. The suite is the institutional form of the lesson: **at the
storage layer, do not reason where you can measure.**

## 2. Why some tests assert documented runtime behaviour instead of business logic

"Don't test your dependencies" is a good rule. It applies when you use a dependency through its
*contract* — the thing its maintainers promise and would treat a change to as a breaking change.

It does not apply when your correctness rests on a behaviour the dependency documents as
version-dependent, deprecated, or outright incorrect. `tests/concurrency/test_storage_premises.py`
covers exactly that narrow set:

- **`test_ID1_...`** pins the sqlite3 driver's *legacy transaction control* — behaviour SQLAlchemy
  documents as a divergence from PEP 249 that "will no longer be the default" in Python 3.16. It is on
  a published removal schedule. Nothing in our code mentions it, yet it decides which exception a
  caller sees when it loses a race.
- **`test_ID2_...`** pins a behaviour SQLAlchemy's own documentation calls **incorrect**: a released
  savepoint that "fails to participate in the enclosing transaction". We assert that the bug is still
  there, because a design was rejected on account of it.

Notice what these have in common: the dependency is doing something surprising, we depend on knowing
which surprise it is, and **no other test in the repository would notice if it changed**. The business
logic here is a find-or-create — five lines with nothing interesting in them. All the risk lives in the
substrate, and a test is the only place a substrate dependency can be written down as an executable
claim rather than a comment someone will eventually delete.

The tie-break for adding a test like this: *if this behaviour changed silently, would we want to know
before our users did, and would anything else tell us?* If yes and no, it belongs here. Otherwise it is
just testing the framework, and it doesn't.

## 3. Reading a failure after a Python, SQLite or SQLAlchemy upgrade

Work in this order.

1. **Did our code change?** If the only thing that moved is a version, stop looking for a bug in the
   diff. If both moved, revert the version bump locally and re-run to separate the two.
2. **Read the failure message.** Every premise test names the section to re-read and says what its
   failure implies. They were written for this moment.
3. **Find the assumption's category** in `IDENTITY_UPSERT_CONCURRENCY.md` §10, then:

| Category | A failure means | Do |
|---|---|---|
| **Stable Contract** (SC1–SC8) | almost certainly *our* problem: a changed pragma, a broken fixture, a schema that did not get created. A real upstream break here would be extraordinary. | Investigate our side first. If it truly is upstream, the design needs re-deriving from scratch — SC4 in particular is what I4 and Q5 rest on. |
| **Observed Behavior** (OB1–OB5) | the world moved in a way nobody promised wouldn't happen. | Re-measure, update the expectation in both the test and §10, then check whether any invariant's *argument* changed. Usually mechanical. |
| **Implementation Detail** (ID1–ID6) | a design-review trigger. | Re-read §10's row for it and §9's premise table. The change may be benign, may make the design *safer*, or may quietly invalidate a step in the proof. |

**The one thing not to do is loosen an assertion to get back to green.** If the assertion no longer
describes reality, then a document is now wrong, and the test going red is the only thing that knows.
Fix the document first; the test follows from it.

A worked example of each direction:

- **ID2 starts failing** — savepoints now participate properly. Nothing breaks: the shipped algorithm
  does not use savepoints. The correct response is to reclassify ID2 in §10 and note that a
  savepoint-scoped design is available again if a reason ever appears.
- **ID1 starts failing** — the driver no longer runs in legacy mode. Now a lost race may arrive as an
  `OperationalError` (a snapshot conflict) instead of an `IntegrityError`. The algorithm already
  catches both, so nothing is broken — but OB1 must be re-measured, and §5's "why this is safe"
  argument needs its second bullet rewritten.

## 4. "Revalidate the design" versus "fix the code"

The two suites answer different questions, and the interesting cases are the mixed ones.

| Invariant tests (`test_identity_upsert.py`) | Premise tests (`test_storage_premises.py`) | Reading |
|---|---|---|
| red | green | **Fix the code.** Our behaviour regressed against an unchanged substrate. |
| green | red | **Revalidate the design.** The substrate moved. See below — this is the dangerous quadrant. |
| red | red | Start with the premise. The invariant failure is probably a symptom. |
| green | green | Nothing to do. |

**The dangerous quadrant is green/red**, and it deserves its own paragraph, because it is the exact
shape of the mistake that cost this design a full revision.

An invariant test asserts an *outcome*. A premise test asserts the *reason* the outcome holds. When the
reason changes but the outcome doesn't, everything looks fine and the proof is quietly no longer valid.
That is what happened with the savepoint algorithm: `ROLLBACK TO SAVEPOINT` behaved exactly as expected
(the outcome was right), so the design "worked" — until the untested half, an outer rollback after a
release, was exercised and turned out to commit rows that should never have existed.

So: **a green invariant suite is not permission to ignore a red premise.** When a premise goes red,
walk §9.5's six interleavings again with the new premise substituted and confirm each still preserves
its invariants. If it does, update the doc and move on. If it doesn't, the algorithm changes — that is
what happened last time, and it is a normal outcome, not a crisis.

## 5. The executable reference implementation

`test_identity_upsert.py` contains `upsert_reference` — the algorithm specified in §4 — alongside the
shipped method, and asserts every property against both.

**Why it exists.** The design was reviewed before it was implemented, and a design that cannot be run
is a claim rather than a specification. Making §4 executable meant the invariant suite could be written
and validated against the design itself, before touching production code. It also gives the review a
control: with both subjects in the same table, the *difference between the columns is exactly the change
being proposed*. Today that difference is two cells — I2 under concurrency, and I8 — which is a far more
honest description of the work than a paragraph claiming the same thing.

**When to remove it.** The moment `Store.upsert_user_by_identity` adopts §4. You will not have to
remember: `test_I8_every_concurrent_caller_resolves[shipped-*]` is an `xfail(strict=True)`, so when the
shipped method starts passing it, the XPASS **fails the suite on purpose**. That red is the removal
notice. Three steps:

1. delete `upsert_reference` and `_attempt` from the test module,
2. collapse `SUBJECTS` to the real method (the parametrisation disappears with it),
3. delete the `XFAIL_SHIPPED` marker.

**Why it must not be kept afterwards.** Two implementations of the same algorithm drift, and the copy
under test is the one nobody runs in production. A suite that proves things about the reference while
the real method has quietly diverged is worse than no suite, because it reports confidence it has not
earned. The reference is scaffolding with an expiry condition, and the expiry is enforced by the tests
rather than by anyone's memory.

## What this suite is not

- **Not a benchmark.** The wall-clock numbers (`busy_timeout`, pool exhaustion) are there to pin
  *behaviour*, not to track performance. Nothing here should ever gate on how fast something is.
- **Not a load test.** `N = 15` is the connection-pool ceiling, not a capacity claim. Capacity lives in
  [`CAPACITY_AND_COST.md`](CAPACITY_AND_COST.md).
- **Not proof of multi-process safety.** `test_R4_...` models a second process with a second engine and
  pool in the same interpreter. Real OS-level file locking is only *modelled* — see §9.8 R4, which is
  still open.
- **Not a substitute for §9.** The tests check the premises; the argument that the premises imply the
  invariants is prose, and prose is where the next mistake will be.
