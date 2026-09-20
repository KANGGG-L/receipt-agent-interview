# -*- coding: utf-8 -*-
"""Unit tests for evalset GT data linkage:
Verifies that GT 抽检确权评测集与单据库（receipts 表）和黄金基准集看板（/api/admin/golden-samples）
完整联通，包括已确权样本在 scope=gt_confirmed 下的展示与 gt_confirmed_total 统计。

数据依赖口径（与 demo/conftest.py 的隔离纪律一致）：本文件的用例一律跑在
「临时库 + 真实语料副本」上，不读 live 库、也不写 demo/evalsets/ 真实语料：
  - 临时库：fixture 显式 monkeypatch DB_PATH；
  - 语料副本：只把 manifest.csv 与 expected/*.json 复制到 tmp_path（匹配按图片文件名进行，
    原图不参与，无需复制数百 MB 的图片）；
  - EVALSET_DIR 指向副本，接口与 linkage 读写的都是副本。
因此断言只表达「关系与口径」（条数 == 计数、confirmed 与 pending 互斥、引用与单据双向一致、
已确权样本数 == 语料声明数），不写死任何 live 计数。
"""

import csv
import hashlib
import json
import logging
import os
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.api_evalset import _load_gt, _load_manifest
from app.main import app
from app.services.evalset_linkage import (
    sync_evalset_receipts_linkage,
    link_or_create_receipt_for_sample,
)

REAL_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")

_MANIFEST_HEADER = [
    "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
    "gt_status", "gt_source_model", "src_sha1", "short_side",
]


def _admin_headers(tenant_id="default"):
    return {
        "X-Role": "admin",
        "X-Email": "admin@demo.hk",
        "X-Tenant-Id": tenant_id,
    }


def _isolate_db(tmp_path, monkeypatch, name="evalset_linkage.db"):
    """把 db 指向本用例的临时库，返回 (恢复用的旧路径, 临时库路径)。"""
    old_db_path = db.DB_PATH
    test_db = str(tmp_path / name)
    monkeypatch.setenv("DB_PATH", test_db)
    db.DB_PATH = test_db
    db._make_engine()
    return old_db_path, test_db


def _corpus_sample_ids(evalset_dir, status):
    """按接口口径（expected json 的 gt_status 优先，其次 manifest）取某状态的样本 id。"""
    out = set()
    for row in _load_manifest(evalset_dir) or []:
        sid = row.get("sample_id")
        if not sid:
            continue
        gt = _load_gt(evalset_dir, sid) or {}
        if (gt.get("gt_status") or row.get("gt_status") or "draft") == status:
            out.add(sid)
    return out


