# -*- coding: utf-8 -*-
"""幂等入库 + 成本核算 + AI 复盘入口（对齐完整版 T5 + 面试三业务能力）。

纪律：只有 owner 在复核 approve 后写库存；写 SKU 流水（inventory_log）；
重复 approve 幂等（状态机拦截）。
"""

import logging

from app import db

logger = logging.getLogger(__name__)


def apply_receipt_to_inventory(row, tenant_id=None):
    """approve 入账：把收据明细写入 SKU 库存（幂等，仅 approved 状态）。

    - 匹配到 SKU → 累加 current_stock + 记录价格历史
    - 未匹配 → 复用唯一匹配实现重匹配，仍无则自动建 SKU（完整版走 SKU 匹配/人工确认）
    - B-P0-1：入库前强制对 name 做 FR-7 核心词归一（剥离 _\\d{10} 流水号），防止 SKU 爆炸
    - P1-1 租户口径：tenant_id 显式传入优先；缺省从 row.tenant_id 兜底
      （单据行已有租户）；两者皆空时不过滤（旧调用向后兼容）。
      SKU 匹配/建档/库存流水写入全部限定该租户。
    - P2-T2 钩子 B：入账时把「该供应商用这个写法指这个 SKU」登记进 vendor_sku_alias，
      使同一供应商的同类写法在**解析阶段**直接命中，同时抑制 SKU 爆炸。
      why 放在这里而不是只放"自动建 SKU"那一个分支：approve 是人工确认过的强信号
      （用户已看过这张单），此时落下的映射才是可信先验；只登记"新建的 SKU"会让
      既有的正确匹配（占多数）学不到任何写法，别名表永远长不大。
    - P2fix-F1（本函数的历史缺陷）：这里曾内联了**第二份** SKU 匹配实现
      （核心词互含"错配校验" → find_sku_by_name → 核心词等值 → create_sku），
      与解析期的 `receipt_utils._match_sku` 分叉，且**不看 `it["matched"]`**。
      后果是自我推翻：解析期刚经别名把「本吧」确认到 SKU「本地牛腩」（别名通道
      本就允许非子串关系），入账时被这份"错配校验"清成 None → 新建重复 SKU「本吧」
      → 紧接着的登记钩子把别名从「本地牛腩」翻转指向那个重复 SKU，下次解析反而
      落到重复项（正是 P2-T2 要消灭的 SKU 爆炸）。现改为：
      1. 未匹配时**复用唯一匹配实现** `_match_sku`（与解析期同源，别名优先）；
      2. 既有指向的处置分两种（P2fix 审核 mustFix #1）：
         - `matched` 为真（= 有确认标记）→ 只校验"存在且同租户"，**不**用商品名层面
           的判据复查 —— 别名通道本就允许非子串关系，复查会把确认过的正确指向清空；
         - `matched` 为假（旧库遗留/外部写入的无证据指向）→ **不沿用**，清空后交给
           `_match_sku` 重新判定，并把结果与新 `matched` 回写。
           此前无条件沿用，后果是错配被原样保留：品名「牛腩」的明细带着指向「白糖」
           的 sku_id 入账，库存真的记到了无关 SKU 上（比"建出重复 SKU"更严重——那至少
           还落在同名 SKU 上）。别名命中同样能保住指向：`_match_sku` 第一跳就是别名通道，
           故"该写法命中别名表"的指向重判后仍回到同一 SKU。
      3. 别名登记带 `protect_live_target`：本次刚新建的 SKU 不得翻转既有存活指向。
    """
    from app.services.receipt_utils import (
        _match_sku, _normalize_sku_name, canonical_sku_name)

    _tenant = tenant_id or getattr(row, "tenant_id", None) or None
    # P2-T2：供应商锚点（别名表按供应商建键）。行上已锚定直接用；未锚定按供应商名
    # 解析一次（唯一解析实现 db.resolve_supplier_id，解析不出留 None → 不登记别名）。
    _supplier_id = getattr(row, "supplier_id", None)
    if not _supplier_id:
        _supplier_id = db.resolve_supplier_id(
            getattr(row, "supplier_name", ""), tenant_id=_tenant)
    # L0 记忆先验档（只追加，既有两档不动；parse 期 hint 档在入账期不可得 —— hint 原文
    # 不落库，其对锚点的贡献已物化为 row.supplier_id）：按名解析仍落空时，用同名字历史
    # 记忆行的锚点兜底（唯一候选才用）。该锚点同时供上面的重匹配与下面的别名登记使用，
    # 保持"登记与匹配收窄用同一锚点"的既有结构。全落空 → 维持 None → 后续
    # upsert_vendor_sku_alias 返回 NO_SUPPLIER（语义与留痕不变）。
    if not _supplier_id:
        _supplier_id = db.resolve_supplier_id_from_memory(
            getattr(row, "supplier_name", ""), tenant_id=_tenant)
        if _supplier_id:
            logger.info(
                "[inventory] receipt %s 供应商锚点经记忆先验回退锚定 supplier_id=%s"
                "（vendor=%r）", row.id, _supplier_id, getattr(row, "supplier_name", ""))
    _alias_ok = 0
    _alias_skip = {}          # reason -> 次数（区分"正常跳过"与"真故障"）
    _alias_protected = 0      # 既有别名指向被保护、未改指的次数

    items = db.get_receipt_items(row.id)
    for it in items:
        # 口径对齐门禁/总额：票面划线作废行（拒收/退货）不入库、不进成本
        if it.get("is_void"):
            continue
        # B-P0-1 归一：确保流水号变体入库时落在同一 SKU
        name = canonical_sku_name(it["name"])
        sku_id = it.get("sku_id")
        _created_sku = False

        # 既有指向只校验"存在且同租户"（跨租户/已删除的死指向必须重新匹配）。
        # 不做商品名层面的"错配校验"：那条判据是解析期匹配规则的副本，而别名通道
        # 本就允许非子串关系（「本吧」→「本地牛腩」），拿它复查会把别名命中/用户
        # 确认过的正确指向清成 None —— 那正是"别名被翻转 + 重复建 SKU"的起点。
        #
        # 但"保护既有指向"的前提是它**有确认标记**（mustFix #1）：`matched` 由解析期
        # 匹配成功或复核台人工指派写入（`api_receipts.save_edited` 与 `update_item_sku`
        # 都以 bool(sku_id) 写 matched），`matched=0` 却带着 sku_id 说明该指向**无证据**
        # （旧库遗留/外部写入）。此前一并沿用，等于把"未确认的猜测"当"已确认的事实"：
        # 实测品名「牛腩」的明细带着指向「白糖」的 sku_id 入账 → sku_id 原样保留 →
        # 库存 +1 记到了无关 SKU 上。故无证据的指向清空，交下面唯一匹配实现重判。
        _stale_sku = sku_id if (sku_id and not it.get("matched")) else None
        if sku_id and db.get_sku(sku_id, tenant_id=_tenant) is None:
            sku_id = None
        elif _stale_sku is not None:
            sku_id = None
            # 必须留痕：这是"入账时修正了一条无依据的历史关联"，属数据质量事件
            logger.info("[inventory] receipt %s 明细 %s 的既有 sku_id=%s 无确认标记"
                        "（matched=0）→ 不沿用，改为重新匹配",
                        row.id, it.get("id"), _stale_sku)

        if not sku_id:
            # 唯一匹配实现：别名通道（供应商优先）→ 精确 → 核心词双向包含。
            # 解析期落库前走的是同一个函数（receipt_utils.py:518），两处必须同源；
            # 本函数此前内联的第二份实现已是"同一根因两份实现只改一份"的实例。
            # 传副本并显式清空 sku_id/matched：解析期调用前这两个值恒为 None/0，
            # 而 `_match_sku` **未命中时不会清空**传入值 —— 不清就会读到残留旧值
            # （死指向、或被上面判为"无证据"的旧指向），把它当成匹配结果（实测踩到）。
            _probe = dict(it, sku_id=None, matched=0)
            _match_sku(_probe, tenant_id=_tenant, supplier_id=_supplier_id)
            sku_id = _probe.get("sku_id")

        if not sku_id:
            # 仍未命中 → 核心词归一后建 SKU（避免流水号变体各建一个）
            sku_id, _cerr = db.create_sku(canonical_sku_name(name),
                                          base_unit=it.get("unit") or "",
                                          tenant_id=_tenant)
            _created_sku = bool(sku_id)
            if not sku_id:
                # 例如并发下同租户同名 SKU 刚被别处建走：**不得静默**（该行会没有 SKU）
                logger.warning("[inventory] receipt %s 品名 %s 建 SKU 失败：%s",
                               row.id, name, _cerr)
        # 回写明细行的 sku_id（否则前端显示"无 SKU"）
        if sku_id and it.get("id"):
            db.update_item_sku(it["id"], sku_id)
        elif _stale_sku is not None and it.get("id"):
            # 无证据的旧指向又没能重判出 SKU（例如并发下建 SKU 失败）：必须把指向清成
            # None。否则明细行仍留着刚被拒绝信任的 sku_id，而下面的流水已按 sku_id=None
            # 落账 —— 明细与流水不一致且无人知（本仓高发的"静默不一致"形态）。
            # update_item_sku 同时把 matched 归 0，与"未匹配"语义一致。
            db.update_item_sku(it["id"], None)
        # P2-T2 钩子 B：登记供应商别名（写入侧唯一入口，与读取侧
        # receipt_utils._match_sku 的 supplier_id 分支配对：这里登记什么写法，
        # 解析阶段就按 item["name"] 精确命中什么写法）
        if sku_id:
            _alias_id, _alias_err = db.upsert_vendor_sku_alias(
                supplier_id=_supplier_id,
                sku_id=sku_id,
                raw_name=it.get("name") or "",
                canonical_name=_normalize_sku_name(it.get("name") or ""),
                unit=it.get("unit") or "",
                tenant_id=_tenant,
                source_ref="receipt:%s" % row.id,
                # 本次刚新建的 SKU 不得翻转既有存活指向（防自我推翻）
                protect_live_target=_created_sku,
            )
            if _alias_err == "TARGET_PROTECTED":
                _alias_protected += 1
            elif _alias_id:
                _alias_ok += 1
            else:
                _alias_skip[_alias_err] = _alias_skip.get(_alias_err, 0) + 1
        db.apply_stock_log(
            sku_id=sku_id, name=canonical_sku_name(name),
            qty=it.get("quantity", 0), unit=it.get("unit") or "",
            amount=it.get("amount", 0), vendor=row.supplier_name,
            date=row.receipt_date or "", receipt_id=row.id, kind="in",
            tenant_id=_tenant,
        )
    # P2-T2：别名登记结果汇总留痕。`NO_SUPPLIER` 是**正常**情形（该单据的供应商没有
    # 档案，别名表按供应商建键故无从登记）→ info；其余原因（INVALID_SKU / CONFLICT /
    # ERROR）都是真故障（SKU 不存在、唯一键竞争、写库异常），升级为 warning。
    # `TARGET_PROTECTED` 同样按异常报：它代表"本次入账想改指既有存活别名却被拦下"，
    # 即入账路径与别名先验出现了分歧 —— 拦下是对的，但必须有痕迹才知道发生过。
    # 绝不能完全不记：别名一条都登记不上却毫无痕迹，正是本仓"功能静默失效"的典型形态。
    if _alias_skip or _alias_protected:
        _abnormal = {k: v for k, v in _alias_skip.items() if k != "NO_SUPPLIER"}
        if _alias_protected:
            _abnormal["TARGET_PROTECTED"] = _alias_protected
        if _abnormal:
            logger.warning("[inventory] receipt %s 供应商别名登记异常 %s（成功 %d 条）",
                           row.id, _abnormal, _alias_ok)
        else:
            logger.info("[inventory] receipt %s 供应商别名登记 %d 条 / 跳过 %d 条"
                        "（供应商档案未锚定）", row.id, _alias_ok,
                        _alias_skip.get("NO_SUPPLIER", 0))


