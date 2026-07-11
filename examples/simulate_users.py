"""Agent-based synthetic-user simulator -- an internal PRODUCT PoC, NOT research evidence.

Generates realistic reading sessions over a REAL article catalog (e.g. Qbias, with gold
AllSides lean + real outlets + topics) so the whole product -- RWE recommender, Information
Health Report, AI Coach, recommendation eval, user metrics -- can be stress-tested and
iterated on before real users exist. **Items are real; users and their clicks are
synthetic.** Every output is stamped SIMULATION; none of it is evidence for the paper.
Once the MVP has real traffic, these synthetic interactions are replaced with real ones.

Each agent has independent characteristics -- political viewpoint, per-topic interests,
openness to opposing views, per-outlet trust, article-quality preference, curiosity/novelty
seeking, activity level, reading speed, and save/share/ignore propensities -- sampled from
plausible population distributions. In a session the agent is shown an organic slate
(popularity x topic relevance) and clicks each item with a probability from a utility model
combining those traits with the article's lean/outlet/topic/quality; clicks get a dwell time
and a save/share/ignore action driven by satisfaction. Openness widens the ideology kernel,
so open agents click cross-cutting content -- the signal the closed-loop AdaptiveRWEB uses.

Outputs (all prefixed ``--tag`` = "sim"):
  * ``sim_users.npz``            -- a MINDData (drop into eval_mind / health_report /
    narrate_report unchanged); item_positions = GOLD lean, user_positions = the TRUE
    synthetic viewpoints (we generated them).
  * ``sim_population.csv``       -- per-user traits + realised metrics (the "user metrics").
  * ``sim_satisfaction_probe.csv`` -- probe-shaped (cross_welcomed_frac from save/share vs
    ignore on cross-cutting clicks) so ``adaptive_satisfaction.py`` closes the loop.
  * ``sim_behaviors.tsv``        -- MIND-format shown-vs-clicked slates -> **Open-Mindedness**
    (a real behavioural metric on the sim's impressions).
  * ``sim_register.csv`` / ``sim_emotion.csv`` -- SYNTHETIC per-article reporting / emotion
    attributes (like ``quality``) -> **Reporting Ratio** / **Emotional Balance**.
  * ``sim_MANIFEST.txt``         -- config + seed + the SIMULATION stamp.

    python examples/simulate_users.py --qbias allsides_balanced_news_headlines-texts.csv \\
        --n-users 3000 --max-items 5000 --out-tag sim
    python examples/eval_mind.py --npz sim_users.npz --no-bprmf
    python examples/health_report.py --npz sim_users.npz --sample 3 --require-political \\
        --behaviors sim_behaviors.tsv --register-csv sim_register.csv \\
        --emotion-csv sim_emotion.csv --subject-label '(simulated) reading diet' \\
        --axis-note 'on the AllSides gold-lean axis (not text-inferred)'
    python examples/adaptive_satisfaction.py --npz sim_users.npz --probe-csv sim_satisfaction_probe.csv
"""

import argparse
import ast
import csv as _csv
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from rwe.data import Dataset
from rwe.mind import MINDData

BANNER = ("=" * 74 + "\n"
          "  SYNTHETIC USERS -- internal product PoC. NOT research evidence.\n"
          "  Items are real; users + all interactions are simulated.\n"
          + "=" * 74)

EMOTION_LABELS = ("fear", "outrage", "analysis", "positive", "neutral")


def _synth_enrichments(positions, rng):
    """SYNTHETIC per-article enrichments (product-PoC only, like ``quality`` -- swappable
    for the real ``classify_register`` / ``classify_emotion`` on real text): a
    reporting-vs-opinion probability and an emotion-bucket share vector over
    ``EMOTION_LABELS``, where fear/outrage skew up with |lean| (extremer pieces run hotter)."""
    n = len(positions)
    absl = np.abs(np.asarray(positions, dtype=float))
    register = rng.beta(3.0, 2.0, n)                     # mostly reporting, some opinion
    alpha = np.stack([1.0 + 0.8 * absl, 0.8 + 0.8 * absl, np.full(n, 2.5),
                      np.full(n, 1.5), 2.0 - 0.4 * absl], axis=1)      # (n, 5)
    g = rng.gamma(np.clip(alpha, 0.05, None))            # Dirichlet via the gamma trick
    return register, g / g.sum(axis=1, keepdims=True)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


