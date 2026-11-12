# -*- coding: utf-8 -*-
"""AJ1：一键验收脚本（`scripts/memory_acceptance_check.py`）的判别式用例。

why（存在的理由）
----------------
验收脚本若把判据写死成「应为 True」，它自己就会变成本仓最怕的东西 —— **恒真用例**：
红变绿看不出为什么，绿也证明不了任何东西。本文件用**临时构造的假数据**反向验证：
- 造出「缺生产调用方 / 裸 except: pass / 缺 written_by / 缺写入方」等场景 →
  断言脚本**真的报 FAIL**（判据不是恒真）；
- 造出「最小合规树」→ 断言脚本报 PASS（判据也不是恒 FAIL）；
- 尤其验证「只把符号写在注释/文档字符串里」**不算**接线（这正是 R1/R10 判据
  必须用 AST 找真实调用点的理由，也是本项目「文案不得领先于代码」红线的机器化）。

反证口径（⑥，交付报告给实测记录）：把脚本里 `check_r2` 的判据改成恒真
（例如直接 `return CheckResult("R2", ..., PASS, ...)` 或忽略权重），
本文件的 `test_r2_detects_signal_ignoring_rerank` 必须失败 —— 它断言「一个忽略
治理信号的 rerank 实现必须被判 FAIL」。

隔离：本文件不 import `app.db`；只用 sqlite3 造临时假库、用临时目录造假文件树。
DB_PATH / RAG_DIR 在 import 任何 app.* 之前指向临时目录。
"""

import os
import sqlite3
import sys
import tempfile
import types

import pytest

