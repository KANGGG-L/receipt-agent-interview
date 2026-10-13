# -*- coding: utf-8 -*-
"""确定性复核标记回归：行级「待核验」与整单级「需人工复核」的触发依据。

背景（产品口径已拍板）：
低置信角标原先按 ITEM_LOW_CONFIDENCE_THRESHOLD=0.40 判定，但实测模型自报置信度
集中在 0.85~0.98（38 个真实读数 min 0.45 / max 0.98），阈值永不触发；抬到 0.50 又会
让整表历史兜底值（0.5）全部标黄，0.40~0.49 之间没有任何可用中间档。
因此改用**确定性信号**触发，置信度降级为参考信息（保留展示、不再触发）。

新口径：
- 行级 = unit_conversion_warning 非空（huama_evaluator 在解析链路上确定性写入的
  街市花码/单位不可折算提示；随行落库、随详情透传）
- 整单级 = doc_form='ncr_handwritten'（街市手写单）或 math_warnings 非空（门禁未通过）
  整单信号只标在整单级提示条/列表行上，绝不贴到每一行。

覆盖：
1. 真实前端函数行为与真实 DOM 产物（node vm 沙箱载入真实 main.js，非抄逻辑）
2. 静态接线：两处表格共用同一份判据；两处整单提示条共用同一份判据
3. 旧阈值路线确已下线（无残留常量/变量）
"""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HARNESS = os.path.join(_DEMO_DIR, "tests", "frontend_review_flag_harness.js")
_MAIN_JS = os.path.join(_DEMO_DIR, "static", "js", "main.js")


