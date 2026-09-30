# -*- coding: utf-8 -*-
"""P2-T6 验收数字：「有效命中率」口径（`memory_metrics.memory_hit_effective`）的回归测试。

why 需要这一批：P2d 审核 mustFix 第 1 条指出该口径此前**只在注释里承诺、未实现为字段**，
脚本仍报 12/701 = 1.71%（分母是全部单据，含「供应商无法确定」与「该供应商根本没有记忆」
两类本就不可能命中的单据）。换成正确分母后，1.71% 这个数字的性质就变了：它不再是能力，
也不能用来看回归。故本文件钉住四件事：

1. **四类单据各自只计入应计的那类**（核心）：可确定且有记忆未注入 / 可确定且有记忆已注入
   / 可确定但无记忆 / 不可确定（未尝试）；
2. **「未尝试」不得计入分子或分母**：供应商不可确定的单据即使 rag_context_json 非空也不算
   命中 —— 否则「谁都没查过」会被算成能力；
3. **边界：分母为 0 输出「无数据」（null）而不是 0%**；反过来，分母非 0 而分子为 0 时必须是
   0.0%（那是「有条件命中却一次都没命中」的真实失败，不能也报「无数据」把它糊过去）；
4. **口径自述字段（criteria）真的被填充**，且声明与旧口径不可混读（防「定义了没接线」）；
5. **记忆总开关（Q3-M3）**：开关关闭时整个口径判为「未测量」（分母归 0 / rate=null /
   no_data=true / disabled_by_switch=true + criteria 自述），与「开着但一次都没命中」
   （分母>0、rate=0.0%）必须可区分 —— 此前开关虽被读出却从未被消费，两种状态在报告里
   长得一模一样；且开关解析必须与生产读侧同源（同一份布尔词表、同一个设置源）。

反证/自校准（口径被破坏时必须失败，见本节两条 `test_self_calibration_*`）：把分母的
「该供应商有可用记忆」条件替换成 `1 = 1` → `eligible_denominator` 必须从 3 变成 4、
有效命中率必须从 33.33 变成 25.0；把「供应商可确定」条件替换成 `1 = 1` →
`vendor_determinable` 必须从 4 变成 5。若统计悄悄退化成「按全部单据当分母」，这两条信号会消失。
开关那条另有独立反证：去掉 `if disabled: eligible = injected = 0` 这段 → 第 7 节关闭态用例必须失败
（分母从 0 变回 1、rate 从 null 变回 0.0），实测记录见交付说明。

隔离：本模块把 DB_PATH / RAG_DIR 指到临时目录（必须在首次 import app.* 之前设置），
临时库用 `db._make_engine()` 建**真实 schema**（手抄 DDL 会让「查询列名与真实表不匹配」
这类缺陷逃过测试）。另有一条用例对 live 库跑一次只读 collect，核 live 库 md5/mtime 与两个
live 向量库指纹前后一致 —— 脚本本身不 import rag，这道核对是「度量不动 live」的正向证据。
"""

import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid

import pytest

# 隔离环境：必须在首次 import app.* 之前设置（本模块自己的临时库与临时向量目录）
_TMP = tempfile.mkdtemp(prefix="memory_effective_hit_")
os.environ["DB_PATH"] = os.path.join(_TMP, "base.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_LIVE_RAG_DIRS = [os.path.join(_DEMO_DIR, ".rag_chroma"),
                  os.path.join(_REPO_ROOT, ".rag_chroma")]

_SCRIPT = os.path.join(_DEMO_DIR, "scripts", "memory_metrics.py")
_spec = importlib.util.spec_from_file_location("memory_metrics_eff", _SCRIPT)
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


# -------------------------------------------------------------
# 夹具：真实 schema 的临时库
# -------------------------------------------------------------
def _mem(mid, vendor, status="active", decay=1.0, valid_to="", created_at="",
         supplier_id=None, notes="供应商别名提示", sample="样例版式"):
    """构造一条 vendor_memory 行（默认＝可用记忆）。"""
    return {"mid": mid, "vendor": vendor, "status": status, "decay": decay,
            "valid_to": valid_to, "created_at": created_at, "supplier_id": supplier_id,
            "notes": notes, "sample": sample}