# 隔离环境：必须在首次 import app.* 之前设置（与同目录其它用例同款）
_TMP = tempfile.mkdtemp(prefix="mem_acceptance_check_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_aj1.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import memory_acceptance_check as acc  # noqa: E402


# -------------------------------------------------------------
# 假数据构造工具
# -------------------------------------------------------------
def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _mk_db(path, ddl_rows):
    """建一个临时 SQLite：ddl_rows = [(建表语句, 插入语句列表)]。"""
    con = sqlite3.connect(path)
    try:
        for ddl, inserts in ddl_rows:
            con.execute(ddl)
            for sql in inserts:
                con.execute(sql)
        con.commit()
    finally:
        con.close()
    return path


def _vendor_memory_db(path, rows):
    """rows = [(memory_id, status, written_by)]。"""
    inserts = ["insert into vendor_memory(memory_id,status,written_by) values(%r,%r,%r)"
               % (m, s, w) for (m, s, w) in rows]
    return _mk_db(path, [(
        "create table vendor_memory(memory_id text, status text, written_by text)",
        inserts)])


def _pending_db(path):
    return _mk_db(path, [(
        "create table pending_memory(id integer primary key, reject_reason text)", [])])


# -------------------------------------------------------------
# R1：治理视图生产消费方 —— 双向（缺标记 FAIL / 最小合规树 PASS）
# -------------------------------------------------------------
def test_r1_fails_when_markers_missing(tmp_path):
    ctx = acc.Ctx(demo_dir=str(tmp_path), db_path=str(tmp_path / "none.db"))
    res = acc.check_r1(ctx)
    assert res.status == acc.FAIL, res
    assert res.criterion, "检查必须能解释自己怎么判"


def test_r1_passes_on_minimal_good_tree(tmp_path):
    root = str(tmp_path)
    _write(root, "app/api_admin.py", (
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.get("/api/admin/vendor-memory")\n'
        'def list_it():\n'
        '    return db.list_vendor_memory(tenant_id="default")\n'
        '@router.get("/api/admin/vendor-memory/{memory_id}")\n'
        'def detail_it(memory_id):\n'
        '    return db.list_vendor_memory(tenant_id="default", include_archived=True)\n'
    ))
    _write(root, "static/js/main.js", (
        "// GET /api/admin/vendor-memory 治理视图（注释里提到该路径不算调用）\n"
        "function loadVendorMemoryGovernance() {\n"
        "    return apiFetch('/api/admin/vendor-memory?status=active');\n"
        "}\n"
        "loadVendorMemoryGovernance();\n"
    ))
    ctx = acc.Ctx(demo_dir=root, db_path=os.path.join(root, "none.db"))
    assert acc.check_r1(ctx).status == acc.PASS


def test_r1_js_defined_but_never_called_is_fail(tmp_path):
    """定义了没人调 = 本仓②类在前端的形态，必须判 FAIL。"""
    root = str(tmp_path)
    _write(root, "app/api_admin.py", (
        '@router.get("/api/admin/vendor-memory")\n'
        'def a():\n'
        '    return db.list_vendor_memory()\n'
        '@router.get("/api/admin/vendor-memory/{memory_id}")\n'
        'def b(memory_id):\n'
        '    return db.list_vendor_memory(include_archived=True)\n'
    ))
    _write(root, "static/js/main.js",
           "function loadVendorMemoryGovernance() {\n"
           "    return apiFetch('/api/admin/vendor-memory?status=active');\n"
           "}\n")
    ctx = acc.Ctx(demo_dir=root, db_path=os.path.join(root, "none.db"))
    assert acc.check_r1(ctx).status == acc.FAIL


# -------------------------------------------------------------
# R2：治理信号被 rerank 消费 —— 含「忽略信号必须 FAIL」的自校准
# -------------------------------------------------------------
def _fake_recall(rerank_impl):
    """用真实 RecallDoc / DEFAULT_WEIGHTS 构造注入用模块，只替换 rerank。"""
    from app.services import memory_recall as real
    m = types.SimpleNamespace()
    m.DEFAULT_WEIGHTS = real.DEFAULT_WEIGHTS
    m.RecallDoc = real.RecallDoc
    m.RECALL_LEG_VECTOR = real.RECALL_LEG_VECTOR
    m.rerank = rerank_impl
    return m


def test_r2_passes_on_real_module():
    assert acc.check_r2(acc.Ctx()).status == acc.PASS


def test_r2_detects_signal_ignoring_rerank():
    """⑥ 的反证对准点：忽略治理信号的 rerank 实现必须被判 FAIL。

    若有人把 check_r2 的判据改成恒真，本用例会失败。
    """
    def _ignore(query, docs, weights=None, top_k=None, now=None, query_vendor=""):
        # 完全忽略权重与治理信号，只按插入序返回（= 旧行为）
        return [types.SimpleNamespace(doc=d, total=0.0) for d in docs]

    res = acc.check_r2(acc.Ctx(), recall=_fake_recall(_ignore))
    assert res.status == acc.FAIL, res
    assert "信号强者" in res.evidence or "非零" in res.evidence, res.evidence


def test_r2_reversed_rerank_is_fail():
    """反序（治理信号弱者在前）也必须 FAIL —— 防止「恒等」被换成「恒反」。"""
    def _reverse(query, docs, weights=None, top_k=None, now=None, query_vendor=""):
        return [types.SimpleNamespace(doc=d, total=0.0) for d in reversed(list(docs))]

    assert acc.check_r2(acc.Ctx(), recall=_fake_recall(_reverse)).status == acc.FAIL


def test_r2_zero_weights_is_fail():
    """治理信号权重被清零（等于没接线）必须 FAIL。"""
    from app.services import memory_recall as real
    m = _fake_recall(real.rerank)
    m.DEFAULT_WEIGHTS = real.RecallWeights(decay=0.0, hit=0.0, recency=0.0)
    assert acc.check_r2(acc.Ctx(), recall=m).status == acc.FAIL


# -------------------------------------------------------------
# R5：裸 except: pass —— 三种形态 + 注释误报防护
# -------------------------------------------------------------
def test_r5_fails_on_block_and_inline_bare_pass(tmp_path):
    root = str(tmp_path)
    _write(root, "app/services/rag.py", (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        h()\n"
        "    except ValueError: pass\n"
    ))
    res = acc.check_r5(acc.Ctx(demo_dir=root, db_path=os.path.join(root, "n.db")))
    assert res.status == acc.FAIL, res


def test_r5_comment_and_docstring_mentions_are_not_false_positive(tmp_path):
    """注释/文档字符串里提到 `except Exception: pass` 不得误报（rag.py 里真有这句注释）。"""
    root = str(tmp_path)
    _write(root, "app/services/rag.py", (
        '"""说明：历史上每一步都是 except Exception: pass，现在改为计数 + warning。"""\n'
        "def f():\n"
        "    # 旧写法是 except Exception: pass\n"
        "    try:\n"
        "        g()\n"
        "    except Exception as e:\n"
        "        logger.warning('降级：%s', e)\n"
    ))
    res = acc.check_r5(acc.Ctx(demo_dir=root, db_path=os.path.join(root, "n.db")))
    assert res.status == acc.PASS, res


# -------------------------------------------------------------
# R6：written_by 审计链 —— FAIL / PASS / SKIP 三态
# -------------------------------------------------------------
def test_r6_fails_when_active_row_lacks_written_by(tmp_path):
    db = _vendor_memory_db(str(tmp_path / "vm.db"),
                           [("m1", "active", "admin@demo.hk"), ("m2", "active", "")])
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db)
    assert acc.check_r6(ctx).status == acc.FAIL