@dataclass
class SimConfig:
    n_users: int = 2000
    max_items: int = 5000            # cap the catalog (denser matrix, faster demo)
    sessions_lambda: float = 8.0     # base sessions/user, scaled by activity
    slate_size: int = 12
    click_bias: float = -1.3         # base logit: most slate items go unclicked
    w_ideo: float = 1.3              # ideology (dis)alignment, gated by openness
    w_topic: float = 1.1             # topic interest
    w_trust: float = 0.9             # per-outlet trust
    w_quality: float = 0.7           # article quality x quality preference
    w_novelty: float = 0.6           # curiosity x novelty (unseen topic/outlet)
    seed: int = 0


@dataclass
class ItemCatalog:
    positions: np.ndarray            # GOLD lean per article [-2, 2]
    outlets: np.ndarray              # publisher name
    topics: np.ndarray               # topic / category
    quality: np.ndarray              # SYNTHETIC latent quality in [0, 1]
    titles: np.ndarray               # headline
    ids: np.ndarray
    register: np.ndarray = field(default=None)      # SYNTHETIC P(reporting) per article
    emotion: np.ndarray = field(default=None)       # SYNTHETIC (n, 5) emotion-bucket shares
    political: np.ndarray = field(default=None)     # REAL per-article flag (None -> caller default)
    topic_idx: np.ndarray = field(default=None)     # topic -> compact index
    outlet_idx: np.ndarray = field(default=None)
    topic_names: np.ndarray = field(default=None)
    outlet_names: np.ndarray = field(default=None)

    def __post_init__(self):
        self.topic_names, self.topic_idx = np.unique(self.topics, return_inverse=True)
        self.outlet_names, self.outlet_idx = np.unique(self.outlets, return_inverse=True)

    @property
    def n(self):
        return len(self.positions)


def synthetic_catalog(n_items=800, n_outlets=25, n_topics=8, seed=0) -> ItemCatalog:
    """A fully synthetic catalog (no Qbias needed) -- for tests and standalone demos."""
    rng = np.random.default_rng(seed)
    outlet_lean = rng.uniform(-2, 2, n_outlets)          # each outlet has a house lean
    oi = rng.integers(0, n_outlets, n_items)
    pos = np.clip(outlet_lean[oi] + rng.normal(0, 0.4, n_items), -2, 2)   # article near its outlet
    reg, emo = _synth_enrichments(pos, rng)
    return ItemCatalog(
        positions=pos,
        outlets=np.array([f"outlet_{o}" for o in oi], dtype=object),
        topics=np.array([f"topic_{t}" for t in rng.integers(0, n_topics, n_items)], dtype=object),
        quality=rng.beta(2.5, 2.5, n_items),
        titles=np.array([f"synthetic headline {i}" for i in range(n_items)], dtype=object),
        ids=np.array([f"S{i}" for i in range(n_items)], dtype=object),
        register=reg, emotion=emo)


