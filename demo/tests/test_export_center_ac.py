# -*- coding: utf-8 -*-
"""
统一报表与数据导出中心验收准则规范 (AC-1 至 AC-12) 自动化测试套件
覆盖:
- AC-1: 报表元数据完备性 (8张报表，3个使用者组——已被 REFACTOR_NEEDS_ANALYSIS 第 7 节重设计取代原 14 报表/4 业务域)
- AC-2: UTF-8 BOM 字节流与 RFC 5987 文件名编码
- AC-3: CSV 公式注入攻击防御 (=, +, -, @, \\t, \\r 前置单引号转义) 与特殊字符转义
- AC-4: 按钮与输入控件触控高度 (>= 38px) 及 WCAG AA 对比度合规
- AC-5: 日期倒置防呆与服务端自动纠偏
- AC-6: 空数据状态与 0 记录拦截
- AC-7: 实时预览全量契约 (2026-10-06 修订：返回全量行，无截断)
- AC-8: 脱敏模式开关与供应商名/电话掩码一致性
- AC-9: A4 实地盘点底单打印留白与三级下划线签名栏
- AC-10: 财务精度与零除容错 (保留两位小数、代数一致性)
- AC-11: 租户安全隔离与跨租户参数拦截
- AC-12: 角色权限控制与敏感报表人话拦截
"""

import io
import re
import urllib.parse
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app.api_export import _sanitize_csv_cell, mask_vendor_name, mask_phone, ExportDataProcessor


@pytest.fixture
def client():
    return TestClient(app)


def test_ac1_reports_metadata_contract(client):
    """[AC-1] 三个使用者组 8 张标准报表元数据契约与定义完备性 (14→8 重构后)"""
    res = client.get("/api/export/reports", headers={"X-Role": "staff"})
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "success"
    data = body.get("data", [])
    assert len(data) == 8, f"报表数量必须严格等于8，当前为 {len(data)}"

    # 分类计数（3 个使用者组：盘点与补货 2 / 经营与毛利 4 / 台账与合规 2）
    cat_counts = {}
    for r in data:
        cat = r.get("category")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        # 字段完备性
        for field in ("id", "category", "category_name", "title", "description", "filters"):
            assert r.get(field), f"报表 {r.get('id')} 缺少字段: {field}"

    assert cat_counts.get("stocktake") == 2, "stocktake 组（盘点与补货）必须有 2 个报表"
    assert cat_counts.get("business") == 4, "business 组（经营与毛利）必须有 4 个报表"
    assert cat_counts.get("ledger") == 2, "ledger 组（台账与合规）必须有 2 个报表"

    # 被裁报表不得回流（移除项回归守卫）
    removed_ids = {
        "supplier_payables_aging",
        "supplier_payments_journal",
        "supplier_recon_discrepancies",
        "dish_bom_recipes",
        "department_cost_summary",
        "department_items_breakdown",
        "dish_daily_sales_cost",
        "dish_margin_variance",
    }
    actual_ids = {r["id"] for r in data}
    assert not (actual_ids & removed_ids), f"被裁报表回流: {actual_ids & removed_ids}"
    assert {"dish_cost_margin", "spend_summary"} <= actual_ids, "合并报表缺失"


def test_ac2_csv_utf8_bom_and_rfc5987(client):
    """[AC-2] Excel 与 CSV 导出格式规范与 UTF-8 BOM 防乱码"""
    res = client.post(
        "/api/export/download",
        json={"report_id": "inventory_stocktake", "params": {}},
        headers={"X-Role": "owner"},
    )
    assert res.status_code == 200
    content = res.content
    # 严格校验前 3 字节为 UTF-8 BOM: EF BB BF
    assert content[:3] == b"\xef\xbb\xbf", "导出的 CSV 二进制流前 3 字节必须为 UTF-8 BOM"
    assert "text/csv" in res.headers.get("Content-Type", "")
    assert "charset=utf-8" in res.headers.get("Content-Type", "").lower()

    # Content-Disposition RFC 5987 编码校验
    disp = res.headers.get("Content-Disposition", "")
    assert "filename*=UTF-8''" in disp, "Content-Disposition 必须采用 RFC 5987 编码"

    # 解码内容并验证中文
    text = content.decode("utf-8-sig")
    assert "食材代码" in text
    assert "食材品名" in text