def _write_manifest(evalset_dir, rows):
    with open(os.path.join(evalset_dir, "manifest.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_MANIFEST_HEADER)
        for sid, gt_status in rows:
            w.writerow([sid, f"receipts/{sid}.png", "test", "printed_delivery_note",
                        "table", "V001", gt_status, "human", "dummy_sha1", "1000"])


def _write_gt(evalset_dir, sample_id, data):
    with open(os.path.join(evalset_dir, "expected", f"{sample_id}.json"), "w",
              encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _corpus_fingerprint(evalset_dir):
    """共享语料内容指纹（manifest.csv + expected/*.json），用于断言闸门零写入。"""
    h = hashlib.sha1()
    paths = [os.path.join(evalset_dir, "manifest.csv")]
    exp_dir = os.path.join(evalset_dir, "expected")
    if os.path.isdir(exp_dir):
        paths += [os.path.join(exp_dir, n) for n in sorted(os.listdir(exp_dir))]
    for p in paths:
        h.update(os.path.basename(p).encode("utf-8"))
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
        else:
            h.update(b"<missing>")
    return h.hexdigest()


def _make_png(path):
    from PIL import Image
    Image.new("RGB", (64, 48), "white").save(str(path))
    return str(path)


# confirm 接口的最小合法 GT（GT_REQUIRED_KEYS 全集 + payment_marked 为布尔）
_CONFIRM_GT = {
    "supplier_name": "闸门测试供应商",
    "date": "2026-08-09",
    "total_amount": 130.0,
    "items": [{"name": "菜心", "quantity": 3.0, "unit": "斤",
               "unit_price": 10.0, "amount": 30.0}],
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
}


def _seed_shared_corpus(tmp_path, monkeypatch, sample_id="S907", gt_status="draft"):
    """建一个临时语料并声明为「共享语料目录」；返回语料目录。

    why 不直接用 demo/evalsets：本组用例要验证「共享语料 + 非默认库 → 拒绝写入」，
    必须在共享语料上跑；真语料是 gitignore 的本机资产，用例只把临时目录登记为
    SHARED_EVALSET_DIR，写入路径与真身完全同构，但不碰真语料一个字节。
    """
    import app.services.evalset_linkage as linkage

    eval_dir = str(tmp_path / "evalsets")
    os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
    _write_manifest(eval_dir, [(sample_id, gt_status)])
    _write_gt(eval_dir, sample_id, dict(_CONFIRM_GT, gt_status=gt_status))
    monkeypatch.setenv("EVALSET_DIR", eval_dir)
    monkeypatch.setattr(linkage, "SHARED_EVALSET_DIR", os.path.abspath(eval_dir))
    return eval_dir


@pytest.fixture
def linked_corpus(tmp_path, monkeypatch):
    """临时库 + 真实语料副本，并跑一次 linkage 建立关联。

    真实语料缺失（gitignore，不随仓库分发）时 skip，口径与 conftest 的 eval 语料一致。
    """
    manifest_src = os.path.join(REAL_EVALSET_DIR, "manifest.csv")
    expected_src = os.path.join(REAL_EVALSET_DIR, "expected")
    if not (os.path.exists(manifest_src) and os.path.isdir(expected_src)):
        pytest.skip(f"评测语料缺失：{REAL_EVALSET_DIR}（真实收据属业务数据，不入库）")

    old_db_path, test_db = _isolate_db(tmp_path, monkeypatch, "linkage_board.db")

    evalset_dir = str(tmp_path / "evalsets")
    os.makedirs(os.path.join(evalset_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(evalset_dir, "receipts"), exist_ok=True)
    shutil.copyfile(manifest_src, os.path.join(evalset_dir, "manifest.csv"))
    for fname in sorted(os.listdir(expected_src)):
        if fname.endswith(".json"):
            shutil.copyfile(os.path.join(expected_src, fname),
                            os.path.join(evalset_dir, "expected", fname))
    monkeypatch.setenv("EVALSET_DIR", evalset_dir)

    stats = sync_evalset_receipts_linkage(evalset_dir=evalset_dir, tenant_id="default")
    try:
        yield {"dir": evalset_dir, "db_path": test_db, "stats": stats}
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_sync_evalset_receipts_linkage_lifecycle(tmp_path, monkeypatch):
    """测试评测集同步模块生命周期：
    1. 自动创建 receipts 记录并回填 expected JSON 的 source_receipt_id；
    2. 已确权样本置位 is_golden_sample=1，草稿置位 is_golden_sample=0；
    3. 幂等性：二次运行无新单据创建。
    """
    old_db_path = db.DB_PATH
    test_db = str(tmp_path / "lifecycle.db")
    monkeypatch.setenv("DB_PATH", test_db)
    db.DB_PATH = test_db
    db._make_engine()

    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)

        # 准备 manifest
        manifest_path = os.path.join(eval_dir, "manifest.csv")
        with open(manifest_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
                "gt_status", "gt_source_model", "src_sha1", "short_side"
            ])
            w.writerow(["S101", "receipts/S101.png", "test", "printed_delivery_note", "table", "V001", "confirmed", "human", "h1", "1000"])
            w.writerow(["S102", "receipts/S102.png", "test", "printed_delivery_note", "table", "V002", "draft", "human", "h2", "1000"])

        # 准备 expected JSON（故意不含 source_receipt_id）
        with open(os.path.join(eval_dir, "expected", "S101.json"), "w", encoding="utf-8") as f:
            json.dump({
                "supplier_name": "Supplier Confirmed",
                "date": "2026-09-01",
                "total_amount": 188.0,
                "items": [{"name": "Item 1", "qty": 2, "unit_price": 94, "amount": 188}],
                "gt_status": "confirmed",
            }, f)

        with open(os.path.join(eval_dir, "expected", "S102.json"), "w", encoding="utf-8") as f:
            json.dump({
                "supplier_name": "Supplier Draft",
                "date": "2026-09-02",
                "total_amount": 99.0,
                "items": [{"name": "Item 2", "qty": 1, "unit_price": 99, "amount": 99}],
                "gt_status": "draft",
            }, f)

        # 第一次同步
        stats1 = sync_evalset_receipts_linkage(evalset_dir=eval_dir, tenant_id="default")
        assert stats1["total"] == 2
        assert stats1["linked"] == 2
        assert stats1["created"] == 2
        assert stats1["confirmed"] == 1

        # 检查 expected JSON 是否被回填 source_receipt_id
        with open(os.path.join(eval_dir, "expected", "S101.json"), encoding="utf-8") as f:
            gt101 = json.load(f)
            s101_rid = gt101.get("source_receipt_id")
            assert s101_rid is not None

        with open(os.path.join(eval_dir, "expected", "S102.json"), encoding="utf-8") as f:
            gt102 = json.load(f)
            s102_rid = gt102.get("source_receipt_id")
            assert s102_rid is not None

        # 检查数据库中的 receipts 记录及其黄金基准集标记
        r101 = db.get_receipt_row(s101_rid)
        assert r101 is not None
        assert r101.supplier_name == "Supplier Confirmed"
        assert r101.total_amount == 188.0
        assert r101.is_golden_sample == 1

        r102 = db.get_receipt_row(s102_rid)
        assert r102 is not None
        assert r102.supplier_name == "Supplier Draft"
        assert r102.is_golden_sample == 0

        # 第二次同步（验证幂等性）
        stats2 = sync_evalset_receipts_linkage(evalset_dir=eval_dir, tenant_id="default")
        assert stats2["total"] == 2
        assert stats2["linked"] == 2
        assert stats2["created"] == 0
        assert stats2["confirmed"] == 1
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_real_evalset_confirmed_samples_appear_in_golden_board(linked_corpus):
    """已确权 GT 样本必须出现在黄金看板 scope=gt_confirmed，且与单据双向一致。"""
    evalset_dir = linked_corpus["dir"]
    client = TestClient(app)

    confirmed_sids = _corpus_sample_ids(evalset_dir, "confirmed")
    assert confirmed_sids, "语料中没有已确权样本，本用例失去验证意义"
    # 语料声明了几个已确权样本，同步统计就该是几个（不写死任何常量）
    assert linked_corpus["stats"]["confirmed"] == len(confirmed_sids)

    data = client.get("/api/admin/golden-samples?scope=gt_confirmed",
                      headers=_admin_headers()).json()
    assert data["status"] == "success"
    assert data["scope"] == "gt_confirmed"

    # 口径自洽（与数据量解耦）：scope 内条数 == 该状态计数；换 scope 计数不变
    assert data["total"] == data["gt_confirmed_total"]
    assert len(data["items"]) == data["total"]
    all_data = client.get("/api/admin/golden-samples?scope=all",
                          headers=_admin_headers()).json()
    assert all_data["gt_confirmed_total"] == data["gt_confirmed_total"]

    returned_sids = {it["sample_id"] for it in data["items"] if it.get("sample_id")}
    assert confirmed_sids.issubset(returned_sids), (
        f"已确权样本未全部出现在黄金看板：{confirmed_sids - returned_sids}")

    # 核心意图：source_receipt_id ↔ receipts ↔ 黄金标记 三者双向一致
    for it in data["items"]:
        assert it["gt_status"] == "confirmed"
        assert it["gt_label"] == "已确权"
        sid = it["sample_id"]
        assert sid in confirmed_sids
        gt = _load_gt(evalset_dir, sid) or {}
        assert gt.get("source_receipt_id") == it["id"], (
            f"{sid} 的 source_receipt_id 与实际命中的单据不一致")
        row = db.get_receipt_row(it["id"])
        assert row is not None and (getattr(row, "is_golden_sample", 0) or 0) == 1, (
            f"{sid} 关联的单据 {it['id']} 不是黄金样本")


def test_scope_gt_pending_includes_draft_evalset_samples(linked_corpus):
    """scope=gt_pending（待确权候选池）包含评测集中的草稿样本，且不与已确权样本混池。"""
    evalset_dir = linked_corpus["dir"]
    client = TestClient(app)

    draft_sids = _corpus_sample_ids(evalset_dir, "draft")
    confirmed_sids = _corpus_sample_ids(evalset_dir, "confirmed")
    assert draft_sids, "语料中没有草稿样本，本用例失去验证意义"

    data = client.get("/api/admin/golden-samples?scope=gt_pending",
                      headers=_admin_headers()).json()
    assert data["status"] == "success"
    assert data["scope"] == "gt_pending"
    # 口径自洽（与数据量解耦）
    assert data["total"] == data["gt_pending_total"]

    returned_sids = {it["sample_id"] for it in data["items"] if it.get("sample_id")}
    assert draft_sids.issubset(returned_sids), (
        f"草稿样本未全部出现在待确权池：{draft_sids - returned_sids}")
    assert not (confirmed_sids & returned_sids), "已确权样本不得混入待确权池"

    for it in data["items"]:
        if not it.get("sample_id"):
            continue
        gt = _load_gt(evalset_dir, it["sample_id"]) or {}
        assert (gt.get("gt_status") or "draft") != "confirmed"


def test_stale_link_is_repaired_to_image_matching_receipt(tmp_path, monkeypatch):
    """旧引用指向「存在但与本样本无关」的单据时，应重挂到图片名对应的真实单据，且不新增单据。

    why：历史语料里的 source_receipt_id 是「ID 回收」年代的顺序号，只校验「单据是否存在」
    会把错误引用永久固化（线上 gt_confirmed_total 掉到 0 的直接成因）。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "stale_repair.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S901", "confirmed")])
        img_abs = os.path.join(eval_dir, "receipts", "S901.png")

        wrong_rid = db.create_receipt_record(
            supplier_name="无关单据", image_path=str(tmp_path / "uploads" / "other.jpg"),
            tenant_id="default")
        right_rid = db.create_receipt_record(
            supplier_name="真供应商", receipt_date="2026-01-02", total_amount=88.0,
            image_path=img_abs, tenant_id="default")

        _write_gt(eval_dir, "S901", {
            "supplier_name": "真供应商", "date": "2026-01-02", "total_amount": 88.0,
            "gt_status": "confirmed", "source_receipt_id": wrong_rid, "items": [],
        })

        before = len(db.list_receipt_rows(tenant_id="default"))
        rid = link_or_create_receipt_for_sample(eval_dir, "S901", tenant_id="default")

        assert rid == right_rid, "陈旧引用应被重挂到图片名对应的真实单据"
        assert len(db.list_receipt_rows(tenant_id="default")) == before, "修复引用不应新增单据"
        assert (_load_gt(eval_dir, "S901") or {}).get("source_receipt_id") == right_rid
        assert (getattr(db.get_receipt_row(right_rid), "is_golden_sample", 0) or 0) == 1
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_stale_unmatchable_link_is_kept_without_creating_receipt(tmp_path, monkeypatch):
    """旧引用存在但与本样本不符、且库中无匹配单据时：保留原引用、不新增单据、不改写语料，
    也不得把这条「并非本样本载体」的单据误标成黄金样本。

    why：① 凭空补建会造出重复载体，且「本次新建单据的 id」只在本库有意义，写进共享语料
    后一旦换库即成为悬空引用（历史 1–33 号顺序号正是这样写进去的）；② 旧实现会直接采信
    这条语义不符的引用，并对已确权样本执行 set_golden_sample(错误单据, 1)，把无关单据
    污染成黄金样本。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "stale_keep.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S902", "confirmed")])

        unrelated_rid = db.create_receipt_record(
            supplier_name="完全无关的供应商", receipt_date="2020-01-01", total_amount=1.0,
            image_path=str(tmp_path / "uploads" / "unrelated.jpg"), tenant_id="default")
        _write_gt(eval_dir, "S902", {
            "supplier_name": "本样本供应商", "date": "2026-03-04", "total_amount": 520.0,
            "gt_status": "confirmed", "source_receipt_id": unrelated_rid, "items": [],
        })

        before = len(db.list_receipt_rows(tenant_id="default"))
        rid = link_or_create_receipt_for_sample(eval_dir, "S902", tenant_id="default")

        assert rid == unrelated_rid, "无可匹配单据时应保留原引用"
        assert len(db.list_receipt_rows(tenant_id="default")) == before, "不得新增单据"
        assert (_load_gt(eval_dir, "S902") or {}).get("source_receipt_id") == unrelated_rid
        assert (getattr(db.get_receipt_row(unrelated_rid), "is_golden_sample", 0) or 0) == 0, (
            "语义不符的引用不得把无关单据标成黄金样本")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_startup_selfheal_skipped_for_non_default_db(tmp_path, monkeypatch):
    """启动自愈绑定到非默认库时必须整体跳过，否则会把临时库的单据 id 回写进共享语料。

    复现路径：测试以 `with TestClient(app)` 进入 lifespan 时 DB_PATH 已被隔离到临时库，
    自愈会为语料样本在临时库里新建单据、并把临时库的 id 写回真实 expected/*.json；
    临时库丢弃后语料里的引用全部悬空（历史 gt_confirmed_total=0 的成因）。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "startup_guard.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S903", "draft")])
        _write_gt(eval_dir, "S903", {
            "supplier_name": "自愈探针供应商", "date": "2026-04-05", "total_amount": 66.0,
            "gt_status": "draft", "items": [],
        })
        monkeypatch.setenv("EVALSET_DIR", eval_dir)

        from app.main import _sync_evalset_receipts_startup
        _sync_evalset_receipts_startup()

        assert len(db.list_receipt_rows(tenant_id="default")) == 0, "非默认库不应触发建单"
        assert "source_receipt_id" not in (_load_gt(eval_dir, "S903") or {}), (
            "非默认库不应回写共享语料")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_existing_triple_only_link_repoints_to_image_carrier(tmp_path, monkeypatch):
    """旧引用仅靠三元组吻合、库中另有以本样本原图为 image_path 的载体时，应以图片载体为准。

    why：同值重复单据会让三元组匹配命中「别人的」单据（历史 S112 的引用落在 660，而
    343/344/660 三条同值；list_receipt_rows 按 id desc 取到 660，确定性但非唯一）。
    只有把「图片即本样本原图」作为最强判据，引用才会收敛到唯一且语义正确的载体。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "prefer_image.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S906", "draft")])
        img_abs = os.path.join(eval_dir, "receipts", "S906.png")

        dup_rid = db.create_receipt_record(
            supplier_name="同值供应商", receipt_date="2026-06-07", total_amount=321.0,
            image_path=str(tmp_path / "uploads" / "dup.jpg"), tenant_id="default")
        carrier_rid = db.create_receipt_record(
            supplier_name="同值供应商", receipt_date="2026-06-07", total_amount=321.0,
            image_path=img_abs, tenant_id="default")
        _write_gt(eval_dir, "S906", {
            "supplier_name": "同值供应商", "date": "2026-06-07", "total_amount": 321.0,
            "gt_status": "draft", "source_receipt_id": dup_rid, "items": [],
        })

        before = len(db.list_receipt_rows(tenant_id="default"))
        rid = link_or_create_receipt_for_sample(eval_dir, "S906", tenant_id="default")

        assert rid == carrier_rid, "应改挂到以本样本原图为 image_path 的载体"
        assert len(db.list_receipt_rows(tenant_id="default")) == before, "重挂不应新增单据"
        assert (_load_gt(eval_dir, "S906") or {}).get("source_receipt_id") == carrier_rid
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_shared_corpus_write_blocked_for_non_default_db(tmp_path, monkeypatch):
    """共享语料 + 非部署默认库（测试临时库等）时必须拒绝回写 source_receipt_id。

    why：source_receipt_id 是「库内 id」，只对与语料成对的默认库有意义。把临时库的 id 写进
    共享语料，临时库一丢引用即悬空（历史 1..33 顺序号污染的写入路径）。启动自愈那条路径
    由 main.py 的闸门挡住，这里挡的是其余显式调用 linkage 的调用点。
    """
    import app.services.evalset_linkage as linkage

    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "shared_guard.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S904", "draft")])
        _write_gt(eval_dir, "S904", {
            "supplier_name": "共享语料闸门供应商", "date": "2026-05-06",
            "total_amount": 77.0, "gt_status": "draft", "items": [],
        })
        monkeypatch.setattr(linkage, "SHARED_EVALSET_DIR", os.path.abspath(eval_dir))

        rid = linkage.link_or_create_receipt_for_sample(eval_dir, "S904", tenant_id="default")

        assert rid is not None, "隔离库上仍应完成关联"
        assert "source_receipt_id" not in (_load_gt(eval_dir, "S904") or {}), (
            "共享语料 + 非默认库不得回写引用")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_write_back_false_keeps_corpus_read_only(tmp_path, monkeypatch):
    """write_back=False 时即便目标是可写的语料副本也不回写（只读核对场景）。"""
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "ro_corpus.db")
    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
        _write_manifest(eval_dir, [("S905", "draft")])
        _write_gt(eval_dir, "S905", {
            "supplier_name": "只读语料供应商", "date": "2026-07-08",
            "total_amount": 45.0, "gt_status": "draft", "items": [],
        })

        rid = link_or_create_receipt_for_sample(
            eval_dir, "S905", tenant_id="default", write_back=False)
        assert rid is not None, "只读模式仍应完成关联"
        assert "source_receipt_id" not in (_load_gt(eval_dir, "S905") or {}), (
            "write_back=False 不得改写语料")

        # 默认（write_back=True）在非共享目录上照常回写，证明拒绝只来自显式只读开关
        rid2 = link_or_create_receipt_for_sample(eval_dir, "S905", tenant_id="default")
        assert (_load_gt(eval_dir, "S905") or {}).get("source_receipt_id") == rid2
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


