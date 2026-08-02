"""Augmented reference corpus — the seam for a future *measured* Information Health Report.

The reference corpus (a ``MINDData`` click matrix + aligned per-item metadata) is a fixed
population of readers. To score a **real** user we represent them as *one more reader in that
same corpus*: append their row (plus any articles they read that aren't already in the catalog
as new columns), then hand the result to the **existing, unmodified** algorithms —
``health_report.compute`` / ``user_report`` for the report, ``MINDData.recommender_inputs`` +
``rwe.RWEB`` for recommendations.

Scope (Milestone B4): this module is *only* that abstraction. It is deliberately **not** wired
into any endpoint (the live product is unchanged), it does **not** ingest or score anything,
and it generates **no** synthetic reading. A real read arrives here already scored — as a
:class:`ScoredRead` — from the ingestion + scoring pipeline that lands in a later milestone.

Design guarantees:

* **The base is never mutated** and the existing readers are preserved exactly — ``augment``
  returns a fresh :class:`CorpusBundle`; the first ``m`` rows / ``n`` columns of the augmented
  matrix equal the base, so the reference population an individual is ranked against is intact.
* **No algorithm is changed** — this only *builds inputs* the current engine already accepts.

    from augmented_corpus import ScoredRead, bundle_from_backend, augment
    base = bundle_from_backend(backend)                 # a read-only view of the corpus
    result = augment(base, [ScoredRead(article_id=..., outlet=..., lean=..., ...), ...])
    pop = health_report.compute(result.bundle.mind, register=result.bundle.register,
                                emotion=result.bundle.emotion, confidence=result.bundle.confidence)
    report = health_report.user_report(pop, result.bundle.mind, result.reader_row)   # unchanged
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
import scipy.sparse as sp

from rwe.data import Dataset
from rwe.mind import MINDData

_NAN = float("nan")


@dataclass(frozen=True)
class ScoredRead:
    """One real article a user read, **already scored** into the engine's feature space.

    This is the stable interface every ingestion source (browser extension, pasted URL, RSS)
    will produce in a later milestone; the scorer fills the fields, and this module only
    *places* the read into the corpus. Only ``article_id`` is required; every unscored field
    degrades to ``n/a`` exactly as the engine already handles missing data, so a partially
    scored read is still usable.

    Parameters
    ----------
    article_id:
        Stable id (a canonical URL works): the dedup key, and how a read is matched to an
        existing catalog column so a shared article isn't double-counted.
    outlet / category / subcategory / title:
        Publisher (the source axis), topic, sub-topic, and headline (display).
    lean:
        Political position in ``[-2, 2]`` (``NaN`` if unknown → excluded from viewpoint).
    political:
        Whether the article is political (gates the viewpoint/echo metrics).
    emotion:
        Optional ``label -> share`` over ``fear/outrage/analysis/positive/neutral``.
    register:
        Optional ``P(reporting)`` in ``[0, 1]`` (reporting-vs-opinion).
    confidence:
        Optional lean-axis confidence in ``[0, 1]`` (down-weights ambiguous items).
    read_at:
        Optional ISO timestamp — carried for future reading history; unused by embedding.
    """

    article_id: str
    outlet: str = ""
    category: str = ""
    subcategory: str = ""
    title: str = ""
    lean: float = _NAN
    political: bool = False
    emotion: Optional[Dict[str, float]] = None
    register: float = _NAN
    confidence: float = _NAN
    read_at: Optional[str] = None


@dataclass
class CorpusBundle:
    """A reference corpus plus the per-item enrichment arrays ``health_report.compute`` reads.

    Bundling them means :func:`augment` extends the matrix and the enrichment *together*, so a
    measured report keeps the same metric set as the demo (Emotional Balance, Reporting Ratio,
    axis confidence). ``register`` / ``emotion`` / ``confidence`` are ``None`` when the base
    corpus doesn't carry them, in which case the augmented bundle carries none either."""

    mind: MINDData
    register: Optional[np.ndarray] = None                 # per-item P(reporting)
    emotion: Optional[Dict[str, np.ndarray]] = None       # label -> per-item share array
    confidence: Optional[np.ndarray] = None               # per-item axis confidence


@dataclass
class AugmentedCorpus:
    """The result of :func:`augment`: the augmented :class:`CorpusBundle` and the matrix row
    index of the appended real reader (feed it to ``user_report`` / the recommender)."""

    bundle: CorpusBundle
    reader_row: int


def bundle_from_backend(backend) -> CorpusBundle:
    """Wrap a running ``Backend``'s reference corpus as a :class:`CorpusBundle` — a read-only
    view of its arrays. This is the integration point a future measured-report path calls; it
    does not modify the Backend or any algorithm (duck-typed, so no import coupling)."""
    return CorpusBundle(mind=backend.mind, register=backend.register,
                        emotion=backend.emotion, confidence=backend.item_confidence)