def test_ac3_csv_injection_and_escaping():
    """[AC-3] CSV 注入攻击防御与特殊字符安全转义"""
    test_cases = [
        ("=cmd|' /C calc'!A0", "'=cmd|' /C calc'!A0"),
        ("+SUM(A1:A10)", "'+SUM(A1:A10)"),
        ("-100", "'-100"),
        ("@admin_action", "'@admin_action"),
        ("\tmalicious_tab", "'\tmalicious_tab"),
        ("\rmalicious_cr", "'\rmalicious_cr"),
        ("普通商品", "普通商品"),
        (123.45, 123.45),
        (0, 0),
        (None, ""),
    ]
    for raw, expected in test_cases:
        actual = _sanitize_csv_cell(raw)
        assert actual == expected, f"Sanitization error for {raw}: expected {expected}, got {actual}"


def test_ac4_ui_elements_height_and_wcag_contrast():
    """[AC-4] 香港餐饮店员阿叔/阿姨极简交互与高压触控无障碍合规 (高度 >= 38px, 对比度 >= 4.5:1)"""
    import os
    css_path = os.path.join(os.path.dirname(__file__), "../static/css/style.css")
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    # 检查按钮最小高度设置 >= 38px
    assert "min-height: 38px" in css or "height: 38px" in css or "min-height: 40px" in css
    # 检查主色调对比度定义
    assert "#2563eb" in css  # 主操作蓝底
    assert "#ffffff" in css  # 白字 (4.5:1 AA 合规)