def test_r6_passes_when_all_active_rows_have_written_by(tmp_path):
    db = _vendor_memory_db(str(tmp_path / "vm.db"),
                           [("m1", "active", "admin@demo.hk"), ("m2", "archived", "")])
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db)
    assert acc.check_r6(ctx).status == acc.PASS


def test_r6_passes_with_info_when_no_active_rows(tmp_path):
    """没有样本时优雅返回 PASS 并给出 [INFO] 提示（空库冷启动不报假红）。"""
    db = _vendor_memory_db(str(tmp_path / "vm.db"), [("m1", "archived", "")])
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db)
    res = acc.check_r6(ctx)
    assert res.status == acc.PASS
    assert "[INFO]" in res.evidence


# -------------------------------------------------------------
# R4 / R10：只写在注释或实现文件里不算接线（AST 真实调用点）
# -------------------------------------------------------------
def test_r10_docstring_mention_is_not_wiring(tmp_path):
    root = str(tmp_path)
    _write(root, "app/api_admin.py",
           'def ep():\n'
           '    """建议接线点：rag.get_retrieve_failure_counts() 尚未有人调。"""\n'
           '    return {}\n')
    ctx = acc.Ctx(demo_dir=root, db_path=os.path.join(root, "n.db"))
    assert acc.check_r10(ctx).status == acc.FAIL


def test_r10_real_call_is_wiring(tmp_path):
    root = str(tmp_path)
    _write(root, "app/api_admin.py",
           "def ep():\n"
           "    counts = rag.get_retrieve_failure_counts()\n"
           "    return counts\n")
    ctx = acc.Ctx(demo_dir=root, db_path=os.path.join(root, "n.db"))
    assert acc.check_r10(ctx).status == acc.PASS


def test_r4_impl_file_alone_is_not_trigger(tmp_path):
    """巡检实现自身不算生产触发方。"""
    root = str(tmp_path)
    _write(root, "app/services/memory_consistency.py",
           "def check_memory_consistency():\n"
           "    return {}\n")
    ctx = acc.Ctx(demo_dir=root, db_path=os.path.join(root, "n.db"))
    assert acc.check_r4(ctx).status == acc.FAIL

    _write(root, "app/main.py",
           "def start():\n"
           "    mc.check_memory_consistency()\n")
    assert acc.check_r4(ctx).status == acc.PASS