def _frontend_result(live_json=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验")
    cmd = [node, _HARNESS, _MAIN_JS]
    if live_json:
        cmd.append(live_json)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    line = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")][-1]
    result = json.loads(line)
    assert proc.returncode == 0, "脚手架断言失败: %s" % result.get("failures")
    return result


def test_frontend_review_flag_behaviour_and_dom():
    """真实 main.js 的函数行为 + 两处表格的真实 DOM 产物。"""
    result = _frontend_result()
    assert result["fail"] == 0, result["failures"]
    assert result["pass"] >= 30, "脚手架用例数异常偏少: %s" % result["pass"]
    sample = result["sample"]
    assert sample["huamaRowHasBadge"] is True, "含花码的行必须渲染角标"
    assert sample["lowConfRowHasBadge"] is False, "仅低置信（无确定性信号）不得再触发角标"
    assert "item-conf-badge" in sample["badgeHtml"], "必须复用既有 item-conf-badge 样式"


def test_frontend_review_flag_on_real_receipt_payload():
    """真实单据 payload 走生产函数：手写单触发整单级、普通印刷单不触发。

    采用 live 库真实单据的详情快照（fixture 固定内容，不放宽成读取 live 文件）。
    """
    handwriting = {
        "receipt_id": 1475, "status": "uploaded", "doc_form": "ncr_handwritten",
        "math_warnings": [], "items": [{"name": "菜心", "confidence": 0.95}],
    }
    printed = {
        "receipt_id": 1491, "status": "parsed", "doc_form": "printed_delivery_note",
        "math_warnings": [], "items": [{"name": "走地鸡", "confidence": 0.95}],
    }
    tmp = os.path.join(_DEMO_DIR, "tests", "_tmp_review_flag_payload.json")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(handwriting, f, ensure_ascii=False)
        live = _frontend_result(tmp)["live"]
        assert live["doc_form"] == "ncr_handwritten"
        assert len(live["reasons"]) == 1, live["reasons"]
        assert "手写单" in live["reasons"][0]
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(printed, f, ensure_ascii=False)
        live2 = _frontend_result(tmp)["live"]
        assert live2["reasons"] == [], "普通印刷单不得被判为需人工复核"
        assert live2["row_flagged_count"] == 0
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read(rel_path):
    with open(os.path.join(_DEMO_DIR, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def _frontend_js_sources():
    """main.js + 已抽离域文件的全文映射（F4/F5 S4-11 起 main.js 按域拆分为多文件，
    行级/整单级判据的调用点分布在 main.js 与域文件两侧，守真不变量按并集判定）。"""
    rels = ["static/js/main.js", "static/js/supplier_archive.js",
            "static/js/dish_consumption.js", "static/js/cost_report.js",
            "static/js/departments.js", "static/js/inventory_board.js",
            "static/js/archive_list.js",
            # F5 S4-11 收口拆分的 8 个域文件（顺序＝index.html 加载链）
            "static/js/common.js", "static/js/upload.js", "static/js/review_core.js",
            "static/js/workbench_queue.js", "static/js/review_modal.js",
            "static/js/version_conflict.js", "static/js/photo_sider.js",
            "static/js/finance_ops.js"]
    return {r: _read(r) for r in rels}


def test_static_wiring_single_source_of_truth():
    sources = _frontend_js_sources()
    union = "\n".join(sources.values())
    # 日志阈值路线彻底下线（main.js 与全部域文件均不得残留）
    for rel, js in sources.items():
        assert "ITEM_LOW_CONFIDENCE_THRESHOLD" not in js, rel + ": 旧阈值常量必须移除"
        assert "isLowConfidence" not in js, rel + ": 不得残留按置信度触发复核的判定"
    # 两处明细表共用同一份行级判据（复核台自 F5 S4-11 起在 review_core.js；归档详情自 F4 S4-11 起在 archive_list.js）
    assert union.count("itemFlags = itemReviewFlags(item);") == 2, \
        "复核台与归档详情必须共用同一份行级判据（全前端并集恰有两处调用点）"
    assert "itemFlags = itemReviewFlags(item);" in sources["static/js/review_core.js"], \
        "复核台调用点应随复核核心域迁至 review_core.js"
    assert "itemFlags = itemReviewFlags(item);" in sources["static/js/archive_list.js"], \
        "归档详情调用点应随域迁至 archive_list.js"
    # 两处整单提示条共用同一份整单判据（1 处定义 + 2 处调用）
    assert union.count("receiptReviewReasons(data)") == 3, \
        "整单级判据必须在复核台与归档详情两处共用"
    # 列表整单级：手写单信号 + 待核对态白名单（判据与详情提示条同源，不抄第二份）
    assert "isHandwrittenReceipt" in union, "列表未接入共用的手写单判据"
    assert "isReceiptPendingHumanReview" in union, "列表黄底缺少「待人工核对」状态判据"
    # 「街市手写单」的字面量只能有一处定义（防止多份实现各自漂移）
    assert union.count("'ncr_handwritten'") == 1, "手写单 doc_form 字面量必须收口为单一常量定义"
    # 置信度保留为参考信息
    assert "tr.dataset.confidence" in union, "置信度读数应仍随行回传（保存闭环）"


def test_receipt_level_signal_read_is_not_silently_swallowed():
    """整单级信号（质量预检/交叉审核分歧）的读取不得再被空 catch 静默吞掉。

    原实现 `try { ... } catch (e) {}`：一旦抛错，「质量预检 / 重点复核」会从该单据的
    每一行上静默消失且不留痕迹（本仓高发第三类缺陷）。现要求：异常可观测（console.warn）
    且 fail-visible（行标「待核验」）。
    """
    # F5 S4-11：appendTableRow 与读取器已随复核核心域迁至 review_core.js；
    # 判据仍钉「唯一实现所在文件」，不放宽为只查存在性。
    js = _read(os.path.join("static", "js", "review_core.js"))
    assert "function readReceiptLevelReviewSignals(" in js, "缺少整单级信号的可观测读取器"
    assert "console.warn('[review-flag]" in js, "读取失败必须留痕（console.warn）"

    start = js.index("function appendTableRow(")
    end = js.index("function addEmptyRow(")
    body = js[start:end]
    assert "catch (e) {}" not in body, "appendTableRow 内不得再有空 catch"
    assert "readReceiptLevelReviewSignals()" in body, "appendTableRow 必须走可观测读取器"
    assert "receipt_signal_unreadable" in body, "读取失败时必须 fail-visible（标「待核验」）"
