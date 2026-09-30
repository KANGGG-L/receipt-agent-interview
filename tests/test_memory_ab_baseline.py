# -*- coding: utf-8 -*-
"""记忆观察性 A/B 基线脚本（demo/scripts/memory_ab_baseline.py）的回归测试。

为什么要这些用例：计划 §7 把「有记忆组修改率相对对照组翻正」写成验收判据，而这条
判据此前只有代理口径（供应商有 vendor_memory 行），会被「难单才注入」的选择偏差
反向污染。本模块钉住三件事：

1. **处理变量的真实定义**：同一批单据上，「rag_context_json 非空」与「供应商有记忆行」
   必须得到**不同**的分组规模。若脚本退化成只算代理口径，本条失败。
2. **Z 检验公式本身正确**：已知比例（1/10 vs 9/10）判方向；再用一对已知 z 值钉住
   双侧 p 的数值（不只是符号），防止有人把 erf 近似换坏。
3. **样本不足必须说「不足以判定」**：任一组 n<30 或 n=0 时不得给出显著结论，也不得抛异常。

反证（防止「正确性断言恒真」）：
- `_definition_sanity` 是「处理组定义是否正确」的判据（两组都非空、划分不重不漏）。
  `test_counterproof_all_receipts_definition_fails_sanity` 故意把处理组定义换成
  「全部单据」（对照组=0，分母错），断言该判据**失败**；若判据恒真（或脚本把全部
  单据当处理组却仍报两组），本条会失败。

隔离：本模块把 DB_PATH / RAG_DIR 指到临时目录（必须在首次 import app.* 之前设置），
临时库用 `db._make_engine()` 建**真实 schema**（手抄 DDL 会让「查询列名与真实表不
匹配」这类缺陷逃过测试）。另有一条用例直接对 live 库跑一次只读 collect，断言 live
库 md5 与 mtime 前后一致 —— 这是「脚本不写库」的正向证据。
"""

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile

import pytest

# 隔离环境：必须在首次 import app.* 之前设置（本模块自己的临时库与临时向量目录）
_TMP = tempfile.mkdtemp(prefix="memory_ab_baseline_")
os.environ["DB_PATH"] = os.path.join(_TMP, "base.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")

_SCRIPT = os.path.join(_DEMO_DIR, "scripts", "memory_ab_baseline.py")
_spec = importlib.util.spec_from_file_location("memory_ab_baseline", _SCRIPT)
mab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mab)


# -------------------------------------------------------------
# 夹具构造：真实 schema 的临时库
# -------------------------------------------------------------
def _seed(tmp_path, receipts, vendors=()):
    """建一个带真实 schema 的临时库。

    receipts = [(id, supplier_name, status, rag_context_json)]
    vendors  = 该库 vendor_memory 表里的供应商名（代理口径的处理组标签）
    """
    path = os.path.join(str(tmp_path), "ab.db")
    db.DB_PATH = path
    db._make_engine()
    conn = sqlite3.connect(path)
    try:
        conn.executemany(
            "INSERT INTO receipts (id, supplier_name, status, rag_context_json) "
            "VALUES (?, ?, ?, ?)", list(receipts))
        conn.executemany(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status) "
            "VALUES (?, ?, 'default', '', '', 'active')",
            [("mid-%d" % i, v) for i, v in enumerate(vendors)])
        conn.commit()
    finally:
        conn.close()
    return path


def _definition_sanity(stats, total):
    """「处理组定义是否正确」的判据：划分必须不重不漏且两组都有样本。

    这是反证的目标：把处理组定义换成「全部单据」时，对照组为 0，本判据必须失败。
    """
    assert stats["treatment"]["receipts"] > 0, "处理组为空，定义反了"
    assert stats["control"]["receipts"] > 0, \
        "对照组为空：处理组定义把全部单据都划进去了（分母错）"
    assert stats["treatment"]["receipts"] + stats["control"]["receipts"] == total, \
        "两组之和必须等于总单据数（划分必须不重不漏）"
    assert stats["treatment"]["receipts"] < total


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# -------------------------------------------------------------
# 1. Z 检验：已知比例判方向 + 已知 z 值钉住双侧 p 的数值
# -------------------------------------------------------------
def test_z_test_direction_and_significance_for_known_proportions():
    r = mab.two_proportion_z(9, 10, 1, 10)
    assert r["z"] > 0, "处理组 9/10 高于对照组 1/10，z 必须为正"
    assert r["p_value"] < 0.05
    assert abs(r["effect_size_pp"] - 80.0) < 1e-6

    rev = mab.two_proportion_z(1, 10, 9, 10)
    assert rev["z"] < 0, "方向反转后 z 必须为负"
    assert abs(rev["p_value"] - r["p_value"]) < 1e-9, "双侧 p 与方向无关"