# -------------------------------------------------------------
# Q2：写入方 + 读侧投射 + 表结构 三段
# -------------------------------------------------------------
def test_q2_requires_writer_projection_and_schema(tmp_path):
    root = str(tmp_path)
    _write(root, "app/db.py",
           'def d():\n'
           '    return {"reject_reason": None}\n')
    _write(root, "app/api_receipts.py",
           "def w():\n"
           "    db.enqueue_pending_memory(v, c, reject_reason=reason, status='rejected')\n")
    db = _pending_db(str(tmp_path / "p.db"))
    ctx = acc.Ctx(demo_dir=root, db_path=db)
    assert acc.check_q2(ctx).status == acc.PASS

    # 去掉写入方（只建列没有写入方 = 本仓②类）→ FAIL
    _write(root, "app/api_receipts.py",
           "def w():\n"
           "    db.enqueue_pending_memory(v, c, status='pending')\n")
    assert acc.check_q2(ctx).status == acc.FAIL


def test_q2_missing_schema_column_is_fail(tmp_path):
    root = str(tmp_path)
    _write(root, "app/db.py", 'def d():\n    return {"reject_reason": None}\n')
    _write(root, "app/api_receipts.py",
           "def w():\n"
           "    db.enqueue_pending_memory(v, c, reject_reason=reason)\n")
    db = _mk_db(str(tmp_path / "p.db"),
                [("create table pending_memory(id integer primary key)", [])])
    ctx = acc.Ctx(demo_dir=root, db_path=db)
    assert acc.check_q2(ctx).status == acc.FAIL


# -------------------------------------------------------------
# R9：口径字段缺一即 FAIL（用注入的假 metrics 输出，不依赖 live 库）
# -------------------------------------------------------------
def test_r9_criterion_logic(monkeypatch, tmp_path):
    good = {"sku_match": {"matched_flag_rate": 23.73,
                          "incomparable_with": "simulated_match_rate",
                          "note": "见 backfill_vendor_sku_alias.py --replay 的 simulated_match_rate"}}
    monkeypatch.setattr(acc, "_run_json",
                        lambda *a, **k: (0, good, "{}", ""))
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=os.path.join(str(tmp_path), "n.db"))
    assert acc.check_r9(ctx).status == acc.PASS

    monkeypatch.setattr(acc, "_run_json",
                        lambda *a, **k: (0, {"sku_match": {"rate_pct": 23.73}}, "{}", ""))
    assert acc.check_r9(ctx).status == acc.FAIL

    monkeypatch.setattr(acc, "_run_json",
                        lambda *a, **k: (1, None, "", "boom"))
    assert acc.check_r9(ctx).status == acc.FAIL


# -------------------------------------------------------------
# D4：入口不存在即 FAIL；真实入口必须能预演
# -------------------------------------------------------------
def test_d4_fails_when_script_missing(tmp_path):
    ctx = acc.Ctx(demo_dir=str(tmp_path), db_path=os.path.join(str(tmp_path), "n.db"))
    assert acc.check_d4(ctx).status == acc.FAIL


def test_d4_real_entry_runs_preview():
    """真实入口：预演必须退出码 0 且声明源库零写入（子进程被强制指向 /tmp 隔离库）。"""
    res = acc.check_d4(acc.Ctx(demo_dir=_DEMO_DIR,
                               db_path=os.path.join(_TMP, "d4.db")))
    assert res.status == acc.PASS, res


# -------------------------------------------------------------
# 元检查：判据自述齐备、run_all 能汇总、退出码语义
# -------------------------------------------------------------
def test_all_checks_have_criterion_text():
    """每条检查都必须能独立解释自己怎么判（① 的机器化断言）。"""
    assert set(acc.CHECK_ORDER) == set(acc._CHECKS) == set(acc._CRITERIA)
    for num in acc.CHECK_ORDER:
        text = acc._CRITERIA[num]()
        assert len(text) > 20, (num, text)


def test_run_all_reports_multiple_failures_on_empty_tree(tmp_path):
    """空树（无 app/、无 JS、库不存在）→ 至少 2 条 FAIL，退出码非 0（判据非恒真）。"""
    ctx = acc.Ctx(demo_dir=str(tmp_path), db_path=str(tmp_path / "none.db"))
    results = acc.run_all(ctx)
    assert {r.num for r in results} == set(acc.CHECK_ORDER)
    fails = [r for r in results if r.status == acc.FAIL]
    assert len(fails) >= 2, [(r.num, r.status) for r in results]
    assert acc.exit_code(results) == 1


