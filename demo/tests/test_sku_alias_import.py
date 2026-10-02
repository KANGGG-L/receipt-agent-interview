# -*- coding: utf-8 -*-
"""SKU 别名批量审批登记工具测试（SPEC_PHASE2A §2.7 合成夹具）。

人工闸红线是本套件的第一优先级：无审批列 / 缺审批人 / 非法状态值 → 整批拒绝
（exit 2、零写入、零审计）；reject/pending 零写入；歧义拒绝有痕；幂等重导入
逐行内容不变（hit_count 保留）；与 L1 钩子并存（source_ref=memory:<mid> 不被覆盖）。

全部经 subprocess CLI 调用（真实验证 live 防护与退出码），夹具库用 app.db 建在
临时目录（模式沿 test_vendor_sku_alias.py / test_sku_alias_candidates.py）。
"""

import csv
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="sku_alias_import_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_import.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

IMPORT_SCRIPT = os.path.join(_SCRIPTS_DIR, "import_sku_alias_approvals.py")

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

WHO = "审批人甲"


# -------------------------------------------------------------
# 夹具基建
# -------------------------------------------------------------
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


def _mk_world(path):
    """预置 suppliers/skus（两个租户），返回 id 字典。模式沿 test_vendor_sku_alias.py。"""
    ids = {}
    ids["sup_a"], err = db.create_supplier("大同行", tenant_id="default")
    assert err is None, err
    ids["sup_b"], err = db.create_supplier("協興行", tenant_id="default")
    assert err is None, err
    ids["sup_t2"], err = db.create_supplier("二租户行", tenant_id="t2")
    assert err is None, err
    ids["sku_vita"], err = db.create_sku("維他檸檬茶500毫升罐", base_unit="罐")
    assert err is None, err
    ids["sku_niu"], err = db.create_sku("黑牛牌黑胡椒牛油", base_unit="瓶")
    assert err is None, err
    ids["sku_t2"], err = db.create_sku("二租户專供品", base_unit="個", tenant_id="t2")
    assert err is None, err
    # 同名 active SKU ×2（歧义门夹具）：create_sku 有重名检查，走直插
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO skus (name, category, base_unit, tenant_id, sku_code,"
                     " active) VALUES ('歧義商品', '', '個', 'default', 'SKU-DUP1', 1)")
        conn.execute("INSERT INTO skus (name, category, base_unit, tenant_id, sku_code,"
                     " active) VALUES ('歧義商品', '', '個', 'default', 'SKU-DUP2', 1)")
        conn.commit()
        ids["sku_dup"] = conn.execute(
            "SELECT id FROM skus WHERE sku_code='SKU-DUP1'").fetchone()[0]
        # 停用 SKU（SKU_NOT_FOUND 夹具用）
        conn.execute("UPDATE skus SET active = 0 WHERE id = ?", (ids["sku_niu"],))
        conn.commit()
        ids["sku_dead"] = ids.pop("sku_niu")
    finally:
        conn.close()
    return ids


def _write_csv(path, rows, headers):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


_MIN_HEADERS = ["canonical_writing", "approval_status", "approved_by", "supplier_id",
                "target_sku_id"]


