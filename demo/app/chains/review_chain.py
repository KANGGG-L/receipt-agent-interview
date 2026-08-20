# -*- coding: utf-8 -*-
"""AI 复盘链：价格异动 / 供应商分析（面试三大业务能力之一）。

输入：库存台账 + 当前单据 → LLM 输出复盘建议（价格异动、SKU 冷启动提示）。
"""

import json
import time

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import build_audit_model

REVIEW_SYSTEM = """你是香港餐饮的采购复盘助手。基于进货台账与供应商历史数据，给出复盘建议。

输出严格 JSON（只输出 JSON）：
{
  "price_alerts": [
    {"name": "菜心", "current_price": 5.0, "previous_price": 4.2,
     "change_pct": 19.0, "note": "价格异动超过 10%"}
  ],
  "supplier_insights": [
    {"vendor": "祥興", "observation": "...", "suggestion": "..."}
  ],
  "summary": "一句话总结"
}
"""


def run_review(inventory: dict, current_receipt: dict = None,
               model=None, config=None) -> dict:
    """AI 复盘入口。inventory = {"菜心": {"qty":..,"amount":..,"vendor":..}}。"""
    try:
        if model is None:
            model = build_audit_model(cfg=config)
        prompt = build_review_prompt(inventory, current_receipt)
        result = model.invoke(prompt)
        raw = result.content if not isinstance(result, str) else result
        return _parse_review(raw)
    except Exception as e:
        return {"error": f"review_error: {e}", "skipped": True}


def build_review_prompt(inventory: dict, current_receipt: dict = None) -> list:
    inv_text = json.dumps(inventory, ensure_ascii=False, indent=2)
    cur_text = json.dumps(current_receipt or {}, ensure_ascii=False, indent=2)
    return [
        SystemMessage(content=REVIEW_SYSTEM),
        HumanMessage(content=(
            "当前库存台账（按商品累计）：\n" + inv_text +
            "\n\n本次入库单据：\n" + cur_text +
            "\n\n请给出复盘建议（价格异动/供应商洞察/总结）。"
        )),
    ]


def _parse_review(raw: str) -> dict:
    if not raw:
        return {"error": "empty_output", "skipped": True}
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start_i = text.find("{")
        end_i = text.rfind("}")
        if start_i == -1 or end_i == -1:
            return {"error": "unparseable", "skipped": True}
        try:
            payload = json.loads(text[start_i:end_i + 1])
        except json.JSONDecodeError:
            return {"error": "unparseable", "skipped": True}
    return payload


# -------------------------------------------------------------
# 阶段 1：AI 发现（weekly_insights，内存缓存 1 小时）
# -------------------------------------------------------------
_INSIGHTS_CACHE: dict = {}
_INSIGHTS_CACHE_TTL = 3600  # 秒


