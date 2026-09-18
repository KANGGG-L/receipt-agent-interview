# -*- coding: utf-8 -*-
"""T4 GT 异构生成 + 人工抽检台 测试（Gap A2）。

覆盖：
1. confirm 后状态流转：draft → confirmed，写 gt_reviewed_by / gt_reviewed_at
2. test 集未全量 confirmed 时，run_eval --require-confirmed 拒绝出分（SystemExit）
3. confirm / sample 详情等抽检接口需 admin 权限（非 admin 403；不带角色的匿名 401 未认证）
4. stats 接口返回各 split 的 draft/confirmed 计数
5. samples 列表按 split + gt_status 过滤
6. 路径穿越变体（../../ 与 ..\..\）被 manifest 白名单拒绝（404）
7. 空总额 confirm 需显式 confirm_blank_total=true（月结单场景）
8. 批量确认按钮风险显式化（前端静态断言）
9. sample 详情返回缩略图（长边 <=1400px 的 JPEG，不再返回原图 base64）
10. confirm 提交 quantity 键时自动归一为 qty 落盘
11. GT schema v2（合并反馈①）：payment_marked 必填且必须布尔；缺 v2 字段 400；
    confirmed 允许再次 confirm（覆盖更新 + 刷新 reviewed_at）
12. 工作台路由 /evalset/workbench/<sid>（合并反馈②）：200 且复用 index.html
    同源复核 DOM（同一关键容器 id），并注入 eval_workbench.js 适配器

全部离线：样例图片用 PIL 生成的小图，零外部调用。
"""

import csv
import json
import os
import sys

os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_gt_review.db")
os.environ.setdefault("AUTH_ENABLED", "0")

_candidates = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo")),
]
DEMO_DIR = next(c for c in _candidates if os.path.isfile(os.path.join(c, "templates", "index.html")))
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
    "payment_marked": False,
    "payment_evidence": "",
    "currency": "HKD",
    "discount_amount": 0.0,
    "deposit_amount": 0.0,
    "delivery_fee": 0.0,
    "service_fee": 0.0,
    "tax_amount": 0.0,
    "rounding_adjustment": 0.0,
    "adjustment_notes": [],
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


def test_require_confirmed_default_enforced_on_test_split(evalset_env):
    """L2 收口：test split 默认强制 GT 确认（存在 draft 即拒绝出分）。

    旧语义（默认关）已收口为生产准入口径：test 默认 True；val/train 默认仍关，
    需显式 --require-confirmed；显式 require_confirmed=False 可出分但报告带
    gt_status=partial 标记。
    """
    evalset_dir, rows = evalset_env
    # 默认（require_confirmed=None → test split 强制）：有 draft 拒绝出分
    with pytest.raises(SystemExit):
        run_eval.run_eval(split="test", engine="stub", evalset_dir=evalset_dir,
                          report_dir=os.path.join(evalset_dir, "reports"))
    # 显式关闭门禁：可出分，报告必须带 partial 标记
    report = run_eval.run_eval(split="test", engine="stub", evalset_dir=evalset_dir,
                               report_dir=os.path.join(evalset_dir, "reports"),
                               require_confirmed=False)
    assert report["n_samples"] == len([r for r in rows if r["split"] == "test"])
    assert report.get("gt_status") == "partial", \
        "test split 显式关闭门禁出分必须带 gt_status=partial 标记"


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
    # 不带角色头 = 匿名：401 未认证（后端不再静默当 owner，避免误报「权限不足：当前角色为老板」）
    resp = client.post("/api/evalset/sample/%s/confirm" % sid, json={"gt": _CANDIDATE_GT})
    assert resp.status_code == 401, "匿名应判未认证 401，实际 %s" % resp.status_code
    detail = resp.json()["detail"]
    assert "未认证" in detail and "X-Role" in detail, detail


def test_stats_requires_admin(evalset_env, client):
    assert client.get("/api/evalset/stats", headers={"X-Role": "owner"}).status_code == 403
    anonymous = client.get("/api/evalset/stats")
    assert anonymous.status_code == 401, "匿名应判未认证 401"
    assert "未认证" in anonymous.json()["detail"]


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
    demo_dir = DEMO_DIR
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

    # 队列页已收敛为无表单导航面（单一编辑表面=工作台），不存在「未保存编辑」问题，
    # 批量确认弹窗应指引用户进工作台做人工校正
    assert "formDirty" not in js, "队列页无内联表单，不应再有表单脏标记逻辑"
    assert "工作台" in js, "批量确认弹窗必须指引用户进工作台做人工校正"

    # 空总额确认链路：前端需携带 confirm_blank_total
    assert "confirm_blank_total" in js