def _run_import(db_path, csv_path, *extra_args, with_db_path=True):
    env = dict(os.environ)
    env["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
    if with_db_path:
        env["DB_PATH"] = db_path
    else:
        env.pop("DB_PATH", None)
    return subprocess.run([sys.executable, IMPORT_SCRIPT, "--csv", csv_path,
                           "--json"] + list(extra_args),
                          env=env, capture_output=True, text=True)


def _alias_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(vendor_sku_alias)")]
        return [dict(zip(cols, r)) for r in
                conn.execute("SELECT * FROM vendor_sku_alias ORDER BY id")]
    finally:
        conn.close()


def _audit_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='system_audit_json'").fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row and row[0] else []


def _set_hit_count(db_path, alias_id, value):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE vendor_sku_alias SET hit_count = ? WHERE id = ?",
                     (value, alias_id))
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------
# 红线条（G1-G4：整批拒绝 = exit 2 + 零写入 + 零审计）
# -------------------------------------------------------------
def test_g1_missing_approval_column_rejects_whole_batch(tmp_path):
    db_path = _fresh_db("g1.db")
    ids = _mk_world(db_path)
    # 只写审批人列、漏 approval_status（拼错/缺列同判）——库零变化、无审计写入
    csv_path = _write_csv(str(tmp_path / "g1.csv"), [
        {"canonical_writing": "維他檸檬茶", "approved_by": WHO,
         "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
    ], ["canonical_writing", "approved_by", "supplier_id", "target_sku_id"])
    proc = _run_import(db_path, csv_path)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert _alias_rows(db_path) == [] and _audit_rows(db_path) == []
    assert "G1" in proc.stdout


def test_g2_missing_approver_rejects_whole_batch(tmp_path):
    db_path = _fresh_db("g2.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "g2.csv"), [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": "", "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "黑牛", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert _alias_rows(db_path) == [] and _audit_rows(db_path) == []
    # 整批拒绝列出违规行号（数据行号按文件行计：表头是第 1 行）
    assert "行 2" in proc.stdout


def test_g3_case_insensitive_status_ok_illegal_value_rejected(tmp_path):
    db_path = _fresh_db("g3.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "g3.csv"), [
        {"canonical_writing": "a写法", "approval_status": "Approved ",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "b写法", "approval_status": "approved",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "c写法", "approval_status": "approved!",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert _alias_rows(db_path) == [] and _audit_rows(db_path) == []
    assert "行 4" in proc.stdout and "approved!" in proc.stdout


# -------------------------------------------------------------
# 三方状态 / 歧义 / 目标门 / 供应商门
# -------------------------------------------------------------
def test_three_status_only_approve_writes(tmp_path):
    db_path = _fresh_db("tri.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "tri.csv"), [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "拒收写法", "approval_status": "reject",
         "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "待定写法", "approval_status": "pending",
         "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    summary = json.loads(proc.stdout)
    assert summary["approve"] == 1 and summary["reject"] == 1 and summary["pending"] == 1
    assert summary["registered"] == 1 and summary["refreshed"] == 0
    assert summary["skipped_by_status"] == 2
    rows = _alias_rows(db_path)
    assert len(rows) == 1 and rows[0]["raw_name"] == "維他檸檬茶" \
        and rows[0]["source_ref"] == "alias_import"
    # reject/pending 零审计：审计里只有登记这一条
    audits = _audit_rows(db_path)
    assert [a["action"] for a in audits] == ["alias_import_registered"]
    assert audits[0]["who"] == WHO


def test_ambiguous_name_skips_with_audit_and_rest_lands(tmp_path):
    db_path = _fresh_db("ambig.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "ambig.csv"), [
        {"canonical_writing": "歧義写法", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_name": "歧義商品"},
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"])},
    ], ["canonical_writing", "approval_status", "approved_by", "supplier_id",
        "target_sku_id", "target_sku_name"])
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    summary = json.loads(proc.stdout)
    assert summary["registered"] == 1
    assert summary["skipped_by_reason"] == {"SKU_AMBIGUOUS": 1}
    rows = _alias_rows(db_path)
    assert len(rows) == 1 and rows[0]["raw_name"] == "維他檸檬茶"
    # 歧义拒绝有痕：skip 审计 who=approved_by（无审批人时才是 alias_import）
    audits = [a for a in _audit_rows(db_path) if a["action"] == "alias_import_skip"]
    assert len(audits) == 1 and audits[0]["who"] == WHO
    payload = json.loads(audits[0]["new"])
    assert payload["reason"] == "SKU_AMBIGUOUS" and payload["candidates"] == 2 \
        and payload["csv_row_no"] == 2 and payload["csv_file"].endswith(".csv")


def test_sku_not_found_and_inactive_sku(tmp_path):
    db_path = _fresh_db("nosku.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "nosku.csv"), [
        {"canonical_writing": "指空写法", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]), "target_sku_id": "999999"},
        {"canonical_writing": "指停用写法", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_dead"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    summary = json.loads(proc.stdout)
    assert summary["skipped_by_reason"] == {"SKU_NOT_FOUND": 2}
    assert _alias_rows(db_path) == []
    # 停用 SKU 的 skip 也有痕（apply 模式）
    reasons = [json.loads(a["new"])["reason"]
               for a in _audit_rows(db_path) if a["action"] == "alias_import_skip"]
    assert sorted(reasons) == ["SKU_NOT_FOUND", "SKU_NOT_FOUND"]


def test_sku_name_mismatch_cross_check(tmp_path):
    db_path = _fresh_db("mismatch.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "mismatch.csv"), [
        {"canonical_writing": "点错行写法", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"]), "target_sku_name": "黑牛牌黑胡椒牛油"},
    ], ["canonical_writing", "approval_status", "approved_by", "supplier_id",
        "target_sku_id", "target_sku_name"])
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    summary = json.loads(proc.stdout)
    assert summary["skipped_by_reason"] == {"SKU_NAME_MISMATCH": 1}
    assert _alias_rows(db_path) == []


def test_supplier_gate_gates(tmp_path):
    db_path = _fresh_db("supgate.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "supgate.csv"), [
        {"canonical_writing": "非整数锚点", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": "abc", "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "不存在的锚点", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": "999999", "target_sku_id": str(ids["sku_vita"])},
        # 跨租户：sup_t2 只在 t2 存在，行缺省 tenant_id=default → 供应商门拦截
        {"canonical_writing": "跨租户锚点", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_t2"]),
         "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    summary = json.loads(proc.stdout)
    assert summary["skipped_by_reason"] == {"NO_SUPPLIER": 1, "SUPPLIER_NOT_FOUND": 2}
    assert _alias_rows(db_path) == []


# -------------------------------------------------------------
# 幂等 / L1 并存 / source_ref
# -------------------------------------------------------------
def test_idempotent_reimport_keeps_rows_and_hit_count(tmp_path):
    db_path = _fresh_db("idem.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "idem.csv"), [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "黑牛", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_b"]),
         "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc1 = _run_import(db_path, csv_path, "--apply")
    assert proc1.returncode == 0, (proc1.stdout, proc1.stderr)
    s1 = json.loads(proc1.stdout)
    assert s1["registered"] == 2
    after1 = _alias_rows(db_path)
    # 模拟运行期 hit_count 累加（upsert 更新分支不得清零它）
    for r in after1:
        _set_hit_count(db_path, r["id"], 7)
    proc2 = _run_import(db_path, csv_path, "--apply")
    assert proc2.returncode == 0, (proc2.stdout, proc2.stderr)
    s2 = json.loads(proc2.stdout)
    assert s2["registered"] == 0 and s2["refreshed"] == 2, (s1, s2)
    after2 = _alias_rows(db_path)
    # 逐行内容不变（id/sku_id/raw/canonical/source_ref/valid_from…），hit_count 保留；
    # updated_at 除外 —— upsert 更新分支按定义刷新它（§2.5 幂等判据不含该列）
    _PINNED = ("id", "tenant_id", "supplier_id", "sku_id", "raw_name", "canonical_name",
               "unit", "valid_from", "valid_to", "status", "source_ref")
    assert [{k: r[k] for k in _PINNED} for r in after2] == \
           [{k: r[k] for k in _PINNED} for r in after1]
    assert [r["hit_count"] for r in after2] == [7, 7]
    # 审计每次追加（日志非状态）：两次各 2 条登记审计
    reg = [a for a in _audit_rows(db_path) if a["action"] == "alias_import_registered"]
    assert len(reg) == 4


def test_coexists_with_l1_hook_source_ref_preserved(tmp_path):
    db_path = _fresh_db("l1.db")
    ids = _mk_world(db_path)
    # 模拟 L1 钩子登记（同 raw 指向 sku_vita，source_ref=memory:M1）
    alias_id, err = db.upsert_vendor_sku_alias(
        supplier_id=ids["sup_a"], sku_id=ids["sku_vita"], raw_name="維他檸檬茶",
        canonical_name="維他檸檬茶", tenant_id="default", source_ref="memory:M1",
        protect_live_target=False)
    assert err is None
    # 导入同一 raw 改指另一 SKU（人工批准改指语义；取夹具里真实存在的 DUP1）
    dup_id = ids["sku_dup"]
    csv_path = _write_csv(str(tmp_path / "l1.csv"), [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(dup_id)},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    rows = _alias_rows(db_path)
    assert len(rows) == 1
    # 改指到新 SKU，且 memory 回溯链保留（更新分支不覆盖 source_ref）
    assert rows[0]["id"] == alias_id and rows[0]["sku_id"] == dup_id
    assert rows[0]["source_ref"] == "memory:M1"
    actions = [a["action"] for a in _audit_rows(db_path)]
    assert "alias_import_registered" in actions


def test_source_ref_on_fresh_row_and_canonical(tmp_path):
    db_path = _fresh_db("srcref.db")
    ids = _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "src.csv"), [
        {"canonical_writing": "6包裝 維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"])},
    ], _MIN_HEADERS)
    proc = _run_import(db_path, csv_path, "--apply")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    rows = _alias_rows(db_path)
    assert len(rows) == 1
    from app.services.receipt_utils import _normalize_sku_name
    assert rows[0]["source_ref"] == "alias_import"
    assert rows[0]["canonical_name"] == _normalize_sku_name("6包裝 維他檸檬茶")


# -------------------------------------------------------------
# dry-run 零写入（缺省）与 I-live 防护 / 源码闸
# -------------------------------------------------------------
def test_dry_run_default_zero_write_and_summary_matches_apply(tmp_path):
    db_path = _fresh_db("dry.db")
    ids = _mk_world(db_path)
    rows_csv = [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "黑牛", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_b"]),
         "target_sku_id": str(ids["sku_vita"])},
        {"canonical_writing": "歧義写法", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": str(ids["sup_a"]),
         "target_sku_name": "歧義商品"},
        {"canonical_writing": "拒收写法", "approval_status": "reject",
         "supplier_id": str(ids["sup_a"]), "target_sku_id": str(ids["sku_vita"])},
    ]
    csv_path = _write_csv(str(tmp_path / "dry.csv"), rows_csv,
                          ["canonical_writing", "approval_status", "approved_by",
                           "supplier_id", "target_sku_id", "target_sku_name"])
    proc_dry = _run_import(db_path, csv_path)
    assert proc_dry.returncode == 0, (proc_dry.stdout, proc_dry.stderr)
    dry = json.loads(proc_dry.stdout)
    assert dry["apply"] is False and dry["registered"] == 2 and dry["refreshed"] == 0
    assert dry["skipped_by_reason"] == {"SKU_AMBIGUOUS": 1}
    # 库零变化（零写入含零审计）
    assert _alias_rows(db_path) == [] and _audit_rows(db_path) == []
    # 摘要与 apply 一致（逐位）
    proc_apply = _run_import(db_path, csv_path, "--apply")
    assert proc_apply.returncode == 0, (proc_apply.stdout, proc_apply.stderr)
    applied = json.loads(proc_apply.stdout)
    for key in ("csv_rows", "approve", "reject", "pending", "empty_status",
                "registered", "refreshed", "skipped_by_status", "skipped_by_reason"):
        assert dry[key] == applied[key], key


def test_live_guard_unconfigured_live_db_and_repo_tree(tmp_path):
    db_path = _fresh_db("guard.db")
    _mk_world(db_path)
    csv_path = _write_csv(str(tmp_path / "guard.csv"), [
        {"canonical_writing": "維他檸檬茶", "approval_status": "approve",
         "approved_by": WHO, "supplier_id": "1", "target_sku_id": "1"},
    ], _MIN_HEADERS)
    # DB_PATH 未设
    proc = _run_import(db_path, csv_path, with_db_path=False)
    assert proc.returncode != 0 and "live 防护" in (proc.stdout + proc.stderr)
    # 指向 live 语义库
    proc = _run_import(os.path.join(_DEMO_DIR, "receipt_demo.db"), csv_path)
    assert proc.returncode != 0 and "live 防护" in (proc.stdout + proc.stderr)
    # 落仓库树内
    proc = _run_import(os.path.join(_DEMO_DIR, "guard_tree.db"), csv_path)
    assert proc.returncode != 0 and "live 防护" in (proc.stdout + proc.stderr)


def test_source_gates():
    """源码闸：登记必须经 db.upsert_vendor_sku_alias 入口；不得进 demo/app；
    不得出现旁路 SQL。"""
    src = open(IMPORT_SCRIPT, encoding="utf-8").read()
    assert "upsert_vendor_sku_alias" in src, "登记必须走 db 唯一写入口"
    # demo/app 下零引用（A5 三文件守卫的 scripts 侧补充）
    for root, _dirs, files in os.walk(os.path.join(_DEMO_DIR, "app")):
        for fn in files:
            if fn.endswith(".py"):
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    assert "import_sku_alias_approvals" not in f.read(), \
                        "导入工具不得被 demo/app 引用"
    for banned in ("INSERT INTO", "UPDATE vendor_sku_alias", "session.add"):
        assert banned not in src, banned
