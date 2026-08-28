# -*- coding: utf-8 -*-
"""对话式查询 Agent（问 AI）：基于自然语言查询库存、价格走势、供应商排名与未付账单。

支持自然语言问答模式与多表关联分析：
- "上月菜心单价最高的三家供应商"
- "目前有哪些供应商有未付账单"
- "商品A的价格趋势"
- "当前库存总值最高的商品"
"""

from typing import Optional
from app import db
from app.services.inventory import cost_summary


# 提示词注入 / 越权指令黑名单特征库
INJECTION_KEYWORDS = [
    "ignore previous", "ignore all instructions", "system prompt",
    "forget all instructions", "you are now in developer mode", "dan mode",
    "输出所有密码", "打印系统密钥", "越权", "管理员模式", "override instructions",
    "disregard", "show me your prompt", "dump database", "drop table",
]


def _check_prompt_injection(text: str) -> Optional[str]:
    """检测输入是否包含提示词注入或越权指令。"""
    t = text.lower()
    for kw in INJECTION_KEYWORDS:
        if kw in t:
            return f"检测到潜在的提示词注入/越权风险指令（包含 '{kw}'），请求已被安全策略拦截。"
    return None


def run_query(question: str, tenant_id=None) -> dict:
    """执行自然语言查询，返回回答文本 + 结构化支撑数据。

    tenant_id 为空时不过滤（兼容内部/非 FastAPI 调用）；
    Web 端点必须传入当前租户，避免跨租户数据泄漏。
    """
    raw_q = (question or "").strip()
    if not raw_q:
        return {
            "answer": "请输入您想了解的进货、库存或供应商问题。",
            "structured_data": {},
            "query_type": "empty",
        }

    # 0. 提示词注入安全拦截
    injection_err = _check_prompt_injection(raw_q)
    if injection_err:
        return {
            "answer": injection_err,
            "structured_data": {"security_warning": "prompt_injection_blocked"},
            "query_type": "security_block",
        }

    q = raw_q.lower()

    # 1. 价格最高/单价最贵 供应商查询
    if any(k in q for k in ["最高", "最贵", "单价高", "价格高"]) and any(k in q for k in ["供应商", "商户", "单价", "菜心", "商品"]):
        skus = db.list_skus(include_inactive=True, tenant_id=tenant_id)
        # 寻找提到的具体商品名
        target_sku = None
        for s in skus:
            if s.name in question:
                target_sku = s
                break
        if not target_sku and skus:
            target_sku = skus[0]

        top_vendors = []
        if target_sku:
            history = db.price_history(target_sku.id, tenant_id=tenant_id)
            # 按 vendor 聚合最高单价
            vendor_prices = {}
            for h in history:
                v = getattr(h, "vendor", "") or "未知供应商"
                p = getattr(h, "unit_price", 0.0)
                if v not in vendor_prices or p > vendor_prices[v]["unit_price"]:
                    vendor_prices[v] = {
                        "vendor": v,
                        "unit_price": p,
                        "date": getattr(h, "date", ""),
                        "item": target_sku.name,
                    }
            sorted_v = sorted(vendor_prices.values(), key=lambda x: x["unit_price"], reverse=True)[:3]
            top_vendors = sorted_v

            ans = f"关于【{target_sku.name}】单价最高的供应商如下：\n"
            for i, v in enumerate(sorted_v, 1):
                ans += f"{i}. {v['vendor']}：单价 HK${v['unit_price']:.2f} ({v['date']})\n"
            if not sorted_v:
                ans = f"暂无【{target_sku.name}】的多供应商历史进货比价记录。"

            return {
                "answer": ans.strip(),
                "structured_data": {"item": target_sku.name, "top_suppliers": sorted_v},
                "query_type": "top_price_suppliers",
            }

    # 2. 未付账单 / 赊单查询
    if any(k in q for k in ["未付", "应付", "赊单", "欠款", "账单"]):
        suppliers = db.list_suppliers(include_inactive=False, tenant_id=tenant_id)
        unpaid = []
        total_unpaid = 0.0
        for s in suppliers:
            stats = db.supplier_stats(s.id, tenant_id=tenant_id)
            if stats["unpaid_credit_total"] > 0:
                unpaid.append({
                    "supplier_name": s.name,
                    "unpaid_total": stats["unpaid_credit_total"],
                    "unpaid_count": stats["unpaid_credit_count"],
                    "payment_terms": s.payment_terms_days,
                })
                total_unpaid += stats["unpaid_credit_total"]

        unpaid.sort(key=lambda x: x["unpaid_total"], reverse=True)
        ans = f"当前共有 {len(unpaid)} 家供应商存在未付赊单，未付总额：HK${total_unpaid:.2f}。\n"
        for i, u in enumerate(unpaid[:5], 1):
            ans += f"{i}. {u['supplier_name']}：未付金额 HK${u['unpaid_total']:.2f} ({u['unpaid_count']}张单，账期{u['payment_terms']}天)\n"

        return {
            "answer": ans.strip(),
            "structured_data": {"total_unpaid": round(total_unpaid, 2), "suppliers": unpaid},
            "query_type": "unpaid_bills",
        }

    # 3. 价格波动 / 趋势查询
    if any(k in q for k in ["趋势", "走势", "波动", "涨价", "历史价"]):
        cost = cost_summary(tenant_id=tenant_id)
        items = cost.get("items", {})
        risers = []
        for name, info in items.items():
            prices = info.get("prices", [])
            if len(prices) >= 2:
                earliest = prices[0]
                latest = prices[-1]
                diff = round((latest - earliest) / earliest * 100, 1)
                risers.append({
                    "item": name,
                    "earliest_price": earliest,
                    "latest_price": latest,
                    "change_pct": diff,
                })
        risers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        ans = "近期价格波动情况统计：\n"
        for r in risers[:5]:
            trend = "上涨" if r["change_pct"] > 0 else "下降"
            ans += f"- {r['item']}：{trend} {abs(r['change_pct'])}% (HK${r['earliest_price']} → HK${r['latest_price']})\n"
        if not risers:
            ans = "暂无充足的多批次价格历史数据。"

        return {
            "answer": ans.strip(),
            "structured_data": {"risers": risers},
            "query_type": "price_trends",
        }

    # 4. 默认通用库存与成本摘要
    cost = cost_summary(tenant_id=tenant_id)
    tot = cost.get("total_cost", 0.0)
    sku_cnt = len(cost.get("items", {}))
    ans = f"系统当前管理 {sku_cnt} 种食材/物料，库存加权总成本为 HK${tot:.2f}。您可以询问例如：\n- '上月单价最高的三家供应商'\n- '目前有哪些未付账单'\n- '哪些商品最近涨价明显'"

    return {
        "answer": ans,
        "structured_data": cost,
        "query_type": "general_summary",
    }