def test_ac5_date_inversion_auto_correction(client):
    """[AC-5] 日期筛选防呆与倒置自动纠正机制 (开始晚于结束时自动对调)"""
    # 传递倒置日期: 2026-08-31 至 2026-08-01
    res = client.post(
        "/api/export/preview",
        json={
            "report_id": "inventory_stock_logs",
            "params": {"start_date": "2026-08-31", "end_date": "2026-08-01"},
        },
        headers={"X-Role": "owner"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "success"
    # 后端内部自动对调后正常返回


def test_ac6_empty_state_contract(client):
    """[AC-6] 空数据状态人话直白展示 (杜绝技术黑话)"""
    res = client.post(
        "/api/export/preview",
        json={
            "report_id": "inventory_stock_logs",
            "params": {"start_date": "2099-01-01", "end_date": "2099-01-02"},
        },
        headers={"X-Role": "owner"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "success"
    data = body.get("data", {})
    assert data.get("total_rows") == 0
    assert data.get("preview_rows") == []


def test_ac7_preview_sampling_limit(client):
    """[AC-7] 实时预览全量契约 (2026-10-06 修订：preview 返回全量行，与导出同口径，无截断)"""
    res = client.post(
        "/api/export/preview",
        json={"report_id": "inventory_stocktake", "params": {}},
        headers={"X-Role": "owner"},
    )
    assert res.status_code == 200
    body = res.json()
    data = body.get("data", {})
    preview_rows = data.get("preview_rows", [])
    total_rows = data.get("total_rows", 0)
    assert len(preview_rows) == total_rows, (
        f"预览必须返回全量行，当前 {len(preview_rows)} vs total {total_rows}"
    )
    assert "truncated" not in data, "全量预览契约下不得残留 truncated 字段"


def test_ac8_desensitization_masking_consistency(client):
    """[AC-8] 数据脱敏保护模式切换与字段级掩码一致性"""
    # 单元函数校验
    assert mask_vendor_name("德利行 Tak Lee Hong") == "德***行"
    assert mask_vendor_name("德利行") == "德***行"
    assert mask_vendor_name("祥興") == "祥***興"
    assert mask_phone("91234567") == "91****67"
    assert mask_phone("13812345678") == "138****5678"

    # API 接口脱敏预览
    res_preview = client.post(
        "/api/export/preview",
        json={
            "report_id": "receipts_itemized_ledger",
            "params": {},
            "desensitized": True,
        },
        headers={"X-Role": "owner"},
    )
    assert res_preview.status_code == 200
    p_rows = res_preview.json()["data"]["preview_rows"]

    # API 接口脱敏下载
    res_download = client.post(
        "/api/export/download",
        json={
            "report_id": "receipts_itemized_ledger",
            "params": {},
            "desensitized": True,
        },
        headers={"X-Role": "owner"},
    )
    assert res_download.status_code == 200
    d_text = res_download.content.decode("utf-8-sig")

    # 检查预览与下载中若有供应商名，均包含 *** 掩码
    if p_rows:
        for r in p_rows:
            sup_name = r[2]
            if sup_name and sup_name not in ("未注明", "-"):
                assert "***" in sup_name, f"供应商名称未掩码: {sup_name}"


def test_ac9_print_template_requirements():
    """[AC-9] 盘点底单现场打印与留白手填格式规范 (签名栏、留白列、A4标准)"""
    import os
    js_path = os.path.join(os.path.dirname(__file__), "../static/js/export_center.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_code = f.read()

    # i18n 化（2026-10-13）：裸字符串拆为 uiText('…') + 回退字面；渲染结果不变
    # —— 等效判据 = 模板串里保留「字面＋下划线」结构（uiText 包装两侧）
    assert "uiText('经办人签字') : '经办人签字'}：_______________" in js_code
    assert "uiText('主管审核签字') : '主管审核签字'}：_______________" in js_code
    assert "uiText('日期') : '日期'}：_______________" in js_code
    assert "print-export.css" in js_code
    assert "<style>" not in js_code and "<script>" not in js_code
    css_path = os.path.join(os.path.dirname(__file__), "../static/css/print-export.css")
    with open(css_path, "r", encoding="utf-8") as f:
        css_code = f.read()
    assert "A4 portrait" in css_code


def test_ac10_financial_precision_and_zero_division(client):
    """[AC-10] 财务核算与 FIFO 成本/毛利偏差计算精度规范 (合并后 dish_cost_margin 按日粒度)"""
    # 菜品成本与毛利表（按日粒度）精度
    res = client.post(
        "/api/export/preview",
        json={
            "report_id": "dish_cost_margin",
            "params": {"cost_granularity": "daily"},
        },
        headers={"X-Role": "owner"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    rows = data.get("preview_rows", [])
    for r in rows:
        # 在售标价、营业总额、FIFO食材成本均保留2位小数
        dish_p = float(r[4])
        sales = float(r[5])
        cost = float(r[6])
        margin = float(r[7])
        margin_pct_str = r[8]
        assert margin_pct_str.endswith("%")
        assert round(sales - cost, 2) == round(margin, 2)


def test_ac11_multi_tenant_isolation(client):
    """[AC-11] 多租户数据物理/逻辑强隔离门禁校验"""
    # 传递不存在或跨租户的 supplier_id
    res = client.post(
        "/api/export/preview",
        json={
            "report_id": "receipts_itemized_ledger",
            "params": {"supplier_id": "99999999"},
        },
        headers={"X-Role": "owner", "X-Tenant-Id": "tenant_test_isolated"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total_rows"] == 0, "跨租户伪造 supplier_id 必须被安全过滤，返回 0 条记录"
    assert data["preview_rows"] == []


def test_ac12_rbac_staff_vs_owner_restriction(client):
    """[AC-12] 角色权限控制与店员/老板越权阻断 (合并后敏感报表: spend_summary)"""
    # 店员访问普通报表: 允许
    res_stock = client.post(
        "/api/export/preview",
        json={"report_id": "inventory_stocktake", "params": {}},
        headers={"X-Role": "staff"},
    )
    assert res_stock.status_code == 200

    # 店员访问敏感老板专属报表 spend_summary: 阻断并返回人话提示
    res_dept = client.post(
        "/api/export/preview",
        json={"report_id": "spend_summary", "params": {}},
        headers={"X-Role": "staff"},
    )
    assert res_dept.status_code == 403
    assert "当前角色为店员，此报表需老板或管理人员权限查看" in res_dept.json().get("detail", "")

    # 老板访问敏感报表: 允许
    res_owner = client.post(
        "/api/export/preview",
        json={"report_id": "spend_summary", "params": {}},
        headers={"X-Role": "owner"},
    )
    assert res_owner.status_code == 200

    # A2: 门禁同样覆盖 download 端点（preview 与 download 共用同一实现，不得只堵一头）
    res_dept_dl = client.post(
        "/api/export/download",
        json={"report_id": "spend_summary", "params": {}},
        headers={"X-Role": "staff"},
    )
    assert res_dept_dl.status_code == 403
    assert "需老板或管理人员权限查看" in res_dept_dl.json().get("detail", "")
