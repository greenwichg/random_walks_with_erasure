"""preflight_embeddings.py — can THIS box carry Stage 1 (sentence embeddings at ingest)? Measured.

Stage 1 of docs/CLUSTERING_APPROACHES_RESEARCH.md §4 is the first dependency ever added to the
serving image (ONNX Runtime + a tokenizer + a multilingual encoder), on a t3.medium with 2 vCPU
and 4 GiB that already runs the engine, the web tier, the poller and a quadratic story build.
Every number this decision needs is measurable on the box itself in a few minutes, so none of
them is estimated: this installs the runtime into a THROWAWAY directory, downloads one candidate
model, loads it, encodes a sample of the real window's headlines, and reports what it saw
against bars fixed here before any number was read. It changes nothing: no image, no
environment, no table. Everything it installs lives under ``--target`` and is deleted at exit
unless ``--keep`` is passed.

    dc run --rm -T api python examples/preflight_embeddings.py --install          # the real run
    dc run --rm -T api python examples/preflight_embeddings.py --dry-run          # plan only
    python examples/preflight_embeddings.py --model-dir DIR                       # files in hand

The bars (registered here, not tuned to fit a result):

  image     deps (numpy excluded — already in the image) + model files   <= 500 MB
  resident  RSS growth from loading the session + tokenizer + one batch   <= 400 MB
  headroom  MemAvailable on the box WITH the model loaded                 >= 1024 MB
  encode    mean ms/article, ONE thread, batch 32, real headlines         <= 50 ms
  semantics cos(same event, paraphrased) - cos(different event)           >= 0.15
            cross-lingual same-event cosine (en/vi, en/ko), the smaller   >= 0.50

The semantics bars are the reason to run a real model rather than trust a paper: they are
the two things Stage 1 spends embeddings on (rejoining disjoint-vocabulary pieces, and the
cross-lingual bridge). A third cosine is printed and NOT barred — the eye-drops / fruit-bars
recall pair, the template trap: it will read HIGH, and that number is the standing reason
embeddings are a second channel and never sole evidence.

Exit codes: 0 = GO, 2 = NO-GO (a resource or model limitation, not an error), 1 = the preflight
itself could not determine the answer (no deps, no model, no rows). NO-GO is a legitimate,
expected outcome: the box staying healthy outranks Stage 1.

**Run 2026-09-02 on production: NO-GO** (record in docs/CLUSTERING_APPROACHES_RESEARCH.md §4,
Stage 1). The t3.medium had 1,183 MB available and 167 MB in swap at load 2.39 BEFORE the
model loaded; with it, 807 MB against the 1,024 MB bar, resident 414 MB against 400, encode
77.6 ms against 50. The model itself passed the paraphrase bar (margin 0.44) and missed the
cross-lingual bar by 0.01 on Korean. Stage 1 waits for an instance with headroom.
"""

from __future__ import annotations

import argparse
import math
import os
import resource
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MB = 1024 * 1024

#: The bars — registered before any number is seen.
BAR_IMAGE_MB = 500.0
BAR_RESIDENT_MB = 400.0
BAR_AVAIL_AFTER_MB = 1024.0
BAR_ENCODE_MS = 50.0
BAR_MARGIN = 0.15
BAR_XLING = 0.50

#: Projection constants, each with its source.
PER_DAY_ARTICLES = 5000        # docs/CAPACITY_AND_COST.md: ~5,000 net-new/day
CATALOG_CAP = 150000           # RWE_RETENTION_MAX_COUNT in deploy/.env.production.example
DOWNLOAD_CAP = 600 * MB        # no candidate is allowed to pull more than this

HF = "https://huggingface.co/{repo}/resolve/main/{path}"

