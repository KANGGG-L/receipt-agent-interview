# -*- coding: utf-8 -*-
"""T1 评测集三分法 + 可复现 eval harness 测试（Gap A1 + A5a）。

覆盖：
1. build_evalset：train/val/test 无交集、每个 split 覆盖全部 doc_form、55/25/20 配比、
   同 seed 可复现、去标识化（manifest 无真实供应商名）
2. run_eval：EvalReport 字段齐全、比对口径正确（accuracy / cer / per_field_accuracy /
   evidence_coverage / edit_proxy）、同输入两次运行完全一致、单张失败不中断整轮
3. HEIC 转换走 sips（macOS），短边分辨率记录
4. 真实语料相关的用例标记 `@pytest.mark.eval`，语料缺失时自动 skip
"""

import csv
import json
import os

import pytest

import build_evalset
import run_eval

# 合成语料：3 种 doc_form × 5 张，共 15 张
DOC_HINTS = ["printed", "handwritten", "thermal"]
PER_FORM = 5

# 合成供应商名（仅测试用，非真实商户）
_SYNTH_SUPPLIERS = ["Alpha Trading Co", "Beta Food Ltd", "Gamma Market"]


def _make_sources(tmp_path, per_form=PER_FORM, hints=DOC_HINTS, ext="png"):
    """构造合成语料目录：图片 + classification_manifest.csv。"""
    from PIL import Image

    src = tmp_path / "sources"
    src.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    n = 0
    for hint in hints:
        for i in range(per_form):
            fname = f"IMG_{hint}_{i:03d}.{ext}"
            img = Image.new("RGB", (1200, 800), "white")
            img.save(str(src / fname))
            manifest_rows.append({
                "filename": fname,
                "supplier": _SYNTH_SUPPLIERS[n % len(_SYNTH_SUPPLIERS)],
                "supplier_raw": _SYNTH_SUPPLIERS[n % len(_SYNTH_SUPPLIERS)],
                "confidence": "high",
                "doc_hint": hint,
                "preview_path": "",
                "notes": "",
            })
            n += 1
    with open(str(src / "classification_manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader()
        w.writerows(manifest_rows)
    return src


def _read_manifest(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_expected(evalset_dir, sample_id, payload, gt_status="confirmed", gt_model="human"):
    """写一张样本的 GT（模拟 T4 生成 + 人工确认后的产物）。"""
    exp_dir = os.path.join(evalset_dir, "expected")
    os.makedirs(exp_dir, exist_ok=True)
    with open(os.path.join(exp_dir, sample_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    mpath = os.path.join(evalset_dir, "manifest.csv")
    rows = _read_manifest(mpath)
    for r in rows:
        if r["sample_id"] == sample_id:
            r["gt_status"] = gt_status
            r["gt_source_model"] = gt_model
    with open(mpath, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


_GT_PAYLOAD = {
    "supplier_name": "Alpha Trading Co",
    "date": "2026-08-01",
    "total_amount": 120.0,
    "items": [
        {"name": "白菜", "quantity": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
        {"name": "蘿蔔", "quantity": 5.0, "unit": "斤", "unit_price": 20.0, "amount": 100.0},
    ],
}


# ------------------------------------------------------------------
# build_evalset
# ------------------------------------------------------------------
def test_splits_are_disjoint_and_complete(tmp_path):
    src = _make_sources(tmp_path)
    out = tmp_path / "evalsets"
    build_evalset.build(str(src), str(out))

    rows = _read_manifest(os.path.join(str(out), "manifest.csv"))
    assert len(rows) == PER_FORM * len(DOC_HINTS)
    ids = [r["sample_id"] for r in rows]
    assert len(set(ids)) == len(ids), "sample_id 必须唯一"

    buckets = {"train": set(), "val": set(), "test": set()}
    for r in rows:
        buckets[r["split"]].add(r["sample_id"])
    assert not (buckets["train"] & buckets["val"])
    assert not (buckets["train"] & buckets["test"])
    assert not (buckets["val"] & buckets["test"])
    assert buckets["train"] | buckets["val"] | buckets["test"] == set(ids)


def test_every_split_covers_every_doc_form(tmp_path):
    """验收标准 #3：每个 split 内各 doc_form 均有样本。"""
    src = _make_sources(tmp_path)
    out = tmp_path / "evalsets"
    build_evalset.build(str(src), str(out))

    rows = _read_manifest(os.path.join(str(out), "manifest.csv"))
    forms = {r["doc_form"] for r in rows}
    for split in ("train", "val", "test"):
        covered = {r["doc_form"] for r in rows if r["split"] == split}
        assert covered == forms, f"{split} 集未覆盖全部 doc_form：缺 {forms - covered}"


def test_split_sizes_follow_55_25_20(tmp_path):
    src = _make_sources(tmp_path)
    out = tmp_path / "evalsets"
    build_evalset.build(str(src), str(out))

    rows = _read_manifest(os.path.join(str(out), "manifest.csv"))
    n = len(rows)
    counts = {s: sum(1 for r in rows if r["split"] == s) for s in ("train", "val", "test")}
    assert sum(counts.values()) == n
    assert counts["train"] >= counts["val"] >= counts["test"] > 0
    for split, ratio in (("train", 0.55), ("val", 0.25), ("test", 0.20)):
        assert abs(counts[split] / n - ratio) <= 0.06, \
            f"{split} 配比 {counts[split]}/{n} 偏离 55/25/20"

    # 163 张真实语料的切分规模与计划口径一致（test 集约 33 张）
    real = build_evalset.allocate_splits(158)
    real["train"] += build_evalset.allocate_splits(5)["train"]
    real["val"] += build_evalset.allocate_splits(5)["val"]
    real["test"] += build_evalset.allocate_splits(5)["test"]
    assert real["test"] == 33, real


def test_same_seed_reproducible_manifest(tmp_path):
    src = _make_sources(tmp_path)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    build_evalset.build(str(src), str(out_a), seed=20260828)
    build_evalset.build(str(src), str(out_b), seed=20260828)
    a = open(os.path.join(str(out_a), "manifest.csv"), encoding="utf-8").read()
    b = open(os.path.join(str(out_b), "manifest.csv"), encoding="utf-8").read()
    assert a == b, "同 seed 同语料必须产出完全一致的 manifest"


def test_manifest_is_deidentified(tmp_path):
    """去标识化：manifest 不得出现真实供应商名，样本 ID 无语义。"""
    src = _make_sources(tmp_path)
    out = tmp_path / "evalsets"
    build_evalset.build(str(src), str(out))

    text = open(os.path.join(str(out), "manifest.csv"), encoding="utf-8").read()
    for name in _SYNTH_SUPPLIERS:
        assert name not in text, f"manifest 泄露供应商名：{name}"
    for row in _read_manifest(os.path.join(str(out), "manifest.csv")):
        assert row["sample_id"].startswith("S") and row["sample_id"][1:].isdigit()
        assert row["image"].startswith("receipts/")
        assert row["supplier_id"].startswith("V")

    # 供应商名 → ID 的映射单独成文件（同样 gitignore）
    smap = os.path.join(str(out), "supplier_map.csv")
    assert os.path.exists(smap)
    smap_text = open(smap, encoding="utf-8").read()
    assert _SYNTH_SUPPLIERS[0] in smap_text


def test_heic_conversion_uses_sips(tmp_path, monkeypatch):
    """HEIC 走 sips 转 PNG；转换失败不阻断整轮构建。"""
    src = tmp_path / "src_heic"
    src.mkdir()
    (src / "IMG_0001.HEIC").write_bytes(b"fake-heic")

    out = tmp_path / "evalsets"
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        dst = cmd[-1]
        from PIL import Image
        Image.new("RGB", (1200, 1600), "white").save(dst)

    monkeypatch.setattr(build_evalset.subprocess, "run", _fake_run)
    build_evalset.build(str(src), str(out))

    assert calls and calls[0][:3] == ["sips", "-s", "format"]
    rows = _read_manifest(os.path.join(str(out), "manifest.csv"))
    assert len(rows) == 1
    assert rows[0]["image"].endswith(".png")
    assert rows[0]["short_side"] == "1200"


# ------------------------------------------------------------------
# run_eval：比对口径
# ------------------------------------------------------------------
def test_compare_detects_field_mismatch():
    exp = dict(_GT_PAYLOAD)
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["total_amount"] = 999.0
    res = run_eval.compare(exp, pred)
    assert res["ok"] is False
    assert "total" in res["mismatched_fields"]
    assert res["field_matched"] == res["field_total"] - 1


def test_compare_perfect_match():
    res = run_eval.compare(_GT_PAYLOAD, json.loads(json.dumps(_GT_PAYLOAD)))
    assert res["ok"] is True
    assert res["mismatched_fields"] == []
    assert res["cer"] == 0.0


def test_cer_counts_character_errors():
    exp = dict(_GT_PAYLOAD)
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["items"][0]["name"] = "白榮"      # 1 字错误
    res = run_eval.compare(exp, pred)
    # 文本字段总长度 = 白菜(2) + 蘿蔔(2) + vendor(16) 等；只要有编辑距离即 >0
    assert res["cer"] > 0
    assert res["cer"] < 1.0


def test_evidence_coverage_helper():
    class _Item:
        def __init__(self, ev):
            self.evidence = ev

    assert run_eval._evidence_coverage([_Item(None), _Item(None)]) == 0.0
    assert run_eval._evidence_coverage([_Item({"bbox": [0, 0, 1, 1]}), _Item(None)]) == 0.5
    assert run_eval._evidence_coverage([]) == 0.0


# ------------------------------------------------------------------
# run_eval：端到端（stub 引擎，离线）
# ------------------------------------------------------------------
def _prepare_corpus(tmp_path, per_form=PER_FORM):
    src = _make_sources(tmp_path, per_form=per_form)
    out = tmp_path / "evalsets"
    build_evalset.build(str(src), str(out))
    return str(out)


def test_run_eval_report_fields(tmp_path):
    evalset = _prepare_corpus(tmp_path)
    rows = _read_manifest(os.path.join(evalset, "manifest.csv"))
    for r in rows:
        _write_expected(evalset, r["sample_id"], _GT_PAYLOAD)

    report = run_eval.run_eval(
        split="test", prompt_version="v1_2_0_sku_clean", engine="stub",
        evalset_dir=evalset, report_dir=str(tmp_path / "reports"),
    )
    for key in ("accuracy", "cer", "per_field_accuracy", "edit_proxy",
                "evidence_coverage", "avg_tokens", "p50_latency_ms",
                "gt_status_breakdown", "sample_ids"):
        assert key in report, f"EvalReport 缺字段：{key}"
    assert report["split"] == "test"
    assert report["engine"] == "stub"
    assert len(report["sample_ids"]) == len([r for r in rows if r["split"] == "test"])


def test_run_eval_stub_scores_perfect(tmp_path):
    evalset = _prepare_corpus(tmp_path)
    for r in _read_manifest(os.path.join(evalset, "manifest.csv")):
        _write_expected(evalset, r["sample_id"], _GT_PAYLOAD)

    report = run_eval.run_eval(
        split="val", prompt_version="v1_2_0_sku_clean", engine="stub",
        evalset_dir=evalset, report_dir=str(tmp_path / "reports"),
    )
    assert report["accuracy"] == 1.0
    assert report["cer"] == 0.0
    assert report["edit_proxy"] == 0.0
    assert all(v == 1.0 for v in report["per_field_accuracy"].values())
    assert report["gt_status_breakdown"] == {"confirmed": len(report["sample_ids"])}
    assert os.path.exists(report["report_path"]), "必须落盘 raw report 供追溯"


def test_run_eval_is_reproducible(tmp_path):
    """同输入两次运行产出完全一致（除 created_at / report_path）。"""
    evalset = _prepare_corpus(tmp_path)
    for r in _read_manifest(os.path.join(evalset, "manifest.csv")):
        _write_expected(evalset, r["sample_id"], _GT_PAYLOAD)

    rep1 = run_eval.run_eval(split="train", prompt_version="v1_2_0_sku_clean",
                             engine="stub", evalset_dir=evalset,
                             report_dir=str(tmp_path / "r1"))
    rep2 = run_eval.run_eval(split="train", prompt_version="v1_2_0_sku_clean",
                             engine="stub", evalset_dir=evalset,
                             report_dir=str(tmp_path / "r2"))
    # 时延是墙钟量，天然不可复现；比对的是「哪些样本对、哪些字段错、各项分数」
    for r in (rep1, rep2):
        r.pop("created_at", None)
        r.pop("report_path", None)
        r.pop("p50_latency_ms", None)
        for rec in r["per_sample"]:
            rec.pop("latency_ms", None)
    assert rep1 == rep2


def test_single_sample_failure_does_not_break_run(tmp_path):
    evalset = _prepare_corpus(tmp_path)
    for r in _read_manifest(os.path.join(evalset, "manifest.csv")):
        _write_expected(evalset, r["sample_id"], _GT_PAYLOAD)

    def _boom(image_path, sample_id):
        raise RuntimeError("模拟引擎故障")

    report = run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=evalset,
                               report_dir=str(tmp_path / "reports"),
                               predictor=_boom)
    assert report["n_samples"] > 0
    assert report["accuracy"] == 0.0
    assert report["errors"] == report["n_samples"]


def test_missing_expected_is_counted_not_crashed(tmp_path):
    evalset = _prepare_corpus(tmp_path)  # 不写任何 expected
    report = run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=evalset,
                               report_dir=str(tmp_path / "reports"))
    assert report["gt_status_breakdown"].get("missing") == report["n_samples"]
    assert report["accuracy"] == 0.0


# ------------------------------------------------------------------
# 真实语料（需要 --run-eval 且 demo/evalsets/ 存在）
# ------------------------------------------------------------------
@pytest.mark.eval
def test_real_corpus_splits_disjoint(evalset_dir):
    rows = _read_manifest(os.path.join(evalset_dir, "manifest.csv"))
    assert len(rows) >= 30, "真实语料样本量过小，不足以支撑三分法"
    buckets = {"train": set(), "val": set(), "test": set()}
    for r in rows:
        buckets[r["split"]].add(r["sample_id"])
    assert not (buckets["train"] & buckets["val"])
    assert not (buckets["train"] & buckets["test"])
    assert not (buckets["val"] & buckets["test"])
