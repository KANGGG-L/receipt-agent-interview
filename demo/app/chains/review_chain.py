# -*- coding: utf-8 -*-
"""AI 复盘链：价格异动 / 供应商分析（面试三大业务能力之一）。

输入：库存台账 + 当前单据 → LLM 输出复盘建议（价格异动、SKU 冷启动提示）。
"""

import json
import time

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import build_audit_model

# T12 SSOT：REVIEW_SYSTEM 上收 ai_registry，显式版本加载（登记自原内联文本，字节等价迁移）；
# review 场景 active 保持 v2_0_0_cards 不变，复盘通道固定使用 v2_0_1_inline_parity。
from ai_registry.registry import ai_registry

REVIEW_SYSTEM = ai_registry.get_prompt("review", "v2_0_1_inline_parity")


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
    from app.models import PRICE_ANOMALY_THRESHOLD_PCT
    from app.services.price_anomaly import compute_vs_avg_and_anomaly
    top_price_risers: list = []
    total_impact = 0.0
    alert_count = 0
    items = cost_summary.get("items") or {}

    # U-02：统一 PRD FR-6 口径（最新单价 vs 均价 > PRICE_ANOMALY_THRESHOLD_PCT%），
    # 与库存页 _compute_vs_avg_and_anomaly 共用同一 helper，保证两处数字一致。
    for sku in _db.list_skus():
        vs_pct, is_anomaly, avg_30d, latest = compute_vs_avg_and_anomaly(sku.id)
        if not is_anomaly:
            continue
        alert_count += 1

        info = items.get(sku.name) if isinstance(items.get(sku.name), dict) else {}
        curr_stock = info.get("qty") or sku.current_stock or 0.0
        unit = info.get("unit") or sku.base_unit or "斤"
        vendor = info.get("vendor") or "主要供应商"

        # 溯源最新单据的供应商名
        try:
            logs = _db.price_history(sku.id)
            if logs and logs[-1].vendor:
                vendor = logs[-1].vendor
        except Exception as e:
            import logging
            logging.getLogger("review_chain").warning(f"[WARN] 溯源价格历史失败: {e}")

        # 量化影响金额 = (最新价 - 30天均价) * 当前在库数量
        impact_amount = round(max(0.0, (latest - avg_30d) * curr_stock), 2)
        total_impact += impact_amount

        evidence = (f"{vendor}：【{sku.name}】最新单价 ${latest:.2f} 较均价 ${avg_30d:.2f} "
                    f"上涨 {vs_pct}%（超过 {PRICE_ANOMALY_THRESHOLD_PCT:.0f}% 阈值），"
                    f"按在库数量预估影响成本 HK${impact_amount:.2f}。")

        top_price_risers.append({
            "sku_id": sku.id,
            "name": sku.name,
            "vendor": vendor,
            "earliest_price": avg_30d,
            "latest_price": latest,
            "change_pct": vs_pct,
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
        anomaly_count = alert_count

    suggestions = _gen_suggestions(top_price_risers, anomaly_count)

    payload = {
        "has_alerts": len(top_price_risers) > 0,
        "total_impact_amount": round(total_impact, 2),
        "alert_count": alert_count,
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
    from app.models import PRICE_ANOMALY_THRESHOLD_PCT
    suggestions: list = []
    if top_risers:
        names = "、".join(r["name"] for r in top_risers[:2])
        suggestions.append(f"「{names}」最新单价较均价涨幅超过 {PRICE_ANOMALY_THRESHOLD_PCT:.0f}%，请在归档页核对调价记录。")
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