# ------------------------------------------------------------------
# 确权 / 回流接口的共享语料写入闸门（同一「写共享语料」动作的其余入口）
# ------------------------------------------------------------------
def test_confirm_refuses_shared_corpus_on_non_default_db(tmp_path, monkeypatch):
    """确权接口在「共享语料 + 非部署默认库」下必须整体拒绝（409）且语料零改动。

    why：确权会写 expected/<sid>.json（GT）、manifest、source_receipt_id 三处。测试把
    DB_PATH 隔离到临时库、又未覆盖 EVALSET_DIR 时，写进共享语料的是临时库 id；临时库
    一丢引用即全部悬空（历史 source_receipt_id=1..33 顺序号污染），与 linkage 闸门同源。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "confirm_guard.db")
    try:
        eval_dir = _seed_shared_corpus(tmp_path, monkeypatch)
        before = _corpus_fingerprint(eval_dir)

        client = TestClient(app)
        resp = client.post("/api/evalset/sample/S907/confirm",
                           json={"gt": _CONFIRM_GT}, headers=_admin_headers())

        assert resp.status_code == 409, resp.text
        assert _corpus_fingerprint(eval_dir) == before, "拒绝写入时共享语料必须零改动"
        gt = _load_gt(eval_dir, "S907") or {}
        assert gt.get("gt_status") == "draft", "拒绝时不得把 GT 流转为 confirmed"
        assert "source_receipt_id" not in gt, "拒绝时不得回写引用"
        rows = {r["sample_id"]: r for r in _load_manifest(eval_dir)}
        assert rows["S907"]["gt_status"] == "draft", "拒绝时不得改写 manifest"
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_confirm_writes_shared_corpus_on_default_db(tmp_path, monkeypatch):
    """部署默认库 + 共享语料是正常业务路径，确权必须真正写入（闸门不得堵死正常流程）。

    用「把 _default_db_path 指向本用例临时库」模拟部署默认库场景：写入能力与线上
    默认库完全同构，但不触碰 live 库与本机真实语料。
    """
    old_db_path, test_db = _isolate_db(tmp_path, monkeypatch, "confirm_default.db")
    monkeypatch.setattr(db, "_default_db_path", test_db)
    try:
        eval_dir = _seed_shared_corpus(tmp_path, monkeypatch)
        before = _corpus_fingerprint(eval_dir)

        client = TestClient(app)
        resp = client.post("/api/evalset/sample/S907/confirm",
                           json={"gt": _CONFIRM_GT}, headers=_admin_headers())

        assert resp.status_code == 200, resp.text
        assert _corpus_fingerprint(eval_dir) != before, "默认库上确权必须真正落盘"
        gt = _load_gt(eval_dir, "S907") or {}
        assert gt.get("gt_status") == "confirmed"
        assert gt.get("source_receipt_id"), "确权联动必须回写 source_receipt_id"
        rows = {r["sample_id"]: r for r in _load_manifest(eval_dir)}
        assert rows["S907"]["gt_status"] == "confirmed", "manifest 必须同步 confirmed"
        # 回写的引用必须指向本库中真实存在的单据
        assert db.get_receipt_row(int(gt["source_receipt_id"])) is not None
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_promote_refuses_shared_corpus_on_non_default_db(tmp_path, monkeypatch):
    """回流接口在「共享语料 + 非部署默认库」下同样整体拒绝，不复制原图、不追加 manifest。

    why：promote 会往共享语料写三处（receipts/<sid>.<ext> 原图、expected/<sid>.json、
    manifest 追加行），是确权之外第二条独立的写入路径；不设闸门时隔离库上的回流会把
    临时库的 receipt_id 固化进共享语料。
    """
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "promote_guard.db")
    try:
        eval_dir = _seed_shared_corpus(tmp_path, monkeypatch)
        src_img = _make_png(tmp_path / "src.png")
        rid = db.create_receipt_record(supplier_name="候选供应商", image_path=src_img,
                                       tenant_id="default")
        cand_gt = dict(_CONFIRM_GT)
        cand_gt["gt_source_model"] = "stub"
        cid, created = db.create_eval_candidate(
            rid, "low_confidence", confidence=0.3,
            doc_form="printed_delivery_note", ai_candidate=cand_gt)
        assert created, "前置条件：必须造出一条 pending 候选"

        before = _corpus_fingerprint(eval_dir)
        client = TestClient(app)
        resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                           headers=_admin_headers())

        assert resp.status_code == 409, resp.text
        assert _corpus_fingerprint(eval_dir) == before, "拒绝写入时共享语料必须零改动"
        assert not os.path.exists(os.path.join(eval_dir, "receipts", "S908.png")), (
            "拒绝时不得把原图复制进共享语料")
        assert (db.get_eval_candidate(cid).status or "pending") == "pending", (
            "拒绝时不得流转候选状态，否则候选会变成永远无法再回流的孤儿")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


# ------------------------------------------------------------------
# 建单分支的明细写入可观测性（receipt_items 是明细的唯一数据源）
# ------------------------------------------------------------------
def _seed_item_corpus(tmp_path, monkeypatch, sample_id="S909"):
    """建一个临时库 + 临时语料，GT 含 2 条明细；返回 (旧库路径, 语料目录)。"""
    old_db_path, _ = _isolate_db(tmp_path, monkeypatch, "item_write_verify.db")
    eval_dir = str(tmp_path / "evalsets")
    os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)
    _write_manifest(eval_dir, [(sample_id, "draft")])
    _write_gt(eval_dir, sample_id, {
        "supplier_name": "明细写入校验供应商", "date": "2026-08-11", "total_amount": 60.0,
        "gt_status": "draft",
        "items": [
            {"name": "菜心", "qty": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
            {"name": "菜苗", "qty": 4.0, "unit": "斤", "unit_price": 10.0, "amount": 40.0},
        ],
    })
    return old_db_path, eval_dir


def test_item_write_silent_noop_is_logged_as_error(tmp_path, monkeypatch, caplog):
    """明细写入「无异常但零行」时必须升级为 logger.error。

    why：items_json 已不再写，receipt_items 是明细的唯一数据源；写入静默不落库会让该载体在
    详情/复核/导出里永久为空，且没有任何第二线索。旧实现只在「抛异常」时留一条 warning，
    静默零写入完全无信号 —— 本用例钉的就是这个盲区。
    """
    old_db_path, eval_dir = _seed_item_corpus(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(db, "set_receipt_items", lambda *a, **k: None)
        with caplog.at_level(logging.ERROR, logger="evalset_linkage"):
            rid = link_or_create_receipt_for_sample(eval_dir, "S909", tenant_id="default")

        assert rid is not None, "前置条件：应完成建单"
        assert db.get_receipt_items(rid) == [], "前置条件：明细表确实为空"
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("明细写入不完整" in m and f"rid={rid}" in m for m in errors), (
            f"静默零写入必须打 error 且带上 rid/预期/实际，实际错误日志：{errors}")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_item_write_exception_is_logged_as_error(tmp_path, monkeypatch, caplog):
    """明细写入抛异常时必须打 logger.error（旧实现只记 warning，级别不足以告警）。"""
    old_db_path, eval_dir = _seed_item_corpus(tmp_path, monkeypatch, "S910")
    try:
        def _boom(*a, **k):
            raise RuntimeError("probe: 明细写入失败")

        monkeypatch.setattr(db, "set_receipt_items", _boom)
        with caplog.at_level(logging.ERROR, logger="evalset_linkage"):
            rid = link_or_create_receipt_for_sample(eval_dir, "S910", tenant_id="default")

        assert rid is not None, "前置条件：应完成建单（明细失败不得中断建单语义）"
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("明细写入失败" in m and "probe: 明细写入失败" in m for m in errors), (
            f"写入异常必须打 error 并带上原因，实际错误日志：{errors}")
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()