def cost_summary(tenant_id=None) -> dict:
    """成本核算：按商品累计 + 总成本；计算加权平均单价（weighted_avg_price）并附加 prices 历史。

    租户口径（P3-5）：tenant_id 非空时仅聚合该租户的 SKU 与价格流水；
    None 不过滤（内部脚本与存量调用向后兼容）。存量数据经迁移归入
    default 租户，传 "default" 与旧行为结果一致。
    """
    from app.chains.review_chain import _INSIGHTS_CACHE  # noqa: F401 触发 weekly_insights 时同步重算
    skus = db.list_skus(include_inactive=True, tenant_id=tenant_id)
    items = {}
    weighted_total = 0.0

    for s in skus:
        prices = []
        weighted_avg = s.last_unit_price or 0.0
        if s.id:
            try:
                history = db.price_history(s.id, tenant_id=tenant_id)
                if history:
                    prices = [round(float(r.unit_price), 4) for r in history if getattr(r, "unit_price", 0) > 0]
                    # 计算加权平均：sum(qty * unit_price) / sum(qty)
                    total_q = sum(getattr(r, "qty", 1.0) for r in history)
                    total_val = sum(getattr(r, "qty", 1.0) * getattr(r, "unit_price", 0.0) for r in history)
                    if total_q > 0:
                        weighted_avg = round(total_val / total_q, 4)
            except Exception:
                prices = []
        if not prices and s.last_unit_price and s.last_unit_price > 0:
            prices = [s.last_unit_price]

        item_amount = round(s.current_stock * (weighted_avg or s.last_unit_price), 2)
        weighted_total += item_amount

        items[s.name] = {
            "qty": s.current_stock,
            "amount": item_amount,
            "unit": s.base_unit,
            "vendor": "",
            "last_price": s.last_unit_price,
            "weighted_avg_price": weighted_avg,
            "prices": prices,
        }
    return {"total_cost": round(weighted_total, 2), "items": items}
