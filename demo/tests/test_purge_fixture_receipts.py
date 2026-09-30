# -*- coding: utf-8 -*-
"""AN1：夹具单据清退工具（``scripts/purge_fixture_receipts.py``）的判别式用例。

**为什么单独给这个工具写测试**：它是一个**不可逆的删除工具**（``--apply`` 直接
``DELETE`` 单据与明细）。历史上这类清理工具出过两种事故：一是误杀（判据太宽，把
真实单据当夹具删掉），二是「预演说的和落笔做的不一样」。本批为每一类风险各造一条
实物对照，**去掉对应判据或改动执行顺序，对应用例必然失败**（回滚判据后的反向验证见交付报告）：

- **防误杀的关键对照**：「夹具名 **但** 有原图」与「真实供应商 **且** 无原图」
  **都必须不命中** —— 这正是「两个条件同时成立才进候选」的判别力所在；该对照对
  `--ids` 的缺省安全双条件同样成立（缺省不得删未过条件的行）；
- **「已读清单」对齐**（P2seed3 mustFix 2）：候选必须被逐条标注「计划内 / 计划外」并给
  分段汇总；`--only-plan-set` 是**真过滤器**（2026-09-23 修正）：只删计划内、
  计划外候选**原样留库**并被点名「还有 N 张计划外未删」；不带该开关时预演必须提示
  「实际删除集合 != 已读清单，清单外还有 M 张」；
- **`--ids` 显式入口**（2026-09-23 新增）：每个 id 必须存在（或可证明「已被删除」），
  「从不存在」即报错、零写入；每行必须过安全双条件（越权需**危险开关** `--allow-ids-unsafe`）；
  它让「判据结构上抓不到的行」也能被精确清退（这是 2026-09-23 暴露的真实缺口）；
- **危险开关的命名与语义一致**（2026-09-23 P23 mustFix 3/4，P33·BA1）：`--allow-ids-unsafe`
  这个名字必须能读成「危险」，且 help / 错误文案 / 报告三处都要逐条点名它**同时关闭两道闸**
  （无夹具标记 / 有原图）；**旧名 `--allow-ids-outside-plan` 必须彻底删掉**（不留别名，
  否则两个名字共存、认知负担仍在）；
- **`--ids` 三类口径可分辨**（P23 mustFix 4）：「从不存在」（编号超出已分配区间）报错，
  「已被删除（幂等重跑）」（编号已分配过或属已读清单）不报错、`total_changes=0` 并在报告里
  分类计数，「存在但未过安全双条件」单独计数 —— 混成一条「不存在」会让人分不清该去核对 id
  还是该收工；
- **幂等与已知事实**：内置清单里的 27 个 id 在 live 上**现已不存在**（用户已批准的清退
  已执行完毕），重跑必须 `total_changes=0`、不报错，并在预演里明说「已不存在」；
- **先删子表再删主表**：对删除语句序列做结构性断言（不靠跑完倒推）；
- **预演零写入**：用文件 md5 自证（比「行数没变」强）；
- **非候选区域指纹前后一致**，且该断言**自带判别力自校准**（人为改一条非候选行，
  指纹必须跟着变 —— 否则这条断言可能恒真，恒真用例比没有更糟）。

全部离线假数据：临时 DB + 临时 RAG 目录（走 demo/conftest.py 的 autouse 隔离，
本模块再在 import 前显式声明一次，确保「import app.* 不会碰到 live 库」）。
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
_TMP = tempfile.mkdtemp(prefix="purge_fixture_receipts_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_purge.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import purge_fixture_receipts as purge  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """每个用例一个全新的临时库（建出完整 schema），并造出对照实物。"""
    dbp = str(tmp_path / "purge.db")
    monkeypatch.setenv("DB_PATH", dbp)
    monkeypatch.setattr(db, "DB_PATH", dbp)
    db._make_engine()                     # 内含 create_all，建出所有表
    _seed(dbp)
    return dbp


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _seed(db_path):
    """造出「候选 3 条 + 防误杀对照 3 条 + 子表/引用」。

    候选：101 测试供应商(无图) / 102 直通审批测试店(无图) / 103 7-ELEVEN (1542)(无图)
    防误杀：201 测试供应商**有原图** / 202 德利行**无原图** / 203 7-ELEVEN (1542)**有原图**
    """
    con = sqlite3.connect(db_path)
    try:
        rows = [
            (101, "测试供应商", "approved", ""),
            (102, "直通审批测试店", "approved", ""),
            (103, "7-ELEVEN (1542)", "parsed", ""),
            (201, "测试供应商", "approved", "/uploads/real201.jpg"),
            (202, "德利行 Tak Lee Hong", "approved", ""),
            (203, "7-ELEVEN (1542)", "parsed", "/uploads/real203.jpg"),
        ]
        for rid, name, status, img in rows:
            con.execute(
                "INSERT INTO receipts (id, supplier_name, status, image_path,"
                " tenant_id, created_at, updated_at) VALUES (?,?,?,?,'default','','')",
                (rid, name, status, img))
        # 明细：候选各若干 + 一条属于非候选（必须活下来）
        items = [(101, "a"), (101, "b"), (102, "c"), (103, "d"), (201, "keep")]
        for rid, nm in items:
            con.execute("INSERT INTO receipt_items (receipt_id, name, tenant_id)"
                        " VALUES (?,?,'default')", (rid, nm))
        # 其它子表（级联范围）
        con.execute("INSERT INTO inventory_log (receipt_id, name, tenant_id)"
                    " VALUES (101,'x','default')")
        con.execute("INSERT INTO inventory_log (receipt_id, name, tenant_id)"
                    " VALUES (201,'keep','default')")
        con.execute("INSERT INTO user_event (receipt_id, event_type, tenant_id)"
                    " VALUES (101,'upload','default')")
        # 引用型 JSON 列：记忆行引用 101（应被摘除）与 999（应保留）
        con.execute(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample,"
            " source_ref, status) VALUES ('m1','测试供应商','default','','',?,'active')",
            (json.dumps(["101", "999"]),))
        con.commit()
    finally:
        con.close()


def _ids(db_path, sql, params=()):
    con = sqlite3.connect(db_path)
    try:
        return [int(r[0]) for r in con.execute(sql, params)]
    finally:
        con.close()


def _scalar(db_path, sql, params=()):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


# -------------------------------------------------------------
# 判据：两个条件必须同时成立
# -------------------------------------------------------------
def test_candidate_requires_both_conditions(_fresh_db):
    """防误杀的核心对照：夹具名但有原图 / 真实供应商无原图 都不命中。"""
    con = purge._connect(_fresh_db)
    try:
        got = [c["id"] for c in purge.find_candidates(con)]
    finally:
        con.close()
    assert got == [101, 102, 103]

    # 名字命中 + 有原图 -> 排除（原因写明），这就是「宁可少杀」的守卫。
    ok, reason, hits = purge.is_candidate("测试供应商", "/uploads/x.jpg")
    assert ok is False and reason == "有原图" and hits
    # 真实供应商 + 无原图 -> 排除（名字不含任何标记）。
    ok, reason, hits = purge.is_candidate("德利行 Tak Lee Hong", "")
    assert ok is False and reason == "名字未命中夹具标记" and hits == []
    # 两条都满足才是候选。
    ok, _reason, _hits = purge.is_candidate("直通审批测试店", "")
    assert ok is True


# -------------------------------------------------------------
# 预演：源库零写入
# -------------------------------------------------------------
def test_dry_run_writes_nothing(_fresh_db):
    before_md5 = _md5(_fresh_db)
    report = purge.run(_fresh_db, apply=False)
    assert report["mode"] == "dry_run"
    assert report["candidate_ids"] == [101, 102, 103]
    assert report["total_changes"] == 0
    assert _md5(_fresh_db) == before_md5          # 比「行数没变」更强的自证
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts") == 6


# -------------------------------------------------------------
# 执行顺序：先子表、后主表
# -------------------------------------------------------------
def test_purge_order_child_before_parent(_fresh_db):
    con = purge._connect(_fresh_db)
    try:
        children = purge.child_tables(con)
    finally:
        con.close()
    assert "receipt_items" in children
    stmts = purge.purge_statements(children, [101, 102])
    tables = [re.search(r'FROM "([^"]+)"', sql).group(1) for sql, _ in stmts]
    assert tables[-1] == "receipts"                       # 主表必须最后删
    assert tables.index("receipt_items") < tables.index("receipts")
    assert "receipts" not in tables[:-1]                  # 中间不得夹主表


# -------------------------------------------------------------
# 落笔：级联删净 + 非候选毫发无伤 + 备份可恢复
# -------------------------------------------------------------
def test_apply_cascades_and_backs_up(_fresh_db, tmp_path):
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path))
    assert report["candidate_count"] == 3
    assert report["total_changes"] > 0
    assert report["fingerprint_ok"] is True

    # 候选被删净（主表 + 子表）。
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [201, 202, 203]
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM receipt_items WHERE receipt_id IN (101,102,103)") == 0
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM inventory_log WHERE receipt_id IN (101,102,103)") == 0
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM user_event WHERE receipt_id IN (101,102,103)") == 0
    # 非候选子表行（201 的明细 / 库存）必须活着。
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM receipt_items WHERE receipt_id = 201") == 1
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM inventory_log WHERE receipt_id = 201") == 1

    # 备份存在、md5 对得上、可恢复。
    assert os.path.exists(report["backup_path"])
    assert report["backup_md5"] == _md5(report["backup_path"])
    assert report["backup_path"] in purge._recovery_command(report)

    # 前后对比读数：receipts 6 -> 3，max(id) 不变（候选都不是最大 id）。
    assert report["before"]["receipts"] == 6 and report["after"]["receipts"] == 3
    assert report["before"]["max_id"] == report["after"]["max_id"] == 203


# -------------------------------------------------------------
# 引用清理：保留行、只摘除被删 id
# -------------------------------------------------------------
def test_reference_scrub_keeps_row_strips_ids(_fresh_db, tmp_path):
    purge.run(_fresh_db, apply=True, backup_to=str(tmp_path))
    con = sqlite3.connect(_fresh_db)
    try:
        row = con.execute("SELECT source_ref FROM vendor_memory WHERE memory_id='m1'").fetchone()
    finally:
        con.close()
    assert row is not None                              # 记忆行保留
    refs = json.loads(row[0])
    assert "101" not in refs and "999" in refs          # 只摘被删 id，其它引用原样


# -------------------------------------------------------------
# 幂等：重跑 0 改动
# -------------------------------------------------------------
def test_idempotent_rerun_zero_changes(_fresh_db, tmp_path):
    report1 = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path))
    ids = report1["candidate_ids"]
    assert report1["total_changes"] > 0

    # 同一批 id 再执行一次：DELETE 命中 0 行，changes 为 0，且不报错。
    con = purge._connect(_fresh_db)
    try:
        children = purge.child_tables(con)
    finally:
        con.close()
    total2, _batches = purge.execute_batches(_fresh_db, ids, children)
    assert total2 == 0

    # 再跑一次完整流程：已无候选，零改动。
    report2 = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path))
    assert report2["candidate_count"] == 0
    assert report2["total_changes"] == 0


# -------------------------------------------------------------
# 非候选指纹：前后一致 + 判别力自校准
# -------------------------------------------------------------
def test_non_candidate_fingerprint_and_self_calibration(_fresh_db, tmp_path):
    con = purge._connect(_fresh_db)
    try:
        fp_before = purge.non_candidate_fingerprint(con, [101, 102, 103])
    finally:
        con.close()
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path))
    fp_after = report["fingerprint_after"]
    assert fp_before["md5"] == fp_after["md5"]
    assert fp_before["ids"] == fp_after["ids"] == [201, 202, 203]

    # 自校准：人为改一条**非候选**行，指纹必须跟着变。
    # （若这条断言不能变，就说明指纹函数是恒真的，等于没在守。）
    con = sqlite3.connect(_fresh_db)
    try:
        con.execute("UPDATE receipts SET status='flagged' WHERE id=202")
        con.commit()
    finally:
        con.close()
    con = purge._connect(_fresh_db)
    try:
        fp_tampered = purge.non_candidate_fingerprint(con, [101, 102, 103])
    finally:
        con.close()
    assert fp_tampered["md5"] != fp_after["md5"]


# -------------------------------------------------------------
# live 闸门：--apply 打 live 需显式 --allow-live
# -------------------------------------------------------------
def test_apply_on_live_requires_allow_live(_fresh_db, monkeypatch):
    monkeypatch.setattr(purge, "LIVE_DB", os.path.abspath(_fresh_db))
    with pytest.raises(SystemExit):
        purge.run(_fresh_db, apply=True, allow_live=False)
    # 未放行时不得产生任何写入（连备份都不该有）。
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts") == 6


# -------------------------------------------------------------
# 「已读清单」：常量、切分与覆盖（P2seed3 mustFix 2）
# -------------------------------------------------------------
def test_builtin_plan_set_is_27_and_excludes_out_of_plan():
    """内置清单 = 计划 §11.0 里能被判据命中的 27 张；清单外 4 张不得在内。

    why 断言条数：这份常量就是「用户已读并拍板的那一版清单」，被误编辑会让
    「实际删除集合 == 已读清单」的核对失去基准（§11.0 的拆分 = 3 + 16 + 8 = 27）。
    """
    assert len(purge.PLAN_SET_IDS) == 27
    for rid in (9, 10, 68, 296):        # 实测的 4 张计划外候选
        assert rid not in purge.PLAN_SET_IDS


def test_resolve_and_partition_plan_set():
    """resolve_plan_set 支持显式覆盖；partition 按 id 切成计划内/计划外两组。"""
    assert purge.resolve_plan_set() == purge.PLAN_SET_IDS
    plan = purge.resolve_plan_set([5, 6])
    assert plan == frozenset({5, 6})

    cands = [{"id": 5}, {"id": 9}, {"id": 6}]
    plan_in, plan_out = purge.partition_candidates(cands, plan)
    assert [c["id"] for c in plan_in] == [5, 6]      # 保持原顺序
    assert [c["id"] for c in plan_out] == [9]


# -------------------------------------------------------------
# (a) --only-plan-set 是真过滤：计划内被删、计划外仍在（2026-09-23 修正的核心）
# -------------------------------------------------------------
def test_only_plan_set_allows_when_all_candidates_in_plan(_fresh_db, tmp_path):
    report = purge.run(_fresh_db, apply=False, only_plan_set=True,
                       plan_ids=[101, 102, 103])
    assert report["only_plan_set"] is True
    assert report["plan_in_count"] == 3
    assert report["plan_out_count"] == 0

    # 落笔路径同样放行（清单覆盖全部候选时不该被过滤掉任何候选）。
    report2 = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path),
                        only_plan_set=True, plan_ids=[101, 102, 103])
    assert report2["total_changes"] > 0
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [201, 202, 203]


def test_only_plan_set_filters_and_keeps_out_of_plan_rows(_fresh_db, tmp_path, capsys):
    """(a) 核心修正：`--only-plan-set` 只删计划内，**计划外候选原样留在库里**。

    修正前它是**闸门**（候选里一出现计划外就整批报错退出、什么都删不掉），
    于是「清单内外都有候选」这种最常见场景**只能拒绝执行** —— 做不到「精确删除已批准
    清单」。修正后删除集 = 计划内，「谁没被删」由报告逐条点名（不静默留存）。
    """
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path),
                       only_plan_set=True, plan_ids=[101, 102])
    assert report["selection"] == "candidates"
    assert report["delete_ids"] == [101, 102]                 # 只删计划内
    assert report["plan_out_count"] == 1
    assert [c["id"] for c in report["out_of_plan_skipped"]] == [103]
    assert report["fingerprint_ok"] is True

    # 计划外的 103 必须原样留下（主表 + 明细都没动）。
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [103, 201, 202, 203]
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipt_items WHERE receipt_id=103") == 1
    # 计划内的被删净（含级联明细）。
    assert _scalar(_fresh_db,
                   "SELECT COUNT(*) FROM receipt_items WHERE receipt_id IN (101,102)") == 0

    purge.print_report(report)
    out = capsys.readouterr().out
    assert "计划外未删" in out and "103" in out               # 未删的必须被点名
    assert "本次删除集合" in out and "2 张（101 102）" in out


# -------------------------------------------------------------
# (b) --ids 点名的 id 必须存在：不存在即报错且零写入
# -------------------------------------------------------------
def test_ids_rejects_missing_id_with_zero_write(_fresh_db, tmp_path):
    """(b) `--ids` 要求「点名的每一行都存在」；有一个不存在就整体报错、零写入。

    why 不放行「静默跳过」：显式点名一个 id 却什么都不删，会让执行者以为删过了 ——
    这比报错危险得多。全或无（同一批里已存在的那几个也不能被删）也是刻意的。
    """
    before = _md5(_fresh_db)
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101, 999])
    msg = str(ex.value)
    assert "不存在" in msg and "999" in msg
    assert _md5(_fresh_db) == before                          # 源库逐字节未变
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts") == 6
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id=101") == 1
    # 连备份都没产（校验在「写入之前、备份之前」）。
    assert not any(".bak_" in f for f in os.listdir(str(tmp_path)))


def test_ids_deletes_non_candidate_row_with_override(_fresh_db, tmp_path):
    """`--ids` 的真正用途：精确清退**判据结构上抓不到**的行（改名后失去标记的夹具）。

    这正是 2026-09-23 执行时暴露的缺口 —— 那批行永不成候选，旧工具**完全无法删**。
    非候选行缺省不安全（无标记），故必须显式越权，且报告逐行留痕。
    """
    con = sqlite3.connect(_fresh_db)
    try:
        con.execute("INSERT INTO receipts (id, supplier_name, status, image_path, tenant_id,"
                    " created_at, updated_at)"
                    " VALUES (300,'九龍豆品廠','approved','','default','','')")
        con.commit()
    finally:
        con.close()

    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[300],
                       allow_ids_unsafe=True)
    assert report["selection"] == "explicit_ids"
    assert report["delete_ids"] == [300]
    assert [r["id"] for r in report["ids_unsafe_rows"]] == [300]   # 越权留痕
    assert report["total_changes"] > 0
    assert report["fingerprint_ok"] is True
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id=300") == 0
    # 非候选、非点名的行一律不受影响。
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [101, 102, 103, 201, 202, 203]


def test_ids_deletes_candidate_rows_without_override(_fresh_db, tmp_path):
    """`--ids` 点名**过安全双条件**的行时，无需越权开关也能删。"""
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101, 102])
    assert report["delete_ids"] == [101, 102]
    assert report["ids_unsafe_rows"] == []
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [103, 201, 202, 203]


# -------------------------------------------------------------
# (c) 幂等：对已删 id 重复 --apply -> 0 改动、不报错、不再产备份
# -------------------------------------------------------------
def test_idempotent_apply_on_already_deleted_ids(_fresh_db, tmp_path, capsys):
    """(c) 幂等：清单里的 id 已不存在时重跑必须安静空转（`total_changes=0`、不报错）。

    与 (b) 的分工（两条要求看似冲突，实为两条入口的不同纪律）：
    - **候选/清单路径**（无 `--ids`）对「已不存在的 id」是**幂等**的 —— 清退完成后
      清单里的 id 本就该全部消失，安静空转才是正确行为；
    - **显式 `--ids` 路径**要求「点名的每一行都存在」并报错 —— 显式点名一个不存在的
      id 属输入错误，静默空转等于谎报成功。
    """
    purge.run(_fresh_db, apply=True, backup_to=str(tmp_path),
              only_plan_set=True, plan_ids=[101, 102, 103])
    assert _ids(_fresh_db, "SELECT id FROM receipts ORDER BY id") == [201, 202, 203]

    backups_before = [f for f in os.listdir(str(tmp_path)) if ".bak_" in f]
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path),
                       only_plan_set=True, plan_ids=[101, 102, 103])
    assert report["candidate_count"] == 0
    assert report["delete_count"] == 0
    assert report["total_changes"] == 0
    assert report["plan_set_missing_count"] == 3              # 已读清单里 3 张全不存在
    # 没有破坏动作就不产备份（避免每次空跑都堆备份文件）。
    assert [f for f in os.listdir(str(tmp_path)) if ".bak_" in f] == backups_before

    purge.print_report(report)
    out = capsys.readouterr().out
    assert "已不存在" in out                                  # 必须明说「这些 id 已不存在」
    assert "本次删除集合为空" in out


# -------------------------------------------------------------
# (e) --ids 缺省必须过安全双条件；危险开关需显式给出，且三处文案要点名两道闸
# -------------------------------------------------------------
def test_safety_default_unchanged_without_switch(_fresh_db, tmp_path):
    """④ 钉住安全性缺省：不加危险开关时，未过条件的行必须被拒、源库零写入。

    why 逐字断言：本波改了开关**名字**与文案，安全性缺省**不得**随之改变。这里钉住
    「拒绝执行 --ids」的首行口径、逐行原因与结论句的未变部分 —— 一旦有人为了好看
    顺手放宽缺省，这条会红。
    """
    before = _md5(_fresh_db)
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[201, 202])
    msg = str(ex.value)
    # ① 未过安全双条件的行：两类原因都要逐行点名（201 有原图 / 202 无标记）。
    assert msg.startswith("拒绝执行 --ids：以下 2 行未过安全双条件")
    assert "-> 未过：有原图" in msg                       # 201
    assert "-> 未过：名字未命中夹具标记" in msg            # 202
    # ② 缺省语义未变（改动前的原文，逐字保留）。
    assert "缺省不得删未过条件的行（防误杀真实单据）。确需清退请显式加" in msg
    # ③ 新文案必须点名「危险开关」与它关掉的两道闸。
    assert "危险开关" in msg and "--allow-ids-unsafe" in msg
    assert "① 名字未命中夹具标记" in msg and "② 有原图" in msg
    # 零写入：主表未变、连备份都没产。
    assert _md5(_fresh_db) == before
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts") == 6
    assert not any(".bak_" in f for f in os.listdir(str(tmp_path)))


def test_ids_requires_safe_dual_condition_by_default(_fresh_db, tmp_path):
    """(e) `--ids` 里出现无标记且无原图的行 -> **缺省拒绝**（零写入）；危险开关才放行。"""
    before = _md5(_fresh_db)
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[202])
    msg = str(ex.value)
    assert "未过安全双条件" in msg and "202" in msg and "名字未命中夹具标记" in msg
    assert _md5(_fresh_db) == before
    assert not any(".bak_" in f for f in os.listdir(str(tmp_path)))

    # 显式危险开关下才允许删，且报告留痕。
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[202],
                       allow_ids_unsafe=True)
    assert report["delete_count"] == 1
    assert [r["id"] for r in report["ids_unsafe_rows"]] == [202]
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id=202") == 0


# -------------------------------------------------------------
# (b) 危险开关：放行 + 报告必须能读出「已关闭哪两道闸」
# -------------------------------------------------------------
def test_allow_ids_unsafe_runs_and_report_names_both_gates(_fresh_db, tmp_path, capsys):
    """(b) 加 `--allow-ids-unsafe` -> 允许；报告必须逐条点名它关掉了哪两道闸。

    两道闸各用一条实物证明：201（夹具名 **但有原图**）与 202（**无标记**且无原图）——
    它们缺的闸不同，报告里的「未过」原因也必须不同（不是一句笼统的「不安全」）。
    """
    # 闸②（有原图）：缺省拒绝。
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[201])
    assert "有原图" in str(ex.value)

    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[201, 202],
                       allow_ids_unsafe=True)
    assert report["allow_ids_unsafe"] is True
    assert report["ids_unsafe_count"] == 2
    reasons = {r["id"]: r["unsafe_reason"] for r in report["ids_unsafe_rows"]}
    assert reasons[201] == "有原图"                        # 闸②
    assert reasons[202] == "名字未命中夹具标记"             # 闸①
    assert report["fingerprint_ok"] is True
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id IN (201,202)") == 0

    purge.print_report(report)
    out = capsys.readouterr().out
    assert "--allow-ids-unsafe 已生效" in out
    assert "同时关闭两道闸" in out
    assert "① 名字未命中夹具标记" in out
    assert "② 有原图" in out
    assert "不是「只放宽清单范围」" in out                  # 命名相符性的关键一句
    assert "本次实际放行 2 行" in out


def test_cli_help_names_both_gates(capsys):
    """help 文案必须自述危险性与两道闸（三处口径的第一处）。"""
    with pytest.raises(SystemExit) as ex:
        purge.main(["--help"])
    assert ex.value.code == 0
    out = capsys.readouterr().out
    assert "--allow-ids-unsafe" in out
    assert "同时关闭两道闸" in out
    assert "名字未命中夹具标记" in out and "有原图" in out


def test_cli_allow_ids_unsafe_wired(_fresh_db, capsys):
    """CLI 接线：新开关必须真的被 main 消费（防「定义了没接线」）。"""
    rc = purge.main(["--dry-run", "--ids", "202", "--allow-ids-unsafe"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--allow-ids-unsafe 已生效" in out
    assert "本次实际放行 1 行" in out
    assert "显式 --ids" in out


def test_legacy_flag_name_is_gone(_fresh_db):
    """旧名 `--allow-ids-outside-plan` 必须**彻底删掉**（不留别名）。

    why：若旧名还能用，就等于「危险开关」有两个名字共存，认知负担与误读风险原样保留 ——
    这正是 P23 mustFix 3 要修的东西。这里钉住「旧名已不可用」（argparse 未知参数，退出码 2）。
    """
    with pytest.raises(SystemExit) as ex:
        purge.main(["--dry-run", "--ids", "202", "--allow-ids-outside-plan"])
    assert ex.value.code == 2        # argparse 的「未知参数」退出码，不是业务 SystemExit


# -------------------------------------------------------------
# 不带开关时：预演逐条标注计划内/计划外 + 分段汇总 + 醒目提示
# -------------------------------------------------------------
def test_dry_run_labels_plan_in_out_and_warns(_fresh_db, capsys):
    report = purge.run(_fresh_db, apply=False, plan_ids=[101])   # 1 内 / 2 外
    purge.print_report(report)
    out = capsys.readouterr().out
    assert "计划内" in out and "计划外" in out
    assert "分段汇总" in out
    assert "计划内 1 张 / 计划外 2 张" in out          # 分段汇总（两段）
    assert "计划外 2 张:" in out                      # 计划外逐条 id 明细
    assert "本次实际删除集合 != 已读清单" in out
    assert "清单外还有 2 张" in out


def test_default_dry_run_writes_nothing(_fresh_db, capsys):
    """缺省预演（不带 --only-plan-set）仍零写入，且给出计划外提示与清单存在性校验。"""
    before = _md5(_fresh_db)
    report = purge.run(_fresh_db, apply=False)
    assert report["mode"] == "dry_run"
    # 内置清单是 live 的 27 张，本夹具的 101/102/103 全在清单外。
    assert report["plan_in_count"] == 0 and report["plan_out_count"] == 3
    # 已知事实：那 27 个 id 在 live 上已随「用户已批准的清退」几乎全消失 -> 明说「已不存在」。
    # 本夹具恰好也造了 id=203（内置清单成员），故这里是「26 张已不存在 + 1 张仍在库」的
    # 混合口径 —— 顺带证明存在性校验是**逐条**算的，不是拿整个清单一刀切。
    assert report["plan_set_missing_count"] == 26
    assert report["plan_set_existing_ids"] == [203]
    purge.print_report(report)
    out = capsys.readouterr().out
    assert "清单外还有 3 张" in out
    assert "26 张已不存在" in out and "其余 1 张仍在库" in out
    assert _md5(_fresh_db) == before


# -------------------------------------------------------------
# (f) `--ids` 三类口径可分辨（P23 mustFix 4）
# -------------------------------------------------------------
def test_classify_missing_ids_two_buckets(_fresh_db):
    """查不到的 id 必须能分成「已被删除」与「从不存在」两类（两类的处置相反）。

    判据（库内可复核的结构性证据）：编号落在已分配区间（<= MAX(id)）或落在已读清单里
    -> 已删除；编号超出已分配区间 -> 从不存在（笔误，必须拦住）。
    """
    con = purge._connect(_fresh_db)
    try:
        # 50 <= MAX(id)=203 但库里没有 -> 「已被删除」（曾写入后被删）。
        # 999 > 203 -> 「从不存在」（编号尚未被分配）。
        deleted, never = purge.classify_missing_ids(con, [50, 999], plan_set={101})
        assert deleted == [50] and never == [999]
        # 清单信号独立成立：超出已分配区间但属已读清单 -> 仍判「已被删除」
        # （已读清单是用户批准过的清退集合，证据强于编号区间）。
        deleted, never = purge.classify_missing_ids(con, [500], plan_set={500})
        assert deleted == [500] and never == []
    finally:
        con.close()


def test_ids_missing_message_distinguishes_never_existed(_fresh_db, tmp_path):
    """② 「从不存在」的文案必须和「已被删除」可分辨，且仍报错、零写入。"""
    before = _md5(_fresh_db)
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[999])
    msg = str(ex.value)
    assert "从不存在" in msg and "999" in msg and "编号超出当前已分配区间" in msg
    assert _md5(_fresh_db) == before
    assert not any(".bak_" in f for f in os.listdir(str(tmp_path)))


def test_ids_repeated_apply_on_deleted_ids_is_idempotent(_fresh_db, tmp_path, capsys):
    """(c) 对已删 id 重复 `--apply` -> `total_changes=0`、不报错、归入「已被删除」类别。

    与 (b) 的分工（看似冲突、实为两类成因）：**从不存在**（笔误）必须报错拦住；
    **已被删除**（已批准清退的幂等重跑）必须安静空转并把读数交回人 ——
    否则「任务早做完了」会被当成「输入错了」，`--ids` 也就无法安全重跑。
    """
    purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101, 102])
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id IN (101,102)") == 0

    backups_before = [f for f in os.listdir(str(tmp_path)) if ".bak_" in f]
    report = purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101, 102])
    assert report["total_changes"] == 0
    assert report["delete_count"] == 0
    assert report["ids_already_deleted"] == [101, 102]
    assert report["ids_already_deleted_count"] == 2
    assert report["ids_never_existed"] == []              # 未被误报成「从不存在」
    assert report["ids_unsafe_count"] == 0
    # 没有破坏动作就不产备份（与候选路径同款纪律）。
    assert [f for f in os.listdir(str(tmp_path)) if ".bak_" in f] == backups_before

    purge.print_report(report)
    out = capsys.readouterr().out
    assert "已被删除（幂等重跑）2 行" in out               # 三类计数：分别可见
    assert "从不存在 0 行" in out
    assert "已批准清退的重跑" in out


def test_ids_mixed_existing_unsafe_and_deleted(_fresh_db, tmp_path):
    """混合输入：已删 id 走幂等口径，但**未过安全双条件**的行仍必须报错、零写入。"""
    purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101])
    before = _md5(_fresh_db)
    with pytest.raises(SystemExit) as ex:
        purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[101, 202])
    assert "未过安全双条件" in str(ex.value) and "202" in str(ex.value)
    assert _md5(_fresh_db) == before                      # 全或无：101 也不能被删
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts WHERE id=202") == 1


# -------------------------------------------------------------
# (d) 防误杀对照仍然有效
# -------------------------------------------------------------
def test_anti_kill_controls_still_hold(_fresh_db, tmp_path):
    # 夹具名 + 有原图 -> 排除。
    ok, reason, _hits = purge.is_candidate("测试供应商", "/uploads/x.jpg")
    assert ok is False and reason == "有原图"
    # 真实供应商 + 无原图 -> 排除。
    ok, reason, _hits = purge.is_candidate("德利行 Tak Lee Hong", "")
    assert ok is False and reason == "名字未命中夹具标记"
    # 两条对照都不该出现在候选里。
    con = purge._connect(_fresh_db)
    try:
        ids = [c["id"] for c in purge.find_candidates(con)]
    finally:
        con.close()
    assert 201 not in ids and 202 not in ids and 203 not in ids

    # 对照对 `--ids` 的**缺省**安全双条件同样成立：201（夹具名但有原图）与
    # 202（真实供应商无原图）缺省都被拒删、源库零写入 —— 显式入口不得成为绕过防误杀的后门。
    for bad in (201, 202):
        before = _md5(_fresh_db)
        with pytest.raises(SystemExit) as ex:
            purge.run(_fresh_db, apply=True, backup_to=str(tmp_path), ids=[bad])
        assert "未过安全双条件" in str(ex.value)
        assert _md5(_fresh_db) == before
    assert _scalar(_fresh_db, "SELECT COUNT(*) FROM receipts") == 6


# -------------------------------------------------------------
# 需单独拍板的供应商：单独列出、不进候选
# -------------------------------------------------------------
def test_pending_decision_vendor_listed_separately(_fresh_db, capsys):
    """`九龍豆品廠`（标记已被命名归一剥掉）只进「需单独拍板」段，不进候选。"""
    con = sqlite3.connect(_fresh_db)
    try:
        con.execute(
            "INSERT INTO receipts (id, supplier_name, status, image_path, tenant_id,"
            " created_at, updated_at) VALUES (300,'九龍豆品廠','approved','','default','','')")
        con.commit()
    finally:
        con.close()

    report = purge.run(_fresh_db, apply=False, plan_ids=[101, 102, 103])
    assert 300 not in report["candidate_ids"]                 # 不进候选
    assert [p["id"] for p in report["pending_decision"]] == [300]
    assert report["pending_decision"][0]["supplier_name"] == "九龍豆品廠"

    purge.print_report(report)
    out = capsys.readouterr().out
    assert "需单独拍板" in out and "九龍豆品廠" in out


# -------------------------------------------------------------
# CLI 接线：新增开关必须真的被 main 消费（防「定义了没接线」）
# -------------------------------------------------------------
def test_cli_only_plan_set_wired(_fresh_db, capsys):
    rc = purge.main(["--dry-run", "--only-plan-set", "--plan-ids", "101,102,103"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "计划外 0 张" in out                       # 开关确实生效并被报告


def test_cli_only_plan_set_filters_instead_of_refusing(_fresh_db, capsys):
    """CLI 接线（新语义）：缺省清单不含 101/102/103 -> 过滤后删除集为空、rc=0、点名未删项。

    旧语义下这里会 `SystemExit`；本用例钉住「改成真过滤」这件事确实落到了 CLI 上。
    """
    rc = purge.main(["--dry-run", "--only-plan-set"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "计划外未删" in out
    assert "本次删除集合" in out and "0 张" in out


def test_cli_ids_wired(_fresh_db, capsys):
    rc = purge.main(["--dry-run", "--ids", "101,102"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "显式 --ids" in out
    assert "本次删除集合" in out and "2 张（101 102）" in out


def test_cli_ids_and_only_plan_set_mutually_exclusive(_fresh_db):
    with pytest.raises(SystemExit) as ex:
        purge.main(["--dry-run", "--ids", "101", "--only-plan-set"])
    assert "互斥" in str(ex.value)


def test_cli_allow_ids_unsafe_requires_ids(_fresh_db):
    with pytest.raises(SystemExit) as ex:
        purge.main(["--dry-run", "--allow-ids-unsafe"])
    assert "只与 --ids 搭配" in str(ex.value)


def test_cli_ids_rejects_non_integer(_fresh_db):
    with pytest.raises(SystemExit) as ex:
        purge.main(["--dry-run", "--ids", "101,abc"])
    assert "只接受整数" in str(ex.value)


def test_cli_plan_ids_rejects_non_integer(_fresh_db):
    with pytest.raises(SystemExit) as ex:
        purge.main(["--dry-run", "--plan-ids", "101,abc"])
    assert "只接受整数" in str(ex.value)