def _first_tag(raw) -> str:
    """Qbias's ``tags`` column is a *stringified* Python list, e.g.
    ``"['White House', 'Politics']"`` -> the first tag as a clean topic string.

    An empty/missing tags cell yields ``""`` — uncategorized stays uncategorized (Commit R2).
    The old ``"general"`` default synthesized a topic that surfaced in explanations ("General has
    been one of your most-read topics") while the same articles rendered blank elsewhere."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parsed = ast.literal_eval(raw)                   # handles "['a', 'b']" and "['a']"
        if isinstance(parsed, (list, tuple)):
            return (str(parsed[0]).strip() if parsed else "")
        return str(parsed).strip()
    except (ValueError, SyntaxError):                    # plain string or odd format
        return raw.split(",")[0].strip().strip("[]'\"")


def _parse_political(raw) -> "bool | None":
    """The CSV ``political`` cell: "1"/"true" -> True, "0"/"false" -> False, ""/unknown -> None."""
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def catalog_from_qbias(csv_path, max_items=None, seed=0) -> ItemCatalog:
    """Build a catalog from Qbias (real gold lean + real outlets + topic tags). Article
    *quality* is SYNTHETIC (Qbias has no quality label) -- a per-article Beta draw.

    ``political`` is REAL per-article classification: the ``political`` column when present
    (the feed exporter writes the scored flag), else derived from tags + url via the shared
    ``ingest.looks_political`` heuristic — never assumed. This is the mask the Information
    Health metrics and the cross-cutting gate consume, so a promo or sports article can no
    longer count as political just because its outlet has a house lean."""
    import sys
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from validate_qbias import _pick_col, label_to_pos, _HEADLINE_COLS, _BIAS_COLS, _OUTLET_COLS
    from ingest import looks_political

    rng = np.random.default_rng(seed)
    pos, outlets, topics, titles, ids, political = [], [], [], [], [], []
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        rd = _csv.DictReader(f)
        hc = _pick_col(rd.fieldnames, _HEADLINE_COLS)
        bc = _pick_col(rd.fieldnames, _BIAS_COLS)
        oc = _pick_col(rd.fieldnames, _OUTLET_COLS)
        tc = _pick_col(rd.fieldnames, ("tags", "topic", "topics", "tag"))
        pc = _pick_col(rd.fieldnames, ("political",))
        uc = _pick_col(rd.fieldnames, ("url", "link"))
        for i, row in enumerate(rd):
            g = label_to_pos(row.get(bc, ""))
            if not np.isfinite(g):
                continue
            pos.append(g)
            outlets.append((row.get(oc) or "unknown").strip() or "unknown")
            topics.append(_first_tag(row.get(tc)) if tc else "")   # clean first tag; "" = untagged
            titles.append((row.get(hc) or "").strip())
            ids.append(f"Q{i}")
            flag = _parse_political(row.get(pc)) if pc else None
            if flag is None:                                # unknown -> derive, never assume
                flag = looks_political(url=(row.get(uc) or "" if uc else ""),
                                       category=str(row.get(tc) or "") if tc else "")
            political.append(bool(flag))
    pos = np.asarray(pos, dtype=float)
    political = np.asarray(political, dtype=bool)
    if max_items and len(pos) > max_items:                # random subsample for a denser demo
        keep = rng.choice(len(pos), size=max_items, replace=False)
        pos, outlets, topics, titles, ids, political = (
            pos[keep], [outlets[k] for k in keep], [topics[k] for k in keep],
            [titles[k] for k in keep], [ids[k] for k in keep], political[keep])
    reg, emo = _synth_enrichments(pos, rng)                            # SYNTHETIC register + emotion
    return ItemCatalog(positions=pos, outlets=np.asarray(outlets, dtype=object),
                       topics=np.asarray(topics, dtype=object),
                       quality=rng.beta(2.5, 2.5, len(pos)),           # SYNTHETIC quality
                       titles=np.asarray(titles, dtype=object),
                       ids=np.asarray(ids, dtype=object), register=reg, emotion=emo,
                       political=political)


@dataclass
class Population:
    theta: np.ndarray                # true political viewpoint [-2, 2]
    openness: np.ndarray             # [0, 1] tolerance of opposing views
    curiosity: np.ndarray            # [0, 1] novelty seeking
    quality_pref: np.ndarray         # [0, 1] sensitivity to article quality
    activity: np.ndarray             # [0, 1] scales #sessions
    reading_speed: np.ndarray        # dwell multiplier (~1)
    save_prop: np.ndarray            # base P(save)
    share_prop: np.ndarray           # base P(share)
    topic_interest: np.ndarray       # (U, T)
    outlet_trust: np.ndarray         # (U, O)


def sample_population(cat: ItemCatalog, cfg: SimConfig) -> Population:
    """Draw ``cfg.n_users`` agents with independent, plausibly-distributed traits."""
    rng = np.random.default_rng(cfg.seed)
    U = cfg.n_users
    T, O = len(cat.topic_names), len(cat.outlet_names)
    # viewpoint: a bimodal electorate (left / right clusters + a smaller centre)
    comp = rng.choice(3, size=U, p=[0.4, 0.4, 0.2])
    mean = np.array([-1.0, 1.0, 0.0])[comp]
    sd = np.array([0.55, 0.55, 0.45])[comp]
    theta = np.clip(rng.normal(mean, sd), -2, 2)

    openness = rng.beta(2.0, 4.0, U)                    # skewed low -- most people are not very open
    curiosity = rng.beta(2.0, 3.0, U)
    quality_pref = rng.beta(3.0, 2.0, U)
    activity = rng.beta(1.6, 3.0, U)                    # long-ish tail: many light, few heavy
    reading_speed = np.exp(rng.normal(0.0, 0.35, U))
    save_prop = rng.beta(1.5, 8.0, U)
    share_prop = rng.beta(1.2, 12.0, U)
    topic_interest = rng.dirichlet(np.ones(T) * 0.6, size=U)   # peaky per-user topic taste

    # per-outlet trust: congenial outlets (lean near theta) trusted more; openness widens it.
    outlet_lean = np.array([np.nanmean(cat.positions[cat.outlet_idx == o]) for o in range(O)])
    dist = np.abs(theta[:, None] - outlet_lean[None, :])                # (U, O)
    outlet_trust = _sigmoid(-(1.6 - openness[:, None]) * dist + rng.normal(0, 0.35, (U, O)))
    return Population(theta, openness, curiosity, quality_pref, activity, reading_speed,
                      save_prop, share_prop, topic_interest, outlet_trust)


def simulate(cat: ItemCatalog, pop: Population, cfg: SimConfig):
    """Run reading sessions -> event list ``(user, item, dwell, action)`` (action in
    ``{ignore, save, share}``). Organic slates (popularity x topic relevance); clicks from
    the utility model; curiosity rewards topics/outlets outside the user's history."""
    rng = np.random.default_rng(cfg.seed + 1)
    n = cat.n
    T = len(cat.topic_names)
    pop_prior = rng.pareto(1.5, n) + 1.0                # long-tail article popularity
    events, impressions = [], []
    for u in range(cfg.n_users):
        item_pref = pop.topic_interest[u][cat.topic_idx]                 # (n,) this user's topic taste
        base_rel = pop_prior * (0.3 + item_pref)
        n_sessions = rng.poisson(cfg.sessions_lambda * (0.15 + pop.activity[u]))
        seen_t, seen_o = set(), set()                    # cumulative history (for novelty)
        for _ in range(int(n_sessions)):
            k = min(cfg.slate_size, n)
            slate = rng.choice(n, size=k, replace=False, p=base_rel / base_rel.sum())
            ti, oi = cat.topic_idx[slate], cat.outlet_idx[slate]
            u_ideo = -np.abs(pop.theta[u] - cat.positions[slate]) * (1.0 - pop.openness[u])
            u_topic = pop.topic_interest[u][ti] * T                      # ~O(1)
            u_trust = pop.outlet_trust[u][oi] - 0.5
            u_qual = (cat.quality[slate] - 0.5) * pop.quality_pref[u]
            novel = np.array([(t not in seen_t) or (o not in seen_o) for t, o in zip(ti, oi)],
                             dtype=float)
            logit = (cfg.click_bias + cfg.w_ideo * u_ideo + cfg.w_topic * u_topic
                     + cfg.w_trust * u_trust + cfg.w_quality * u_qual
                     + cfg.w_novelty * pop.curiosity[u] * novel)
            clicked = rng.random(k) < _sigmoid(logit)
            impressions.append((u, list(zip(slate.tolist(), clicked.tolist()))))   # shown slate
            for j in np.flatnonzero(clicked):
                it = int(slate[j])
                align = 1.0 - np.abs(pop.theta[u] - cat.positions[it]) / 4.0     # [0,1]
                sat = np.clip(0.5 * align + 0.5 * cat.quality[it], 0, 1)
                dwell = max(2.0, rng.gamma(2.0, 1.0) * pop.reading_speed[u] * (0.5 + sat) * 20)
                p_share = pop.share_prop[u] * (0.3 + 1.4 * sat)
                p_save = pop.save_prop[u] * (0.3 + 1.4 * sat)
                r = rng.random()
                action = "share" if r < p_share else "save" if r < p_share + p_save else "ignore"
                events.append((u, it, float(dwell), action))
                seen_t.add(int(cat.topic_idx[it]))
                seen_o.add(int(cat.outlet_idx[it]))
    return events, impressions


