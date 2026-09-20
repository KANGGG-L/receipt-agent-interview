# -*- coding: utf-8 -*-
"""M1（T5 收尾）回归测试：前端两条保存路径必须透传 item 级 confidence / unit_conversion_warning。

why：T5 让解析层不再丢弃 item 级 confidence、落库层也会写入 unit_conversion_warning，
但前端两条保存路径（AI 自动保存的 buildSavePayloadFromData、复核保存的
collectReviewFormData）都没把这两列放进 save payload；/api/save_edited 的兜底值
（confidence 0.5 / unit_conversion_warning 空串）随即把真值覆盖 —— 表现为
「T5 新增的逐行字段被自动保存抹平」，低置信行与花码提示在保存后消失。

P11 收尾口径变更：save_edited 侧不再对缺失的 item 置信度兜底 0.5，改落 NULL
（与识别落库同口径）——「无数据」不得伪装成「置信度 0.5」。因此本文件第 3 组
反向用例断言的是 NULL 而非 0.5。

覆盖（全离线，零外部模型调用、零 live 库写入）：
1. 真实前端函数产出：以 node vm 沙箱载入真实 main.js，调用真实的
   buildSavePayloadFromData / collectReviewFormData 取实际 payload（不是抄一段逻辑）
2. 端到端回环：把该 payload 原样 POST /api/save_edited(source=auto) → 落库 → 详情回读，
   断言两值原样保留（修复前为 0.5 / 空串）
3. 反向：缺列 / 非法值（confidence='high'、警告空串）不携带键 → 后端兜底语义与修复前一致
4. 静态接线：渲染期 appendTableRow 把两列挂到行 dataset（collectReviewFormData 的取值来源）
"""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HARNESS = os.path.join(_DEMO_DIR, "tests", "m1_save_payload_harness.js")
_MAIN_JS = os.path.join(_DEMO_DIR, "static", "js", "main.js")

_CONF = 0.40
_WARN = "包含街市花码，需人工核验"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """强制把 DB_PATH 指到 tmp_path，绝不写 live demo 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_item_conf_payload.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _frontend_payload(mode):
    """跑真实 main.js 的前端保存函数，返回实际产出的 save payload。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验（后端回环仍由静态断言兜底）")
    proc = subprocess.run([node, _HARNESS, _MAIN_JS, mode],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


def _post_and_read(client, payload):
    """POST /api/save_edited(source=auto) 后回读落库明细与详情视图。"""
    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    payload = dict(payload)
    payload["receipt_id"] = rid
    # 真实前端保存前先 GET 详情拿到 version（D17 乐观锁），此处等价补上
    payload["version"] = db.get_receipt_row(rid).version
    resp = client.post("/api/save_edited", headers={"X-Role": "staff"}, json=payload)
    assert resp.status_code == 200, resp.text
    stored = db.get_receipt_items(rid)[0]
    detail = client.get("/api/receipt/%d" % rid, headers={"X-Role": "staff"}).json()
    detail_item = (detail.get("data") or {}).get("items")[0]
    return stored, detail_item


# -------------------------------------------------------------
# 1. 两条保存路径的真实产出都带上了 T5 两列
# -------------------------------------------------------------
@pytest.mark.parametrize("mode", ["funnel", "form"])
def test_frontend_save_payload_carries_t5_item_fields(mode):
    item = _frontend_payload(mode)["items"][0]
    assert item.get("confidence") == pytest.approx(_CONF), (
        "%s 路径未透传 item 置信度（会被后端兜底 0.5 抹平）" % mode)
    assert item.get("unit_conversion_warning") == _WARN, (
        "%s 路径未透传单位换算警告（会被后端兜底空串抹平）" % mode)


# -------------------------------------------------------------
# 2. 端到端回环：值原样保留（修复前实测 0.5 / 空串）
# -------------------------------------------------------------
@pytest.mark.parametrize("mode", ["funnel", "form"])
def test_save_edited_preserves_t5_item_fields(client, mode):
    stored, detail_item = _post_and_read(client, _frontend_payload(mode))
    assert stored["confidence"] == pytest.approx(_CONF), "落库 item 置信度被兜底值覆盖"
    assert stored["unit_conversion_warning"] == _WARN, "落库单位换算警告被兜底值覆盖"
    assert detail_item["confidence"] == pytest.approx(_CONF), "详情视图未回读 item 置信度"
    assert detail_item["unit_conversion_warning"] == _WARN


# -------------------------------------------------------------
# 3. 反向：缺列/非法值不携带键 → 后端落 NULL，不再伪造 0.5
# -------------------------------------------------------------
@pytest.mark.parametrize("mode", ["funnel-missing", "form-missing"])
def test_missing_or_invalid_fields_store_null_not_fabricated_default(client, mode):
    payload = _frontend_payload(mode)
    item = payload["items"][0]
    assert "confidence" not in item, "非数值 item 置信度不得上浮到 payload"
    assert "unit_conversion_warning" not in item, "空警告不得上浮到 payload"

    stored, detail_item = _post_and_read(client, payload)
    assert stored["confidence"] is None, (
        "缺失时必须落 NULL——兜底 0.5 会把「无从判断」伪装成「中等置信度」")
    assert detail_item["confidence"] is None, "详情视图同样不得把 NULL 兜成数值"
    assert (stored["unit_conversion_warning"] or "") == ""


# -------------------------------------------------------------
# 4. 静态接线：取值来源（行 dataset）在渲染期必须写入
# -------------------------------------------------------------
def _read(rel_path):
    with open(os.path.join(_DEMO_DIR, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def test_render_row_exposes_t5_fields_on_dataset():
    js = _read(os.path.join("static", "js", "main.js"))
    assert "tr.dataset.confidence" in js, (
        "appendTableRow 必须把 item 置信度挂到行 dataset，否则复核保存采集不到")
    assert "tr.dataset.unitConversionWarning" in js, (
        "appendTableRow 必须把单位换算警告挂到行 dataset，否则复核保存采集不到")
    assert "itemPayload.confidence" in js, "buildSavePayloadFromData 未透传 confidence"
    assert "itemPayload.unit_conversion_warning" in js, (
        "buildSavePayloadFromData 未透传 unit_conversion_warning")