#: Candidate encoders, tried in order; the first whose files download is measured. Every entry
#: is the same 118M-parameter multilingual MiniLM-L12 (384 dims, ~50 languages) in a different
#: packaging, plus one E5-small alternative that needs a "query: " prefix. ``needs`` are CPU
#: flags the quantised file requires — a VNNI kernel on a non-VNNI core is slower than fp32,
#: not faster, so the box's flags decide the order rather than a preference.
CANDIDATES = (
    {"name": "st-minilm-l12-int8-avx512vnni",
     "repo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
     "model": "onnx/model_qint8_avx512_vnni.onnx", "tokenizer": "tokenizer.json",
     "needs": ("avx512f", "avx512_vnni"), "prefix": ""},
    {"name": "st-minilm-l12-int8-avx512",
     "repo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
     "model": "onnx/model_qint8_avx512.onnx", "tokenizer": "tokenizer.json",
     "needs": ("avx512f",), "prefix": ""},
    {"name": "xenova-minilm-l12-int8",
     "repo": "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
     "model": "onnx/model_quantized.onnx", "tokenizer": "tokenizer.json",
     "needs": (), "prefix": ""},
    {"name": "qdrant-minilm-l12-q",
     "repo": "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
     "model": "model_optimized.onnx", "tokenizer": "tokenizer.json",
     "needs": (), "prefix": ""},
    {"name": "st-minilm-l12-fp32",
     "repo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
     "model": "onnx/model.onnx", "tokenizer": "tokenizer.json",
     "needs": (), "prefix": ""},
    {"name": "xenova-e5-small-int8",
     "repo": "Xenova/multilingual-e5-small",
     "model": "onnx/model_quantized.onnx", "tokenizer": "tokenizer.json",
     "needs": (), "prefix": "query: "},
)

#: The semantics exhibits — fixed text, so two runs of two models compare like for like.
SAME_A = "Mass shooting reported at Seattle Center; several people wounded"
SAME_B = "Gunfire erupts near Seattle Center, police say multiple people hurt"
DIFFERENT = "Seattle Kraken sign veteran goaltender to a two-year contract"
TEMPLATE_A = "Nearly 40,000 bottles of eye drops recalled over possible contamination nationwide"
TEMPLATE_B = "Frozen fruit bars recalled nationwide over possible glass contamination"
XLING_EN = "Lionel Messi announces retirement from international football"
XLING_VI = "Messi tuyên bố từ giã đội tuyển Argentina"
XLING_KO = "메시, 아르헨티나 대표팀 은퇴 선언"


# --------------------------------------------------------------------------- #
# Host facts — read, never estimated.
# --------------------------------------------------------------------------- #
def cpu_flags() -> "set[str]":
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("flags"):
                    return set(line.split(":", 1)[1].split())
    except Exception:                                    # noqa: BLE001 — non-Linux
        pass
    return set()


def meminfo() -> dict:
    """``/proc/meminfo`` in MB — MemTotal, MemAvailable, SwapTotal, SwapFree."""
    out: dict = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                out[k.strip()] = int(v.split()[0]) / 1024.0
    except Exception:                                    # noqa: BLE001
        pass
    return out