def test_exit_code_semantics():
    def _r(status):
        return acc.CheckResult("X", "t", status, "e", "c")
    assert acc.exit_code([_r(acc.PASS), _r(acc.PASS)]) == 0
    assert acc.exit_code([_r(acc.PASS), _r(acc.SKIP)]) == 0
    assert acc.exit_code([_r(acc.PASS), _r(acc.FAIL)]) == 1
    assert acc.exit_code([_r(acc.PASS), _r(acc.SKIP), _r(acc.FAIL)]) == 1


# -------------------------------------------------------------
# G1-G9 用例与反证测试
# -------------------------------------------------------------
def test_g1_fails_when_db_missing(tmp_path):
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=str(tmp_path / "missing.db"))
    res = acc.check_g1(ctx)
    assert res.status == acc.FAIL


def test_g1_fails_when_table_missing(tmp_path):
    db_path = str(tmp_path / "empty.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE other_table (id int)")
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path)
    res = acc.check_g1(ctx)
    assert res.status == acc.FAIL
    assert "vendor_memory 表不存在" in res.evidence


def test_g1_passes_on_valid_empty_schema(tmp_path):
    db_path = str(tmp_path / "valid.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, vendor text, tenant_id text, status text, written_by text)")
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path)
    res = acc.check_g1(ctx)
    assert res.status == acc.PASS
    assert "[INFO]" in res.evidence


def test_g2_fails_on_relative_path(monkeypatch, tmp_path):
    from app.services import rag_paths
    monkeypatch.setattr(rag_paths, "resolve_rag_dir", lambda *a: "relative/path")
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=str(tmp_path / "db.db"))
    res = acc.check_g2(ctx)
    assert res.status == acc.FAIL


def test_g2_passes_on_absolute_path(tmp_path):
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=str(tmp_path / "db.db"), rag_dir=str(tmp_path / "rag"))
    res = acc.check_g2(ctx)
    assert res.status == acc.PASS


