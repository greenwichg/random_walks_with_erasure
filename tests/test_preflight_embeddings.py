"""Stage 1 preflight (examples/preflight_embeddings.py) — every decision path, with no network,
no pip and no model. The runtime path (a real ONNX session and tokenizer) is exercised only
when onnxruntime, tokenizers and onnx happen to be importable, on a synthetic model built
here, so the plumbing that would otherwise first meet reality on the production box is
checked before it gets there.
"""
import json
import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import preflight_embeddings as pf   # noqa: E402


# --------------------------------------------------------------------------- #
# Pure arithmetic.
# --------------------------------------------------------------------------- #
def test_mean_pool_ignores_masked_positions_and_never_divides_by_zero():
    hidden = np.array([[[1.0, 2.0], [3.0, 4.0], [100.0, 100.0]],
                       [[5.0, 5.0], [0.0, 0.0], [0.0, 0.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0], [0, 0, 0]], dtype=np.int64)
    pooled = pf.mean_pool(hidden, mask)
    assert pooled[0].tolist() == [2.0, 3.0], "the masked third token contributes nothing"
    assert pooled[1].tolist() == [0.0, 0.0], "an all-masked row divides by one, not by zero"


def test_l2norm_and_cosine():
    v = pf.l2norm(np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32))
    assert np.allclose(v[0], [0.6, 0.8]) and np.allclose(v[1], [0.0, 0.0])
    assert pf.cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == pytest.approx(1.0)
    assert pf.cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)


def test_projections_scale_with_rate_window_and_dim():
    p = pf.projections(20.0, 45_000, 384)
    assert p["cpuMinutesPerDay"] == pytest.approx(5000 * 20.0 / 60000)
    assert p["backfillMinutes"] == pytest.approx(45_000 * 20.0 / 60000)
    assert p["windowMBFloat32"] == pytest.approx(45_000 * 384 * 4 / pf.MB)
    assert p["catalogMBFloat16"] == pytest.approx(150_000 * 384 * 2 / pf.MB)
    assert pf.projections(None, 10, None)["cpuMinutesPerDay"] == 0.0


def test_dir_bytes_excludes_the_numpy_already_in_the_image(tmp_path):
    (tmp_path / "onnxruntime").mkdir()
    (tmp_path / "onnxruntime" / "lib.so").write_bytes(b"x" * 1000)
    (tmp_path / "numpy").mkdir()
    (tmp_path / "numpy" / "core.so").write_bytes(b"x" * 5000)
    (tmp_path / "numpy.libs").mkdir()
    (tmp_path / "numpy.libs" / "blas.so").write_bytes(b"x" * 7000)
    (tmp_path / "top.txt").write_bytes(b"x" * 10)
    assert pf.dir_bytes(str(tmp_path)) == 1010
    assert pf.dir_bytes(str(tmp_path / "missing")) == 0


# --------------------------------------------------------------------------- #
# Candidate selection and download.
# --------------------------------------------------------------------------- #
def test_candidates_follow_the_cpu_flags_and_only_narrows():
    names = [c["name"] for c in pf.select_candidates({"avx2"})]
    assert "st-minilm-l12-int8-avx512vnni" not in names and "st-minilm-l12-int8-avx512" not in names
    assert names[0] == "xenova-minilm-l12-int8", "without AVX-512 the dynamic-int8 export leads"
    names = [c["name"] for c in pf.select_candidates({"avx2", "avx512f", "avx512_vnni"})]
    assert names[0] == "st-minilm-l12-int8-avx512vnni" and names[1] == "st-minilm-l12-int8-avx512"
    assert [c["name"] for c in pf.select_candidates({"avx512f"}, only="qdrant-minilm-l12-q")] == \
        ["qdrant-minilm-l12-q"]


def test_obtain_model_takes_the_first_candidate_that_arrives_and_records_the_rest(tmp_path):
    calls = []

    def fake_fetch(url, dest):
        calls.append(url)
        if "Xenova" not in url:
            raise RuntimeError("HTTP 404")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"m" * 2048)
        return 2048
    cands = pf.select_candidates({"avx512f", "avx512_vnni"})
    got = pf.obtain_model(cands, str(tmp_path / "models"), fetch=fake_fetch)
    assert got["candidate"]["name"] == "xenova-minilm-l12-int8"
    assert got["bytes"] == 4096 and len(got["skipped"]) == 2
    assert all("404" in why for _, why in got["skipped"])
    assert not (tmp_path / "models" / "st-minilm-l12-int8-avx512vnni").exists(), \
        "a failed candidate leaves no partial files behind"
    # Second call: the files are present, nothing is fetched.
    before = len(calls)
    again = pf.obtain_model([cands[2]], str(tmp_path / "models"), fetch=fake_fetch)
    assert again["bytes"] == 4096 and len(calls) == before