def _concat_obj(base_arr, values) -> np.ndarray:
    """Append ``values`` to an object/string metadata array, preserving the base entries."""
    return np.concatenate([np.asarray(base_arr, dtype=object),
                           np.array(list(values), dtype=object)])


def _extend_float(base_arr: Optional[np.ndarray], values) -> Optional[np.ndarray]:
    """Extend a per-item float array by the novel values, or ``None`` if the base has none."""
    if base_arr is None:
        return None
    return np.concatenate([np.asarray(base_arr, dtype=float),
                           np.array(list(values), dtype=float)])


def augment(base: CorpusBundle, reads: Sequence[ScoredRead],
            user_id: str = "__real_user__") -> AugmentedCorpus:
    """Return a copy of ``base`` with one appended reader whose feedback is ``reads``.

    Reads already in the catalog reuse their column (the catalog stays authoritative for those
    items); reads on unseen articles are appended as new columns carrying the read's scored
    metadata. Feedback is de-duplicated per ``article_id`` and binary, matching the corpus. The
    base bundle is not mutated and the existing readers/items are preserved unchanged."""
    reads = list(reads)
    if not reads:
        raise ValueError("augment() needs at least one read to place a reader in the corpus")

    A = base.mind.dataset.matrix.tocsr()
    m, n = A.shape
    id_to_col = {str(i): c for c, i in enumerate(base.mind.dataset.item_ids)}

    read_cols: List[int] = []
    novel: List[ScoredRead] = []
    seen: set = set()
    next_col = n
    for r in reads:
        aid = str(r.article_id)
        if aid in seen:
            continue                        # de-dup: a repeat read counts once (binary)
        seen.add(aid)
        col = id_to_col.get(aid)
        if col is None:
            col, next_col = next_col, next_col + 1
            novel.append(r)
        read_cols.append(col)

    k = len(novel)
    n_new = n + k

    # Matrix: [ base | 0 ] over the existing rows, then the new reader's 1s in row m.
    top = sp.hstack([A, sp.csr_matrix((m, k))], format="csr") if k else A
    rc = np.asarray(read_cols, dtype=int)
    new_row = sp.csr_matrix((np.ones(rc.size), (np.zeros(rc.size, dtype=int), rc)), shape=(1, n_new))
    A_aug = sp.vstack([top, new_row], format="csr")

    # Aligned per-item metadata: base + the novel articles' scored fields.
    categories = _concat_obj(base.mind.categories, (r.category for r in novel))
    subcategories = _concat_obj(base.mind.subcategories, (r.subcategory for r in novel))
    titles = _concat_obj(base.mind.titles, (r.title for r in novel))
    outlets = _concat_obj(base.mind.outlets, (r.outlet for r in novel))
    political = np.concatenate([np.asarray(base.mind.political, dtype=bool),
                                np.array([r.political for r in novel], dtype=bool)])
    # Novel columns land on the SAME position lattice as the corpus ({0, ±0.6, ±1}) — a read's
    # ``lean`` is the registry-scale [-2, 2] scored value, and concatenating it raw made a novel
    # Fox read weigh +2.0 in the reader's click-mean where a catalog-joined one weighed +1.0
    # (docs/LEAN_CONSISTENCY.md F4). Unknown leans stay NaN, exactly as before.
    from validate_qbias import scored_to_position
    positions = np.concatenate([np.asarray(base.mind.item_positions, dtype=float),
                                np.array([scored_to_position(r.lean) for r in novel], dtype=float)])
    item_ids = _concat_obj(base.mind.dataset.item_ids, (str(r.article_id) for r in novel))
    user_ids = _concat_obj(base.mind.dataset.user_ids, [user_id])

    # User positions: append the reader's click-mean lean only when the base carries positions
    # (else recommender_inputs derives theta from clicks, unchanged).
    if base.mind.user_positions is not None:
        leans = positions[rc]
        known = np.isfinite(leans)
        theta_new = float(leans[known].mean()) if known.any() else 0.0
        user_positions = np.concatenate([np.asarray(base.mind.user_positions, dtype=float),
                                         [theta_new]])
    else:
        user_positions = None

    aug_mind = MINDData(
        dataset=Dataset(matrix=A_aug.tocsr(), user_ids=user_ids, item_ids=item_ids),
        categories=categories, subcategories=subcategories, titles=titles, outlets=outlets,
        political=political, item_positions=positions, user_positions=user_positions,
    )

    register = _extend_float(base.register, (r.register for r in novel))
    confidence = _extend_float(base.confidence, (r.confidence for r in novel))
    emotion: Optional[Dict[str, np.ndarray]] = None
    if base.emotion is not None:
        emotion = {}
        for label, arr in base.emotion.items():
            novel_vals = [(r.emotion.get(label, _NAN) if r.emotion else _NAN) for r in novel]
            emotion[label] = np.concatenate([np.asarray(arr, dtype=float),
                                             np.array(novel_vals, dtype=float)])

    bundle = CorpusBundle(mind=aug_mind, register=register, emotion=emotion, confidence=confidence)
    return AugmentedCorpus(bundle=bundle, reader_row=m)