def _seed(tmp_path, receipts, memories=(), settings=None):
    """建一个带真实 schema 的临时库。

    receipts = [(id, supplier_name, supplier_id, status, rag_context_json)]
    memories = _mem(...) 列表
    settings = app_settings 覆盖值（如 {"memory_ttl_days": 30}）
    """
    path = os.path.join(str(tmp_path), "eff_%s.db" % uuid.uuid4().hex[:8])
    db.DB_PATH = path
    db._make_engine()
    conn = sqlite3.connect(path)
    try:
        if receipts:
            conn.executemany(
                "INSERT INTO receipts (id, supplier_name, supplier_id, status, rag_context_json) "
                "VALUES (?, ?, ?, ?, ?)", list(receipts))
        for m in memories:
            conn.execute(
                "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status, "
                "decay_score, valid_to, created_at, supplier_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m["mid"], m["vendor"], "default", m["notes"], m["sample"], m["status"],
                 m["decay"], m["valid_to"], m["created_at"], m["supplier_id"]))
        for k, v in (settings or {}).items():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()
    finally:
        conn.close()
    return path


_ACTIVE_MEM = _mem("m-active", "德利行")


def _eff(path):
    return mm.collect(db_path=path)["memory_hit_effective"]


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _rag_fingerprint():
    """live 向量库指纹：目录不存在记 ABSENT，否则记 (文件数, chroma sqlite md5)。"""
    out = []
    for d in _LIVE_RAG_DIRS:
        if not os.path.isdir(d):
            out.append((d, "ABSENT"))
            continue
        names = []
        for root, _dirs, files in os.walk(d):
            names.extend(os.path.join(root, n) for n in sorted(files))
        sqlite = os.path.join(d, "chroma.sqlite3")
        out.append((d, (len(names), _md5(sqlite) if os.path.isfile(sqlite) else "-")))
    return out


# -------------------------------------------------------------
# 1. 四类单据各自只计入应计的那类（核心口径）
# -------------------------------------------------------------
@pytest.mark.parametrize("label,receipts,memories,expect", [
    ("可确定且有记忆但未注入",
     [(1, "德利行", None, "parsed", "")], [_ACTIVE_MEM], (1, 0)),
    ("可确定且有记忆且已注入",
     [(1, "德利行", None, "parsed", '{"vendor": "德利行"}')], [_ACTIVE_MEM], (1, 1)),
    ("可确定但该供应商无记忆（无条件兑现，不进分母）",
     [(1, "新商號", None, "parsed", "")], [], (0, 0)),
    ("供应商不可确定（未尝试，即便真的注入了也不算命中）",
     [(1, None, None, "parsed", '{"vendor": "x"}')], [_ACTIVE_MEM], (0, 0)),
])
def test_each_class_counts_only_where_it_should(tmp_path, label, receipts, memories, expect):
    path = _seed(tmp_path, receipts, memories)
    eff = _eff(path)
    assert (eff["eligible_denominator"], eff["injected"]) == expect, label
    if expect[0] == 0:
        # 分母为 0 ＝「没有可命中的样本」：输出无数据，不是 0%
        assert eff["no_data"] is True and eff["effective_rate_pct"] is None, label
    else:
        assert eff["no_data"] is False, label


def test_aggregate_mixes_the_four_classes_exactly_once_each(tmp_path):
    """四类混在一批：每个数字都只能计入应计的那类（组合口径不能碰巧正确）。"""
    receipts = [
        (1, "德利行", None, "parsed", ""),                    # 计入分母、不计分子
        (2, "德利行", None, "edited", '{"vendor": "德利行"}'),  # 计入分母、计入分子
        (3, "德利行", None, "parsed", ""),                    # 计入分母、不计分子
        (4, "新商號", None, "parsed", ""),                     # 无记忆：两者都不计
        (5, None, None, "parsed", '{"vendor": "x"}'),         # 不可确定：两者都不计
    ]
    path = _seed(tmp_path, receipts, [_ACTIVE_MEM])
    rep = mm.collect(db_path=path)
    eff = rep["memory_hit_effective"]

    assert eff["receipts_total"] == 5
    assert eff["vendor_determinable"] == 4      # 第 5 条不可确定，不计
    assert eff["eligible_denominator"] == 3     # 第 4 条无可用记忆，不计
    assert eff["injected"] == 1
    assert eff["excluded_no_usable_memory"] == 1
    assert eff["effective_rate_pct"] == 33.33
    assert eff["no_data"] is False

    # 旧口径保留且与新口径给出不同数字（分母不同，不可混读）
    old = rep["memory_hit"]
    assert old["total_receipts"] == 5 and old["with_memory_context"] == 2
    assert old["rate_pct"] == 40.0
    assert old["rate_pct"] != eff["effective_rate_pct"]
    assert old["note"], "旧口径必须自带说明，不能让人以为它和新口径是一回事"
    assert eff["incomparable_with"] == "memory_hit"
    assert eff["incomparable_reason"]