def test_obtain_model_raises_when_nothing_arrives(tmp_path):
    def nope(url, dest):
        raise RuntimeError("HTTP 403")
    with pytest.raises(RuntimeError, match="no candidate model"):
        pf.obtain_model(pf.select_candidates({"avx2"}), str(tmp_path), fetch=nope)


def test_fetch_to_streams_and_refuses_past_the_cap(tmp_path):
    class Resp:
        def __init__(self, n):
            self.left = n

        def read(self, k):
            take = min(k, self.left)
            self.left -= take
            return b"z" * take

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    dest = str(tmp_path / "a" / "b.onnx")
    assert pf.fetch_to("http://x", dest, cap=10_000, opener=lambda req, timeout: Resp(5000)) == 5000
    assert os.path.getsize(dest) == 5000
    with pytest.raises(RuntimeError, match="cap"):
        pf.fetch_to("http://x", dest, cap=100, opener=lambda req, timeout: Resp(5000))


# --------------------------------------------------------------------------- #
# The decision.
# --------------------------------------------------------------------------- #
def _passing():
    return {"host": {"memTotalMB": 4000.0},
            "imageMB": 180.0, "residentMB": 220.0, "availAfterMB": 2100.0,
            "encode1": {"ms": 18.0}, "semantics": {"same": 0.82, "different": 0.31,
                                                   "template": 0.7, "xling_vi": 0.74,
                                                   "xling_ko": 0.66}}


def test_decide_go_when_every_bar_holds():
    go, checks = pf.decide(_passing())
    assert go is True and all(ok for _, _, ok, _ in checks) and len(checks) == 6
    assert pf.verdicts(checks) == {"box": True, "model": True}


@pytest.mark.parametrize("field, value, bar, group", [
    ("imageMB", 501.0, "image", "box"), ("residentMB", 401.0, "resident", "box"),
    ("availAfterMB", 900.0, "headroom", "box"), ("encode1", {"ms": 50.1}, "encode", "box"),
    ("semantics", {"same": 0.5, "different": 0.4, "template": 0.7, "xling_vi": 0.9, "xling_ko": 0.9},
     "margin", "model"),
    ("semantics", {"same": 0.9, "different": 0.1, "template": 0.7, "xling_vi": 0.9, "xling_ko": 0.49},
     "cross-lingual", "model"),
])
def test_each_bar_can_fail_on_its_own_and_names_its_group(field, value, bar, group):
    rep = _passing()
    rep[field] = value
    go, checks = pf.decide(rep)
    assert go is False
    failed = [(g, name) for g, name, ok, _ in checks if ok is False]
    assert failed == [(group, bar)]
    per = pf.verdicts(checks)
    assert per[group] is False and per["model" if group == "box" else "box"] is True


def test_the_memory_bars_scale_with_the_box_and_never_loosen_below_the_floor():
    """Registered before the t3.large re-run: on the 3,832 MB box the shares resolve to the
    same or stricter numbers than the constants they replaced (383 / 1,024 MB), and an 8 GiB
    box must show proportionally more room (about 780 / 1,950 MB)."""
    assert pf.memory_bars(3832.0) == pytest.approx((383.2, 1024.0))
    assert pf.memory_bars(7800.0) == pytest.approx((780.0, 1950.0))
    assert pf.memory_bars(0.0) == (400.0, 1024.0), "no /proc/meminfo: the 4 GiB constants"
    # The t3.medium run's numbers, replayed: resident 414 and headroom 807 still fail on 3,832 MB…
    rep = _passing()
    rep.update({"host": {"memTotalMB": 3832.0}, "residentMB": 414.0, "availAfterMB": 807.0})
    assert pf.verdicts(pf.decide(rep)[1])["box"] is False
    # …and the same process cost on an 8 GiB box passes both memory bars.
    rep.update({"host": {"memTotalMB": 7800.0}, "availAfterMB": 4500.0})
    assert pf.verdicts(pf.decide(rep)[1])["box"] is True


