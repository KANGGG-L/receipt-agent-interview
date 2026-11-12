# -*- coding: utf-8 -*-
"""E02 / C-EVAL-OFFLINE：配对 A/B 与延迟/FER 评测器（纯离线合成 fixture）。

覆盖任务卡 E02 的最低验收：
- `test_paired_only_memory_varies`：同一批样本/配置/提示/记忆快照，两臂只差 memory
  是否注入；on 臂若额外拿答案或整批资产漂移 → 整批 invalid；
- `test_failures_not_dropped`：失败逐臂进入漏斗，未配对样本不参与估值但必须可见；
- `test_p95_retry_queue_accounting`：p95 延迟与重试/队列记账；未终态截尾阻断 SLO；
- `test_line_alignment_fer_reference`：行对齐与 FER 参考分母显式可复核。

全程不调用真实模型、不读 evalsets/manifest、不写 DB/app_settings。
"""

import copy
import json
import os
import sys
import tempfile

# 环境隔离（必须在 import app.* / scripts 之前）
_TMP = tempfile.mkdtemp(prefix="e02_offline_eval_")
os.environ["DB_PATH"] = os.path.join(_TMP, "e02.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "e02_rag")
os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
for _p in (_DEMO_DIR, _REPO_ROOT, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import run_eval  # noqa: E402
from app.services import eval_reporter  # noqa: E402


# -------------------------------------------------------------
# 合成 fixture 构造器（所有期望/预测都是本地字面量）
# -------------------------------------------------------------
def _gt(items, vendor="測試供應商", total=None):
    if total is None:
        total = round(sum(float(i["amount"]) for i in items), 2)
    return {
        "doc_form": "printed_delivery_note",
        "vendor": vendor,
        "date": "2026-10-26",
        "total": total,
        "items": items,
        "payment_marked": False,
        "currency": "HKD",
    }


def _items(names_amounts):
    return [{"name": n, "qty": 1, "unit": "斤", "unit_price": a, "amount": a}
            for n, a in names_amounts]


def _shared(sid, **overrides):
    shared = {
        "prompt_version": "v1_2_0_sku_clean",
        "model": "offline-fixture",
        "config_fingerprint": "cfg-A",
        "memory_snapshot_id": "ms-1",
        "gt_source": "human_confirmed",
        "gt_condition": "vendor_hint=GT（两臂相同）",
        "input_hash": "hash-%s" % sid,
    }
    shared.update(overrides)
    return shared


def _sample(sid, expected, on_pred, off_pred, *,
            status_on="done", status_off="done",
            lat_on=1000, lat_off=1100, retry_on=0, retry_off=0,
            queue_on=0, queue_off=0, depth_on=0, depth_off=0,
            shared=None, on_extra=None, off_extra=None,
            error_on="", error_off=""):
    shared = _shared(sid) if shared is None else shared
    on_inputs = dict(shared)
    on_inputs["memory_injected"] = True
    on_inputs.update(on_extra or {})
    off_inputs = dict(shared)
    off_inputs["memory_injected"] = False
    off_inputs.update(off_extra or {})
    return {
        "sample_id": sid,
        "shared_inputs": shared,
        "expected": copy.deepcopy(expected),
        "arms": {
            "on": {
                "memory_injected": True, "inputs": on_inputs,
                "status": status_on, "latency_ms": lat_on,
                "retry_count": retry_on, "queue_wait_ms": queue_on,
                "queue_depth": depth_on, "predicted": copy.deepcopy(on_pred),
                "error": error_on,
            },
            "off": {
                "memory_injected": False, "inputs": off_inputs,
                "status": status_off, "latency_ms": lat_off,
                "retry_count": retry_off, "queue_wait_ms": queue_off,
                "queue_depth": depth_off, "predicted": copy.deepcopy(off_pred),
                "error": error_off,
            },
        },
    }


def _good_batch():
    """4 单：on 臂修正了 off 臂的品名/金额错误，其余字段完全相同。"""
    samples = []
    for idx, (name, amount) in enumerate(
            [("菜心", 10), ("牛肉", 20), ("活魚", 30), ("豆腐", 40)]):
        sid = "S%d" % (idx + 1)
        expected = _gt(_items([(name, amount), ("配菜%d" % idx, 5)]))
        off_pred = _gt(_items([(name + "錯", amount), ("配菜%d" % idx, 5)]))
        on_pred = _gt(_items([(name, amount), ("配菜%d" % idx, 5)]))
        samples.append(_sample(sid, expected, on_pred, off_pred,
                               lat_on=800 + idx * 100, lat_off=900 + idx * 100))
    return samples


# -------------------------------------------------------------
# 1. 两臂只差 memory 注入；额外答案/配置漂移 → 整批 invalid
# -------------------------------------------------------------
def test_paired_only_memory_varies():
    samples = _good_batch()
    report = eval_reporter.run_paired_eval(samples)
    assert report["batch_valid"] is True, report["invalid_reasons"]
    assert report["estimable"] is True
    assert report["n_samples"] == 4 and report["n_pairs"] == 4
    # 配对差值：on 臂更少字段错误（fer 更低）；n=4 < 30 → CI 不可用，不得冒充精度
    fer = report["stats"]["fer"]
    assert fer["n_pairs"] == 4
    assert fer["mean_delta_on_minus_off"] < 0
    assert fer["ci95"] is None
    assert "n_pairs=4" in fer["ci_unavailable"]
    assert fer["ci_method"] == "unavailable"
    assert report["bootstrap"]["seed"] == eval_reporter.DEFAULT_BOOTSTRAP_SEED
    # 每单差异必须逐单输出
    assert len(report["per_sample_diff"]) == 4
    assert all(p["scored"] is True for p in report["per_sample_diff"])
    assert all(set(p["delta"]) == {"fer", "accuracy", "cer"}
               for p in report["per_sample_diff"])
    # 未注入 defaults_reader → 未验证（None），不再硬编码 False
    assert report["production_defaults_modified"] is None
    assert report["production_defaults_verified"] is False

    # 目标只给 on 额外答案 → 混杂，整批 invalid（不是"on 更好"的收益）
    confounded = copy.deepcopy(samples)
    confounded[0]["arms"]["on"]["inputs"]["gt_answer_hint"] = "A/B/C 正确答案"
    bad = eval_reporter.run_paired_eval(confounded)
    assert bad["batch_valid"] is False
    assert any("超出 memory 注入" in r for r in bad["invalid_reasons"]), bad["invalid_reasons"]
    assert bad["estimable"] is False
    assert bad["stats"]["fer"]["mean_delta_on_minus_off"] is None

    # 资产/配置漂移（第二单换了 config）→ 整批 invalid
    drifted = copy.deepcopy(samples)
    drifted[1]["shared_inputs"]["config_fingerprint"] = "cfg-B"
    drifted[1]["arms"]["on"]["inputs"]["config_fingerprint"] = "cfg-B"
    drifted[1]["arms"]["off"]["inputs"]["config_fingerprint"] = "cfg-B"
    bad2 = eval_reporter.run_paired_eval(drifted)
    assert bad2["batch_valid"] is False
    assert any("资产/配置漂移" in r for r in bad2["invalid_reasons"]), bad2["invalid_reasons"]


# -------------------------------------------------------------
# 2. 失败不得丢出漏斗
# -------------------------------------------------------------
def test_failures_not_dropped():
    samples = _good_batch()[:3]
    # S2：on 臂失败（终态 error）
    samples[1]["arms"]["on"].update(
        status="error", predicted=None, error="识别超时（合成）")
    # S3：off 臂未终态（parsing）
    samples[2]["arms"]["off"].update(
        status="parsing", predicted=None, error="")

    report = eval_reporter.run_paired_eval(samples)
    assert report["n_samples"] == 3 and report["n_pairs"] == 1
    fun = report["failure_funnel"]
    assert fun["attempted_arms"] == 6
    assert fun["paired_samples"] == 1
    # failed=终态失败（S2 on），nonterminal=未终态截尾（S3 off），不得重复计数
    assert fun["failed_arms"] == 1
    assert fun["terminal_failed_arms"] == 1
    assert fun["nonterminal_arms"] == 1
    assert fun["failure_reasons_preserved"] == 2
    assert fun["unpaired_samples"] == 2
    # 每个样本都在 per_sample_diff 里（没有被"成功样本"过滤掉）
    assert {p["sample_id"] for p in report["per_sample_diff"]} == {"S1", "S2", "S3"}
    failure_ids = {(f["sample_id"], f["arm"]) for f in report["failures"]}
    assert ("S2", "on") in failure_ids and ("S3", "off") in failure_ids
    # 失败样本不参与估值；但 n 仍如实报告
    assert report["stats"]["fer"]["n_pairs"] == 1
    assert report["stats"]["fer"]["estimable"] is True

    # 全部失败 → 未测不估值：均值/CI 为 None，不是 0.0
    all_bad = copy.deepcopy(samples)
    for s in all_bad:
        s["arms"]["on"].update(status="error", predicted=None, error="boom")
        s["arms"]["off"].update(status="error", predicted=None, error="boom")
    report2 = eval_reporter.run_paired_eval(all_bad)
    assert report2["n_pairs"] == 0
    assert report2["estimable"] is False
    for metric in ("fer", "accuracy", "cer"):
        stat = report2["stats"][metric]
        assert stat["mean_on"] is None and stat["mean_off"] is None
        assert stat["mean_delta_on_minus_off"] is None and stat["ci95"] is None
    assert report2["slo"]["slo_pass"] is False


# -------------------------------------------------------------
# 3. p95 延迟 / 重试 / 队列记账；未终态截尾阻断 SLO
# -------------------------------------------------------------
def test_p95_retry_queue_accounting():
    samples = _good_batch()
    retries_on = [0, 1, 2, 3]
    retries_off = [0, 0, 1, 1]
    queue_on = [0, 20, 40, 60]
    for idx, s in enumerate(samples):
        s["arms"]["on"].update(latency_ms=1000 * (idx + 1),
                               retry_count=retries_on[idx],
                               queue_wait_ms=queue_on[idx], queue_depth=idx)
        s["arms"]["off"].update(latency_ms=500 * (idx + 1),
                                retry_count=retries_off[idx],
                                queue_wait_ms=10, queue_depth=1)

    report = eval_reporter.run_paired_eval(
        samples, slo_p95_ms=20000, min_terminal_arms=8)
    lat = report["latency"]
    assert lat["n_terminal_arms"] == 8
    assert lat["p95_undersampled"] is False
    # 终态延迟 [500,1000,1000,1500,2000,3000,4000...]；nearest-rank p95 取最大观测 4000
    assert lat["p95_ms"] == 4000.0
    rq = report["retry_queue"]
    assert rq["retry_total"] == sum(retries_on) + sum(retries_off)
    assert rq["retry_p95"] is not None
    assert rq["queue_wait_total_ms"] == sum(queue_on) + 10 * 4
    assert rq["queue_wait_p95_ms"] is not None
    assert rq["max_queue_depth"] == 3
    assert report["slo"]["slo_blocked"] is False
    assert report["slo"]["slo_pass"] is True

    # 未终态截尾：即使延迟很低，也不得声称 SLO 通过
    truncated = copy.deepcopy(samples)
    truncated[0]["arms"]["on"].update(status="running", predicted=None,
                                      latency_ms=1.0)
    report2 = eval_reporter.run_paired_eval(
        truncated, slo_p95_ms=20000, min_terminal_arms=8)
    assert report2["failure_funnel"]["nonterminal_arms"] == 1
    assert report2["slo"]["slo_blocked"] is True
    assert report2["slo"]["slo_pass"] is False
    assert any("截尾" in r for r in report2["slo"]["slo_blocked_reasons"])


# -------------------------------------------------------------
# 3b. SLO 欠采样：1 样本 2 臂不得因为 p95 低就 slo_pass=True
# -------------------------------------------------------------
def test_slo_requires_min_terminal_arms():
    report = eval_reporter.run_paired_eval(_good_batch()[:1], slo_p95_ms=12000)
    assert report["latency"]["n_terminal_arms"] == 2
    assert report["latency"]["min_terminal_arms"] == 30
    assert report["latency"]["p95_undersampled"] is True
    assert report["slo"]["slo_pass"] is False
    assert any("欠采样" in r for r in report["slo"]["slo_blocked_reasons"])


# -------------------------------------------------------------
# 3c. CI 可用性：n<30 或零方差时不可用；>=30 且有方差才输出
# -------------------------------------------------------------
def _ci_batch(vary=True, n=30):
    samples = []
    for i in range(n):
        expected = _gt(_items([("菜心", 10), ("配菜", 5)]))
        typo_count = (i % 3) if vary else 1
        off_items = [
            ("菜心錯" if typo_count >= 1 else "菜心", 10),
            ("配菜錯" if typo_count >= 2 else "配菜", 5),
        ]
        samples.append(_sample("CI%d" % i, expected, copy.deepcopy(expected),
                               _gt(_items(off_items)),
                               lat_on=1000 + i, lat_off=1000 + i))
    return samples


def test_ci_unavailable_undersampled_or_zero_variance():
    small = eval_reporter.run_paired_eval(_good_batch()[:4])
    stat = small["stats"]["fer"]
    assert stat["ci95"] is None
    assert "n_pairs=4" in stat["ci_unavailable"]
    assert stat["ci_method"] == "unavailable"

    zero = eval_reporter.run_paired_eval(
        _ci_batch(vary=False), min_terminal_arms=30)
    assert zero["stats"]["fer"]["ci95"] is None
    assert "zero_variance" in zero["stats"]["fer"]["ci_unavailable"]

    enough = eval_reporter.run_paired_eval(
        _ci_batch(vary=True), min_terminal_arms=30)
    ci = enough["stats"]["fer"]
    assert ci["ci95"] is not None and len(ci["ci95"]) == 2
    assert ci["ci95"][0] < ci["ci95"][1], "不得输出零宽点估计冒充高精度"
    assert ci["ci_unavailable"] == ""
    assert ci["ci_method"] == "bootstrap_percentile"


# -------------------------------------------------------------
# 4. 行对齐 / FER 参考分母
# -------------------------------------------------------------
def test_line_alignment_fer_reference():
    expected = _gt(_items([("菜心", 10), ("牛肉", 20), ("活魚", 30)]))
    # on 臂少一行、且品名模糊错一处；行对齐应报 2/3、未配对 GT 1 行
    on_pred = _gt(_items([("菜心", 10), ("牛肉錯", 20)]))
    off_pred = _gt(_items([("菜心", 10)]))
    sample = _sample("S-align", expected, on_pred, off_pred)
    report = eval_reporter.run_paired_eval([sample])

    line = report["line_alignment"]["on"]
    assert line["gt_lines"] == 3
    assert line["pred_lines"] == 2
    assert line["aligned_lines"] == 2
    assert line["unmatched_gt_lines"] == 1
    assert line["unmatched_pred_lines"] == 0
    assert line["alignment_rate"] == round(2 / 3.0, 4)

    fer = report["fer_reference"]["on"]
    assert fer["field_total"] > 0
    assert fer["field_mismatched"] > 0
    assert fer["fer"] == round(fer["field_mismatched"] / float(fer["field_total"]), 4)
    assert "compared_field_instances" in fer["formula"]
    # 每单输出也带行对齐，便于逐单复核
    per = report["per_sample_diff"][0]
    assert per["on"]["line_alignment"]["gt_lines"] == 3
    assert per["on"]["fer"] == report["stats"]["fer"]["mean_on"]


# -------------------------------------------------------------
# 4b. 生产 defaults 前后真实快照（注入读取函数，不做硬编码断言）
# -------------------------------------------------------------
def test_eval_does_not_modify_production_defaults():
    from app.services import settings_service

    snapshots = []

    def _reader():
        snap = settings_service.all_settings()
        snapshots.append(snap)
        return snap

    report = eval_reporter.run_paired_eval(
        _good_batch(), min_terminal_arms=8, defaults_reader=_reader)
    assert report["production_defaults_verified"] is True
    assert report["production_defaults_modified"] is False
    assert report["production_defaults_before"] == report["production_defaults_after"]
    assert len(snapshots) >= 2
    # 关键策略值前后一致（首轮/预判 False、抽取 True、kind TTL 保持）
    before = report["production_defaults_before"]
    assert before["vendor_precheck_enabled"] is False
    assert before["memory_first_round_inject_enabled"] is False
    assert before["memory_extract_enabled"] is True
    assert before["memory_ttl_days_price_range"] == 90


# -------------------------------------------------------------
# 5. CLI 入口：--offline-fixture 可离线运行且与既有 run_eval 兼容
# -------------------------------------------------------------
def test_cli_offline_fixture_runs_without_evalset(tmp_path):
    fixture = {
        "meta": {"prompt_version": "v1_2_0_sku_clean",
                 "config_fingerprint": "cfg-A",
                 "memory_snapshot_id": "ms-1",
                 "slo_p95_ms": 20000,
                 "min_terminal_arms": 8},
        "samples": _good_batch(),
    }
    fixture_path = tmp_path / "paired_fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    report_dir = tmp_path / "reports"

    rc = run_eval.main(["--split", "test", "--offline-fixture", str(fixture_path),
                        "--report-dir", str(report_dir)])
    assert rc == 0
    files = list(report_dir.glob("*_paired_ab_offline.json"))
    assert len(files) == 1, files
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["mode"] == "paired_ab_offline"
    assert saved["batch_valid"] is True and saved["n_pairs"] == 4
    assert saved["slo"]["slo_pass"] is True
    # CLI 未注入 defaults_reader → 诚实记为未验证（None），不硬编码 False
    assert saved["production_defaults_modified"] is None
    assert saved["production_defaults_verified"] is False