def build_dataset(events, cat: ItemCatalog, cfg: SimConfig, pop: Population) -> MINDData:
    """Events -> a MINDData with a binary click matrix (all catalog items as columns),
    GOLD item positions, and user_positions = the TRUE synthetic viewpoints."""
    n = cat.n
    rows = np.array([e[0] for e in events], dtype=np.int64)
    cols = np.array([e[1] for e in events], dtype=np.int64)
    M = sp.coo_matrix((np.ones(rows.size), (rows, cols)),
                      shape=(cfg.n_users, n)).tocsr()
    M.sum_duplicates()
    M.data[:] = 1.0
    ds = Dataset(matrix=M, user_ids=np.array([f"sim_u{i}" for i in range(cfg.n_users)],
                                             dtype=object), item_ids=cat.ids.astype(object))
    # REAL per-article political mask when the catalog carries one (qbias/feed corpora);
    # all-True only for purely synthetic catalogs that never classified their items.
    political = (np.asarray(cat.political, dtype=bool) if cat.political is not None
                 else np.ones(n, dtype=bool))
    return MINDData(dataset=ds, categories=cat.topics.astype(object),
                    subcategories=cat.topics.astype(object), titles=cat.titles.astype(object),
                    outlets=cat.outlets.astype(object), political=political,
                    item_positions=cat.positions.astype(float),
                    user_positions=pop.theta.astype(float))


