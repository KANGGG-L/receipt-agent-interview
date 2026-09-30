# -*- coding: utf-8 -*-
"""P2fix-F1：approve 入账路径不得推翻别名/人工确认的匹配，别名不得被翻转指向重复 SKU。

缺陷背景（why 需要这批用例）：`services/inventory.apply_receipt_to_inventory` 里曾
**内联了第二份 SKU 匹配实现**（商品名"错配校验" → `find_sku_by_name` → 核心词等值 →
`create_sku`），与解析期的 `receipt_utils._match_sku` 分叉，且**完全不看 `it["matched"]`**。
于是同一条明细在链路里被两次判定、第二次推翻第一次：

1. 解析期 `_match_sku` 经**别名通道**把「本吧」确认到 SKU「本地牛腩」（sku_id=1,
   matched=1）—— 别名通道本就允许"写法与标准名不是子串关系"，这正是 P2-T2 的动机场景；
2. approve 入账时那份内联实现的互含判据把「本吧」vs「本地牛腩」判为**错配** →
   sku_id 清成 None → 新建重复 SKU「本吧」；
3. 紧随其后的别名登记钩子用同一唯一键把别名从「本地牛腩」**翻转指向**重复 SKU；
4. 净效果：下次解析反而落到重复 SKU —— 该机制要消灭的 SKU 爆炸被它自己触发。

覆盖（每条都尽量写成"回滚修复即失败"的判别式）：
1. **判别式**：别名指向 A、品名与 A 名非子串关系、明细已带确认过的 sku_id(A) →
   approve 后 SKU 数不增、别名仍指向 A、二次解析仍命中 A（回滚成旧内联实现即失败）；
2. 别名已存在但明细 sku_id 为空时，**入账路径自己**经唯一匹配实现命中别名（不新建）；
3. **判别式**：`matched=1` 的指向不得因"商品名不互含"被清空（要求 ②）；
4. 指向失效（跨租户/不存在）时仍要重新匹配 —— 保护不得演变成"死指向也照用"；
5. **判别式**（要求 ③）：`upsert_vendor_sku_alias(protect_live_target=True)` 不把
   指向存活 SKU 的既有别名改指到本次新建的重复 SKU；旧目标已死时仍允许改指；
6. **接线守卫**（防"定义了没接线"）：approve 只有在**本次真的新建了 SKU** 时才传
   `protect_live_target=True`，且确实把匹配委托给 `receipt_utils._match_sku`；
7. **兜底判别式**：即使匹配实现（被回滚/替换成旧的）漏掉别名并新建了重复 SKU，
   别名也不得被翻转 —— 这是要求 ③ 的端到端形态。
8. **负向对照（P2fix 审核 mustFix #1）**：`matched=0` 且既有 sku_id 指向**存在但无关**
   的 SKU（核心词互不含）时，入账必须纠正/重建，且**库存流水不得记到旧 SKU**
   （原实现无条件沿用既有指向 → 库存真的记到无关 SKU 上）；反向对照是
   `matched=0` 但指向恰好正确的 SKU 时不得因此建出重复 SKU（防"一律清空"过度纠正）。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库/向量库写入。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="vendor_sku_alias_approve_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_alias_approve.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
import app.services.receipt_utils as receipt_utils  # noqa: E402
from app.services.inventory import apply_receipt_to_inventory  # noqa: E402
from app.services.receipt_utils import _match_sku, _normalize_sku_name  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与 P2 系列用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"alias_approve_{uuid.uuid4().hex[:8]}.db")
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
    return int(sid)


def _mk_sku(name, base_unit="", tenant_id="default"):
    sid, err = db.create_sku(name, base_unit=base_unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err} ({name})"
    return int(sid)


def _item(name, unit="斤", unit_price=10.0, amount=10.0, sku_id=None, matched=None):
    """构造与 `save_parsed_data` 落库同形的明细行。

    matched 缺省按落库口径派生（= bool(sku_id)），与 api_receipts.py:971 同口径。
    """
    return {
        "name": name, "raw_name": name, "quantity": 1, "unit": unit,
        "unit_price": unit_price, "amount": amount, "sku_id": sku_id,
        "cost_center_id": None, "confidence": 0.9,
        "matched": int(bool(sku_id)) if matched is None else matched,
        "price_anomaly": 0, "price_anomaly_direction": "", "price_diff_percent": 0.0,
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


def _sku_names(tenant_id="default"):
    return {r.id: r.name for r in db.list_skus(include_inactive=True,
                                               tenant_id=tenant_id)}


def _stock_logs(receipt_id):
    """该单据落下的库存流水行（用于断言"库存记到了哪个 SKU"）。"""
    s = db.get_session()
    try:
        return s.query(db._StockLogRow).filter(
            db._StockLogRow.receipt_id == int(receipt_id)).all()
    finally:
        s.close()


def _seed_alias(sup, sku_id, raw_name, unit="斤", canonical=None):
    aid, err = db.upsert_vendor_sku_alias(
        sup, sku_id, raw_name,
        canonical_name=canonical if canonical is not None else _normalize_sku_name(raw_name),
        unit=unit, tenant_id="default", source_ref="seed")
    assert err is None and aid, f"种子别名登记失败: {err}"
    return int(aid)


# -------------------------------------------------------------
# 1. 主判别式：别名命中的确认指向，approve 不得推翻
# -------------------------------------------------------------
def test_approve_preserves_alias_confirmed_match():
    """核心场景（复现探针）：别名「本吧」→「本地牛腩」，入账后别名与 SKU 都不变。"""
    _fresh_db()
    sup = _mk_supplier("甲供應商")
    sku_niunan = _mk_sku("本地牛腩", base_unit="斤")
    _seed_alias(sup, sku_niunan, "本吧")

    # 解析期：_match_sku 经别名把「本吧」确认到「本地牛腩」
    item = _item("本吧")
    _match_sku(item, tenant_id="default", supplier_id=sup)
    assert item["sku_id"] == sku_niunan and item["matched"] == 1

    rid = _mk_receipt("甲供應商", [item], supplier_id=sup)
    before = _sku_names()

    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    after = _sku_names()
    assert after == before, f"不得新建重复 SKU（新增: {set(after) - set(before)}）"
    rows = _alias_rows("default", sup, "本吧")
    assert len(rows) == 1 and rows[0].sku_id == sku_niunan, \
        f"别名被翻转指向重复 SKU：{[(r.raw_name, r.sku_id) for r in rows]}"
    # 二次解析仍命中同一 SKU（若别名被翻转，这里会落到重复 SKU）
    again = _item("本吧")
    _match_sku(again, tenant_id="default", supplier_id=sup)
    assert again["sku_id"] == sku_niunan


def test_approve_resolves_alias_itself_without_preselected_sku():
    """明细未带 sku_id 时，入账路径**自己**经别名命中既有 SKU（不再新建）。"""
    _fresh_db()
    sup = _mk_supplier("乙供應商")
    sku_niunan = _mk_sku("本地牛腩", base_unit="斤")
    _seed_alias(sup, sku_niunan, "本吧")

    rid = _mk_receipt("乙供應商", [_item("本吧")], supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    assert len(_sku_names()) == 1, "别名已给出答案，不得再建重复 SKU"
    row = db.get_sku(sku_niunan, tenant_id="default")
    assert row.current_stock == 1.0, "库存必须记在别名指向的 SKU 上"
    # 明细行的 sku_id 被回写（否则前端显示"无 SKU"）
    items = db.get_receipt_items(rid)
    assert items[0]["sku_id"] == sku_niunan and items[0]["matched"] is True


# -------------------------------------------------------------
# 2. 要求 ②：matched 的指向不得被"商品名不互含"清空
# -------------------------------------------------------------
def test_approve_does_not_clear_matched_sku_on_name_mismatch():
    """`matched=1` 是"已确认"标记；品名与 SKU 名非子串关系也不得清空重判。"""
    _fresh_db()
    sup = _mk_supplier("丙供應商")
    sku = _mk_sku("本地牛腩", base_unit="斤")
    # 非别名通道、纯人工确认：品名「牛腩3號」与 SKU 名「本地牛腩」互不包含
    rid = _mk_receipt("丙供應商", [_item("牛腩3號", sku_id=sku, matched=1)],
                      supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    assert len(_sku_names()) == 1, "已确认的指向不得被清成 None 后另建 SKU"
    assert db.get_sku(sku, tenant_id="default").current_stock == 1.0


def test_approve_rematches_when_pointed_sku_missing():
    """指向不存在/跨租户时仍必须重新匹配（保护不能变成"死指向也照用"）。"""
    _fresh_db()
    _mk_supplier("丁供應商")
    rid = _mk_receipt("丁供應商", [_item("本地牛腩", sku_id=999999, matched=1)])
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    sku = db.find_sku_by_name("本地牛腩", tenant_id="default")
    assert sku is not None, "死指向必须触发重新匹配/建档"
    assert db.get_receipt_items(rid)[0]["sku_id"] == sku.id


# -------------------------------------------------------------
# 3. 要求 ③：别名登记不得把既有存活指向改指到本次新建的重复 SKU
# -------------------------------------------------------------
def test_upsert_protect_live_target_blocks_repoint():
    """判别式：protect_live_target=True 时既有存活指向被保留；False 时仍可改指。"""
    _fresh_db()
    sup = _mk_supplier("戊供應商")
    sku_a = _mk_sku("本地牛腩")
    sku_b = _mk_sku("本吧")
    _seed_alias(sup, sku_a, "本吧")

    aid, err = db.upsert_vendor_sku_alias(sup, sku_b, "本吧", protect_live_target=True)
    assert err == "TARGET_PROTECTED", f"应拦住改指，实际 reason={err}"
    assert aid is not None, "被保护时行仍存在，id 必须返回（否则调用方误判为丢失）"
    rows = _alias_rows("default", sup, "本吧")
    assert len(rows) == 1 and rows[0].sku_id == sku_a, "既有存活指向必须保留"

    # 默认（False）保持既有改指语义：重新匹配/合并场景仍可改指
    _, err2 = db.upsert_vendor_sku_alias(sup, sku_b, "本吧")
    assert err2 is None
    assert _alias_rows("default", sup, "本吧")[0].sku_id == sku_b


def test_upsert_protect_allows_repoint_when_old_target_dead():
    """旧目标已停用（被合并）时不得保护 —— 否则别名会永久指向死 SKU。"""
    _fresh_db()
    sup = _mk_supplier("己供應商")
    sku_a = _mk_sku("本地牛腩")
    sku_b = _mk_sku("本吧")
    _seed_alias(sup, sku_a, "本吧")
    _, uerr = db.update_sku(sku_a, active=0, tenant_id="default")
    assert uerr is None, uerr

    _, err = db.upsert_vendor_sku_alias(sup, sku_b, "本吧", protect_live_target=True)
    assert err is None, f"旧目标已死 → 必须允许改指（实际 {err}）"
    assert _alias_rows("default", sup, "本吧")[0].sku_id == sku_b


# -------------------------------------------------------------
# 4. 接线守卫（防"定义了没接线"与本仓"第二份实现"回潮）
# -------------------------------------------------------------
def test_approve_passes_protect_flag_only_when_sku_created(monkeypatch):
    """入账只在**本次新建 SKU** 时传 protect_live_target=True。"""
    _fresh_db()
    sup = _mk_supplier("庚供應商")
    calls = []

    def _spy(supplier_id, sku_id, raw_name, **kw):
        calls.append({"raw_name": raw_name, "protect": kw.get("protect_live_target")})
        return 1, None

    monkeypatch.setattr(db, "upsert_vendor_sku_alias", _spy)

    # 情形一：库里没有该品名 → 会新建 SKU → 必须保护
    rid_new = _mk_receipt("庚供應商", [_item("本地牛腩")], supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid_new), tenant_id="default")
    assert calls[-1]["protect"] is True, "本次新建 SKU 时必须带保护标志"

    # 情形二：库里已有同品名 SKU（未新建）→ 不保护（合法改指语义保留）
    rid_old = _mk_receipt("庚供應商", [_item("本地牛腩")], supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid_old), tenant_id="default")
    assert calls[-1]["protect"] is False, "复用既有 SKU 时不得带保护标志"


def test_approve_delegates_matching_to_receipt_utils(monkeypatch):
    """判别式：入账的匹配必须委托 `receipt_utils._match_sku`（唯一实现）。"""
    _fresh_db()
    sup = _mk_supplier("辛供應商")
    sentinel = _mk_sku("哨兵 SKU")
    rid = _mk_receipt("辛供應商", [_item("任意品名")], supplier_id=sup)
    seen = []

    def _stub(item, tenant_id=None, supplier_id=None):
        seen.append((item["name"], tenant_id, supplier_id))
        item["sku_id"] = sentinel
        item["matched"] = 1

    monkeypatch.setattr(receipt_utils, "_match_sku", _stub)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    assert seen == [("任意品名", "default", sup)], \
        "必须调用唯一匹配实现并透传租户/供应商"
    assert len(_sku_names()) == 1, "匹配实现给出答案后不得再走内联的第二份实现"
    assert db.get_sku(sentinel, tenant_id="default").current_stock == 1.0


def test_approve_does_not_flip_alias_when_matcher_misses(monkeypatch):
    """兜底判别式（要求 ③ 的端到端形态）：匹配实现漏掉别名并新建重复 SKU 时，
    别名仍不得被翻转 —— 否则"后跑的实现推翻前一套成果"会重演。"""
    _fresh_db()
    sup = _mk_supplier("壬供應商")
    sku_a = _mk_sku("本地牛腩", base_unit="斤")
    _seed_alias(sup, sku_a, "本吧")

    def _missing(item, tenant_id=None, supplier_id=None):
        """模拟漏掉别名通道的旧/退化匹配实现（不做任何回填）。"""
        return None

    monkeypatch.setattr(receipt_utils, "_match_sku", _missing)
    rid = _mk_receipt("壬供應商", [_item("本吧")], supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    rows = _alias_rows("default", sup, "本吧")
    assert len(rows) == 1 and rows[0].sku_id == sku_a, \
        "既有存活别名不得被改指到本次新建的重复 SKU"
    hit = db.find_vendor_sku_alias("本吧", tenant_id="default", supplier_id=sup)
    assert hit is not None and hit.id == sku_a


# -------------------------------------------------------------
# 5. 审核 mustFix #1：无确认标记（matched=0）的既有指向不得被沿用
# -------------------------------------------------------------
def test_approve_rematches_unconfirmed_pointer_to_unrelated_sku():
    """负向对照（mustFix #1）：matched=0 且指向存在的**无关** SKU → 必须纠正/重建。

    这是"库存记到错的 SKU"的直接判别式：品名「牛腩」与既有指向的 SKU 名「白糖」
    核心词互不包含（`_normalize_sku_name` 后无双向包含），即典型错配。
    matched=0 表示该指向没有确认标记（旧库遗留/外部写入），不得当作已确认事实。
    """
    _fresh_db()
    sup = _mk_supplier("癸供應商")
    wrong = _mk_sku("白糖", base_unit="斤")

    rid = _mk_receipt("癸供應商", [_item("牛腩", sku_id=wrong, matched=0)],
                      supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    items = db.get_receipt_items(rid)
    final = items[0]["sku_id"]
    assert final != wrong, "无确认标记的错配指向必须被纠正，不得原样沿用"
    assert final is not None, "纠正是重建/重匹配，不是清成空"
    assert db.get_sku(final, tenant_id="default").name == "牛腩"
    # 新结果与新 matched 一并回写（update_item_sku 同步 matched）
    assert items[0]["matched"] is True

    # 库存不得记到旧 SKU：SKU 行与流水行两处都要核（流水是"记到哪"的直接证据）
    assert db.get_sku(wrong, tenant_id="default").current_stock == 0.0, \
        "无关 SKU 的当前库存不得被 +1"
    logs = _stock_logs(rid)
    assert logs and all(int(r.sku_id) != wrong for r in logs), \
        f"入库流水不得挂到旧 SKU：{[(r.sku_id, r.name) for r in logs]}"
    assert logs[0].sku_id == final


def test_approve_keeps_unconfirmed_pointer_when_name_matches():
    """反向对照：matched=0 但指向恰好正确的 SKU → 重判后仍落在同一 SKU。

    防"一律清空"式的过度纠正：若实现改成一见 matched=0 就无条件另建 SKU，
    这条用例会因多出重复 SKU 而失败（本仓已出现过的 SKU 爆炸形态）。
    """
    _fresh_db()
    sup = _mk_supplier("子供應商")
    right = _mk_sku("牛腩", base_unit="斤")

    rid = _mk_receipt("子供應商", [_item("牛腩", sku_id=right, matched=0)],
                      supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id="default")

    assert len(_sku_names()) == 1, "指向本来就对，不得因此建出重复 SKU"
    assert db.get_sku(right, tenant_id="default").current_stock == 1.0
    items = db.get_receipt_items(rid)
    assert items[0]["sku_id"] == right and items[0]["matched"] is True