def weekly_insights(cost_summary: dict) -> dict:
    """基于 cost_summary 产出极简量化 AI 发现。

    返回：{
      top_price_risers: list[{sku_id, name, vendor, earliest_price, latest_price, change_pct, impact_amount, unit, evidence_text}],
      total_impact_amount: float,
      anomaly_count: int,
      suggestions: list[str],
      updated_at: str
    }
    """
    now = time.time()
    if _INSIGHTS_CACHE.get("updated_at") and (now - _INSIGHTS_CACHE["updated_at"]) < _INSIGHTS_CACHE_TTL:
        return dict(_INSIGHTS_CACHE["payload"])

    from app import db as _db
    top_price_risers: list = []
    total_impact = 0.0
    items = cost_summary.get("items") or {}

    for name, info in items.items():
        if not isinstance(info, dict):
            continue
        prices = info.get("prices") or []
        prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        if len(prices) < 2:
            continue
        earliest = prices[0]
        latest = prices[-1]
        if earliest <= 0:
            continue

        change_pct = round((latest - earliest) / earliest * 100, 1)
        if change_pct > 15:  # 灵敏度收紧至 15%
            sku_obj = _db.find_sku_by_name(name)
            sku_id = sku_obj.id if sku_obj else None
            curr_stock = info.get("qty", 1.0) or 1.0
            unit = info.get("unit") or "斤"
            vendor = info.get("vendor") or "主要供应商"

            # 溯源最新单据的供应商名
            if sku_id:
                try:
                    logs = _db.price_history(sku_id)
                    if logs and logs[-1].vendor:
                        vendor = logs[-1].vendor
                except Exception:
                    pass

            # 量化影响金额 = (最新价 - 基准价) * 当前累计/在库数量
            impact_amount = round(max(0.0, (latest - earliest) * curr_stock), 2)
            total_impact += impact_amount

            evidence = f"{vendor}：【{name}】近期单价由 ${earliest:.2f} 涨至 ${latest:.2f} (+{change_pct}%)，已累计影响成本 HK${impact_amount:.2f}。"

            top_price_risers.append({
                "sku_id": sku_id,
                "name": name,
                "vendor": vendor,
                "earliest_price": round(earliest, 2),
                "latest_price": round(latest, 2),
                "change_pct": change_pct,
                "impact_amount": impact_amount,
                "unit": unit,
                "evidence_text": evidence
            })

    top_price_risers.sort(key=lambda x: (x["impact_amount"], x["change_pct"]), reverse=True)
    top_price_risers = top_price_risers[:5]

    # anomaly_count
    try:
        all_rows = _db.list_receipt_rows()
        flagged_count = sum(1 for r in all_rows if r and r.status == "flagged")
        anomaly_items = 0
        for r in all_rows:
            if not r:
                continue
            items_ = _db.get_receipt_items(r.id)
            anomaly_items += sum(1 for it in items_ if it.get("price_anomaly"))
        anomaly_count = anomaly_items + flagged_count
    except Exception:
        anomaly_count = len(top_price_risers)

    suggestions = _gen_suggestions(top_price_risers, anomaly_count)

    payload = {
        "has_alerts": len(top_price_risers) > 0,
        "total_impact_amount": round(total_impact, 2),
        "alert_count": len(top_price_risers),
        "top_price_risers": top_price_risers,
        "anomaly_count": anomaly_count,
        "suggestions": suggestions,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _INSIGHTS_CACHE["payload"] = payload
    _INSIGHTS_CACHE["updated_at"] = now
    return dict(payload)


def _gen_suggestions(top_risers, anomaly_count):
    """根据发现生成 1-3 条事实陈述与复核建议。"""
    suggestions: list = []
    if top_risers:
        names = "、".join(r["name"] for r in top_risers[:2])
        suggestions.append(f"「{names}」近期价格涨幅超过 15%，请在归档页核对调价记录。")
    if anomaly_count > 0:
        suggestions.append(f"当前有 {anomaly_count} 处价格/状态异常记录，请在归档页优先复核对应单据。")
    if len(suggestions) < 2:
        suggestions.append("当前食材价格波动处于平稳区间。")
    return suggestions[:3]


def generate_insight_cards(cost_summary_dict: dict) -> list[dict]:
    """生成式 UI 卡片：成本异常与价格波动事实总结。"""
    insights = weekly_insights(cost_summary_dict)
    cards = []

    # 1. 价格异动卡片 (Anomaly Card)
    risers = insights.get("top_price_risers", [])
    if risers:
        cards.append({
            "card_type": "anomaly_alert",
            "title": "食材价格大幅异动事实",
            "level": "warning",
            "badge": f"{len(risers)} 项商品涨幅超标",
            "items": [
                {
                    "name": r["name"],
                    "metric": f"+{r['change_pct']}%",
                    "detail": f"单价由 HK${r['earliest_price']} 变动至 HK${r['latest_price']}",
                    "vendor": r.get("vendor") or "主要供应商",
                } for r in risers
            ],
            "action_text": "查看价格走势并核对单据",
        })

    # 2. 总体健康度卡片 (Overview Card)
    cards.append({
        "card_type": "health_summary",
        "title": "食材价格波动概览",
        "level": "success" if not risers else "warning",
        "badge": "正常受控" if not risers else "需关注异动",
        "summary": "、".join(insights.get("suggestions", [])),
        "updated_at": insights.get("updated_at", ""),
    })

    return cards