def test_zero_injected_with_nonzero_denominator_reports_zero_not_no_data(tmp_path):
    """分母非 0、分子为 0 ＝「有条件命中却一次都没命中」，必须报 0.0（真实失败），
    不能输出「无数据」把它糊过去 —— 这是「分母为 0 输出无数据」的镜像错误。"""
    path = _seed(tmp_path, [(1, "德利行", None, "parsed", "")], [_ACTIVE_MEM])
    eff = _eff(path)
    assert eff["eligible_denominator"] == 1 and eff["injected"] == 0
    assert eff["no_data"] is False
    assert eff["effective_rate_pct"] == 0.0
    assert mm._fmt_rate_pct(eff["effective_rate_pct"]) == "0.0%"
    assert mm._fmt_rate_pct(None) == "无数据（分母为 0）"


# -------------------------------------------------------------
# 2. 「可用记忆」的每个条件都必须在判定里生效
# -------------------------------------------------------------
def test_usable_memory_requires_active_unexpired_decayed_and_contentful(tmp_path):
    """五个供应商各带一条「不合格」记忆，只有全条件合格的那条让单据进分母。

    刻意逐条拆开：只测「status='active'」会让「过期 / 劣化 / 空内容」三条漏网，
    而它们同样让记忆不可能被注入（= 分母里混进无缘命中的成员，正是本任务要修的错误）。
    """
    receipts = [(1, "甲", None, "parsed", '{"v": 1}'), (2, "乙", None, "parsed", ""),
                (3, "丙", None, "parsed", ""), (4, "丁", None, "parsed", ""),
                (5, "戊", None, "parsed", ""), (6, "己", None, "parsed", "")]
    memories = [
        _mem("m-ok", "甲"),                                        # 可用
        _mem("m-archived", "乙", status="archived"),                # 归档：不注入
        _mem("m-expired", "丙", valid_to="2000-01-01T00:00:00"),   # 显式失效期已过
        _mem("m-decayed", "丁", decay=0.3),                        # 低于归档阈值 0.5
        _mem("m-ttl", "戊", created_at="2020-01-01T00:00:00"),     # 超过 TTL
        _mem("m-empty", "己", notes="", sample=""),                # 无内容：检索侧跳过
    ]
    path = _seed(tmp_path, receipts, memories, settings={"memory_ttl_days": 30})
    eff = _eff(path)
    assert eff["vendor_determinable"] == 6
    assert eff["eligible_denominator"] == 1, "五个不合格条件必须各切掉一条"
    assert eff["excluded_no_usable_memory"] == 5
    assert eff["injected"] == 1 and eff["effective_rate_pct"] == 100.0
    # 阈值真的被从 app_settings 读到（而不是写死），并把实时值写进口径自述
    assert eff["criteria"]["settings"]["memory_ttl_days"] == 30
    assert eff["criteria"]["settings"]["memory_archive_threshold"] == 0.5


def test_unparseable_timestamps_are_treated_as_not_expired(tmp_path):
    """时间戳格式脏 → 按未过期处理（与 db._memory_row_expired 一致）：
    宁可注入一条时间戳脏的记忆，也不因格式问题静默丢掉整批记忆。"""
    path = _seed(tmp_path, [(1, "甲", None, "parsed", "")],
                 [_mem("m-dirty", "甲", valid_to="不是时间", created_at="也不是时间")],
                 settings={"memory_ttl_days": 30})
    assert _eff(path)["eligible_denominator"] == 1


def test_memory_of_another_tenant_does_not_qualify(tmp_path):
    """记忆按租户隔离：别的租户的记忆不能让本租户单据进分母（否则读侧也拿不到）。"""
    path = _seed(tmp_path, [(1, "甲", None, "parsed", "")], [_mem("m-t2", "甲")])
    conn = sqlite3.connect(path)
    try:
        conn.execute("update vendor_memory set tenant_id='t2' where memory_id='m-t2'")
        conn.commit()
    finally:
        conn.close()
    assert _eff(path)["eligible_denominator"] == 0