def test_a_missing_measurement_is_undetermined_not_a_verdict():
    rep = _passing()
    rep["encode1"] = None
    go, checks = pf.decide(rep)
    assert go is None
    assert any(name == "encode" and ok is None for _, name, ok, _ in checks)
    assert pf.verdicts(checks) == {"box": None, "model": True}


# --------------------------------------------------------------------------- #
# The whole run, with fakes for pip, the network, the model and the store.
# --------------------------------------------------------------------------- #
class FakeEncoder:
    """Unit vectors chosen by keyword, so the exhibits' cosines are known in advance."""
    AXES = (("shooting", 0), ("gunfire", 0), ("goaltender", 1), ("recalled", 2),
            ("messi", 3), ("메시", 3))

    def __init__(self, ms_per_text=0.0, dim=6):
        self.ms = ms_per_text
        self.dim = dim
        self.tokens_seen = 0
        self.seen = []

    def encode(self, texts, batch=32):
        import time
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            low = t.lower()
            axis = next((a for k, a in self.AXES if k in low), 4)
            out[i, axis] = 1.0
            self.tokens_seen += max(1, len(t.split()))
        self.seen.extend(texts)
        if self.ms:
            time.sleep(self.ms * len(texts) / 1000.0)
        return out


class FakeStore:
    pass


def _rows(n):
    return [{"canonicalUrl": f"u{i}", "title": f"Headline number {i} about things",
             "description": "A dek that says more." if i % 2 else "", "language": "en" if i % 3 else "vi"}
            for i in range(n)]


def _run(tmp_path, monkeypatch, *, ms=0.0, install=True, dry_run=False, rows=200):
    import story_service
    monkeypatch.setattr(story_service, "_fetch", lambda st: _rows(rows))
    fetched, installed = [], []

    def installer(target):
        installed.append(target)
        os.makedirs(target, exist_ok=True)
        os.makedirs(os.path.join(target, "onnxruntime"), exist_ok=True)
        with open(os.path.join(target, "onnxruntime", "lib.so"), "wb") as f:
            f.write(b"x" * 60 * pf.MB)
        return {"seconds": 1.5, "bytes": pf.dir_bytes(target)}

    def fetch(url, dest):
        fetched.append(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"m" * (100 * pf.MB if dest.endswith(".onnx") else 1024))
        return os.path.getsize(dest)
    encoders = []

    def factory(threads):
        e = FakeEncoder(ms_per_text=ms)
        encoders.append((threads, e))
        return e
    mem_reads = iter([{"MemAvailable": 2500.0, "MemTotal": 3900.0, "SwapTotal": 0.0, "SwapFree": 0.0},
                      {"MemAvailable": 2200.0}])
    rss_reads = iter([100.0, 340.0])
    rep = pf.run(target=str(tmp_path / "t"), install=install, only=None, model_dir=None,
                 sample=50, batch=16, max_len=64, dry_run=dry_run,
                 installer=installer, fetch=fetch, encoder_factory=factory,
                 store_factory=FakeStore, mem=lambda: next(mem_reads, {"MemAvailable": 2200.0}),
                 rss=lambda: next(rss_reads, 340.0), peak=lambda: 360.0)
    return rep, fetched, installed, encoders


def test_dry_run_prints_the_plan_and_touches_nothing(tmp_path, monkeypatch, capsys):
    rep, fetched, installed, encoders = _run(tmp_path, monkeypatch, dry_run=True)
    assert not fetched and not installed and not encoders
    text = pf.render(rep)
    assert "dry run" in text and "huggingface.co" in text and "--install" in text
    assert "verdict" not in text


def test_the_full_run_measures_everything_and_decides_go(tmp_path, monkeypatch):
    rep, fetched, installed, encoders = _run(tmp_path, monkeypatch)
    assert installed and len(fetched) == 2, "one candidate: its model and its tokenizer"
    assert rep["deps"]["bytes"] == 60 * pf.MB and rep["model"]["bytes"] == 100 * pf.MB + 1024
    assert rep["imageMB"] == pytest.approx(160.0, abs=0.1)
    assert rep["residentMB"] == pytest.approx(240.0) and rep["availAfterMB"] == 2200.0
    assert rep["encode1"]["n"] == 50 and rep["sample"]["window"] == 200
    assert rep["sample"]["langs"] == {"en": 33, "vi": 17}, "every 4th row of 200, real language mix"
    assert [t for t, _ in encoders] == [1, 2], "one thread for the bar, two for information"
    sem = rep["semantics"]
    assert sem["same"] == pytest.approx(1.0) and sem["different"] == pytest.approx(0.0)
    assert sem["xling_vi"] == pytest.approx(1.0) and sem["xling_ko"] == pytest.approx(1.0)
    assert sem["template"] == pytest.approx(1.0), "the trap reads high, and is reported not barred"
    go, _ = pf.decide(rep)
    assert go is True
    text = pf.render(rep)
    for needed in ("GO —", "image growth    : 160 MB", "1 thread", "template trap",
                   "cross-lingual en/ko", "backfill", "float16", "box   : GO", "model : GO"):
        assert needed in text, needed