# ------------------------------------------------------------------
# 9. 显式翻页按钮：必须走 IIFE 内的包装函数（inline onclick 拿不到局部 pos）
# ------------------------------------------------------------------
def test_explicit_paging_buttons_use_wrapper_functions():
    demo_dir = DEMO_DIR
    with open(os.path.join(demo_dir, "templates", "evalset.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(demo_dir, "static", "js", "evalset.js"), encoding="utf-8") as f:
        js = f.read()

    assert 'onclick="nextSample()"' in html, "下一张按钮必须调用 nextSample 包装函数"
    assert 'onclick="prevSample()"' in html, "上一张按钮必须调用 prevSample 包装函数"
    assert 'gotoSample(pos' not in html, "inline onclick 引用局部 pos 会 ReferenceError，禁止"
    assert "window.nextSample = nextSample" in js and "window.prevSample = prevSample" in js
    # 快捷键逻辑保留（仅 J/K 导航；A 键确认已随队列表单移除，确认只在工作台发生）
    assert "e.key === 'j'" in js and "e.key === 'k'" in js
    assert "e.key === 'a'" not in js, "队列页不应有 A 键确认，单张确认只在工作台发生"


# ------------------------------------------------------------------
# 9b. 单一编辑表面收敛（用户反馈③）：队列页无内联校正表单（f* 无付款标记控件，
#     与店员界面不一致），单张确认唯一入口是工作台 /evalset/workbench/<sid>
# ------------------------------------------------------------------
def test_evalset_queue_page_is_navigation_only_single_edit_surface():
    demo_dir = DEMO_DIR
    with open(os.path.join(demo_dir, "templates", "evalset.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(demo_dir, "static", "js", "evalset.js"), encoding="utf-8") as f:
        js = f.read()

    # T4 时期的内联校正表单必须整体移除（模板与 JS 双侧）
    for forbidden in ("fSupplier", "fDate", "fTotal", "fDocForm", "itemsBody",
                      "collectGt", "addItemRow", "confirmCurrent", "formDirty"):
        assert forbidden not in html, "队列页模板不应再含队列表单残留: %s" % forbidden
        assert forbidden not in js, "队列页 JS 不应再读队列表单字段: %s" % forbidden
    assert "<input" not in html and "<table class=\"items\"" not in html, \
        "队列页不得含任何可编辑输入框或明细行编辑表格"

    # 空出版面用只读样本信息卡补充（来自 samples 接口，无可编辑控件）
    assert "样本信息" in html and "候选来源模型" in html and "gtStatusBadge" in html
    assert "sampleInfoCard" in html and "infoSampleId" in html

    # 单张确认唯一入口=工作台：按钮与逐行入口、选中高亮都必须存在
    assert "在工作台核对" in html, "队列页必须保留「在工作台核对」入口"
    assert "openWorkbench" in js and "/evalset/workbench/" in js
    assert "sample-row" in js and "selected" in js, "样本列表当前选中样本必须视觉高亮"

    # 保留面：过滤/进度/批量原样确认/回流候选
    assert "splitSelect" in html and "statusSelect" in html
    assert "progressFill" in html
    assert "batchConfirmUntouched" in js and "api/evalset/candidates" in js


# ------------------------------------------------------------------
# 10. gen_gt_candidates 落盘归一：quantity -> qty（模型再返回 quantity 也转 qty）
#     v2：payment_marked 宽容转布尔、费用缺省 0、币种缺省 HKD
# ------------------------------------------------------------------
def test_gen_gt_normalize_and_validate():
    import gen_gt_candidates

    gt = {"supplier_name": "X", "date": "2026-08-01", "total_amount": 20.0,
          "payment_marked": "true", "items": [{"name": "白菜", "quantity": 2.0,
                                               "unit": "斤", "unit_price": 10.0,
                                               "amount": 20.0}]}
    gt = gen_gt_candidates.normalize_gt_items(gt)
    assert gen_gt_candidates.validate_gt(gt) is None
    assert gt["items"][0]["qty"] == 2.0
    assert "quantity" not in gt["items"][0]
    # v2 归一：payment_marked 转布尔、缺省字段补齐
    assert gt["payment_marked"] is True
    assert gt["currency"] == "HKD"
    assert gt["delivery_fee"] == 0.0
    assert gt["adjustment_notes"] == []

    # 已带 qty 时 quantity 冗余键被丢弃，且不覆盖 qty
    gt2 = {"items": [{"name": "a", "qty": 5, "quantity": 9}]}
    gt2 = gen_gt_candidates.normalize_gt_items(gt2)
    assert gt2["items"][0]["qty"] == 5 and "quantity" not in gt2["items"][0]

    # 缺 qty/quantity 的明细校验失败
    bad = {"supplier_name": "X", "date": "", "total_amount": 0,
           "payment_marked": False, "items": [{"name": "a", "unit": "斤"}]}
    err = gen_gt_candidates.validate_gt(bad)
    assert err is not None and "qty" in err

    # 兼容旧 prompt 输出 quantity：normalize 后再校验可通过
    old_gt = {"supplier_name": "X", "date": "", "total_amount": 0,
              "payment_marked": False,
              "items": [{"name": "a", "quantity": 3}]}
    assert gen_gt_candidates.validate_gt(
        gen_gt_candidates.normalize_gt_items(old_gt)) is None


def test_gen_gt_validate_requires_bool_payment_marked():
    """v2：payment_marked 缺失/非布尔在 validate_gt 拦截（生成侧质量门）。"""
    import gen_gt_candidates

    base = {"supplier_name": "X", "date": "", "total_amount": 0,
            "items": [{"name": "a", "qty": 1, "unit": "斤", "unit_price": 1, "amount": 1}]}
    no_mark = dict(base)
    assert "payment_marked" in gen_gt_candidates.validate_gt(no_mark)
    str_mark = dict(base, payment_marked="true")
    assert "布尔" in gen_gt_candidates.validate_gt(str_mark)
    int_mark = dict(base, payment_marked=1)
    assert "布尔" in gen_gt_candidates.validate_gt(int_mark)


# ------------------------------------------------------------------
# 11. GT schema v2 confirm 校验（合并反馈①）：
#     payment_marked 必填且必须布尔；缺 v2 字段 400；再确认覆盖更新
# ------------------------------------------------------------------
def test_confirm_missing_payment_marked_400(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    gt = json.loads(json.dumps(_CANDIDATE_GT))
    del gt["payment_marked"]
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 400
    assert "payment_marked" in resp.json()["msg"], "400 提示必须点名缺失字段"


def test_confirm_payment_marked_must_be_bool(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    for bad in ("true", 1, None, "已付款"):
        gt = json.loads(json.dumps(_CANDIDATE_GT))
        gt["payment_marked"] = bad
        resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                           json={"gt": gt}, headers=_admin_headers())
        assert resp.status_code == 400, "payment_marked=%r 必须被拒绝" % bad
        assert "布尔" in resp.json()["msg"], "400 提示必须为人话（说明需要 true/false）"

    # manifest 状态不得被 400 请求改动
    rows2 = {r["sample_id"]: r for r in _read_manifest(os.path.join(evalset_dir, "manifest.csv"))}
    assert rows2[sid]["gt_status"] == "draft"


def test_confirm_missing_v2_field_400(evalset_env, client):
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    gt = json.loads(json.dumps(_CANDIDATE_GT))
    del gt["adjustment_notes"]
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 400
    assert "adjustment_notes" in resp.json()["msg"]


def test_confirm_v2_fields_saved_verbatim(evalset_env, client):
    """v2 全集落盘：payment_marked/currency/费用/注记原样保留（不静默改写）。"""
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    gt = json.loads(json.dumps(_CANDIDATE_GT))
    gt["payment_marked"] = True
    gt["payment_evidence"] = "右上角红色 PAID 印章"
    gt["currency"] = "CNY"
    gt["delivery_fee"] = 25.0
    gt["adjustment_notes"] = ["短裝一斤"]
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["payment_marked"] is True
    assert saved["payment_evidence"] == "右上角红色 PAID 印章"
    assert saved["currency"] == "CNY"
    assert saved["delivery_fee"] == 25.0
    assert saved["adjustment_notes"] == ["短裝一斤"]


def test_confirm_none_v2_optional_fields_fill_defaults(evalset_env, client):
    """v2 可选字段显式传 None 时补中性默认（费用 0/币种 HKD/证据空串/注记空数组）。"""
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]
    gt = json.loads(json.dumps(_CANDIDATE_GT))
    for k in ("payment_evidence", "currency", "delivery_fee", "adjustment_notes"):
        gt[k] = None
    resp = client.post("/api/evalset/sample/%s/confirm" % sid,
                       json={"gt": gt}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["currency"] == "HKD"
    assert saved["delivery_fee"] == 0.0
    assert saved["payment_evidence"] == ""
    assert saved["adjustment_notes"] == []


def test_reconfirm_overwrites_and_refreshes_reviewed_at(evalset_env, client):
    """confirmed 允许再次 confirm：覆盖更新内容并刷新 reviewed_at（迁移期重抽）。"""
    evalset_dir, rows = evalset_env
    test_rows = [r for r in rows if r["split"] == "test"]
    sid = test_rows[0]["sample_id"]

    first = json.loads(json.dumps(_CANDIDATE_GT))
    resp1 = client.post("/api/evalset/sample/%s/confirm" % sid,
                        json={"gt": first}, headers=_admin_headers())
    assert resp1.status_code == 200, resp1.text
    reviewed_at_1 = resp1.json()["data"]["gt_reviewed_at"]
    assert reviewed_at_1

    second = json.loads(json.dumps(_CANDIDATE_GT))
    second["total_amount"] = 131.0
    second["payment_marked"] = True
    resp2 = client.post("/api/evalset/sample/%s/confirm" % sid,
                        json={"gt": second}, headers=_admin_headers())
    assert resp2.status_code == 200, "已 confirmed 样本必须允许再次 confirm 覆盖更新"
    data2 = resp2.json()["data"]
    assert data2["gt_reviewed_at"] >= reviewed_at_1, "再次确认必须刷新 reviewed_at"

    with open(os.path.join(evalset_dir, "expected", sid + ".json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["total_amount"] == 131.0
    assert saved["payment_marked"] is True
    assert saved["gt_status"] == "confirmed"


# ------------------------------------------------------------------
# 12. 工作台路由 /evalset/workbench/<sid>（合并反馈②）：
#     与店员复核界面同源（同一 index.html 模板 + 关键容器 id）+ 适配器注入
# ------------------------------------------------------------------
def test_workbench_route_reuses_review_dom(evalset_env, client):
    resp = client.get("/evalset/workbench/S005")
    assert resp.status_code == 200
    html = resp.text
    # 注入了 eval 标志位与适配器
    assert "__EVAL_WORKBENCH__" in html and "S005" in html
    assert "eval_workbench.js" in html
    # 与真实收据识别 Tab 同源的关键复核容器 id（同一套 DOM，禁止平行表单）
    for dom_id in ("splitViewArea", "prefillFormCard", "previewImg", "inpSupplier",
                   "inpDate", "inpTotal", "inpPaymentMark", "inpCurrency",
                   "itemTableBody", "btnSaveReview"):
        assert 'id="%s"' % dom_id in html, "工作台必须复用 index.html 的 #%s" % dom_id
    # main.js 同源加载（渲染与字段组装函数来自同一份代码）
    assert "main.js" in html


def _read_demo_file(rel_path):
    with open(os.path.join(DEMO_DIR, *rel_path.split("/")), encoding="utf-8") as f:
        return f.read()


def test_workbench_embedding_does_not_duplicate_app_shell():
    """工作台内嵌与闭环融合：不得重复画一套应用外壳（用户反馈：GT 抽检里核对会冒出第二个 sider）。

    Task 3 闭环约束：
      1. 移除全局内嵌 iframe 递归风险，通过直达链接在新标签页独立打开 /evalset；
      2. 适配器隐藏 .sidebar 与 .breadcrumb-bar；
      3. 灰测样本观测卡片与 Modal 均支持直接跳转 GT 标注工作台；
      4. main.js 动态组装 mModalWorkbenchLink 单据标注工作台链接。
    """
    html = _read_demo_file("templates/index.html")
    assert 'id="mModalWorkbenchLink"' in html, "Modal 须提供跳转 GT 标注工作台链接"
    assert 'href="/evalset"' in html, "观测台须提供直接打开 GT 抽检台入口"
    assert '<iframe src="about:blank" data-src="/evalset"' not in html, "已移除冗余内嵌 iframe"

    adapter = _read_demo_file("static/js/eval_workbench.js")
    assert "'.app-wrapper > .sidebar'" in adapter, "工作台须隐藏复用来的侧边栏"
    assert "'.breadcrumb-bar'" in adapter, "工作台须隐藏复用来的顶部面包屑"

    main_js = _read_demo_file("static/js/main.js")
    assert "mModalWorkbenchLink" in main_js, "Modal 打开时须动态组装单据工作台链接"
