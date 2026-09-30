# -*- coding: utf-8 -*-
"""P2-T2：供应商×SKU 别名表（建表 / 读取钩子 / 入账登记 / 合并联动）测试。

背景（why 需要这一批）：SKU 匹配此前完全按商品名做全局「精确 → 核心词双向包含」，
不看供应商。港式手写单里同一写法（缩写、行话）在不同供应商下含义不同，于是
1. 漏匹配：写法与标准名不是子串关系时匹配不上（live 实测匹配率仅 23.7%）；
2. 错匹配：互相包含的不同商品会互相放行；
3. SKU 爆炸：同一供应商的同类写法每出现一次就建一个新 SKU。
本批新增 `vendor_sku_alias(tenant_id, supplier_id, sku_id, raw_name, canonical_name,
unit, hit_count, valid_from, valid_to, status, source_ref)` 把「该供应商用这个写法指
这个 SKU」沉淀成结构化事实，接三个钩子：读取（`_match_sku` 供应商优先）/ 写入
（approve 入账登记）/ 合并联动（merge_skus 与 deduplicate_skus_by_canonical 改指）。

覆盖：
1. 建表幂等 + 唯一索引（`(tenant_id, supplier_id, raw_name)`）—— 唯一性不是优化，
   是"同一写法解析到哪个 SKU"确定性的语义要件；
2. **判别式**：同一缩写在不同供应商下解析到不同 SKU（关掉供应商优先分支即失败，
   见任务报告的反证）；
3. **判别式**：第二次出现命中第一次登记的别名，并累加 hit_count；
4. 租户收窄：同写法跨租户不互相命中，找不到就是 None（不返回全量）；
5. 别名生命周期：过期（valid_to）与 retired 状态不参与命中；
6. `upsert_vendor_sku_alias` 幂等 / 改指 / 四类失败原因；
7. 入账钩子：approve（`apply_receipt_to_inventory`）登记别名，字段与来源留痕正确；
8. **判别式**：`merge_skus` 改指别名（回滚改指 → 别名留在被停用的副 SKU 上，
   读取侧判为死链 → 用例失败），且副 SKU 的**全部**写法都要改指；
9. `deduplicate_skus_by_canonical`（启动自愈的第二个合并入口）同样改指；
10. `delete_sku` 物理删除时级联清别名（不留指向不存在 SKU 的死链）；
11. **判别式**：候选不可用时**不得早退**（P2a 审核 M5）—— 精确写法 retired/过期/
    指向死 SKU 时要继续走 canonical 通道找 active 候选；两条通道都不可用才回落全局
    匹配，且绝不返回指向停用 SKU 的别名；精确通道可用时优先级不变。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="vendor_sku_alias_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_alias.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services.inventory import apply_receipt_to_inventory  # noqa: E402
from app.services.receipt_utils import _match_sku, _normalize_sku_name  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与 P1/P2 系列用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"alias_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _mk_supplier(name, tenant_id="default"):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    assert sid is not None
    return int(sid)


def _mk_sku(name, base_unit="", tenant_id="default"):
    sid, err = db.create_sku(name, base_unit=base_unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err} ({name})"
    assert sid is not None
    return int(sid)


def _item(name, unit="斤", unit_price=10.0, amount=10.0, raw_name=None):
    """构造与 `save_parsed_data` 落库同形的明细行（_match_sku 只读其中几个键）。"""
    return {
        "name": name, "raw_name": raw_name or name, "quantity": 1, "unit": unit,
        "unit_price": unit_price, "amount": amount, "sku_id": None,
        "cost_center_id": None, "confidence": 0.9, "matched": 0, "price_anomaly": 0,
        "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }


def _mk_receipt(supplier_name, items, tenant_id="default", supplier_id=None,
                status="parsed"):
    rid = db.create_receipt_record(
        supplier_name=supplier_name, supplier_id=supplier_id,
        tenant_id=tenant_id, status=status)
    db.set_receipt_items(rid, items)
    return rid


def _alias_rows(tenant_id=None, supplier_id=None, raw_name=None):
    s = db.get_session()
    try:
        q = s.query(db._VendorSkuAliasRow)
        if tenant_id is not None:
            q = q.filter(db._VendorSkuAliasRow.tenant_id == tenant_id)
        if supplier_id is not None:
            q = q.filter(db._VendorSkuAliasRow.supplier_id == int(supplier_id))
        if raw_name is not None:
            q = q.filter(db._VendorSkuAliasRow.raw_name == raw_name)
        return q.all()
    finally:
        s.close()


def _one(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        row = conn.execute(stmt, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _exec(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(stmt, params)
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------
# 1. 建表
# -------------------------------------------------------------
def test_table_created_idempotently_with_unique_index():
    _fresh_db()
    cols = {r[1] for r in sqlite3.connect(db.DB_PATH).execute(
        "PRAGMA table_info(vendor_sku_alias)").fetchall()}
    assert {
        "id", "tenant_id", "supplier_id", "sku_id", "raw_name", "canonical_name",
        "unit", "hit_count", "valid_from", "valid_to", "status", "source_ref",
        "updated_at",
    } <= cols, f"别名表缺列: {cols}"
    idx = {r[1]: r[2] for r in sqlite3.connect(db.DB_PATH).execute(
        "PRAGMA index_list(vendor_sku_alias)").fetchall()}
    assert idx.get("ux_vendor_sku_alias_key") == 1, "缺唯一索引 ux_vendor_sku_alias_key"

    # 幂等：再 import 两次（等价于服务重启/多次 import）不得报错、不得重复建表
    importlib.reload(db)
    importlib.reload(db)
    cols2 = {r[1] for r in sqlite3.connect(db.DB_PATH).execute(
        "PRAGMA table_info(vendor_sku_alias)").fetchall()}
    idx2 = [r[1] for r in sqlite3.connect(db.DB_PATH).execute(
        "PRAGMA index_list(vendor_sku_alias)").fetchall()]
    assert cols2 == cols and idx2.count("ux_vendor_sku_alias_key") == 1


# -------------------------------------------------------------
# 2. 判别式：同缩写在不同供应商解析到不同 SKU
# -------------------------------------------------------------
def test_same_abbreviation_resolves_per_supplier():
    """「本吧」在 A 家指牛腩、在 B 家指番茄 —— 只有供应商维度能分辨。"""
    _fresh_db()
    sup_a = _mk_supplier("甲供應商")
    sup_b = _mk_supplier("乙供應商")
    sku_niunan = _mk_sku("本地牛腩", base_unit="斤")
    sku_fanqie = _mk_sku("本地番茄", base_unit="斤")
    # 别名登记（写入侧三钩子之外的直接登记入口，供本用例构造先验）
    assert db.upsert_vendor_sku_alias(
        sup_a, sku_niunan, "本吧", canonical_name=_normalize_sku_name("本吧"),
        unit="斤", tenant_id="default")[1] is None
    assert db.upsert_vendor_sku_alias(
        sup_b, sku_fanqie, "本吧", canonical_name=_normalize_sku_name("本吧"),
        unit="斤", tenant_id="default")[1] is None

    item_a = _item("本吧")
    _match_sku(item_a, tenant_id="default", supplier_id=sup_a)
    item_b = _item("本吧")
    _match_sku(item_b, tenant_id="default", supplier_id=sup_b)
    assert item_a["sku_id"] == sku_niunan and item_a["matched"] == 1
    assert item_b["sku_id"] == sku_fanqie and item_b["matched"] == 1

    # 前提断言：没有供应商维度时，「本吧」既不等于也不包含任一 SKU 名
    # （否则用例会退化成恒真 —— 关掉供应商优先分支也照样通过）
    item_none = _item("本吧")
    _match_sku(item_none, tenant_id="default", supplier_id=None)
    assert item_none["sku_id"] is None and item_none["matched"] == 0, \
        "全局口径本不该匹配上「本吧」，否则本用例不是判别式"


# -------------------------------------------------------------
# 3. 判别式：二次出现命中登记过的别名 + hit_count
# -------------------------------------------------------------
def test_second_occurrence_hits_registered_alias_and_counts_hit():
    _fresh_db()
    sup = _mk_supplier("丙供應商")
    sku = _mk_sku("本地牛腩", base_unit="斤")
    alias_id, err = db.upsert_vendor_sku_alias(
        sup, sku, "本吧", canonical_name=_normalize_sku_name("本吧"),
        unit="斤", tenant_id="default", source_ref="test")
    assert err is None and alias_id
    assert _alias_rows("default", sup, "本吧")[0].hit_count == 0

    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] == sku and item["matched"] == 1
    assert _alias_rows("default", sup, "本吧")[0].hit_count == 1, "命中必须累加 hit_count"

    # 再出现一次：仍然命中，计数继续累加
    item2 = _item("本吧")
    _match_sku(item2, tenant_id="default", supplier_id=sup)
    assert item2["sku_id"] == sku
    assert _alias_rows("default", sup, "本吧")[0].hit_count == 2

    # 归一通道：核心词相同的另一种写法（包装前缀差异）也要命中
    item3 = _item("10包裝本吧")
    _match_sku(item3, tenant_id="default", supplier_id=sup)
    assert item3["sku_id"] == sku, "canonical 归一通道应覆盖包装前缀差异"


# -------------------------------------------------------------
# 4. 租户收窄
# -------------------------------------------------------------
def test_alias_scoped_by_tenant():
    _fresh_db()
    sup_t1 = _mk_supplier("丁供應商", tenant_id="t1")
    sup_t2 = _mk_supplier("丁供應商二號", tenant_id="t2")
    sku_t1 = _mk_sku("本地牛腩", tenant_id="t1")
    sku_t2 = _mk_sku("頭抽醬油", tenant_id="t2")
    db.upsert_vendor_sku_alias(sup_t1, sku_t1, "本吧", tenant_id="t1")
    db.upsert_vendor_sku_alias(sup_t2, sku_t2, "本吧", tenant_id="t2")

    item_t1 = _item("本吧")
    _match_sku(item_t1, tenant_id="t1", supplier_id=sup_t1)
    item_t2 = _item("本吧")
    _match_sku(item_t2, tenant_id="t2", supplier_id=sup_t2)
    assert item_t1["sku_id"] == sku_t1
    assert item_t2["sku_id"] == sku_t2

    # 跨租户：用 t2 的供应商 id 在 t1 视角查询 → None（不是全量、也不是别人的 SKU）
    assert db.find_vendor_sku_alias(
        "本吧", tenant_id="t1", supplier_id=sup_t2) is None


# -------------------------------------------------------------
# 5. 生命周期：过期 / retired 不命中
# -------------------------------------------------------------
def test_expired_and_retired_alias_not_hit():
    _fresh_db()
    sup = _mk_supplier("戊供應商")
    sku = _mk_sku("本地牛腩")
    db.upsert_vendor_sku_alias(sup, sku, "本吧", tenant_id="default")

    # 过期：valid_to 已是过去时刻 → 不命中（回落全局匹配，本例全局也匹配不上）
    _exec("UPDATE vendor_sku_alias SET valid_to = '2000-01-01T00:00:00' "
          "WHERE raw_name = '本吧'")
    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] is None, "过期别名不得参与匹配"

    # retired 状态同样不命中
    _exec("UPDATE vendor_sku_alias SET valid_to = '', status = 'retired' "
          "WHERE raw_name = '本吧'")
    item2 = _item("本吧")
    _match_sku(item2, tenant_id="default", supplier_id=sup)
    assert item2["sku_id"] is None, "retired 别名不得参与匹配"

    # 复原后重新命中（证明前面两条失败是闸门造成的，不是别名通道整体失灵）
    _exec("UPDATE vendor_sku_alias SET status = 'active' WHERE raw_name = '本吧'")
    item3 = _item("本吧")
    _match_sku(item3, tenant_id="default", supplier_id=sup)
    assert item3["sku_id"] == sku


def test_canonical_channel_prefers_most_used_alias():
    """归一通道有多条候选时按 hit_count 取用得最多的一条（hit_count 的消费方）。"""
    _fresh_db()
    sup = _mk_supplier("丑供應商")
    sku_a = _mk_sku("本地牛腩")
    sku_b = _mk_sku("本地番茄")
    id_a, _ = db.upsert_vendor_sku_alias(sup, sku_a, "本吧甲", canonical_name="本吧")
    id_b, _ = db.upsert_vendor_sku_alias(sup, sku_b, "本吧乙", canonical_name="本吧")
    assert id_a and id_b

    # 两条写法的 canonical 都是「本吧」，都还没被用过 → 取 id 较小者（确定性）
    first = db.find_vendor_sku_alias("本吧 一斤", tenant_id="default", supplier_id=sup,
                                     canonical_name="本吧")
    assert first is not None and first.id == sku_a

    # 把乙的 hit_count 抬到 5 → 归一通道改取乙
    for _ in range(5):
        assert db.find_vendor_sku_alias("本吧乙", tenant_id="default",
                                        supplier_id=sup).id == sku_b
    again = db.find_vendor_sku_alias("本吧 一斤", tenant_id="default", supplier_id=sup,
                                     canonical_name="本吧")
    assert again is not None and again.id == sku_b, "归一通道应取命中次数最多的先验"


# -------------------------------------------------------------
# 5b. 判别式：候选不可用时不得早退（P2a 审核 M5）
# -------------------------------------------------------------
def test_retired_exact_alias_falls_through_to_active_canonical_candidate():
    """精确写法被置 retired，但同 canonical 另一条 active → 必须命中那条 active。

    回归守卫：早退实现（精确行命中后 status != active 就 return None）会让这个
    本该命中的写法回落到全局匹配 —— 别名通道的能力被一条停用行整条堵死。
    """
    _fresh_db()
    sup = _mk_supplier("卯供應商")
    sku_old = _mk_sku("本地牛腩甲")
    sku_new = _mk_sku("本地牛腩")
    # 该供应商的两条写法共享核心词「本地牛腩」，其中精确那条已被人工置停
    aid_old, err_old = db.upsert_vendor_sku_alias(
        sup, sku_old, "本吧", canonical_name="本地牛腩")
    aid_new, err_new = db.upsert_vendor_sku_alias(
        sup, sku_new, "牛腩", canonical_name="本地牛腩")
    assert err_old is None and err_new is None and aid_old != aid_new
    _exec("UPDATE vendor_sku_alias SET status = 'retired' WHERE raw_name = '本吧'")

    hit = db.find_vendor_sku_alias("本吧", tenant_id="default", supplier_id=sup,
                                   canonical_name="本地牛腩")
    assert hit is not None, "精确写法 retired 后应继续走归一通道，而不是直接放弃"
    assert hit.id == sku_new
    assert hit.id != sku_old, "不得错返 retired 那条指向的 SKU"

    # hit_count 只累加真正命中的那条（被跳过的候选不得被计数，否则度量失真）
    rows = {r.raw_name: r for r in _alias_rows("default", sup)}
    assert rows["本吧"].hit_count == 0
    assert rows["牛腩"].hit_count == 1


def test_retired_exact_alias_with_same_canonical_still_resolves():
    """`_match_sku` 端到端：写法本身就是最短核心词（canonical == raw）时，
    精确行 retired 仍要命中同 canonical 的 active 候选。

    why 单列这条：canonical 与 raw 相等是常见形态（干净写法归一后不变），若沿用
    「canon == raw 就不查归一通道」的短路，召回仍会丢一半。
    """
    _fresh_db()
    sup = _mk_supplier("辰供應商")
    sku_old = _mk_sku("本地牛腩")
    sku_new = _mk_sku("本地牛腩條")
    db.upsert_vendor_sku_alias(sup, sku_old, "本吧", canonical_name="本吧")
    db.upsert_vendor_sku_alias(sup, sku_new, "本吧一", canonical_name="本吧")
    _exec("UPDATE vendor_sku_alias SET status = 'retired' WHERE raw_name = '本吧'")

    # 前提断言：全局口径（无供应商维度）匹配不上「本吧」，否则本用例不是判别式
    probe = _item("本吧")
    _match_sku(probe, tenant_id="default", supplier_id=None)
    assert probe["sku_id"] is None, "全局口径本不该匹配上「本吧」，否则本用例不是判别式"

    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] == sku_new and item["matched"] == 1


def test_all_candidates_unusable_falls_back_and_never_returns_dead_sku():
    """两条通道的候选全部 retired / 过期 / 指向停用 SKU → 返回 None 回落全局匹配，
    且**绝不返回指向停用 SKU 的别名**。

    「不早退」不得变成「把死链也当命中返回」：安全性与召回要同时满足。
    """
    _fresh_db()
    sup = _mk_supplier("巳供應商")
    sku_alive = _mk_sku("本地牛腩")
    sku_dead = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, sku_dead, "本吧", canonical_name="本地牛腩")
    db.upsert_vendor_sku_alias(sup, sku_alive, "牛腩", canonical_name="本地牛腩")
    # 副 SKU 被合并停用（等价于 merge 后别名没改指：别名指向 active=0 的死链）
    _exec("UPDATE skus SET active = 0 WHERE id = %d" % int(sku_dead))
    # 另一条候选已过期
    _exec("UPDATE vendor_sku_alias SET valid_to = '2000-01-01T00:00:00' "
          "WHERE raw_name = '牛腩'")

    hit = db.find_vendor_sku_alias("本吧", tenant_id="default", supplier_id=sup,
                                   canonical_name="本地牛腩")
    assert hit is None, "候选全部不可用时必须回落全局匹配，不得返回死链"

    # 端到端：仍回落全局匹配（本例全局也匹配不上），不得把停用 SKU 当成命中
    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] is None and item["matched"] == 0


def test_live_exact_alias_still_wins_over_canonical_candidate():
    """精确写法可用时优先级不变：即使归一候选 hit_count 更高，也不得被顶掉。"""
    _fresh_db()
    sup = _mk_supplier("午供應商")
    sku_exact = _mk_sku("本地牛腩")
    sku_canon = _mk_sku("本地牛腩條")
    db.upsert_vendor_sku_alias(sup, sku_exact, "本吧", canonical_name="本地牛腩")
    db.upsert_vendor_sku_alias(sup, sku_canon, "牛腩", canonical_name="本地牛腩")
    _exec("UPDATE vendor_sku_alias SET hit_count = 5 WHERE raw_name = '牛腩'")

    hit = db.find_vendor_sku_alias("本吧", tenant_id="default", supplier_id=sup,
                                   canonical_name="本地牛腩")
    assert hit is not None and hit.id == sku_exact, "精确通道优先级不得被归一通道越过"


# -------------------------------------------------------------
# 6. upsert 幂等 / 改指 / 失败原因
# -------------------------------------------------------------
def test_upsert_idempotent_repoint_and_failure_reasons():
    _fresh_db()
    sup = _mk_supplier("己供應商")
    sku_a = _mk_sku("本地牛腩")
    sku_b = _mk_sku("本地番茄")

    aid1, err1 = db.upsert_vendor_sku_alias(sup, sku_a, "本吧", unit="斤")
    aid2, err2 = db.upsert_vendor_sku_alias(sup, sku_a, "本吧", unit="斤")
    assert err1 is None and err2 is None and aid1 == aid2
    assert len(_alias_rows("default", sup, "本吧")) == 1, "同键二次登记不得新增行"

    # 改指（重新匹配/合并场景）：同键换 SKU 不新增行
    aid3, err3 = db.upsert_vendor_sku_alias(sup, sku_b, "本吧")
    assert err3 is None and aid3 == aid1
    rows = _alias_rows("default", sup, "本吧")
    assert len(rows) == 1 and rows[0].sku_id == sku_b

    # 失败原因（不静默）
    assert db.upsert_vendor_sku_alias(None, sku_a, "本吧")[1] == "NO_SUPPLIER"
    assert db.upsert_vendor_sku_alias(sup, 999999, "本吧")[1] == "INVALID_SKU"
    assert db.upsert_vendor_sku_alias(sup, sku_a, "   ")[1] == "INVALID_RAW_NAME"
    assert len(_alias_rows("default", sup)) == 1, "失败路径不得落任何行"


# -------------------------------------------------------------
# 7. 入账钩子：approve 登记别名
# -------------------------------------------------------------
def test_approve_registers_alias_for_auto_created_sku():
    _fresh_db()
    sup = _mk_supplier("庚供應商")
    rid = _mk_receipt("庚供應商", [_item("本地牛腩", unit="斤")], supplier_id=sup)

    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    sku = db.find_sku_by_name("本地牛腩", tenant_id="default")
    assert sku is not None, "入账应自动建 SKU"
    rows = _alias_rows("default", sup, "本地牛腩")
    assert len(rows) == 1, "approve 入账必须登记该供应商的写法"
    assert rows[0].sku_id == sku.id
    assert rows[0].unit == "斤"
    assert rows[0].source_ref == f"receipt:{rid}"
    assert (rows[0].status or "active") == "active" and rows[0].valid_to == ""

    # 二次入账幂等：不新增别名行
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")
    assert len(_alias_rows("default", sup, "本地牛腩")) == 1


def test_approve_skips_alias_when_supplier_unresolved():
    """供应商档案未锚定时跳过登记（reason=NO_SUPPLIER），且不凭空建档案。"""
    _fresh_db()
    before = int(_one("SELECT COUNT(*) FROM suppliers") or 0)
    rid = _mk_receipt("沒有檔案的商號", [_item("奇異果")])
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")
    assert int(_one("SELECT COUNT(*) FROM suppliers") or 0) == before, \
        "解析不出档案时不得凭空新建供应商"
    assert int(_one("SELECT COUNT(*) FROM vendor_sku_alias") or 0) == 0, \
        "无供应商维度时登记了也没人消费，必须跳过而不是落 NULL 行"


def test_approve_logs_warning_on_abnormal_alias_failure(monkeypatch, caplog):
    """登记异常（非"没有供应商档案"）必须留 warning —— 防"静默失效"。"""
    import logging

    _fresh_db()
    _mk_supplier("寅供應商")
    rid = _mk_receipt("寅供應商", [_item("本地牛腩")])
    monkeypatch.setattr(db, "upsert_vendor_sku_alias",
                        lambda *a, **kw: (None, "ERROR"))
    with caplog.at_level(logging.WARNING, logger="app.services.inventory"):
        apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")
    assert any("供应商别名登记异常" in r.getMessage() for r in caplog.records), \
        "写库异常必须留 warning（不能静默）"


# -------------------------------------------------------------
# 8. 判别式：merge_skus 改指
# -------------------------------------------------------------
def test_merge_skus_repoints_alias_to_primary():
    _fresh_db()
    sup = _mk_supplier("辛供應商")
    primary = _mk_sku("本地牛腩")
    secondary = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, secondary, "本吧", tenant_id="default")
    assert db.find_vendor_sku_alias(
        "本吧", tenant_id="default", supplier_id=sup).id == secondary

    result, err = db.merge_skus(primary, [secondary], tenant_id="default")
    assert err is None, err
    assert result["alias_repointed"] == 1

    rows = _alias_rows("default", sup, "本吧")
    assert len(rows) == 1 and rows[0].sku_id == primary, "合并后别名必须改指主 SKU"
    # 读取侧仍能命中（若改指被回滚，别名留在 active=0 的副 SKU 上 → 这里返回 None → 失败）
    hit = db.find_vendor_sku_alias("本吧", tenant_id="default", supplier_id=sup)
    assert hit is not None and hit.id == primary
    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] == primary


def test_merge_skus_repoints_all_aliases_of_secondary():
    """副 SKU 的**全部**写法都要改指（不止第一条），且不被租户/单位差异漏掉。"""
    _fresh_db()
    sup = _mk_supplier("壬供應商")
    primary = _mk_sku("本地牛腩")
    secondary = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, secondary, "本吧", unit="斤", tenant_id="default")
    db.upsert_vendor_sku_alias(sup, secondary, "本巴", unit="件", tenant_id="default")
    assert len(_alias_rows("default", sup)) == 2

    result, err = db.merge_skus(primary, [secondary], tenant_id="default")
    assert err is None and result["alias_repointed"] == 2
    rows = _alias_rows("default", sup)
    assert {r.sku_id for r in rows} == {primary}
    assert {r.raw_name for r in rows} == {"本吧", "本巴"}, "改指不得丢失写法"


def test_deduplicate_skus_by_canonical_repoints_alias():
    """第二个合并入口（启动自愈每次调用）同样必须改指。"""
    _fresh_db()
    sup = _mk_supplier("癸供應商")
    dirty = _mk_sku("本地菜心_1787140411")
    clean = _mk_sku("本地菜心")
    db.upsert_vendor_sku_alias(sup, dirty, "菜心", tenant_id="default")

    reports = db.deduplicate_skus_by_canonical(tenant_id="default")
    assert reports, "应触发一次按 canonical 的去重合并"
    merged = [r for r in reports if r.get("canonical") == "本地菜心"]
    assert merged and merged[0]["alias_repointed"] == 1
    rows = _alias_rows("default", sup, "菜心")
    assert len(rows) == 1 and rows[0].sku_id == merged[0]["primary_id"]
    assert db.find_vendor_sku_alias(
        "菜心", tenant_id="default", supplier_id=sup) is not None


# -------------------------------------------------------------
# 9. 删除级联
# -------------------------------------------------------------
def test_delete_sku_cascades_alias_rows():
    _fresh_db()
    sup = _mk_supplier("子供應商")
    sku = _mk_sku("本地牛腩")
    db.upsert_vendor_sku_alias(sup, sku, "本吧", tenant_id="default")

    ok, action = db.delete_sku(sku, tenant_id="default")
    assert (ok, action) == (True, "DELETED")
    assert _alias_rows("default", sup, "本吧") == [], "物理删 SKU 必须级联清别名"


if __name__ == "__main__":   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