def test_anchor_channel_matches_even_when_name_differs(tmp_path):
    """锚点通道：`receipts.supplier_id` 命中记忆的 `supplier_id` 即算有可用记忆
    （同一供应商的不同写法，与 db.list_vendor_memory 的锚点优先口径一致）。"""
    path = _seed(tmp_path, [(1, "德利行有限公司", 7, "parsed", "")],
                 [_mem("m-anchored", "德利行", supplier_id=7)])
    assert _eff(path)["eligible_denominator"] == 1


# -------------------------------------------------------------
# 3. 口径自述字段真的被填充（防「定义了没接线」）
# -------------------------------------------------------------
def test_criteria_field_self_describes_the_denominator(tmp_path):
    path = _seed(tmp_path, [(1, "德利行", None, "parsed", "")], [_ACTIVE_MEM])
    eff = _eff(path)
    for f in ("eligible_denominator", "injected", "effective_rate_pct", "no_data"):
        assert f in eff, "缺少验收要求的字段: %s" % f
    c = eff["criteria"]
    for k in ("denominator", "numerator", "vendor_determinable", "usable_memory",
              "not_attempted", "denominator_zero", "settings", "source"):
        assert c.get(k), "口径自述缺少字段: %s" % k
    assert "未尝试" in c["vendor_determinable"] or "未尝试" in c["not_attempted"]
    assert "null" in c["denominator_zero"]


# -------------------------------------------------------------
# 4. 自校准：条件被破坏 → 数字必须变化（口径退化的信号）
# -------------------------------------------------------------
def _four_class_db(tmp_path):
    receipts = [
        (1, "德利行", None, "parsed", ""),
        (2, "德利行", None, "edited", '{"vendor": "德利行"}'),
        (3, "德利行", None, "parsed", ""),
        (4, "新商號", None, "parsed", ""),
        (5, None, None, "parsed", '{"vendor": "x"}'),
    ]
    return _seed(tmp_path, receipts, [_ACTIVE_MEM])


def test_self_calibration_dropping_usable_memory_condition_changes_numbers(tmp_path, monkeypatch):
    """任务要求的反证：去掉分母里的「该供应商有可用记忆」条件 → 口径必须变化。"""
    path = _four_class_db(tmp_path)
    ok = _eff(path)
    assert ok["eligible_denominator"] == 3, "先证明判据在这份数据上是通过的（排除恒失败）"

    monkeypatch.setattr(mm, "USABLE_MEMORY_PREDICATE", "1 = 1")
    bad = _eff(path)
    assert bad["eligible_denominator"] == bad["vendor_determinable"] == 4
    assert bad["eligible_denominator"] != ok["eligible_denominator"]
    assert bad["effective_rate_pct"] == 25.0 and bad["effective_rate_pct"] != ok["effective_rate_pct"]


def test_self_calibration_dropping_vendor_determinable_condition_changes_numbers(tmp_path,
                                                                                 monkeypatch):
    """去掉「供应商可确定」条件（未尝试也被算进来）→ 分母的中间量必须变化。"""
    path = _four_class_db(tmp_path)
    ok = _eff(path)
    assert ok["vendor_determinable"] == 4 and ok["excluded_no_usable_memory"] == 1

    monkeypatch.setattr(mm, "VENDOR_DETERMINABLE_PREDICATE", "1 = 1")
    bad = _eff(path)
    assert bad["vendor_determinable"] == 5, "条件被去掉后必须变化，否则说明统计没用这个条件"
    assert bad["excluded_no_usable_memory"] == 2


# -------------------------------------------------------------
# 5. 只读：不改库、不产生新文件、live 侧零副作用
# -------------------------------------------------------------
def test_collect_is_readonly_on_temp_db(tmp_path):
    path = _seed(tmp_path, [(1, "德利行", None, "parsed", "")], [_ACTIVE_MEM])
    before_md5, before_mtime = _md5(path), os.path.getmtime(path)
    before_files = sorted(os.listdir(str(tmp_path)))

    eff = _eff(path)
    assert eff["eligible_denominator"] == 1

    assert _md5(path) == before_md5, "只读脚本不得改动库内容"
    assert os.path.getmtime(path) == before_mtime
    assert sorted(os.listdir(str(tmp_path))) == before_files


