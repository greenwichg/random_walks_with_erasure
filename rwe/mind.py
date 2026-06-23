"""MIND (Microsoft News Dataset) loader + ideology ingestion.

Turns a downloaded MIND release (``news.tsv`` + ``behaviors.tsv``) into the
structures this package needs for the real-data evaluation in ``docs/PAPER_PLAN.md``:

* a user-item **click matrix** (reusing :class:`rwe.data.Dataset`) built from each
  user's history + positive impressions, ready for :class:`rwe.FeedbackGraph`;
* a **political / non-political** mask (rule-based on MIND's sub-category, with an
  optional title-keyword booster and a hook for a learned classifier);
* per-item **ideological positions** in ``[-2, +2]`` (AllSides-style L..R) from an
  **outlet-lean join**.

Get MIND at <https://msnews.github.io/> (start with *MINDsmall*).

The outlet-lean limitation (read this)
--------------------------------------
MIND's ``URL`` column is an **MSN** URL; the *original publisher is not in the
file*. So the outlet-lean join needs a ``source_map`` (news-id -> outlet) that you
supply -- e.g. from an augmented MIND release, from the EB-NeRD dataset (which does
carry publishers), or by resolving the MSN "provider" yourself. Without it, the
click matrix and the political mask still work (both come from MIND alone), but
``item_positions`` will be ``NaN``. As a data-driven alternative that needs **no
outlet labels at all**, call :meth:`MINDData.fit_ideology` -- it fits
:class:`rwe.IdeologyModel` on the click matrix to place users *and* articles on a
shared latent left--right axis.

The bundled :data:`DEFAULT_LEAN` table is **illustrative** (≈50 major US outlets,
AllSides/MBFC-style, as of ~2024). Media-bias ratings are contested and change over
time, and lean is topic/article-dependent -- outlet lean is a coarse proxy. **For a
paper, replace it with the official AllSides or Media Bias/Fact Check data and cite
that source.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import scipy.sparse as sp

from .data import Dataset, from_interactions
from .ideology import IdeologyModel, IdeologyResult

# Sub-category substrings that mark a MIND article as political (lower-cased).
DEFAULT_POLITICAL_TERMS = ("politic", "election")

# Label -> numeric position on the AllSides 5-point scale.
LEAN_LABELS = {"left": -2, "lean-left": -1, "leanleft": -1, "center": 0,
               "lean-right": 1, "leanright": 1, "right": 2}

# Illustrative outlet -> lean (see module docstring caveat). Keys are normalised
# at import time, so display names ("Fox News") and domains ("foxnews.com") match.
_RAW_LEAN = {
    -2: ["MSNBC", "HuffPost", "Vox", "Slate", "Mother Jones", "The Nation",
         "Daily Kos", "Jacobin", "The Intercept", "Salon", "AlterNet"],
    -1: ["CNN", "NBC News", "ABC News", "CBS News", "The Washington Post",
         "The Guardian", "Politico", "Time", "The Atlantic", "NPR", "Bloomberg",
         "Axios", "USA Today", "Business Insider", "The New Yorker",
         "Los Angeles Times", "The New York Times", "nytimes.com",
         "washingtonpost.com", "Vanity Fair", "BuzzFeed News"],
    0: ["Reuters", "Associated Press", "AP", "BBC", "The Hill", "NewsNation",
        "Christian Science Monitor", "Forbes", "MarketWatch", "C-SPAN", "Axios"],
    1: ["The Wall Street Journal", "Wall Street Journal", "wsj.com",
        "New York Post", "Washington Examiner", "RealClearPolitics",
        "The Dispatch", "Reason", "National Review"],
    2: ["Fox News", "foxnews.com", "Breitbart", "The Daily Wire",
        "The Daily Caller", "Newsmax", "OAN", "One America News",
        "The Federalist", "The Blaze", "Townhall", "The Washington Times"],
}


def _norm(s: str) -> str:
    """Normalise an outlet name/domain to a comparison key.

    Lower-cases, drops a leading ``the``, removes non-alphanumerics and a trailing
    ``com``/``org``/``net`` so ``"Fox News"`` and ``"foxnews.com"`` both map to
    ``"foxnews"``.
    """
    s = s.strip().lower()
    if s.startswith("the "):
        s = s[4:]
    s = re.sub(r"[^a-z0-9]+", "", s)
    for tld in ("com", "org", "net"):
        if s.endswith(tld) and len(s) > len(tld) + 2:
            return s[: -len(tld)]
    return s


DEFAULT_LEAN = {_norm(name): v for v, names in _RAW_LEAN.items() for name in names}


# --------------------------------------------------------------------------- #
# Low-level parsers (manual split: MIND fields contain unescaped quotes, so a
# plain tab-split is more robust than csv; fields never contain tabs/newlines).
# --------------------------------------------------------------------------- #
def _read_news(path: Path) -> dict:
    """``news.tsv`` -> ``{news_id: (category, subcategory, title, url)}``."""
    news = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            news[p[0]] = (p[1], p[2], p[3], p[5])
    return news


def _read_clicks(path: Path, include_impressions: bool = True) -> tuple[list, list]:
    """``behaviors.tsv`` -> parallel ``(user_ids, news_ids)`` click lists.

    A click = a news id in the user's ``History`` or a positive (``-1``) token in
    ``Impressions``.  De-duplicated per user (MIND repeats the history snapshot on
    every impression row).
    """
    seen: dict[str, set] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            s = seen.setdefault(p[1], set())
            if p[3]:
                s.update(p[3].split())
            if include_impressions and p[4]:
                for tok in p[4].split():
                    nid, _, lab = tok.rpartition("-")
                    if lab == "1" and nid:
                        s.add(nid)
    users, items = [], []
    for uid, nids in seen.items():
        users.extend([uid] * len(nids))
        items.extend(nids)
    return users, items


def _filter_min(users, items, min_user: int, min_item: int):
    """Iteratively drop items/users below the per-side click thresholds."""
    users, items = np.asarray(users), np.asarray(items)
    if min_user <= 1 and min_item <= 1:
        return users, items
    for _ in range(5):
        n0 = users.size
        iu, ic = np.unique(items, return_counts=True)
        m = np.isin(items, iu[ic >= min_item])
        users, items = users[m], items[m]
        uu, uc = np.unique(users, return_counts=True)
        m = np.isin(users, uu[uc >= min_user])
        users, items = users[m], items[m]
        if users.size == n0:
            break
    return users, items


def _outlet_from_url(url: str) -> str:
    """Best-effort publisher domain from a URL (``""`` for MSN/empty URLs)."""
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower().lstrip("www.")
    if not netloc or "msn." in netloc:
        return ""          # MIND URLs are MSN -> publisher unknown
    return netloc


def _load_source_map(source_map) -> dict:
    """A dict, or a 2-column ``news_id<TAB/comma>outlet`` file, of news-id->outlet."""
    if source_map is None:
        return {}
    if isinstance(source_map, dict):
        return source_map
    out = {}
    with open(source_map, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t") if "\t" in line else line.split(",")
            if len(parts) >= 2:
                out[parts[0].strip()] = parts[1].strip()
    return out


def load_lean_table(path) -> dict:
    """Load an ``outlet,lean`` table (lean = int in [-2,2] or an L..R label)."""
    table = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.lower().startswith(("outlet", "source", "#")):
                continue
            parts = line.split("\t") if "\t" in line else line.split(",")
            if len(parts) < 2:
                continue
            name, lean = parts[0].strip(), parts[1].strip().lower()
            try:
                table[_norm(name)] = int(float(lean))
            except ValueError:
                if lean in LEAN_LABELS:
                    table[_norm(name)] = LEAN_LABELS[lean]
    return table


def _is_political(subcategory: str, title: str, terms, title_terms=None) -> bool:
    sub = subcategory.lower()
    if any(t in sub for t in terms):
        return True
    if title_terms:
        tl = title.lower()
        return any(t in tl for t in title_terms)
    return False


# --------------------------------------------------------------------------- #
# Public container
# --------------------------------------------------------------------------- #
@dataclass
class MINDData:
    """Ingested MIND: a click :class:`Dataset` + aligned item ideology metadata.

    Every metadata array is aligned to ``dataset.item_ids`` (one entry per column
    of the click matrix).
    """

    dataset: Dataset
    categories: np.ndarray
    subcategories: np.ndarray
    titles: np.ndarray
    outlets: np.ndarray
    political: np.ndarray            # bool mask
    item_positions: np.ndarray       # float in [-2, 2]; NaN if outlet/lean unknown
    user_positions: np.ndarray | None = None   # set by fit_ideology / with_ideology

    @property
    def n_users(self) -> int:
        return self.dataset.n_users

    @property
    def n_items(self) -> int:
        return self.dataset.n_items

    def summary(self) -> dict:
        pos = self.item_positions
        known = ~np.isnan(pos)
        nnz = self.dataset.matrix.nnz
        return {
            "users": self.n_users,
            "items": self.n_items,
            "clicks": int(nnz),
            "sparsity": nnz / max(self.n_users * self.n_items, 1),
            "political_items": int(self.political.sum()),
            "items_with_lean": int(known.sum()),
            "political_with_lean": int((self.political & known).sum()),
            "lean_hist": {int(v): int(c) for v, c in
                          zip(*np.unique(pos[known].astype(int), return_counts=True))},
        }

    def user_positions_from_clicks(self, fill: float = np.nan) -> np.ndarray:
        """Heuristic user position = mean lean of the user's known-lean clicks.

        A quick proxy for ``theta``; the principled route is
        :meth:`rwe.IdeologyModel.fit`.  Users with no known-lean click get ``fill``.
        """
        A = self.dataset.matrix.tocsr()
        known = ~np.isnan(self.item_positions)
        pos = np.where(known, self.item_positions, 0.0)
        num = np.asarray(A.multiply(pos[None, :]).sum(axis=1)).ravel()
        den = np.asarray(A.multiply(known.astype(float)[None, :]).sum(axis=1)).ravel()
        out = np.full(self.n_users, fill, dtype=float)
        nz = den > 0
        out[nz] = num[nz] / den[nz]
        return out

    def political_subset(self, require_lean: bool = True,
                         drop_empty_users: bool = True) -> "MINDData":
        """Restrict to political items (optionally only those with a known lean)."""
        mask = self.political.copy()
        if require_lean:
            mask &= ~np.isnan(self.item_positions)
        cols = np.flatnonzero(mask)
        A = self.dataset.matrix.tocsr()[:, cols]
        user_ids = self.dataset.user_ids
        keep = np.ones(A.shape[0], dtype=bool)
        if drop_empty_users:
            keep = np.asarray(A.sum(axis=1)).ravel() > 0
            A, user_ids = A[keep], user_ids[keep]
        up = self.user_positions[keep] if self.user_positions is not None else None
        ds = Dataset(matrix=A.tocsr(), user_ids=user_ids,
                     item_ids=self.dataset.item_ids[cols])
        return MINDData(ds, self.categories[cols], self.subcategories[cols],
                        self.titles[cols], self.outlets[cols],
                        self.political[cols], self.item_positions[cols], up)

    def fit_ideology(self, *, model=None, orient_by_lean=True, max_cells=5e7,
                     **fit_kwargs) -> "IdeologyFit":
        """Estimate latent 1-D positions from click behaviour alone (no outlets).

        Fits the elite-only ideal-point model (:class:`rwe.IdeologyModel`) on the
        ``users × articles`` click matrix: users get ``theta``, articles get
        ``phi`` on a shared, standardised latent scale. The recovered axis is
        whatever drives co-clicking; on a **political subset** that is usually
        ideology — but verify. If some items already carry an outlet lean,
        ``lean_corr`` reports how well the learned axis matches it, and the axis
        is sign-flipped to agree (otherwise the global sign is arbitrary).

        Typically called on ``political_subset(require_lean=False)`` and after
        ``min_*_clicks`` filtering — the model is dense ``O(users × items)`` per
        iteration. ``fit_kwargs`` (``n_iter``, ``lr``, ``lam``, ``mu``, ``seed``)
        pass through to :class:`rwe.IdeologyModel`.
        """
        m, n = self.dataset.matrix.shape
        if m * n > max_cells:
            raise ValueError(
                f"click matrix is {m}×{n} = {m * n:.0f} cells (> max_cells="
                f"{max_cells:.0f}); the ideal-point model is dense. Filter first "
                "(political_subset, min_user_clicks/min_item_clicks) or raise max_cells.")
        mdl = model or IdeologyModel(**fit_kwargs)
        res = mdl.fit(self.dataset.matrix)        # elite-only: phi == item positions
        theta, item_pos = res.theta, res.phi
        lean_corr = None
        known = ~np.isnan(self.item_positions)
        if orient_by_lean and known.sum() >= 3:
            a, b = item_pos[known], self.item_positions[known]
            if a.std() > 0 and b.std() > 0:
                c = float(np.corrcoef(a, b)[0, 1])
                if not np.isnan(c):
                    if c < 0:                      # align latent axis with L..R lean
                        theta, item_pos, c = -theta, -item_pos, -c
                    lean_corr = c
        return IdeologyFit(user_positions=theta, item_positions=item_pos,
                           lean_corr=lean_corr, result=res)

    def with_ideology(self, fit: "IdeologyFit") -> "MINDData":
        """Return a copy whose user/item positions come from a :meth:`fit_ideology`."""
        return replace(self, item_positions=fit.item_positions,
                       user_positions=fit.user_positions)

    def recommender_inputs(self, user_fill: float = 0.0):
        """Prep for evaluation: ``(Dataset, user_positions, item_positions)``.

        Drops items whose ideological position is unknown (``NaN``) and then users
        left with no clicks, so :class:`rwe.RWEB` and the ideological metrics are
        well-defined.  Uses the stored ``user_positions`` (e.g. from
        :meth:`fit_ideology`) when present, else the click-mean proxy
        :meth:`user_positions_from_clicks`.
        """
        item_pos = np.asarray(self.item_positions, dtype=float)
        theta = (self.user_positions if self.user_positions is not None
                 else self.user_positions_from_clicks(fill=user_fill))
        theta = np.nan_to_num(np.asarray(theta, dtype=float), nan=user_fill)
        A = self.dataset.matrix.tocsr()
        item_ids = self.dataset.item_ids
        finite = np.isfinite(item_pos)
        if not finite.all():
            cols = np.flatnonzero(finite)
            A, item_pos, item_ids = A[:, cols], item_pos[cols], item_ids[cols]
        keep = np.asarray(A.sum(axis=1)).ravel() > 0
        A, theta, user_ids = A[keep].tocsr(), theta[keep], self.dataset.user_ids[keep]
        return Dataset(A, user_ids, item_ids), theta, item_pos

    def save(self, path) -> None:
        """Save to a ``.npz`` (matrix + aligned metadata, incl. user positions)."""
        A = self.dataset.matrix.tocsr()
        has_up = self.user_positions is not None
        np.savez(path, data=A.data, indices=A.indices, indptr=A.indptr,
                 shape=A.shape, user_ids=self.dataset.user_ids,
                 item_ids=self.dataset.item_ids, categories=self.categories,
                 subcategories=self.subcategories, titles=self.titles,
                 outlets=self.outlets, political=self.political,
                 item_positions=self.item_positions,
                 user_positions=self.user_positions if has_up else np.zeros(0),
                 has_user_positions=np.array([has_up]))

    @classmethod
    def load(cls, path) -> "MINDData":
        z = np.load(path, allow_pickle=True)
        A = sp.csr_matrix((z["data"], z["indices"], z["indptr"]), shape=tuple(z["shape"]))
        ds = Dataset(matrix=A, user_ids=z["user_ids"], item_ids=z["item_ids"])
        up = (z["user_positions"] if "has_user_positions" in z
              and bool(z["has_user_positions"][0]) else None)
        return cls(ds, z["categories"], z["subcategories"], z["titles"],
                   z["outlets"], z["political"], z["item_positions"], up)


@dataclass
class IdeologyFit:
    """Output of :meth:`MINDData.fit_ideology`.

    ``user_positions`` (``theta``) and ``item_positions`` (``phi``) are on a shared
    standardised latent scale. ``lean_corr`` is the correlation of the learned item
    axis with any known outlet leans (``None`` if no leans were available); a value
    near ±1 is reassurance that the latent axis really is ideological. ``result`` is
    the full :class:`rwe.IdeologyResult` (``alpha``/``beta``/objective ``history``).
    """

    user_positions: np.ndarray
    item_positions: np.ndarray
    lean_corr: float | None
    result: IdeologyResult


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def load_mind(mind_dir, *, source_map=None, lean=None,
              political_terms=DEFAULT_POLITICAL_TERMS, title_terms=None,
              include_impressions=True, min_user_clicks=1, min_item_clicks=1) -> MINDData:
    """Ingest a MIND directory containing ``news.tsv`` and ``behaviors.tsv``.

    Parameters
    ----------
    mind_dir:
        Directory with ``news.tsv`` and ``behaviors.tsv`` (a MIND split).
    source_map:
        Optional news-id -> outlet mapping (dict or 2-column file) used for the
        outlet-lean join.  Required for non-NaN ``item_positions`` (MIND URLs are
        MSN URLs and do not carry the publisher).
    lean:
        Optional outlet -> position dict (normalised keys).  Defaults to
        :data:`DEFAULT_LEAN` (illustrative -- replace for a paper).
    political_terms / title_terms:
        Sub-category (and optional title) substrings marking political articles.
    include_impressions:
        Count positive impression tokens as clicks in addition to history.
    min_user_clicks / min_item_clicks:
        Iterative minimum-degree filtering thresholds.
    """
    mind_dir = Path(mind_dir)
    lean = DEFAULT_LEAN if lean is None else lean
    smap = _load_source_map(source_map)

    news = _read_news(mind_dir / "news.tsv")
    users, items = _read_clicks(mind_dir / "behaviors.tsv", include_impressions)
    users, items = _filter_min(users, items, min_user_clicks, min_item_clicks)
    if len(users) == 0:
        raise ValueError("no clicks left after filtering; lower the thresholds.")
    ds = from_interactions(users, items)
    ds.matrix.data[:] = 1.0          # binary implicit feedback

    cats, subs, titles, outlets, political, positions = [], [], [], [], [], []
    for nid in ds.item_ids:
        cat, sub, title, url = news.get(nid, ("", "", "", ""))
        outlet = smap.get(nid, "") or _outlet_from_url(url)
        cats.append(cat)
        subs.append(sub)
        titles.append(title)
        outlets.append(outlet)
        political.append(_is_political(sub, title, political_terms, title_terms))
        positions.append(lean.get(_norm(outlet), np.nan) if outlet else np.nan)

    return MINDData(
        dataset=ds,
        categories=np.array(cats, dtype=object),
        subcategories=np.array(subs, dtype=object),
        titles=np.array(titles, dtype=object),
        outlets=np.array(outlets, dtype=object),
        political=np.array(political, dtype=bool),
        item_positions=np.array(positions, dtype=float),
    )