def test_g3_detects_fixture_collections(tmp_path):
    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con.execute("CREATE TABLE collections (id text, name text)")
    con.execute("INSERT INTO collections VALUES ('1', 'vendor_memory_hash_256')")
    con.execute("INSERT INTO collections VALUES ('2', 'tenant_uat_test_coll')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=str(tmp_path / "db.db"), rag_dir=rag_dir)
    res = acc.check_g3(ctx)
    assert res.status == acc.FAIL
    assert "tenant_uat_test_coll" in res.evidence


def test_g3_passes_on_clean_collections(tmp_path):
    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con.execute("CREATE TABLE collections (id text, name text)")
    con.execute("INSERT INTO collections VALUES ('1', 'vendor_memory_hash_256')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=str(tmp_path / "db.db"), rag_dir=rag_dir)
    res = acc.check_g3(ctx)
    assert res.status == acc.PASS


def test_g4_m10_architecture_reserved_slot():
    ctx = acc.Ctx(demo_dir=_DEMO_DIR)
    res = acc.check_g4(ctx)
    assert res.status == acc.PASS
    assert "预留槽位=True" in res.evidence


def test_g5_db_chroma_consistency_on_empty(tmp_path):
    db_path = str(tmp_path / "empty.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, tenant_id text, status text)")
    con.commit()
    con.close()
    rag_dir = str(tmp_path / "rag")
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=rag_dir)
    res = acc.check_g5(ctx)
    assert res.status == acc.PASS
    assert "[INFO]" in res.evidence


def test_g6_status_machine_catches_invalid_status(tmp_path):
    db_path = str(tmp_path / "vm.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, status text, superseded_by text, created_at text, updated_at text)")
    con.execute("INSERT INTO vendor_memory VALUES ('m1', 'invalid_status_xyz', NULL, '', '')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path)
    res = acc.check_g6(ctx)
    assert res.status == acc.FAIL
    assert "invalid_status_xyz" in res.evidence


def test_g6_status_machine_catches_broken_supersede(tmp_path):
    db_path = str(tmp_path / "vm.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, status text, superseded_by text, created_at text, updated_at text)")
    con.execute("INSERT INTO vendor_memory VALUES ('m1', 'superseded', 'm_non_existent', '', '')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path)
    res = acc.check_g6(ctx)
    assert res.status == acc.FAIL
    assert "m_non_existent" in res.evidence


def test_g6_status_machine_passes_on_valid_data(tmp_path):
    db_path = str(tmp_path / "vm.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, status text, superseded_by text, created_at text, updated_at text)")
    con.execute("INSERT INTO vendor_memory VALUES ('m1', 'superseded', 'm2', '2026-01-01', '2026-01-02')")
    con.execute("INSERT INTO vendor_memory VALUES ('m2', 'active', NULL, '2026-01-02', '2026-01-02')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path)
    res = acc.check_g6(ctx)
    assert res.status == acc.PASS


def test_g7_vector_metadata_symmetry_detects_mismatch(tmp_path):
    db_path = str(tmp_path / "vm.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, vendor text, tenant_id text)")
    con.execute("INSERT INTO vendor_memory VALUES ('m1', 'VendorA', 'default')")
    con.commit()
    con.close()

    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con_c = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con_c.execute("CREATE TABLE embedding_metadata (id int, key text, string_value text)")
    con_c.execute("INSERT INTO embedding_metadata VALUES (1, 'memory_id', 'm1')")
    con_c.execute("INSERT INTO embedding_metadata VALUES (1, 'vendor', 'VendorB_Mismatch')")
    con_c.execute("INSERT INTO embedding_metadata VALUES (1, 'tenant_id', 'default')")
    con_c.commit()
    con_c.close()

    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=rag_dir)
    res = acc.check_g7(ctx)
    assert res.status == acc.FAIL
    assert "vendor不匹配" in res.evidence


def test_g8_tenant_single_bucket_detects_rogue_tenant(tmp_path):
    db_path = str(tmp_path / "vm.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE vendor_memory (memory_id text, tenant_id text)")
    con.execute("INSERT INTO vendor_memory VALUES ('m1', 'rogue_tenant_123')")
    con.commit()
    con.close()
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=str(tmp_path / "rag"))
    res = acc.check_g8(ctx)
    assert res.status == acc.FAIL
    assert "rogue_tenant_123" in res.evidence


def test_g9_endpoint_alignment():
    ctx = acc.Ctx(demo_dir=_DEMO_DIR)
    res = acc.check_g9(ctx)
    assert res.status == acc.PASS


# -------------------------------------------------------------
# purge_orphans 三道安全闸测试
# -------------------------------------------------------------
def test_purge_orphans_safety_gate_1_protects_default_core_bucket(tmp_path):
    """安全闸 1: 核心桶不可触碰，如果尝试删除 vendor_memory/default 必须报错。"""
    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con_c = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con_c.execute("CREATE TABLE collections (id text, name text)")
    con_c.execute("INSERT INTO collections VALUES ('1', 'vendor_memory')")
    con_c.commit()
    con_c.close()

    db_path = str(tmp_path / "vm.db")
    con_db = sqlite3.connect(db_path)
    con_db.execute("CREATE TABLE vendor_memory (memory_id text, status text)")
    con_db.commit()
    con_db.close()

    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=rag_dir)
    # 模拟如果 candidates 包含核心集合
    import pytest
    with pytest.raises(RuntimeError, match="Core protected collection cannot be deleted"):
        # 强制 candidates 识别到受保护集合并进入 Gate 1 校验
        acc._FIXTURE_COLL_RE = acc.re.compile(r"^vendor_memory")
        try:
            acc.purge_orphans(ctx, apply=False)
        finally:
            acc._FIXTURE_COLL_RE = acc.re.compile(r"^(?:tenant_)?(?:test|mock|tmp|fixture|uat)_", acc.re.IGNORECASE)


def test_purge_orphans_safety_gate_2_active_record_overlap_aborts(tmp_path):
    """安全闸 2: Active 行零重叠校验，若待删集合含有 active 记忆 ID 则立即熔断。"""
    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con_c = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con_c.execute("CREATE TABLE collections (id text, name text)")
    con_c.execute("CREATE TABLE segments (id text, collection text)")
    con_c.execute("CREATE TABLE embeddings (id int, segment_id text)")
    con_c.execute("CREATE TABLE embedding_metadata (id int, key text, string_value text)")

    con_c.execute("INSERT INTO collections VALUES ('col1', 'test_fixture_col')")
    con_c.execute("INSERT INTO segments VALUES ('seg1', 'col1')")
    con_c.execute("INSERT INTO embeddings VALUES (101, 'seg1')")
    con_c.execute("INSERT INTO embedding_metadata VALUES (101, 'memory_id', 'active_mid_001')")
    con_c.commit()
    con_c.close()

    db_path = str(tmp_path / "vm.db")
    con_db = sqlite3.connect(db_path)
    con_db.execute("CREATE TABLE vendor_memory (memory_id text, status text)")
    con_db.execute("INSERT INTO vendor_memory VALUES ('active_mid_001', 'active')")
    con_db.commit()
    con_db.close()

    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=rag_dir)
    import pytest
    with pytest.raises(RuntimeError, match="Active record overlap detected"):
        acc.purge_orphans(ctx, apply=False)


