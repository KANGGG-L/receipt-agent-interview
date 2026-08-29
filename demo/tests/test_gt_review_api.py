# -*- coding: utf-8 -*-
"""T4 GT 异构生成 + 人工抽检台 测试（Gap A2）。

覆盖：
1. confirm 后状态流转：draft → confirmed，写 gt_reviewed_by / gt_reviewed_at
2. test 集未全量 confirmed 时，run_eval --require-confirmed 拒绝出分（SystemExit）
3. confirm / sample 详情等抽检接口需 admin 权限（非 admin 403）
4. stats 接口返回各 split 的 draft/confirmed 计数
5. samples 列表按 split + gt_status 过滤
6. 路径穿越变体（../../ 与 ..\..\）被 manifest 白名单拒绝（404）
7. 空总额 confirm 需显式 confirm_blank_total=true（月结单场景）
8. 批量确认按钮风险显式化（前端静态断言）
9. sample 详情返回缩略图（长边 <=1400px 的 JPEG，不再返回原图 base64）
10. confirm 提交 quantity 键时自动归一为 qty 落盘

全部离线：样例图片用 PIL 生成的小图，零外部调用。
"""

import csv
import json
import os
import sys

os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_gt_review.db")
os.environ.setdefault("AUTH_ENABLED", "0")

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(DEMO_DIR, "scripts")
for _p in (SCRIPTS_DIR, DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import build_evalset
import run_eval


# ------------------------------------------------------------------
# 夹具：临时 evalsets 目录（合成小图 + manifest），零外部调用
# ------------------------------------------------------------------
_SYNTH_SUPPLIERS = ["Alpha Trading Co", "Beta Food Ltd"]
GT_MODEL = "test/vision-model"


def _make_sources(tmp_path, per_form=3):
    """构造合成语料目录：小图 + classification_manifest.csv。"""
    from PIL import Image

    src = tmp_path / "sources"
    src.mkdir(parents=True, exist_ok=True)
    rows = []
    n = 0
    for hint in ("printed", "handwritten"):
        for i in range(per_form):
            fname = "IMG_%s_%02d.png" % (hint, i)
            Image.new("RGB", (1100, 800), "white").save(str(src / fname))
            rows.append({
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
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return src


def _read_manifest(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_manifest(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


_CANDIDATE_GT = {
    "supplier_name": "Alpha Trading Co",
    "date": "2026-08-01",
    "total_amount": 120.0,
    "items": [
        {"name": "白菜", "quantity": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
    ],
}


def _write_draft(evalset_dir, sample_id, payload=None):
    """模拟 gen_gt_candidates 的产物：expected/<id>.json（draft）+ manifest 状态。"""
    exp_dir = os.path.join(evalset_dir, "expected")
    os.makedirs(exp_dir, exist_ok=True)
    body = dict(payload or _CANDIDATE_GT)
    body["gt_status"] = "draft"
    body["gt_source_model"] = GT_MODEL
    body["gt_reviewed_by"] = None
    body["gt_reviewed_at"] = None
    with open(os.path.join(exp_dir, sample_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    mpath = os.path.join(evalset_dir, "manifest.csv")
    rows = _read_manifest(mpath)
    for r in rows:
        if r["sample_id"] == sample_id:
            r["gt_status"] = "draft"
            r["gt_source_model"] = GT_MODEL
    _write_manifest(mpath, rows)
    return body


@pytest.fixture
def evalset_env(tmp_path, monkeypatch):
    """建一个临时评测集并注入 EVALSET_DIR；返回 (目录路径, manifest行)。"""
    out = tmp_path / "evalsets"
    build_evalset.build(str(_make_sources(tmp_path)), str(out))
    evalset_dir = str(out)
    monkeypatch.setenv("EVALSET_DIR", evalset_dir)
    rows = _read_manifest(os.path.join(evalset_dir, "manifest.csv"))
    test_rows = [r for r in rows if r["split"] == "test"]
    assert test_rows, "合成语料必须产生 test 样本"
    # 全部 test 样本先落 draft 候选
    for r in test_rows:
        _write_draft(evalset_dir, r["sample_id"])
    return evalset_dir, rows


@pytest.fixture
def client(evalset_env):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _admin_headers(role="admin"):
    return {"X-Role": role}


# ------------------------------------------------------------------
# 1. confirm 状态流转
# ------------------------------------------------------------------
def test_confirm_flow_draft_to_confirmed(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    corrected = json.loads(json.dumps(_CANDIDATE_GT))
    corrected["total_amount"] = 130.0
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": corrected}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text

    # expected JSON：状态流转 + 审阅人/时间
    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["gt_status"] == "confirmed"
    assert saved["total_amount"] == 130.0
    assert saved["gt_reviewed_by"] == "admin@demo.hk"
    assert saved["gt_reviewed_at"], "必须写 gt_reviewed_at"

    # manifest 同步更新
    rows2 = {r["sample_id"]: r for r in _read_manifest(os.path.join(evalset_dir, "manifest.csv"))}
    assert rows2[sid]["gt_status"] == "confirmed"


def test_confirm_unknown_sample_404(evalset_env, client):
    resp = client.post("/api/evalset/sample/S999/confirm",
                       json={"gt": _CANDIDATE_GT}, headers=_admin_headers())
    assert resp.status_code == 404


def test_confirm_rejects_empty_gt(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": {}}, headers=_admin_headers())
    assert resp.status_code == 400


# ------------------------------------------------------------------
# 2. --require-confirmed 门禁（未全量 confirmed 拒绝出分）
# ------------------------------------------------------------------
def test_require_confirmed_refuses_to_score(evalset_env):
    evalset_dir, rows = evalset_env
    # test 集有 draft（fixture 已写）→ 必须拒绝
    with pytest.raises(SystemExit) as ei:
        run_eval.run_eval(split="test", engine="stub", evalset_dir=evalset_dir,
                          report_dir=os.path.join(evalset_dir, "reports"),
                          require_confirmed=True)
    msg = str(ei.value)
    assert "confirmed" in msg, "错误信息必须说明 GT 未确认"

    # 全部 confirmed 后即可出分
    mpath = os.path.join(evalset_dir, "manifest.csv")
    all_rows = _read_manifest(mpath)
    for r in all_rows:
        if r["split"] == "test":
            r["gt_status"] = "confirmed"
    _write_manifest(mpath, all_rows)
    report = run_eval.run_eval(split="test", engine="stub", evalset_dir=evalset_dir,
                               report_dir=os.path.join(evalset_dir, "reports"),
                               require_confirmed=True)
    assert report["n_scored"] == len([r for r in all_rows if r["split"] == "test"])


def test_require_confirmed_default_off_backward_compatible(evalset_env):
    """默认关：draft 状态下不带开关照常出分（向后兼容）。"""
    evalset_dir, rows = evalset_env
    report = run_eval.run_eval(split="test", engine="stub", evalset_dir=evalset_dir,
                               report_dir=os.path.join(evalset_dir, "reports"))
    assert report["n_samples"] == len([r for r in rows if r["split"] == "test"])


# ------------------------------------------------------------------
# 3. 抽检接口权限（仅 admin）
# ------------------------------------------------------------------
def test_confirm_requires_admin(evalset_env, client):
    evalset_dir, rows = evalset_env
    sid = [r for r in rows if r["split"] == "test"][0]["sample_id"]
    for role in ("owner", "staff"):
        resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                           json={"gt": _CANDIDATE_GT}, headers={"X-Role": role})
        assert resp.status_code == 403, "%s 不应允许 confirm" % role
    # 不带角色头（默认 owner）同样 403
    resp = client.post("/api/evalset/sample/%s/confirm" % sid, json={"gt": _CANDIDATE_GT})
    assert resp.status_code == 403


def test_stats_requires_admin(evalset_env, client):
    assert client.get("/api/evalset/stats", headers={"X-Role": "owner"}).status_code == 403
    assert client.get("/api/evalset/stats").status_code == 403


def test_sample_detail_requires_admin(evalset_env, client):
    evalset_dir, rows = evalset_env
    sid = [r for r in rows if r["split"] == "test"][0]["sample_id"]
    assert client.get("/api/evalset/sample/%s" % sid, headers={"X-Role": "owner"}).status_code == 403


# ------------------------------------------------------------------
# 4. stats 各 split draft/confirmed 计数
# ------------------------------------------------------------------
def test_stats_counts_by_split(evalset_env, client):
    evalset_dir, rows = evalset_env
    resp = client.get("/api/evalset/stats", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]["splits"]
    for split in ("train", "val", "test"):
        assert split in data
        assert set(data[split].keys()) >= {"draft", "confirmed", "missing", "total"}
    n_test = len([r for r in rows if r["split"] == "test"])
    assert data["test"]["draft"] == n_test
    assert data["test"]["confirmed"] == 0
    assert data["test"]["total"] == n_test


# ------------------------------------------------------------------
# 5. samples 列表 + 详情
# ------------------------------------------------------------------
def test_samples_list_filters(evalset_env, client):
    evalset_dir, rows = evalset_env
    resp = client.get("/api/evalset/samples?split=test&gt_status=draft", headers=_admin_headers())
    assert resp.status_code == 200
    items = resp.json()["data"]["samples"]
    assert items and all(s["gt_status"] == "draft" for s in items)
    assert all(s["split"] == "test" for s in items)

    resp2 = client.get("/api/evalset/samples?split=test&gt_status=confirmed", headers=_admin_headers())
    assert resp2.json()["data"]["samples"] == []


def test_sample_detail_returns_image_and_gt(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    resp = client.get("/api/evalset/sample/%s" % sid, headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["sample_id"] == sid
    assert data["image_base64"].startswith("/9j/"), "必须返回图片 base64（JPEG 缩略图头）"
    assert data["image_mime"] == "image/jpeg"
    assert data["gt"]["gt_status"] == "draft"
    assert data["gt"]["gt_source_model"] == GT_MODEL


def test_sample_detail_returns_thumbnail_not_original(evalset_env, client):
    """详情返回压缩缩略图：长边 <=1400px、JPEG；payload 远小于原图。"""
    import base64 as _b64
    import io

    from PIL import Image

    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    # 用大图（3024x4032 噪点，接近真实 iPhone 原图）替换 receipts 里的样本图
    rel = None
    for r in rows:
        if r["sample_id"] == sid:
            rel = r["image"]
    big_path = os.path.join(evalset_dir, rel)
    Image.effect_noise((3024, 4032), 32).convert("RGB").save(big_path, "PNG")

    resp = client.get("/api/evalset/sample/%s" % sid, headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["image_base64"].startswith("/9j/"), "缩略图必须是 JPEG"
    raw = _b64.b64decode(data["image_base64"])
    assert len(raw) < 1_500_000, "缩略图 base64 必须控制在约 1.5MB 内（原图约 14MB）"
    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) <= 1400, "缩略图长边必须 <=1400px"
    # 原图文件本身不动
    with Image.open(big_path) as orig:
        assert max(orig.size) == 4032, "原图文件不得被缩略逻辑改写"


def test_confirm_normalizes_quantity_to_qty(evalset_env, client):
    """用户/候选提交 quantity 键时，confirm 落盘统一归一为 qty。"""
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    gt = json.loads(json.dumps(_CANDIDATE_GT))   # _CANDIDATE_GT.items 用的是 quantity 键
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text

    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["items"], "明细行不得丢失"
    for it in saved["items"]:
        assert "qty" in it, "落盘明细必须统一为 qty 键"
        assert "quantity" not in it, "落盘明细不得残留 quantity 键"
    assert saved["items"][0]["qty"] == _CANDIDATE_GT["items"][0]["quantity"], "qty 数值必须保留"


def test_sample_detail_gt_normalizes_quantity_to_qty(evalset_env, client):
    """详情接口返回的 gt 同样归一 quantity -> qty（前端表单读 qty 键）。"""
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    # 直接落一份带 quantity 键的 draft 候选（模拟旧生成脚本产物）
    _write_draft(evalset_dir, sid)
    resp = client.get("/api/evalset/sample/%s" % sid, headers=_admin_headers())
    assert resp.status_code == 200
    for it in resp.json()["data"]["gt"]["items"]:
        assert "qty" in it and "quantity" not in it


# ------------------------------------------------------------------
# 6. 路径穿越防御（manifest 白名单：_find_row 精确匹配，命中不了即 404）
# ------------------------------------------------------------------
_TRAVERSAL_IDS = [
    "..%2F..%2Fetc%2Fpasswd",            # URL 编码的 ../../etc/passwd（到达 handler 内的 _find_row）
    "..%5C..%5Cetc%5Cpasswd",            # URL 编码的 ..\..\etc\passwd
    "..%2F..%2Fexpected%2FS001%2Ejson",  # 指向 expected 目录本身的穿越变体
]


def test_path_traversal_get_sample_404(evalset_env, client):
    evalset_dir, _ = evalset_env
    exp_dir = os.path.join(evalset_dir, "expected")
    before = set(os.listdir(exp_dir)) if os.path.isdir(exp_dir) else set()
    for bad in _TRAVERSAL_IDS:
        resp = client.get("/api/evalset/sample/%s" % bad, headers=_admin_headers())
        assert resp.status_code == 404, "穿越变体 %s 必须被 manifest 白名单拒绝" % bad
    # 纯反斜杠变体（不触发客户端 URL 归一化，直接进入 _find_row）
    resp = client.get("/api/evalset/sample/..\\..\\etc\\passwd", headers=_admin_headers())
    assert resp.status_code == 404
    after = set(os.listdir(exp_dir)) if os.path.isdir(exp_dir) else set()
    assert before == after, "穿越请求不得在 expected 目录留下任何新文件"


def test_path_traversal_confirm_404(evalset_env, client):
    evalset_dir, _ = evalset_env
    exp_dir = os.path.join(evalset_dir, "expected")
    before = set(os.listdir(exp_dir)) if os.path.isdir(exp_dir) else set()
    for bad in _TRAVERSAL_IDS:
        resp = client.post("/api/evalset/sample/%s/confirm" % bad,
                           json={"gt": dict(_CANDIDATE_GT)}, headers=_admin_headers())
        assert resp.status_code == 404, "穿越变体 %s 的 confirm 必须被拒绝" % bad
    resp = client.post("/api/evalset/sample/..\\..\\etc\\passwd/confirm",
                       json={"gt": dict(_CANDIDATE_GT)}, headers=_admin_headers())
    assert resp.status_code == 404
    after = set(os.listdir(exp_dir)) if os.path.isdir(exp_dir) else set()
    assert before == after, "穿越 confirm 不得写入任何 expected 文件"


# ------------------------------------------------------------------
# 7. 空总额守卫（月结单等场景可留空，但必须显式确认）
# ------------------------------------------------------------------
def test_confirm_blank_total_requires_explicit_flag(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    gt = json.loads(json.dumps(_CANDIDATE_GT))
    gt["total_amount"] = None
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 400
    assert "总额为空" in resp.json()["msg"], "400 提示必须为人话"
    assert "confirm_blank_total=true" in resp.json()["msg"]

    # 空字符串同样按空总额处理
    gt2 = json.loads(json.dumps(_CANDIDATE_GT))
    gt2["total_amount"] = ""
    resp2 = client.post("/api/evalset/sample/%s/confirm" % sid,
                        json={"gt": gt2}, headers=_admin_headers())
    assert resp2.status_code == 400

    # manifest 状态不得被 400 请求改动
    rows2 = {r["sample_id"]: r for r in _read_manifest(os.path.join(evalset_dir, "manifest.csv"))}
    assert rows2[sid]["gt_status"] == "draft"


def test_confirm_blank_total_with_explicit_flag_succeeds(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    gt = json.loads(json.dumps(_CANDIDATE_GT))
    gt["total_amount"] = None
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt, "confirm_blank_total": True}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text

    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["total_amount"] is None, "显式确认后空总额必须原样保留（禁止静默补 0）"
    assert saved["gt_status"] == "confirmed"

    # 合法金额 0 不算空总额，无需显式字段
    sid2 = test_rows[1]["sample_id"] if len(test_rows) > 1 else sid
    gt2 = json.loads(json.dumps(_CANDIDATE_GT))
    gt2["total_amount"] = 0
    resp2 = client.post("/api/evalset/sample/%s/confirm" % sid2,
                        json={"gt": gt2}, headers=_admin_headers())
    assert resp2.status_code == 200, "金额 0 是合法金额，不应被空总额守卫拦截"


# ------------------------------------------------------------------
# 8. 批量确认按钮：风险显式化（前端静态断言，模式同 tests/test_u10）
# ------------------------------------------------------------------
def test_batch_confirm_ui_discloses_unreviewed_risk():
    demo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(demo_dir, "templates", "evalset.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(demo_dir, "static", "js", "evalset.js"), encoding="utf-8") as f:
        js = f.read()

    # 按钮文案必须声明「未经逐张核对」
    assert "未经逐张核对" in html, "批量按钮文案必须显式声明未经逐张核对"
    assert "批量确认本批未改动样本" not in html, "旧文案暗示已核对，必须移除"

    # 确认弹窗必须列出 ID 清单与总数，并警告直接参与出分
    assert "未经人工逐张核对" in js
    assert "将直接参与出分" in js
    assert "ids.join" in js, "弹窗必须列出将确认的样本 ID 清单"
    assert "ids.length" in js, "弹窗必须给出将确认的总数"

    # 有未保存编辑时必须先让用户选择放弃或取消
    assert "formDirty" in js, "必须有表单脏标记检测"
    assert "放弃编辑" in js and "取消" in js

    # 空总额确认链路：前端需携带 confirm_blank_total
    assert "confirm_blank_total" in js


# ------------------------------------------------------------------
# 9. 显式翻页按钮：必须走 IIFE 内的包装函数（inline onclick 拿不到局部 pos）
# ------------------------------------------------------------------
def test_explicit_paging_buttons_use_wrapper_functions():
    demo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(demo_dir, "templates", "evalset.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(demo_dir, "static", "js", "evalset.js"), encoding="utf-8") as f:
        js = f.read()

    assert 'onclick="nextSample()"' in html, "下一张按钮必须调用 nextSample 包装函数"
    assert 'onclick="prevSample()"' in html, "上一张按钮必须调用 prevSample 包装函数"
    assert 'gotoSample(pos' not in html, "inline onclick 引用局部 pos 会 ReferenceError，禁止"
    assert "window.nextSample = nextSample" in js and "window.prevSample = prevSample" in js
    # 快捷键逻辑保留
    assert "e.key === 'j'" in js and "e.key === 'k'" in js and "e.key === 'a'" in js


# ------------------------------------------------------------------
# 10. gen_gt_candidates 落盘归一：quantity -> qty（模型再返回 quantity 也转 qty）
# ------------------------------------------------------------------
def test_gen_gt_normalize_and_validate():
    import gen_gt_candidates

    gt = {"supplier_name": "X", "date": "2026-08-01", "total_amount": 20.0,
          "items": [{"name": "白菜", "quantity": 2.0, "unit": "斤",
                     "unit_price": 10.0, "amount": 20.0}]}
    gt = gen_gt_candidates.normalize_gt_items(gt)
    assert gen_gt_candidates.validate_gt(gt) is None
    assert gt["items"][0]["qty"] == 2.0
    assert "quantity" not in gt["items"][0]

    # 已带 qty 时 quantity 冗余键被丢弃，且不覆盖 qty
    gt2 = {"items": [{"name": "a", "qty": 5, "quantity": 9}]}
    gt2 = gen_gt_candidates.normalize_gt_items(gt2)
    assert gt2["items"][0]["qty"] == 5 and "quantity" not in gt2["items"][0]

    # 缺 qty/quantity 的明细校验失败
    bad = {"supplier_name": "X", "date": "", "total_amount": 0,
           "items": [{"name": "a", "unit": "斤"}]}
    err = gen_gt_candidates.validate_gt(bad)
    assert err is not None and "qty" in err

    # 兼容旧 prompt 输出 quantity：normalize 后再校验可通过
    old_gt = {"supplier_name": "X", "date": "", "total_amount": 0,
              "items": [{"name": "a", "quantity": 3}]}
    assert gen_gt_candidates.validate_gt(
        gen_gt_candidates.normalize_gt_items(old_gt)) is None