@pytest.mark.skipif(not os.path.exists(_LIVE_DB), reason="live 库不存在时跳过")
def test_collect_on_live_db_is_readonly_and_gives_numbers():
    """对 live 库跑一次真实测量（脚本以 mode=ro 打开，只读）。

    同时核 live 向量库指纹：脚本不 import rag，本核对是「度量不动 live 向量」的正面证据。
    """
    before_db = (_md5(_LIVE_DB), os.path.getmtime(_LIVE_DB))
    before_rag = _rag_fingerprint()

    rep = mm.collect(db_path=_LIVE_DB)
    eff = rep["memory_hit_effective"]

    assert eff["receipts_total"] == rep["memory_hit"]["total_receipts"]
    assert eff["eligible_denominator"] <= eff["vendor_determinable"] <= eff["receipts_total"]
    assert eff["injected"] <= eff["eligible_denominator"]
    if eff["no_data"]:
        assert eff["effective_rate_pct"] is None
    else:
        assert eff["effective_rate_pct"] == round(
            100.0 * eff["injected"] / eff["eligible_denominator"], 2)

    assert (_md5(_LIVE_DB), os.path.getmtime(_LIVE_DB)) == before_db, "度量动了 live 库"
    assert _rag_fingerprint() == before_rag, "度量动了 live 向量库"
    print("[live] %s" % {k: eff[k] for k in ("receipts_total", "vendor_determinable",
                                             "eligible_denominator", "injected",
                                             "effective_rate_pct", "no_data")})


# -------------------------------------------------------------
# 6. 接线：新字段必须真的出现在脚本自身的输出路径上
# -------------------------------------------------------------
def test_json_output_exposes_the_new_fields(tmp_path, monkeypatch, capsys):
    """新字段若无任何消费方就是「定义了没接线」。

    消费方：① 本脚本 `--json` / 默认输出（本用例）；② `GET /api/admin/memory-metrics`
    原样返回 `collect()` 结果（demo/app/api_admin.py:944）；③ `--compare` 单列一行。
    """
    path = _seed(tmp_path, [(1, "德利行", None, "parsed", "")], [_ACTIVE_MEM])
    monkeypatch.setattr(mm, "DB", path)
    monkeypatch.setattr(sys, "argv", ["memory_metrics.py", "--json"])

    mm.main()
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    eff = payload["memory_hit_effective"]
    assert eff["eligible_denominator"] == 1 and eff["injected"] == 0
    assert eff["effective_rate_pct"] == 0.0
    assert eff["criteria"]["denominator"]


# -------------------------------------------------------------
# 7. 记忆总开关（Q3-M3）：关闭 ⇒ 口径「未测量」，不得用 0.00% 冒充
# -------------------------------------------------------------
def _seed_with_switch(tmp_path, raw, receipts=None, memories=None):
    """种一份「供应商可确定 + 该供应商有 active 可用记忆」的数据，并按生产写入路径设置开关。

    why 用 `db.set_app_setting` 而不是在 `_seed` 里直接 INSERT：开关的写入侧在生产就是这一条
    路径（管理台 → settings_service.set_value → db.set_app_setting）。让度量脚本读出它写下的值，
    才证明「口径用的开关 = 生产停注入用的开关」同源（同一个库、同一个键）。
    raw=None 表示不设置该键（= 未设置 → 缺省开启）。
    """
    path = _seed(tmp_path,
                 receipts if receipts is not None else [(1, "德利行", None, "parsed", "")],
                 memories if memories is not None else [_ACTIVE_MEM])
    if raw is not None:
        db.set_app_setting("memory_enabled", raw)
    return path


def test_switch_off_reports_unmeasured_instead_of_zero_pct(tmp_path):
    """开关关闭 ⇒ eligible 归 0 / rate=null / no_data=true / disabled_by_switch=true。

    修前实测（审核探针 /tmp/aud_m3_probe.py）：关与开逐字段完全相同
    （eligible=1 / injected=0 / rate=0.0 / no_data=false）→ `SWITCH_OFF_STILL_REPORTS_0.00_PCT`。
    """
    path = _seed_with_switch(tmp_path, "0")
    eff = _eff(path)

    # 自校准：脚本读到的开关原始值必须就是生产写入路径写下的那个（防读错源导致假绿）
    s = eff["criteria"]["settings"]
    assert s["memory_enabled_raw"] == "0"
    assert s["memory_enabled"] is False
    assert s["memory_enabled_read_error"] is None

    assert eff["disabled_by_switch"] is True
    assert eff["eligible_denominator"] == 0
    assert eff["injected"] == 0
    assert eff["effective_rate_pct"] is None, "关闭态不得输出 0%（那是『测了没命中』）"
    assert eff["no_data"] is True
    # 不用错的理由：excluded 字段名说的是「因**无可用记忆**被排除」，开关关闭时差额不是这个原因
    assert eff["excluded_no_usable_memory"] == 0
    # 数据事实照实报出（供应商确实可确定）——只是命中率没测量
    assert eff["vendor_determinable"] == 1
    assert eff["criteria"]["switch_off"], "口径自述必须说明『总开关关闭时不计分母』"