def test_z_test_p_value_matches_known_value():
    """30/100 vs 50/100：pooled p̂=0.4，SE=0.0692820，z=2.886751，双侧 p≈0.0038927。

    钉住数值而不是只钉符号：把 math.erf 换成旧的近似实现（φ 会失真）时本条失败。
    """
    r = mab.two_proportion_z(50, 100, 30, 100)
    assert abs(r["z"] - 2.886751) < 1e-5
    assert abs(r["p_value"] - 0.0038927) < 1e-6


# -------------------------------------------------------------
# 2. 边界：任一组 n=0 / 方差为 0 时不抛异常，且不说「显著」
# -------------------------------------------------------------
def test_boundary_zero_group_does_not_raise():
    r = mab.two_proportion_z(0, 0, 5, 10)
    assert r["z"] is None and r["p_value"] is None
    assert r["note"], "不可判定必须给出 note，不能静默返回空结构"

    rows = [{"edited": True}, {"edited": False}]
    s = mab.ab_stats(rows, lambda _r: mab.TREATMENT)  # 对照组恒为空
    assert s["control"]["receipts"] == 0
    assert s["significant_at_0_05"] is False
    assert "不足以判定" in s["verdict"]


def test_boundary_zero_variance_is_not_significant():
    """两组各 40 条、一条都没被编辑 → p̂=0、方差为 0，z 无定义但不许抛异常。"""
    rows = [{"id": i, "edited": False} for i in range(80)]
    s = mab.ab_stats(rows, lambda r: mab.TREATMENT if r["id"] < 40 else mab.CONTROL)
    assert s["treatment"]["receipts"] == 40 and s["control"]["receipts"] == 40
    assert s["low_confidence"] is False
    assert s["z_test"]["p_value"] is None
    assert s["significant_at_0_05"] is False
    assert "方差为 0" in s["z_test"]["note"]


def test_low_sample_group_never_reports_significant():
    """n<30 一律「不足以判定」：10 vs 10 的极端差异（0/10 vs 10/10）也不得报显著。"""
    rows = ([{"edited": False}] * 10 + [{"edited": True}] * 10)
    s = mab.ab_stats(rows, lambda r: mab.TREATMENT if r["edited"] else mab.CONTROL)
    assert s["low_confidence"] is True
    assert s["significant_at_0_05"] is False
    assert "不足以判定" in s["verdict"]


# -------------------------------------------------------------
# 3. 两种口径必须给出不同的分组（脚本不能用代理变量冒充真实定义）
# -------------------------------------------------------------
def test_real_and_proxy_definitions_differ(tmp_path):
    receipts = [
        # 真注入、供应商无记忆 → 真实口径 treatment / 代理口径 control
        (1, "德利行", "edited", '{"vendor": "德利行"}'),
        # 未注入、供应商有记忆 → 真实口径 control / 代理口径 treatment
        (2, "祥興", "parsed", ""),
        (3, "祥興", "approved", "{}"),
        (4, "無記憶商號", "parsed", ""),
    ]
    path = _seed(tmp_path, receipts, vendors=["祥興"])
    report = mab.collect(db_path=path)

    real = report["definitions"]["rag_context"]
    proxy = report["definitions"]["vendor_memory"]
    assert real["treatment"]["receipts"] == 1
    assert real["control"]["receipts"] == 3
    assert proxy["treatment"]["receipts"] == 2
    assert proxy["control"]["receipts"] == 2

    _definition_sanity(real, 4)
    _definition_sanity(proxy, 4)
    assert real["label"] != proxy["label"]