def population_metrics(events, cat: ItemCatalog, pop: Population, cfg: SimConfig):
    """Per-user trait + realised-behaviour rows (the 'user metrics'), and probe-shaped
    cross-cutting reception rows for the closed loop. Returns ``(metric_rows, probe_rows)``."""
    U = cfg.n_users
    clicks = [[] for _ in range(U)]                      # (item, dwell, action) per user
    for u, it, dwell, action in events:
        clicks[u].append((it, dwell, action))
    metric_rows, probe_rows = [], []
    for u in range(U):
        side = int(np.sign(pop.theta[u])) or 0
        ev = clicks[u]
        n_click = len(ev)
        cross = [(it, d, a) for (it, d, a) in ev
                 if side != 0 and np.sign(cat.positions[it]) == -side and abs(cat.positions[it]) >= 0.5]
        cross_n = len(cross)
        cross_welcomed = np.mean([a in ("save", "share") for (_, _, a) in cross]) if cross else float("nan")
        saves = np.mean([a == "save" for (_, _, a) in ev]) if ev else float("nan")
        shares = np.mean([a == "share" for (_, _, a) in ev]) if ev else float("nan")
        metric_rows.append(dict(
            user=f"sim_u{u}", viewpoint=round(float(pop.theta[u]), 3),
            openness=round(float(pop.openness[u]), 3), curiosity=round(float(pop.curiosity[u]), 3),
            quality_pref=round(float(pop.quality_pref[u]), 3), activity=round(float(pop.activity[u]), 3),
            n_clicks=n_click, cross_cutting=cross_n,
            cross_cutting_rate=round(cross_n / n_click, 3) if n_click else 0.0,
            mean_dwell=round(float(np.mean([d for (_, d, _) in ev])), 1) if ev else 0.0,
            save_rate=round(float(saves), 3), share_rate=round(float(shares), 3)))
        if cross_n:                                      # probe row: reception of cross-cutting
            probe_rows.append(dict(
                user=f"sim_u{u}", side=side, cross_n=cross_n,
                cross_upvoted_frac=round(float(cross_welcomed), 3),
                cross_welcomed_frac=round(float(cross_welcomed), 3),
                cross_reply_frac=""))
    return metric_rows, probe_rows


def run(cfg: SimConfig, qbias=None):
    cat = catalog_from_qbias(qbias, max_items=cfg.max_items, seed=cfg.seed) if qbias \
        else synthetic_catalog(n_items=cfg.max_items, seed=cfg.seed)
    pop = sample_population(cat, cfg)
    events, impressions = simulate(cat, pop, cfg)
    mind = build_dataset(events, cat, cfg, pop)
    metric_rows, probe_rows = population_metrics(events, cat, pop, cfg)
    return cat, pop, events, impressions, mind, metric_rows, probe_rows


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_behaviors_tsv(path, impressions, cat):
    """MIND-format behaviors.tsv: each shown slate as ``<id>-1`` (clicked) / ``<id>-0`` (not),
    so ``health_report.py --behaviors`` computes Open-Mindedness (cross-cutting click-through
    of the *shown* opposite-side items -- a real behavioural metric on the sim's slates)."""
    with open(path, "w", encoding="utf-8") as f:
        for k, (u, slate) in enumerate(impressions):
            imps = " ".join(f"{cat.ids[it]}-{1 if c else 0}" for it, c in slate)
            f.write(f"imp{k}\tsim_u{u}\t\t\t{imps}\n")