def test_switch_on_same_data_reports_real_zero_pct(tmp_path):
    """同一份种子只翻开关 → 必须变回「真 0%」（分母 1 / 分子 0）而不是「无数据」。

    这条同时是上一条的自校准：证明种子确实满足「有条件命中」，上一条的 0 是开关造成的、
    不是数据本来就不够（否则那条用例可能是因为分母天然为 0 而碰巧通过）。
    """
    on_path = _seed_with_switch(tmp_path, "1")
    on = _eff(on_path)
    s = on["criteria"]["settings"]
    assert s["memory_enabled_raw"] == "1" and s["memory_enabled"] is True
    assert on["disabled_by_switch"] is False
    assert (on["eligible_denominator"], on["injected"]) == (1, 0)
    assert on["no_data"] is False
    assert on["effective_rate_pct"] == 0.0
    assert mm._eff_rate_text(on) == "0.0%"

    off = _eff(_seed_with_switch(tmp_path, "0"))
    assert (off["eligible_denominator"], off["injected"]) == (0, 0)
    assert off["no_data"] is True and off["disabled_by_switch"] is True
    assert off["effective_rate_pct"] is None
    assert mm._eff_rate_text(off) == "无数据（记忆总开关已关闭）", "文案必须讲出关闭态而不是「分母为 0」"


@pytest.mark.parametrize("raw,expected_on", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("False", False), ("no", False), ("off", False),
    ("", True),   # 空串＝未设置 → 缺省开启
    (None, True),  # 键不存在 → 缺省开启
])
def test_switch_parse_follows_production_words(tmp_path, raw, expected_on):
    """开关解析必须与生产读侧用同一份布尔词表（settings_service.coerce_bool_raw）。

    自校准：读到的原始值必须等于写进去的那个；再与 `coerce_bool_raw` 逐值比对。
    这条能抓住「读错源/用错解析」：脚本此前用 `float(raw)` 解析开关，`false`/`off`/`no`
    会抛 ValueError 后回退缺省 True —— 即把**显式关闭读成开启**（本参数化用例里这几档会失败）。
    """
    from app.services.settings_service import coerce_bool_raw

    path = _seed_with_switch(tmp_path, raw)
    s = _eff(path)["criteria"]["settings"]
    assert s["memory_enabled_raw"] == raw, "读到的原始值必须就是写进库里的那个"
    assert s["memory_enabled_read_error"] is None
    assert s["memory_enabled"] is expected_on
    assert s["memory_enabled"] == coerce_bool_raw(raw, True), "必须与生产词表逐值一致"
    assert _eff(path)["disabled_by_switch"] is (not expected_on)


@pytest.mark.parametrize("cwd", ["repo_root", "demo"])
def test_switch_read_does_not_import_app_db(tmp_path, cwd):
    """红线守卫：度量脚本解析开关**不得** import app.db（import 期 _make_engine() 会执行迁移块，
    对 DB_PATH 指向的库做 ALTER —— 本项目已两次让 live 库发生加法迁移）。

    why 用子进程且 DB_PATH 指向一个**不存在**的路径：本测试模块自身早已 import app.db，
    在进程内断言 sys.modules 是恒真的假绿；子进程里只要脚本 import 了 app.db，那个影子库就会
    被创建并迁移，文件随即出现。同时断言解析入口**确实加载成功**（否则「没 import app.db」
    可能只是因为 import 整个失败而假绿）。
    """
    shadow_db = os.path.join(str(tmp_path), "shadow_never_created_%s.db" % cwd)
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('mm_probe', %r)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print('app_db_imported=%%s loader_ok=%%s' %% ('app.db' in sys.modules,"
        " m._COERCE_BOOL_RAW is not None))\n"
    ) % _SCRIPT
    env = dict(os.environ, DB_PATH=shadow_db, RAG_DIR=os.path.join(str(tmp_path), "shadow_rag"))
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True,
                         cwd=(_REPO_ROOT if cwd == "repo_root" else _DEMO_DIR), env=env)
    assert out.returncode == 0, out.stderr
    assert "app_db_imported=False loader_ok=True" in out.stdout, out.stdout
    assert not os.path.exists(shadow_db), "解析开关时 import 了 app.db → 影子库被创建/迁移"