def test_a_slow_encoder_is_a_box_no_go_with_the_model_verdict_intact(tmp_path, monkeypatch):
    rep, *_ = _run(tmp_path, monkeypatch, ms=55.0, rows=40)
    go, checks = pf.decide(rep)
    assert go is False and [n for _, n, ok, _ in checks if ok is False] == ["encode"]
    text = pf.render(rep)
    assert "NO-GO —" in text and "box   : NO-GO" in text and "model : GO" in text


def test_main_exit_codes_and_cleanup(tmp_path, monkeypatch, capsys):
    """0 = GO, 2 = NO-GO, 1 = undetermined; the throwaway target is removed unless --keep."""
    import story_service
    monkeypatch.setattr(story_service, "_fetch", lambda st: _rows(60))
    monkeypatch.setattr(pf, "install_deps", lambda target: (_ for _ in ()).throw(RuntimeError("pip down")))
    target = tmp_path / "gone"
    rc = pf.main(["--install", "--target", str(target), "--sample", "10"])
    out = capsys.readouterr().out
    assert rc == 1 and "UNDETERMINED" in out and "pip down" in out
    assert not target.exists(), "the throwaway directory is removed"
    rc = pf.main(["--dry-run", "--target", str(tmp_path / "plan")])
    assert rc == 0 and "dry run" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# The runtime path on a synthetic model — only where the libraries happen to exist.
# --------------------------------------------------------------------------- #
def _synthetic_model(dirpath):
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, pre_tokenizers
    vocab = ["<pad>", "[UNK]", "mass", "shooting", "reported", "at", "seattle", "center", "gunfire",
             "erupts", "near", "police", "say", "kraken", "sign", "veteran", "goaltender", "messi",
             "eye", "drops", "recalled", "fruit", "bars", "nationwide", "contamination"]
    tok = Tokenizer(models.WordLevel({w: i for i, w in enumerate(vocab)}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(os.path.join(dirpath, "tokenizer.json"))
    table = np.random.RandomState(7).randn(len(vocab), 8).astype(np.float32)
    table[0] = 0.0
    ids = helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"])
    mask = helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"])
    types = helper.make_tensor_value_info("token_type_ids", TensorProto.INT64, ["b", "s"])
    out = helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b", "s", 8])
    gather = helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"], axis=0)
    graph = helper.make_graph([gather], "toy", [ids, mask, types], [out],
                              initializer=[numpy_helper.from_array(table, "table")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, os.path.join(dirpath, "model.onnx"))


def test_the_onnx_encoder_runs_a_synthetic_export_end_to_end(tmp_path, monkeypatch, capsys):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    _synthetic_model(str(tmp_path))
    enc = pf.OnnxEncoder(os.path.join(tmp_path, "model.onnx"),
                         os.path.join(tmp_path, "tokenizer.json"), threads=1, max_len=16)
    assert set(enc.input_names) == {"input_ids", "attention_mask", "token_type_ids"}
    v = enc.encode(["mass shooting reported at seattle center", "gunfire erupts near seattle center",
                    "kraken sign veteran goaltender"], batch=2)
    assert v.shape == (3, 8) and enc.dim == 8
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0), "unit vectors"
    assert pf.cosine(v[0], v[1]) > pf.cosine(v[0], v[2]), \
        "shared tokens pool into nearer vectors even on random embeddings"
    # And the whole preflight over it, sampling from a fake window, with --model-dir.
    import story_service
    monkeypatch.setattr(story_service, "_fetch", lambda st: _rows(30))
    rep = pf.run(target=str(tmp_path / "t"), install=False, only=None, model_dir=str(tmp_path),
                 sample=10, batch=4, max_len=16, dry_run=False, store_factory=FakeStore)
    assert rep.get("modelError") is None and rep["encode1"]["n"] == 10
    assert rep["dim"] == 8 and rep["semantics"]["same"] is not None
    assert "verdict" in pf.render(rep)