def test_empty_marker_values_are_not_treated_as_injected(tmp_path):
    """''、'{}'、'\"\"' 三种空标记都表示「没注入」，口径与 memory_metrics.py 一致。"""
    receipts = [(1, "甲", "parsed", ""), (2, "甲", "parsed", "{}"),
                (3, "甲", "parsed", '""'), (4, "甲", "parsed", "  "),
                (5, "甲", "parsed", '[{"vendor": "甲"}]')]
    path = _seed(tmp_path, receipts)
    report = mab.collect(db_path=path)
    assert report["definitions"]["rag_context"]["treatment"]["receipts"] == 1


# -------------------------------------------------------------
# 4. 反证：处理组定义故意写错（全部单据）→ 正确性断言必须失败
# -------------------------------------------------------------
def test_counterproof_all_receipts_definition_fails_sanity(tmp_path):
    receipts = [
        (1, "德利行", "edited", '{"vendor": "德利行"}'),
        (2, "祥興", "parsed", ""),
        (3, "祥興", "approved", ""),
        (4, "無記憶商號", "parsed", ""),
    ]
    path = _seed(tmp_path, receipts, vendors=["祥興"])
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        rows, _vendors = mab.load_receipts(conn)
    finally:
        conn.close()

    # 正确定义先证明判据在本题上是通过的（排除「判据恒失败」）
    ok = mab.ab_stats(rows, mab.classify_rag_context)
    _definition_sanity(ok, 4)

    # 故意把处理组定义换成全部单据（分母错）→ 同一条判据必须失败
    wrong = mab.ab_stats(rows, lambda _r: mab.TREATMENT)
    assert wrong["control"]["receipts"] == 0
    with pytest.raises(AssertionError, match="对照组为空"):
        _definition_sanity(wrong, 4)


# -------------------------------------------------------------
# 5. 只读：collect 不改库内容，也不产生任何新文件
# -------------------------------------------------------------
def test_collect_is_readonly(tmp_path):
    receipts = [(1, "德利行", "edited", '{"vendor": "德利行"}'), (2, "祥興", "parsed", "")]
    path = _seed(tmp_path, receipts, vendors=["祥興"])
    before_md5, before_mtime = _md5(path), os.path.getmtime(path)
    before_files = sorted(os.listdir(str(tmp_path)))

    report = mab.collect(db_path=path)

    assert report["total_receipts"] == 2
    assert _md5(path) == before_md5, "只读脚本不得改动库内容"
    assert os.path.getmtime(path) == before_mtime
    assert sorted(os.listdir(str(tmp_path))) == before_files


@pytest.mark.skipif(not os.path.exists(_LIVE_DB), reason="live 库不存在时跳过")
def test_collect_on_live_db_is_readonly():
    """对 live 库跑一次真实基线：只读、不抛异常、给出结论字段。

    live 向量库不参与本脚本（脚本不 import rag），故只核 live DB 的 md5 / mtime。
    """
    before_md5, before_mtime = _md5(_LIVE_DB), os.path.getmtime(_LIVE_DB)
    report = mab.collect(db_path=_LIVE_DB)
    assert _md5(_LIVE_DB) == before_md5
    assert os.path.getmtime(_LIVE_DB) == before_mtime

    assert set(report["definitions"]) == {"rag_context", "vendor_memory"}
    for s in report["definitions"].values():
        assert s["verdict"], "每个口径都必须给出「是否显著」的结论"
        if s["low_confidence"]:
            assert "不足以判定" in s["verdict"], "样本不足时不得给显著结论"
    assert report["conclusion"].startswith("以")


# -------------------------------------------------------------
# 6. CLI：--save / --compare / --json 与 memory_metrics.py 风格一致
# -------------------------------------------------------------
def test_cli_save_and_compare(tmp_path, monkeypatch, capsys):
    receipts = [(1, "德利行", "edited", '{"vendor": "德利行"}'),
                (2, "祥興", "parsed", ""), (3, "祥興", "approved", "")]
    path = _seed(tmp_path, receipts, vendors=["祥興"])
    monkeypatch.setattr(mab, "DB", path)
    base = str(tmp_path / "baseline.json")

    assert mab.main(["--save", base]) == 0
    out = capsys.readouterr().out
    assert "已保存 A/B 基线" in out
    with open(base, encoding="utf-8") as f:
        saved = __import__("json").load(f)
    assert saved["definitions"]["rag_context"]["treatment"]["receipts"] == 1

    assert mab.main(["--compare", base]) == 0
    out = capsys.readouterr().out
    assert "与基线对比" in out and "delta_pp" in out and "结论：" in out