def test_purge_orphans_dry_run_and_apply_lifecycle(tmp_path):
    """安全闸 3 与生命周期: dry-run 审计不落盘，--apply 实际删除夹具集合。"""
    rag_dir = str(tmp_path / "rag")
    os.makedirs(rag_dir, exist_ok=True)
    con_c = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    con_c.execute("CREATE TABLE collections (id text, name text)")
    con_c.execute("CREATE TABLE collection_metadata (collection_id text)")
    con_c.execute("CREATE TABLE segments (id text, collection text)")
    con_c.execute("CREATE TABLE segment_metadata (segment_id text)")
    con_c.execute("CREATE TABLE embeddings (id int, segment_id text)")
    con_c.execute("CREATE TABLE embedding_metadata (id int, key text, string_value text)")

    con_c.execute("INSERT INTO collections VALUES ('col1', 'test_orphan_fixture')")
    con_c.execute("INSERT INTO segments VALUES ('seg1', 'col1')")
    con_c.execute("INSERT INTO embeddings VALUES (101, 'seg1')")
    con_c.execute("INSERT INTO embedding_metadata VALUES (101, 'memory_id', 'archived_mid_999')")
    con_c.commit()
    con_c.close()

    db_path = str(tmp_path / "vm.db")
    con_db = sqlite3.connect(db_path)
    con_db.execute("CREATE TABLE vendor_memory (memory_id text, status text)")
    con_db.execute("INSERT INTO vendor_memory VALUES ('archived_mid_999', 'archived')")
    con_db.close()

    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db_path, rag_dir=rag_dir)
    # Dry run: collection 仍在
    ret_dry = acc.purge_orphans(ctx, apply=False)
    assert ret_dry == 0
    con_check = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    cols_after_dry = [r[0] for r in con_check.execute("SELECT name FROM collections").fetchall()]
    con_check.close()
    assert cols_after_dry == ["test_orphan_fixture"]

    # Apply: 真正删除
    ret_apply = acc.purge_orphans(ctx, apply=True)
    assert ret_apply == 0
    con_check2 = sqlite3.connect(os.path.join(rag_dir, "chroma.sqlite3"))
    cols_after_apply = [r[0] for r in con_check2.execute("SELECT name FROM collections").fetchall()]
    con_check2.close()
    assert cols_after_apply == []



if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
