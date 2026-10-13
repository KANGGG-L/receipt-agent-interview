# -*- coding: utf-8 -*-
"""SKU 别名候选生成器测试（SPEC_PHASE1 §4.5 合成夹具 + §4.6 真实副本门禁）。

核心红线用例是 test_no_auto_registration（§4.4 三道闸之闸 1+2）：源码 grep 零写语义命中
+ 副本库 vendor_sku_alias / skus / receipt_items 逐行不变。生成器全部经 subprocess CLI
调用（真实验证 live 防护与退出码），夹具库用 app.db 建在临时目录。
"""

import ast
import csv
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="sku_alias_candidates_test_")
# 运行方注入的环境库路径（必须在下面模块级 DB_PATH 覆盖**之前**捕获；_fresh_db 之后
# 还会继续改写 os.environ["DB_PATH"]。真实副本量级断言要对的是运行方指定的 live 校准副本）
_AMBIENT_DB_PATH = os.environ.get("DB_PATH")
# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置；与
# tests/test_vendor_sku_alias.py 同款模块级声明语义，conftest 按 ambient 尊重）
os.environ["DB_PATH"] = os.path.join(_TMP, "init_candidates.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GEN_SCRIPT = os.path.join(_SCRIPTS_DIR, "gen_sku_alias_candidates.py")

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _item(name, unit="斤", unit_price=10.0, amount=10.0):
    """与 save_parsed_data 落库同形的明细行（set_receipt_items 的输入形状）。"""
    return {
        "name": name, "raw_name": name, "quantity": 1, "unit": unit,
        "unit_price": unit_price, "amount": amount, "sku_id": None,
        "cost_center_id": None, "confidence": 0.9, "matched": 0, "price_anomaly": 0,
        "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }


def _fresh_db(name):
    path = os.path.join(_TMP, name)
    if os.path.exists(path):
        os.remove(path)
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return path


def _add_receipt(supplier, items, supplier_id=None):
    rid = db.create_receipt_record(supplier_name=supplier, supplier_id=supplier_id,
                                   tenant_id="default", status="parsed")
    db.set_receipt_items(rid, items)
    return rid


def _run_gen(db_path, out_dir, extra_env=None):
    env = dict(os.environ)
    env["DB_PATH"] = db_path
    env["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, GEN_SCRIPT, "--out-dir", out_dir],
        env=env, capture_output=True, text=True)


def _read_csv(out_dir):
    path = os.path.join(out_dir, "sku_alias_candidates.csv")
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# -------------------------------------------------------------
# C-分组 / C-'0T'（同一夹具库）
# -------------------------------------------------------------
def test_grouping_by_normalize_and_family_confidence(tmp_path):
    """C-分组：3 行同写法（6包裝 維他朱古奶×2、維他朱古奶×1）+ 1 行 江鯽魚 →
    归一后 2 种写法；維他朱古奶 row_count=3、raw 2 种；江鯽魚 confidence=high（家族）。"""
    db_path = _fresh_db("grouping.db")
    _add_receipt("大同行", [_item("6包裝 維他朱古奶"), _item("6包裝 維他朱古奶"),
                            _item("維他朱古奶"), _item("江鯽魚")])
    out = str(tmp_path / "out1")
    proc = _run_gen(db_path, out)
    assert proc.returncode == 0, proc.stderr
    rows = _read_csv(out)
    assert len(rows) == 2
    by_key = {r["canonical_writing"]: r for r in rows}
    assert set(by_key) == {"維他朱古奶", "江鯽魚"}
    vg = by_key["維他朱古奶"]
    assert vg["row_count"] == "3"
    assert vg["raw_writings"].count("×") == 2 and "6包裝 維他朱古奶×2" in vg["raw_writings"] \
        and "維他朱古奶×1" in vg["raw_writings"]
    # 本库无 SKU → 未锚定重复 → high（候选列为主数据缺失文案）
    assert vg["confidence"] == "high" and vg["replay_match"] == "miss"
    assert "主数据缺失" in vg["candidate_sku"]
    jy = by_key["江鯽魚"]
    assert jy["confidence"] == "high" and "OCR 家族:江團魚" in jy["evidence"]


def test_zero_t_never_produces_candidate(tmp_path):
    """C-'0T'：name='0T' 的行不出现在 CSV（永不可匹配，无候选可修）。"""
    db_path = _fresh_db("zerot.db")
    _add_receipt("大同行", [_item("0T"), _item("西蘭花")])
    out = str(tmp_path / "out2")
    assert _run_gen(db_path, out).returncode == 0
    rows = _read_csv(out)
    assert [r["canonical_writing"] for r in rows] == ["西蘭花"]


# -------------------------------------------------------------
# C-候选锚定
# -------------------------------------------------------------
def test_anchored_candidate_points_to_existing_sku(tmp_path):
    """C-候选锚定：预置 SKU 維他朱古奶 → replay_match=hit 且候选列为该 sku；
    **库中别名表仍 0 行**（回放命中不是登记）。"""
    db_path = _fresh_db("anchored.db")
    sku_id, err = db.create_sku("維他朱古奶", base_unit="瓶", tenant_id="default")
    assert err is None
    # 写法与 SKU 名一致才算命中；带「6包裝」前缀的写法只是候选，不再自动命中（S1-P0-03）
    _add_receipt("大同行", [_item("維他朱古奶"), _item("維他朱古奶")])
    out = str(tmp_path / "out3")
    assert _run_gen(db_path, out).returncode == 0
    rows = _read_csv(out)
    vg = [r for r in rows if r["canonical_writing"] == "維他朱古奶"][0]
    assert vg["replay_match"] == "hit"
    assert vg["candidate_sku"] == "SKU#%s(維他朱古奶)" % sku_id
    assert vg["confidence"] == "low"   # 已锚定写法只作对照，不进人审首屏
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM vendor_sku_alias").fetchone()[0] == 0
    finally:
        conn.close()


# -------------------------------------------------------------
# C-live 防护
# -------------------------------------------------------------
def test_live_guard_rejects_missing_and_live_db(tmp_path):
    """C-live 防护：DB_PATH 未设 / 指向 demo/receipt_demo.db → SystemExit（非零退出）。"""
    out = str(tmp_path / "out4")
    # 未设置 DB_PATH
    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    env["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
    proc = subprocess.run([sys.executable, GEN_SCRIPT, "--out-dir", out],
                          env=env, capture_output=True, text=True)
    assert proc.returncode != 0 and "live 防护" in (proc.stdout + proc.stderr)
    # 指向 live 语义库（任意树下的 demo/receipt_demo.db）
    proc = _run_gen(os.path.join(_DEMO_DIR, "receipt_demo.db"), out)
    assert proc.returncode != 0 and "live 防护" in (proc.stdout + proc.stderr)


# -------------------------------------------------------------
# C-确定性
# -------------------------------------------------------------
def test_deterministic_csv_bytes(tmp_path):
    db_path = _fresh_db("det.db")
    _add_receipt("大同行", [_item("6包裝 維他朱古奶"), _item("江鯽魚"), _item("江庫魚")])
    out1, out2 = str(tmp_path / "out5a"), str(tmp_path / "out5b")
    assert _run_gen(db_path, out1).returncode == 0
    assert _run_gen(db_path, out2).returncode == 0
    p1 = os.path.join(out1, "sku_alias_candidates.csv")
    p2 = os.path.join(out2, "sku_alias_candidates.csv")
    assert open(p1, "rb").read() == open(p2, "rb").read()


# -------------------------------------------------------------
# C-零登记（核心，§4.4 闸 1 + 闸 2）
# -------------------------------------------------------------
_WRITE_KEYWORDS = ["upsert_vendor_sku_alias", "create_sku", "INSERT INTO",
                   "UPDATE ", "session.add", "db.execute"]


def _code_lines_without_docstrings(path):
    """源码行集合，剔除 docstring 行（docstring 里的红线**描述**不算写语义）。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    doc_ranges = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if getattr(node, "body", None) else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                doc_ranges.append((first.value.lineno, first.value.end_lineno))
    return [(i, line) for i, line in enumerate(src.splitlines(), 1)
            if not any(a <= i <= b for a, b in doc_ranges)]


def test_no_auto_registration(tmp_path):
    # 闸 1（代码闸）：剔除 docstring 后源码对写语义关键词零命中
    code_lines = _code_lines_without_docstrings(GEN_SCRIPT)
    for kw in _WRITE_KEYWORDS:
        hits = [i for i, line in code_lines if kw in line]
        assert hits == [], "写语义关键词 %r 出现在 %s" % (kw, hits)

    # 闸 2（测试闸）：临时副本上跑完生成器，库逐行不变
    db_path = _fresh_db("noreg.db")
    sku_id, err = db.create_sku("維他朱古奶", base_unit="瓶", tenant_id="default")
    assert err is None
    _add_receipt("大同行", [_item("6包裝 維他朱古奶"), _item("維他朱古奶"), _item("江鯽魚")])
    _add_receipt("默認來源測試", [_item("菜心")])   # 残渣行也不得被改动

    def _snapshot():
        conn = sqlite3.connect(db_path)
        try:
            return {
                "alias": conn.execute("SELECT count(*) FROM vendor_sku_alias").fetchone()[0],
                "skus": conn.execute("SELECT count(*) FROM skus").fetchone()[0],
                "items": conn.execute(
                    "SELECT ifnull(sku_id,-1), ifnull(matched,-1) FROM receipt_items"
                    " ORDER BY id").fetchall(),
            }
        finally:
            conn.close()

    before = _snapshot()
    out = str(tmp_path / "out6")
    assert _run_gen(db_path, out).returncode == 0
    assert _snapshot() == before
    assert before["alias"] == 0 and before["skus"] == 1

    # 闸 3（流程闸）：docstring 写明登记只能走 approve 路径
    module_doc = ast.get_docstring(ast.parse(open(GEN_SCRIPT, encoding="utf-8").read())) or ""
    assert "approve" in module_doc and "导入工具不在本阶段交付" in module_doc


# -------------------------------------------------------------
# 真实副本门禁（§4.6 C-R1/C-R2/C-R4/C-R5；DB_PATH 副本 receipts≠659 时 skip）
# -------------------------------------------------------------
def _receipt_count(db_path):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        return conn.execute("SELECT count(*) FROM receipts").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


@pytest.mark.eval
def test_real_corpus_coverage_and_high_confidence(tmp_path):
    db_path = _AMBIENT_DB_PATH
    if not db_path or _receipt_count(db_path) != 659:
        pytest.skip("DB_PATH 未指向 live 校准副本（receipts≠659）")
    out = str(tmp_path / "out_real")
    proc = _run_gen(db_path, out)
    assert proc.returncode == 0, proc.stderr
    rows = _read_csv(out)
    # C-R1：live 行全量覆盖（545 = 645 − 残渣 93 − '0T' 7）、distinct 归一写法 201
    # （SPEC_PHASE2A §4.4 有意行为变更：③后缀剥离后 204→201，維他 6 键并 1、
    #   獅球/雙喜各自并入其裸名；校准实测见 §4.3）
    assert sum(int(r["row_count"]) for r in rows) == 545
    assert len(rows) == 201
    # 排序确定性：confidence → row_count 降序 → canonical_writing 升序
    order = {"high": 0, "medium": 1, "low": 2}
    keys = [(order[r["confidence"]], -int(r["row_count"]), r["canonical_writing"]) for r in rows]
    assert keys == sorted(keys)
    # C-R4：'0T' 永不产候选
    assert all(r["canonical_writing"] != "0T" for r in rows)
    # C-R5：evidence 全非空（人审可判定的最小信息集）
    assert all(r["evidence"].strip() for r in rows)
    # C-R2：ERRORS §1.2 点名写法 + 四家族代表必须全部在高置信段（按归一键、空格不敏感）。
    # SPEC_PHASE2A §4.4：后缀剥离后点名键改为剥离后的核心词（5L*2樽/25kg/500毫升 已剥）；
    # 維他檸檬茶 25 行并键后 replay 翻为 hit（家族 high 语义不变），獅球/雙喜仍 miss→high
    def _find(writing):
        target = writing.replace(" ", "")
        return [r for r in rows if r["canonical_writing"].replace(" ", "") == target]
    for name in ["獅球嘜粟米油", "雙喜牌特級香米", "急凍豬頸肉", "西蘭花",
                 "特濃凍奶茶", "鮮牛油菠蘿包", "乾炒牛河", "菜冧", "江團魚", "半殼生蠔",
                 "五香肉丁", "維他檸檬茶"]:
        found = _find(name)
        assert found, "C-R2 点名写法缺失：%s" % name
        assert found[0]["confidence"] == "high", name
    vt = _find("維他檸檬茶")[0]
    assert vt["replay_match"] == "hit" and vt["row_count"] == "25", vt
