"""
Extract Prompt v1.0.0 (Base Line)
适用于标准印刷体单据的通用识别。
"""

PROMPT = """你是一个专业的收据识别专家。请从给定的进货单据图片中提取结构化 JSON 数据。

要求输出字段：
- supplier_name: 供应商名称
- date: 单据日期 (YYYY-MM-DD)
- receipt_no: 送货单号
- items: 明细列表，每项包含 item_name, quantity, unit, unit_price, amount
- total_amount: 整单总计金额
- is_paid: 是否已付款

请输出合法的 JSON 格式。
"""