def write_enrichment_csvs(register_path, emotion_path, cat):
    """SYNTHETIC per-article register (reporting) + emotion CSVs keyed by the sim item ids, so
    ``--register-csv`` / ``--emotion-csv`` populate Reporting Ratio / Emotional Balance."""
    with open(register_path, "w", encoding="utf-8") as f:
        f.write("news_id,reporting\n")
        for nid, r in zip(cat.ids, cat.register):
            f.write(f"{nid},{r:.4f}\n")
    with open(emotion_path, "w", encoding="utf-8") as f:
        f.write("news_id," + ",".join(EMOTION_LABELS) + "\n")
        for nid, row in zip(cat.ids, cat.emotion):
            f.write(f"{nid}," + ",".join(f"{v:.4f}" for v in row) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--qbias", default=None,
                    help="Qbias allsides_balanced_news_*.csv (else a fully synthetic catalog)")
    ap.add_argument("--n-users", type=int, default=2000)
    ap.add_argument("--max-items", type=int, default=5000)
    ap.add_argument("--sessions", type=float, default=8.0, help="base sessions/user")
    ap.add_argument("--slate-size", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-tag", default="sim", help="output filename prefix")
    args = ap.parse_args()

    cfg = SimConfig(n_users=args.n_users, max_items=args.max_items,
                    sessions_lambda=args.sessions, slate_size=args.slate_size, seed=args.seed)
    print(BANNER)
    cat, pop, events, impressions, mind, metric_rows, probe_rows = run(cfg, qbias=args.qbias)
    tag = args.out_tag
    mind.save(f"{tag}_users.npz")
    _write_csv(f"{tag}_population.csv", metric_rows)
    _write_csv(f"{tag}_satisfaction_probe.csv", probe_rows)
    write_behaviors_tsv(f"{tag}_behaviors.tsv", impressions, cat)       # -> Open-Mindedness
    write_enrichment_csvs(f"{tag}_register.csv", f"{tag}_emotion.csv", cat)  # -> Reporting/Emotional
    s = mind.summary()
    with open(f"{tag}_MANIFEST.txt", "w", encoding="utf-8") as f:
        f.write("SIMULATION -- synthetic users, NOT real behaviour, NOT research evidence.\n")
        f.write("register.csv / emotion.csv are SYNTHETIC article attributes (like quality); "
                "behaviors.tsv is the sim's real shown-vs-clicked slates.\n")
        f.write(f"catalog: {'Qbias ' + args.qbias if args.qbias else 'synthetic'}\n")
        f.write(f"config: {cfg}\n")
        f.write(f"summary: {s}\n")
    print(f"\nsimulated {cfg.n_users} users x {cat.n} items -> {len(events):,} clicks "
          f"({s['sparsity']*100:.2f}% dense), {len(impressions):,} slate impressions")
    print(f"wrote {tag}_users.npz, {tag}_population.csv, {tag}_satisfaction_probe.csv, "
          f"{tag}_behaviors.tsv, {tag}_register.csv, {tag}_emotion.csv, {tag}_MANIFEST.txt")
    print("\nnext (SIMULATION -- product PoC only):")
    print(f"  python examples/eval_mind.py --npz {tag}_users.npz --no-bprmf")
    print(f"  python examples/health_report.py --npz {tag}_users.npz --sample 3 --require-political \\\n"
          f"      --behaviors {tag}_behaviors.tsv --register-csv {tag}_register.csv "
          f"--emotion-csv {tag}_emotion.csv --subject-label '(simulated) reading diet' \\\n"
          f"      --axis-note 'on the AllSides gold-lean axis (not text-inferred)'")
    print(f"  python examples/adaptive_satisfaction.py --npz {tag}_users.npz "
          f"--probe-csv {tag}_satisfaction_probe.csv")


if __name__ == "__main__":
    main()