def rss_mb() -> float:
    """This process's resident set, from ``/proc/self/statm`` (pages × page size)."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / MB
    except Exception:                                    # noqa: BLE001
        return 0.0


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0   # Linux: kB


def dir_bytes(path: str, exclude_prefixes: tuple = ("numpy",)) -> int:
    """Bytes under ``path``, skipping top-level entries whose name starts with an excluded
    prefix — numpy is already in the image, so a ``pip --target`` copy of it is not growth."""
    total = 0
    if not os.path.isdir(path):
        return 0
    for entry in os.listdir(path):
        if any(entry.startswith(p) for p in exclude_prefixes):
            continue
        full = os.path.join(path, entry)
        if os.path.isfile(full):
            total += os.path.getsize(full)
            continue
        for root, _dirs, files in os.walk(full):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    return total


# --------------------------------------------------------------------------- #
# Dependencies and model files — into a throwaway directory.
# --------------------------------------------------------------------------- #
def install_deps(target: str, run: Callable = subprocess.run) -> dict:
    """``pip install`` ONNX Runtime (with its dependencies) and the tokenizers library
    (without — its only dependencies are hub-download conveniences this never uses) into
    ``target``. Returns seconds and the bytes that would be image growth (numpy excluded)."""
    t0 = time.perf_counter()
    base = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
            "--target", target]
    for extra in (["onnxruntime"], ["--no-deps", "tokenizers"]):
        r = run(base + extra, capture_output=True, text=True)
        if getattr(r, "returncode", 0) != 0:
            raise RuntimeError(f"pip failed: {' '.join(extra)}: "
                               f"{(getattr(r, 'stderr', '') or '')[-400:]}")
    if target not in sys.path:
        sys.path.insert(0, target)
    return {"seconds": time.perf_counter() - t0, "bytes": dir_bytes(target)}


def fetch_to(url: str, dest: str, cap: int = DOWNLOAD_CAP,
             opener: Callable = urllib.request.urlopen) -> int:
    """Stream ``url`` to ``dest``; refuse to exceed ``cap``. Returns bytes written."""
    req = urllib.request.Request(url, headers={"User-Agent": "hidden-view-preflight/1"})
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    written = 0
    with opener(req, timeout=120) as r, open(dest, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > cap:
                raise RuntimeError(f"download exceeds the {cap // MB} MB cap: {url}")
            out.write(chunk)
    return written


def select_candidates(flags: "set[str]", only: Optional[str] = None) -> list:
    """The candidates this CPU can run, in preference order; ``only`` narrows to one name."""
    out = []
    for c in CANDIDATES:
        if only and c["name"] != only:
            continue
        if all(f in flags for f in c["needs"]):
            out.append(c)
    return out


def obtain_model(candidates: list, model_dir: str, fetch: Callable = fetch_to) -> dict:
    """Download the first candidate whose files arrive (files already present are reused),
    recording why each earlier one was skipped. Returns ``{candidate, model, tokenizer,
    bytes, seconds, skipped}`` or raises when none could be obtained."""
    skipped = []
    for c in candidates:
        base = os.path.join(model_dir, c["name"])
        model_path = os.path.join(base, os.path.basename(c["model"]))
        tok_path = os.path.join(base, "tokenizer.json")
        t0 = time.perf_counter()
        total = 0
        try:
            for path, rel in ((model_path, c["model"]), (tok_path, c["tokenizer"])):
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    total += os.path.getsize(path)
                    continue
                total += fetch(HF.format(repo=c["repo"], path=rel), path)
        except Exception as e:                           # noqa: BLE001 — 404, cap, network
            skipped.append((c["name"], f"{type(e).__name__}: {str(e)[:120]}"))
            shutil.rmtree(base, ignore_errors=True)
            continue
        return {"candidate": c, "model": model_path, "tokenizer": tok_path, "bytes": total,
                "seconds": time.perf_counter() - t0, "skipped": skipped}
    raise RuntimeError("no candidate model could be obtained: "
                       + "; ".join(f"{n} ({why})" for n, why in skipped))


# --------------------------------------------------------------------------- #
# The encoder — mean pooling over the transformer's last hidden state, L2-normalised.
# --------------------------------------------------------------------------- #
def mean_pool(hidden, mask):
    """``[batch, seq, dim]`` token states × ``[batch, seq]`` attention mask -> ``[batch, dim]``:
    the sentence-transformers pooling these models were trained with. A position the mask
    excludes contributes nothing; an all-masked row divides by one, not by zero."""
    import numpy as np
    m = mask.astype(np.float32)[:, :, None]
    summed = (hidden * m).sum(axis=1)
    counts = np.maximum(m.sum(axis=1), 1.0)
    return summed / counts


def l2norm(x):
    import numpy as np
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.maximum(norms, 1e-12)


def cosine(a, b) -> float:
    import numpy as np
    return float(np.dot(l2norm(a[None, :])[0], l2norm(b[None, :])[0]))


class OnnxEncoder:
    """One session, one tokenizer, ``encode(texts) -> [n, dim]`` unit vectors.

    Inputs are fed by NAME from ``session.get_inputs()`` (``input_ids``, ``attention_mask``,
    and ``token_type_ids`` as zeros when the export asks for it), so the same code runs a BERT-
    or XLM-R-shaped export. Output 0 is pooled when it is token-level (rank 3) and used as-is
    when the export already pooled (rank 2)."""

    def __init__(self, model_path: str, tokenizer_path: str, *, threads: int = 1,
                 max_len: int = 128, prefix: str = ""):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.session.get_inputs()]
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        pad_id = 0
        for pad in ("<pad>", "[PAD]"):
            pid = self.tokenizer.token_to_id(pad)
            if pid is not None:
                pad_id = pid
                break
        self.tokenizer.enable_truncation(max_len)
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        self.prefix = prefix
        self.tokens_seen = 0
        self.dim: Optional[int] = None

    def encode(self, texts: list, batch: int = 32):
        import numpy as np
        out = []
        for i in range(0, len(texts), batch):
            chunk = [self.prefix + (t or "") for t in texts[i:i + batch]]
            encs = self.tokenizer.encode_batch(chunk)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            self.tokens_seen += int(mask.sum())
            feed = {}
            for name in self.input_names:
                if name == "input_ids":
                    feed[name] = ids
                elif name == "attention_mask":
                    feed[name] = mask
                elif name == "token_type_ids":
                    feed[name] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            vec = mean_pool(hidden, mask) if hidden.ndim == 3 else hidden
            out.append(l2norm(np.asarray(vec, dtype=np.float32)))
        stacked = np.concatenate(out, axis=0) if out else np.zeros((0, 0), dtype=np.float32)
        self.dim = int(stacked.shape[1]) if stacked.size else self.dim
        return stacked


# --------------------------------------------------------------------------- #
# The measurements.
# --------------------------------------------------------------------------- #
def sample_texts(store_, n: int) -> "tuple[list, dict]":
    """Every k-th row of the clustering window (deterministic — lowest index first, no RNG),
    as the text Stage 1 would encode: headline, then the dek's first 200 characters. Returns
    the texts and the language mix they came from, so the rate is known to be measured on the
    real headline mix rather than on English alone."""
    import story_service
    rows = story_service._fetch(store_)
    if not rows:
        return [], {}
    k = max(1, len(rows) // max(1, n))
    picked = rows[::k][:n]
    langs: dict = {}
    texts = []
    for r in picked:
        lang = ((r.get("language") or "?").strip().lower()[:2]) or "?"
        langs[lang] = langs.get(lang, 0) + 1
        dek = (r.get("description") or "").strip()
        texts.append((r.get("title") or "").strip() + (". " + dek[:200] if dek else ""))
    return texts, {"window": len(rows), "langs": langs}


def measure_encode(encoder, texts: list, batch: int = 32) -> dict:
    """Warm up on one batch (session initialisation is not the steady state), then time the
    whole sample. Returns mean ms/article, tokens/article and the elapsed seconds."""
    if not texts:
        return {"ms": None, "tokens": None, "seconds": 0.0, "n": 0}
    encoder.encode(texts[:min(batch, len(texts))], batch=batch)
    encoder.tokens_seen = 0
    t0 = time.perf_counter()
    encoder.encode(texts, batch=batch)
    seconds = time.perf_counter() - t0
    return {"ms": 1000.0 * seconds / len(texts), "tokens": encoder.tokens_seen / len(texts),
            "seconds": seconds, "n": len(texts)}


def sanity(encode: Callable) -> dict:
    """The semantics exhibits, as cosines."""
    v = encode([SAME_A, SAME_B, DIFFERENT, TEMPLATE_A, TEMPLATE_B, XLING_EN, XLING_VI, XLING_KO])
    return {
        "same": cosine(v[0], v[1]),
        "different": cosine(v[0], v[2]),
        "template": cosine(v[3], v[4]),
        "xling_vi": cosine(v[5], v[6]),
        "xling_ko": cosine(v[5], v[7]),
    }


def projections(ms_per_article: Optional[float], window: int, dim: Optional[int]) -> dict:
    ms = ms_per_article or 0.0
    d = dim or 0
    return {
        "cpuMinutesPerDay": PER_DAY_ARTICLES * ms / 60000.0,
        "backfillMinutes": window * ms / 60000.0,
        "windowMBFloat32": window * d * 4 / MB,
        "windowMBFloat16": window * d * 2 / MB,
        "catalogMBFloat32": CATALOG_CAP * d * 4 / MB,
        "catalogMBFloat16": CATALOG_CAP * d * 2 / MB,
    }


# --------------------------------------------------------------------------- #
# The decision — bars applied mechanically; hand-reading happens in the report, not here.
# --------------------------------------------------------------------------- #
def decide(rep: dict) -> "tuple[Optional[bool], list]":
    """``(go, checks)`` — ``go`` is None when a measurement the bars need is missing (the
    preflight could not determine the answer), else True/False. Each check is
    ``(name, ok, detail)``."""
    checks = []
    missing = False

    def bar(name, value, ok, detail):
        nonlocal missing
        if value is None:
            missing = True
            checks.append((name, None, f"not measured — {detail}"))
        else:
            checks.append((name, ok, detail))

    image = rep.get("imageMB")
    bar("image", image, image is not None and image <= BAR_IMAGE_MB,
        f"{image:.0f} MB (deps + model, numpy excluded) <= {BAR_IMAGE_MB:.0f} MB" if image is not None
        else "deps or model not installed")
    res = rep.get("residentMB")
    bar("resident", res, res is not None and res <= BAR_RESIDENT_MB,
        f"{res:.0f} MB RSS growth <= {BAR_RESIDENT_MB:.0f} MB" if res is not None else "model not loaded")
    avail = rep.get("availAfterMB")
    bar("headroom", avail, avail is not None and avail >= BAR_AVAIL_AFTER_MB,
        f"{avail:.0f} MB available with the model loaded >= {BAR_AVAIL_AFTER_MB:.0f} MB"
        if avail is not None else "/proc/meminfo unreadable")
    ms = (rep.get("encode1") or {}).get("ms")
    bar("encode", ms, ms is not None and ms <= BAR_ENCODE_MS,
        f"{ms:.1f} ms/article at 1 thread <= {BAR_ENCODE_MS:.0f} ms" if ms is not None
        else "no window rows to encode")
    sem = rep.get("semantics") or {}
    if sem:
        margin = sem["same"] - sem["different"]
        bar("margin", margin, margin >= BAR_MARGIN,
            f"cos(same) {sem['same']:.2f} - cos(different) {sem['different']:.2f} = {margin:.2f} "
            f">= {BAR_MARGIN:.2f}")
        xl = min(sem["xling_vi"], sem["xling_ko"])
        bar("cross-lingual", xl, xl >= BAR_XLING,
            f"min(en/vi {sem['xling_vi']:.2f}, en/ko {sem['xling_ko']:.2f}) = {xl:.2f} >= {BAR_XLING:.2f}")
    else:
        bar("margin", None, False, "model not loaded")
        bar("cross-lingual", None, False, "model not loaded")
    if missing:
        return None, checks
    return all(ok for _, ok, _ in checks), checks


def render(rep: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append("STAGE 1 PREFLIGHT — embeddings at ingest. Throwaway install; nothing is changed.")
    L.append("=" * 78)
    h = rep.get("host") or {}
    L.append("\n-- host --")
    L.append(f"  cpus            : {h.get('cpus')}   flags: "
             f"{' '.join(f for f in ('avx2', 'avx512f', 'avx512_vnni') if f in h.get('flags', ()))}"
             or "  cpus            : ?")
    L.append(f"  memory          : {h.get('memTotalMB', 0):.0f} MB total, "
             f"{h.get('memAvailMB', 0):.0f} MB available before anything is loaded")
    L.append(f"  swap            : {h.get('swapTotalMB', 0):.0f} MB total, "
             f"{h.get('swapUsedMB', 0):.0f} MB in use")
    L.append(f"  load average    : {h.get('load1', 0):.2f} (1-min)")
    L.append(f"  disk at target  : {h.get('diskFreeMB', 0):.0f} MB free")
    if rep.get("dryRun"):
        L.append("\n-- plan (dry run; nothing installed or downloaded) --")
        for c in rep.get("plan", []):
            L.append(f"  {c['name']:<32} {HF.format(repo=c['repo'], path=c['model'])}")
        L.append("\n  Run again with --install to measure.")
        return "\n".join(L)
    d = rep.get("deps")
    L.append("\n-- dependencies (pip --target, numpy excluded from the size) --")
    if d:
        L.append(f"  onnxruntime + tokenizers : {d['bytes'] / MB:.0f} MB installed in {d['seconds']:.0f}s"
                 + (f"   (onnxruntime {d.get('ortVersion')}, tokenizers {d.get('tokVersion')})"
                    if d.get("ortVersion") else ""))
    else:
        L.append(f"  {rep.get('depsError') or 'not installed (pass --install)'}")
    m = rep.get("model")
    L.append("\n-- model --")
    if m:
        c = m["candidate"]
        L.append(f"  {c['name']:<32} {c['repo']} / {c['model']}")
        L.append(f"  files           : {m['bytes'] / MB:.0f} MB, obtained in {m['seconds']:.0f}s")
        for name, why in m.get("skipped", []):
            L.append(f"  skipped {name:<24} {why}")
    else:
        L.append(f"  {rep.get('modelError') or 'not obtained'}")
    if rep.get("imageMB") is not None:
        L.append(f"  image growth    : {rep['imageMB']:.0f} MB (deps + model)")
    if rep.get("loadSeconds") is not None:
        L.append("\n-- load --")
        L.append(f"  session + tokenizer : {rep['loadSeconds']:.1f}s to create")
        L.append(f"  resident growth     : {rep['residentMB']:.0f} MB "
                 f"(RSS {rep['rssBeforeMB']:.0f} -> {rep['rssAfterMB']:.0f} MB; peak {rep['peakMB']:.0f} MB)")
        L.append(f"  box headroom        : MemAvailable {h.get('memAvailMB', 0):.0f} -> "
                 f"{rep['availAfterMB']:.0f} MB with the model loaded")
    s = rep.get("sample") or {}
    e1, e2 = rep.get("encode1"), rep.get("encode2")
    if e1:
        L.append("\n-- encode (real window headlines + dek, batch 32) --")
        langs = ", ".join(f"{k} {v}" for k, v in sorted(s.get("langs", {}).items(),
                                                      key=lambda kv: -kv[1])[:8])
        L.append(f"  sample          : {e1['n']} of {s.get('window', 0):,} window rows ({langs})")
        L.append(f"  1 thread        : {e1['ms']:.1f} ms/article, {e1['tokens']:.0f} tokens/article, "
                 f"{e1['seconds']:.1f}s")
        if e2:
            L.append(f"  2 threads       : {e2['ms']:.1f} ms/article, {e2['seconds']:.1f}s")
    sem = rep.get("semantics")
    if sem:
        L.append("\n-- semantics (fixed exhibits) --")
        L.append(f"  same event, paraphrased   : {sem['same']:.2f}   Seattle Center shooting, two wordings")
        L.append(f"  different event, same city: {sem['different']:.2f}   the Kraken signing")
        L.append(f"  template trap             : {sem['template']:.2f}   eye drops vs fruit bars, both "
                 f"'recalled nationwide' — NOT barred; the reason embeddings are never sole evidence")
        L.append(f"  cross-lingual en/vi       : {sem['xling_vi']:.2f}   Messi retirement")
        L.append(f"  cross-lingual en/ko       : {sem['xling_ko']:.2f}")
    p = rep.get("projections")
    if p and e1:
        L.append("\n-- projections (from the measured rate; window and catalog sizes are real) --")
        L.append(f"  ingest cost     : {p['cpuMinutesPerDay']:.1f} CPU-minutes/day at "
                 f"{PER_DAY_ARTICLES:,} articles/day, 1 thread")
        L.append(f"  backfill        : {p['backfillMinutes']:.0f} minutes for the {s.get('window', 0):,}-row "
                 f"window, one time")
        L.append(f"  storage         : window {p['windowMBFloat32']:.0f} MB float32 / "
                 f"{p['windowMBFloat16']:.0f} MB float16; {CATALOG_CAP:,}-row catalog "
                 f"{p['catalogMBFloat32']:.0f} / {p['catalogMBFloat16']:.0f} MB")
    go, checks = decide(rep)
    L.append("\n-- decision against the registered bars --")
    for name, ok, detail in checks:
        mark = "[ok]  " if ok else ("[ -- ]" if ok is None else "[FAIL]")
        L.append(f"  {mark} {name:<14} {detail}")
    L.append("\n-- verdict --")
    if go is None:
        L.append("  UNDETERMINED — a measurement the bars need is missing (see above). Fix the "
                 "cause and re-run; nothing is inferred.")
    elif go:
        L.append("  GO — this box carries Stage 1 within the registered bars. Next: encode at "
                 "ingest and store the vector, consuming nothing (the two-switch shape).")
    else:
        L.append("  NO-GO — a registered bar failed. This is a resource or model limitation, not "
                 "an error; do not tune the bar to fit. Options: a bigger instance, a smaller "
                 "encoder, or leaving Stage 1 open.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# The run.
# --------------------------------------------------------------------------- #
def host_facts(target: str, mem_reader: Callable = meminfo) -> dict:
    mem = mem_reader()
    try:
        load1 = os.getloadavg()[0]
    except Exception:                                    # noqa: BLE001
        load1 = 0.0
    try:
        free = shutil.disk_usage(target if os.path.isdir(target) else os.path.dirname(target) or "/").free / MB
    except Exception:                                    # noqa: BLE001
        free = 0.0
    return {"cpus": os.cpu_count() or 1, "flags": cpu_flags(),
            "memTotalMB": mem.get("MemTotal", 0.0), "memAvailMB": mem.get("MemAvailable", 0.0),
            "swapTotalMB": mem.get("SwapTotal", 0.0),
            "swapUsedMB": mem.get("SwapTotal", 0.0) - mem.get("SwapFree", 0.0),
            "load1": load1, "diskFreeMB": free}


def run(*, target: str, install: bool, only: Optional[str], model_dir: Optional[str],
        sample: int, batch: int, max_len: int, dry_run: bool, db: Optional[str] = None,
        installer: Optional[Callable] = None, fetch: Optional[Callable] = None,
        encoder_factory: Optional[Callable] = None, store_factory: Optional[Callable] = None,
        mem: Callable = meminfo, rss: Callable = rss_mb, peak: Callable = peak_rss_mb) -> dict:
    # Resolved at call time, not bound at definition, so a test (or a future caller) that
    # replaces the module-level installer/fetcher is honoured by `main()` too.
    installer = installer or install_deps
    fetch = fetch or fetch_to
    rep: dict = {"host": host_facts(target, mem), "target": target, "dryRun": dry_run}
    cands = select_candidates(rep["host"]["flags"], only)
    rep["plan"] = cands
    if dry_run:
        return rep
    os.makedirs(target, exist_ok=True)
    # 1. dependencies. The libraries are needed only by the real encoder; an injected
    # encoder_factory (tests, or a future non-ONNX runtime) proceeds without them.
    if install:
        try:
            rep["deps"] = installer(target)
        except Exception as e:                           # noqa: BLE001
            rep["depsError"] = f"install failed: {e}"
            return rep
    else:
        rep["deps"] = {"bytes": dir_bytes(target), "seconds": 0.0}
    try:
        import onnxruntime as _ort
        import tokenizers as _tok
        rep["deps"]["ortVersion"] = _ort.__version__
        rep["deps"]["tokVersion"] = _tok.__version__
    except Exception as e:                               # noqa: BLE001
        if encoder_factory is None:
            rep["depsError"] = (f"installed but not importable: {e}" if install
                                else "onnxruntime/tokenizers not importable — pass --install")
            return rep
    # 2. model files
    try:
        if model_dir:
            rep["model"] = {"candidate": {"name": "local", "repo": model_dir, "model": "model.onnx",
                                          "prefix": ""},
                            "model": os.path.join(model_dir, "model.onnx"),
                            "tokenizer": os.path.join(model_dir, "tokenizer.json"),
                            "bytes": sum(os.path.getsize(os.path.join(model_dir, f))
                                         for f in ("model.onnx", "tokenizer.json")
                                         if os.path.isfile(os.path.join(model_dir, f))),
                            "seconds": 0.0, "skipped": []}
        else:
            rep["model"] = obtain_model(cands, os.path.join(target, "models"), fetch)
    except Exception as e:                               # noqa: BLE001
        rep["modelError"] = str(e)
        return rep
    rep["imageMB"] = (rep["deps"]["bytes"] + rep["model"]["bytes"]) / MB
    # 3. load, with memory read before and after
    m = rep["model"]
    factory = encoder_factory or (lambda threads: OnnxEncoder(
        m["model"], m["tokenizer"], threads=threads, max_len=max_len,
        prefix=m["candidate"].get("prefix", "")))
    rep["rssBeforeMB"] = rss()
    t0 = time.perf_counter()
    try:
        enc = factory(1)
        enc.encode([SAME_A], batch=1)                    # the first batch allocates the arenas
    except Exception as e:                               # noqa: BLE001
        rep["modelError"] = f"load failed: {type(e).__name__}: {e}"
        return rep
    rep["loadSeconds"] = time.perf_counter() - t0
    rep["rssAfterMB"] = rss()
    rep["peakMB"] = peak()
    rep["residentMB"] = max(0.0, rep["rssAfterMB"] - rep["rssBeforeMB"])
    rep["availAfterMB"] = mem().get("MemAvailable", 0.0)
    # 4. encode the real window sample
    texts, sample_facts = [], {}
    try:
        import store as store_mod
        st = store_factory() if store_factory else store_mod.Store(db)
        texts, sample_facts = sample_texts(st, sample)
    except Exception as e:                               # noqa: BLE001
        rep["sampleError"] = f"{type(e).__name__}: {e}"
    rep["sample"] = sample_facts
    rep["encode1"] = measure_encode(enc, texts, batch) if texts else None
    if texts and (rep["host"]["cpus"] or 1) >= 2:
        try:
            enc2 = factory(2)
            rep["encode2"] = measure_encode(enc2, texts, batch)
        except Exception:                                # noqa: BLE001
            rep["encode2"] = None
    # 5. semantics
    rep["semantics"] = sanity(lambda ts: enc.encode(ts, batch=8))
    rep["dim"] = enc.dim
    rep["projections"] = projections((rep["encode1"] or {}).get("ms"),
                                     sample_facts.get("window", 0), enc.dim)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--install", action="store_true",
                    help="pip-install onnxruntime + tokenizers into --target for this run")
    ap.add_argument("--target", default="/tmp/ih-embed-preflight",
                    help="throwaway directory for the deps and model (deleted unless --keep)")
    ap.add_argument("--model", default=None, choices=[c["name"] for c in CANDIDATES],
                    help="measure one named candidate instead of the first that downloads")
    ap.add_argument("--model-dir", default=None,
                    help="use model.onnx + tokenizer.json from this directory; no download")
    ap.add_argument("--sample", type=int, default=500, help="window rows to encode")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=128, help="token truncation per article")
    ap.add_argument("--dry-run", action="store_true", help="print host facts and the plan only")
    ap.add_argument("--keep", action="store_true", help="leave --target in place afterwards")
    args = ap.parse_args(argv)

    try:
        rep = run(target=args.target, install=args.install, only=args.model,
                  model_dir=args.model_dir, sample=args.sample, batch=args.batch,
                  max_len=args.max_len, dry_run=args.dry_run, db=args.db)
        print(render(rep))
        if rep.get("dryRun"):
            return 0
        go, _ = decide(rep)
        return 0 if go else (1 if go is None else 2)
    finally:
        if not args.keep and not args.dry_run and not args.model_dir:
            shutil.rmtree(args.target, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
