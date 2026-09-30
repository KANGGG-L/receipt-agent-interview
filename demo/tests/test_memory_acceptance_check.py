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


def test_r6_skips_when_no_active_rows(tmp_path):
    """没有样本时 SKIP，不用 PASS 冒充（无数据与全通过是两件事）。"""
    db = _vendor_memory_db(str(tmp_path / "vm.db"), [("m1", "archived", "")])
    ctx = acc.Ctx(demo_dir=_DEMO_DIR, db_path=db)
    assert acc.check_r6(ctx).status == acc.SKIP


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
    assert acc.exit_code([_r(acc.PASS), _r(acc.SKIP)]) == 2
    assert acc.exit_code([_r(acc.PASS), _r(acc.SKIP), _r(acc.FAIL)]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
