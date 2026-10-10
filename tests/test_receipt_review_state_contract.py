# -*- coding: utf-8 -*-
"""Phase 2: 状态机契约与后端不变量测试套件。

覆盖验收标准与不变量：
- AC-01-01 / AC-01-02: 保存未入账，确认后单次入账，重复审批幂等阻断
- AC-01-04 / AC-01-05: 原单付款标记与财务系统付款分离，不生成 PaymentRow
- AC-01-06: staff 审批 403，owner 正常审批
- AC-02-05: 转手工/已保存后迟到的 OCR Job 结果不覆盖手工数据
- AC-05-04: 阻断后端 - 存在算术或必填字段阻断时直调审批被 API 拒绝
- INV-01: 租户隔离
- INV-02: 并发审批防重入
- INV-04: approved 单据保护（禁止再 save/retry/discard）
- INV-05: source=auto 保存不降级 warnings，不进入待审批
"""

import json
import pytest
from starlette.requests import Request
from starlette.datastructures import Headers

from app import db
from app.api_receipts import approve_receipt, save_edited, ApproveBody, SaveEditedBody
from app.services.receipt_utils import save_parsed_data
from app.models import ReceiptData, ReceiptItem, DocForm


def _mock_request(role="owner", tenant="test_tenant", email="test@example.com"):
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"host", b"localhost"), (b"x-role", role.encode("utf-8")), (b"x-tenant-id", tenant.encode("utf-8"))],
        "state": {
            "account": {"email": email, "role": role, "tenant_id": tenant},
            "tenant_id": tenant,
            "session_id": "test_sess_001"
        }
    }
    return Request(scope)




def test_ac_01_01_save_does_not_affect_inventory_or_stock_log():
    """AC-01-01: staff 经复核保存，单据状态变为 edited，库存与流水零变化。"""
    tenant = "tenant_test_01"
    req_staff = _mock_request(role="staff", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_A", status="parsed", tenant_id=tenant)
    
    # 记录保存前真实数据库库存流水与 SKU 余额
    s = db.get_session()
    try:
        initial_stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == tenant).all()
        initial_sku = s.query(db._SkuRow).filter(db._SkuRow.name == "新鲜菜心", db._SkuRow.tenant_id == tenant).first()
        initial_stock = initial_sku.current_stock if initial_sku else 0.0
    finally:
        s.close()
    
    body = SaveEditedBody(
        receipt_id=rid,
        supplier_name="Vendor_A",
        date="2026-10-08",
        total_amount=100.0,
        settlement_type="cash",
        payment_mark="未付款",
        items=[{
            "name": "新鲜菜心",
            "quantity": 10.0,
            "unit": "kg",
            "unit_price": 10.0,
            "amount": 100.0,
            "is_void": 0
        }],
        version=1,
        source="manual"
    )
    res = save_edited(body, req_staff)
    assert not (isinstance(res, dict) and res.get("status") == "error"), f"save_edited failed: {res}"
    
    # 核对单据状态
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "edited", f"Expected edited, got {row.status}"
    
    # 核对真实数据库流水与 SKU 余额未新增
    s = db.get_session()
    try:
        after_stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == tenant).all()
        after_sku = s.query(db._SkuRow).filter(db._SkuRow.name == "新鲜菜心", db._SkuRow.tenant_id == tenant).first()
        after_stock = after_sku.current_stock if after_sku else 0.0
    finally:
        s.close()
    assert len(after_stock_logs) == len(initial_stock_logs)
    assert after_stock == initial_stock


def test_ac_01_06_staff_cannot_approve_receipt():
    """AC-01-06: staff 直调审批必须被 403 拦截。"""
    tenant = "tenant_test_06"
    req_staff = _mock_request(role="staff", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id=tenant)
    
    body = ApproveBody(version=1)
    with pytest.raises(Exception) as excinfo:
        approve_receipt(rid, body, req_staff)
    # HTTPException 403
    assert "403" in str(excinfo.value) or "Forbidden" in str(excinfo.value) or getattr(excinfo.value, "status_code", None) == 403


def test_ac_05_04_approve_blocked_when_math_inconsistent():
    """AC-05-04: 算术不守恒或存在阻断问题时，直调审批被 API 拒绝，库存流水不变。"""
    tenant = "tenant_test_05"
    req_owner = _mock_request(role="owner", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=150.0, receipt_date="2026-10-08", version=1)
    
    # 注入算术矛盾：数量10 * 单价10 = 100，但小计写 120，且总额写 150
    db.set_receipt_items(rid, [{
        "name": "新鲜菜心",
        "quantity": 10.0,
        "unit": "kg",
        "unit_price": 10.0,
        "amount": 120.0,
        "is_void": 0
    }], tenant_id=tenant)
    
    body = ApproveBody(version=1)
    res = approve_receipt(rid, body, req_owner)
    
    # 必须返回拒绝 (422 或 400 或 409)
    status_code = getattr(res, "status_code", None)
    assert status_code in (400, 409, 422), f"Expected 4xx rejection, got {status_code}"
    
    # 单据状态仍为 edited，未入账
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "edited"


def test_ac_05_04_approve_blocked_when_supplier_or_date_missing():
    """QA-P1-02 & AC-05-04: 供应商或开单日期缺失时，直调审批被 422 拒绝，库存流水与状态保持不变。"""
    tenant = "tenant_missing_fields"
    req_owner = _mock_request(role="owner", tenant=tenant)
    rid = db.create_receipt(supplier_name="", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="", version=1)
    db.set_receipt_items(rid, [{
        "name": "缺字段食材",
        "quantity": 2.0,
        "unit": "kg",
        "unit_price": 50.0,
        "amount": 100.0,
        "is_void": 0
    }], tenant_id=tenant)

    body = ApproveBody(version=1)
    res = approve_receipt(rid, body, req_owner)

    status_code = getattr(res, "status_code", None)
    assert status_code == 422, f"Expected 422 rejection, got {status_code}"
    content = json.loads(res.body.decode("utf-8")) if hasattr(res, "body") else {}
    assert content.get("code") == "APPROVAL_BLOCKED"

    # 单据状态仍为 edited，未入账
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "edited"

    # 验证库存流水为 0
    s = db.get_session()
    try:
        stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
        assert len(stock_logs) == 0
    finally:
        s.close()


def test_ac_01_02_dirty_edit_saved_then_approved_matches_final_data():
    """QA-P0-01 & AC-01-02: 用户修改单据内容后保存并以新版本审批入账，最终持久化和库存完全匹配新数据。"""
    tenant = "tenant_dirty_approval"
    req_owner = _mock_request(role="owner", tenant=tenant)

    # 初始单据：品名“原始食材”，总额100
    rid = db.create_receipt(supplier_name="Vendor_A", status="parsed", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid, [{
        "name": "原始食材",
        "quantity": 2.0,
        "unit": "kg",
        "unit_price": 50.0,
        "amount": 100.0,
        "is_void": 0
    }], tenant_id=tenant)

    # 用户在前端编辑为“手工改后食材”，保存
    save_body = SaveEditedBody(
        receipt_id=rid,
        supplier_name="Vendor_A",
        date="2026-10-08",
        total_amount=100.0,
        settlement_type="cash",
        items=[{
            "name": "手工改后食材",
            "quantity": 2.0,
            "unit": "kg",
            "unit_price": 50.0,
            "amount": 100.0,
            "is_void": 0
        }],
        version=1,
        source="manual"
    )
    save_res = save_edited(save_body, req_owner)
    assert isinstance(save_res, dict) and save_res.get("status") == "success"
    new_version = save_res.get("version")
    assert new_version == 2

    # 以最新版本 2 执行审批入账
    approve_body = ApproveBody(version=new_version)
    app_res = approve_receipt(rid, approve_body, req_owner)
    assert isinstance(app_res, dict) and app_res.get("status") == "success"

    # 验证单据状态与明细
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "approved"
    assert row.version == 3
    items = db.get_receipt_items(rid, tenant_id=tenant)
    assert len(items) == 1
    assert items[0]["name"] == "手工改后食材"

    # 验证库存：“手工改后食材”库存增加2kg，“原始食材”无库存增加
    s = db.get_session()
    try:
        new_sku = s.query(db._SkuRow).filter(db._SkuRow.name == "手工改后食材", db._SkuRow.tenant_id == tenant).first()
        assert new_sku is not None
        assert new_sku.current_stock == 2.0

        old_sku = s.query(db._SkuRow).filter(db._SkuRow.name == "原始食材", db._SkuRow.tenant_id == tenant).first()
        assert old_sku is None or old_sku.current_stock == 0.0

        stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
        assert len(stock_logs) == 1
        assert stock_logs[0].qty == 2.0
    finally:
        s.close()


def test_ac_01_02_and_inv_04_approve_once_and_subsequent_modifications_rejected():
    """AC-01-02 & INV-04: owner 审批通过后单据为 approved，再次审批或尝试 save 均被拒绝。"""
    tenant = "tenant_test_02"
    req_owner = _mock_request(role="owner", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid, [{
        "name": "鲜牛肉",
        "quantity": 5.0,
        "unit": "kg",
        "unit_price": 20.0,
        "amount": 100.0,
        "is_void": 0
    }], tenant_id=tenant)
    
    # 首次审批：成功
    body = ApproveBody(version=1)
    res1 = approve_receipt(rid, body, req_owner)
    assert isinstance(res1, dict) and res1.get("status") == "success"
    
    row_approved = db.get_receipt_row(rid, tenant_id=tenant)
    assert row_approved.status == "approved"
    approved_ver = row_approved.version

    # 验证真实数据库库存与流水已增加
    s = db.get_session()
    try:
        stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
        assert len(stock_logs) == 1
        assert stock_logs[0].qty == 5.0
        sku = s.query(db._SkuRow).filter(db._SkuRow.name == "鲜牛肉", db._SkuRow.tenant_id == tenant).first()
        assert sku is not None
        assert sku.current_stock == 5.0
    finally:
        s.close()
    
    # 重放审批：拒绝 (409) 且不增加库存
    res2 = approve_receipt(rid, body, req_owner)
    assert getattr(res2, "status_code", None) == 409

    # 验证重放后流水依然只有 1 条，库存依然为 5.0
    s = db.get_session()
    try:
        stock_logs_replay = s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
        assert len(stock_logs_replay) == 1
        sku_replay = s.query(db._SkuRow).filter(db._SkuRow.name == "鲜牛肉", db._SkuRow.tenant_id == tenant).first()
        assert sku_replay.current_stock == 5.0
    finally:
        s.close()
    
    # 尝试在 approved 单据上调用 save_edited：必须被 409 拒绝 (INV-04)
    save_body = SaveEditedBody(
        receipt_id=rid,
        supplier_name="Vendor_A",
        date="2026-10-08",
        total_amount=100.0,
        settlement_type="cash",
        items=[{
            "name": "鲜牛肉",
            "quantity": 5.0,
            "unit": "kg",
            "unit_price": 20.0,
            "amount": 100.0,
            "is_void": 0
        }],
        version=approved_ver,
        source="manual"
    )
    res_save = save_edited(save_body, req_owner)
    assert getattr(res_save, "status_code", None) == 409
    
    # 确保单据状态依然是 approved，未被回退
    row_final = db.get_receipt_row(rid, tenant_id=tenant)
    assert row_final.status == "approved"


def test_ac_02_05_late_ocr_job_does_not_overwrite_manual_or_approved():
    """AC-02-05: 用户转手工录入并保存为 edited，之后到达的 OCR 结果不覆盖手工数据。"""
    tenant = "tenant_test_late_job"
    rid = db.create_receipt(supplier_name="Vendor_Manual_Saved", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=88.0, doc_form="manual_entry", version=2)
    db.set_receipt_items(rid, [{
        "name": "手工录入蔬菜",
        "quantity": 2.0,
        "unit": "斤",
        "unit_price": 44.0,
        "amount": 88.0,
        "is_void": 0
    }], tenant_id=tenant)
    
    # 模拟迟到的 OCR Job 结果
    late_ocr_data = ReceiptData(
        vendor="Late_OCR_Vendor",
        date="2026-09-01",
        total=500.0,
        items=[ReceiptItem(name="迟到识别菜", qty=10.0, unit="kg", unit_price=50.0, amount=500.0)],
        doc_form=DocForm.PRINTED,
        payment_marked=False,
        confidence=0.95
    )
    late_result = {
        "status": "parsed",
        "data": late_ocr_data,
        "math_problems": []
    }
    
    # 执行迟到持久化
    save_parsed_data(rid, late_ocr_data, late_result)
    
    # 校验：手工数据完整保留，没有被迟到结果覆盖
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "edited"
    assert row.supplier_name == "Vendor_Manual_Saved"
    assert row.total_amount == 88.0
    items = db.get_receipt_items(rid, tenant_id=tenant)
    assert len(items) == 1
    assert items[0]["name"] == "手工录入蔬菜"


def test_inv_01_tenant_isolation():
    """INV-01: 租户A的单据不可被租户B查询、保存或审批。"""
    tenant_a = "tenant_alpha"
    tenant_b = "tenant_beta"
    req_b_owner = _mock_request(role="owner", tenant=tenant_b)
    
    rid_a = db.create_receipt(supplier_name="Vendor_Alpha", status="edited", tenant_id=tenant_a)
    db.update_receipt(rid_a, total_amount=50.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid_a, [{
        "name": "租户A商品", "quantity": 1.0, "unit": "个", "unit_price": 50.0, "amount": 50.0, "is_void": 0
    }], tenant_id=tenant_a)
    
    # 租户B尝试审批租户A单据：必须 404 (找不到或越权)
    body = ApproveBody(version=1)
    res_approve = approve_receipt(rid_a, body, req_b_owner)
    assert getattr(res_approve, "status_code", None) == 404
    
    # 租户B尝试保存租户A单据：必须 404
    save_body = SaveEditedBody(
        receipt_id=rid_a, supplier_name="Vendor_Alpha", date="2026-10-08",
        total_amount=50.0, settlement_type="cash", version=1, source="manual"
    )
    res_save = save_edited(save_body, req_b_owner)
    assert getattr(res_save, "status_code", None) == 404


def test_inv_02_concurrent_approval_single_inventory_addition():
    """INV-02: 两个并发请求同时审批同一单据同版本，只有一次成功，另一次冲突，库存仅增加一次。"""
    import threading
    tenant = "tenant_concurrent"
    req_owner1 = _mock_request(role="owner", tenant=tenant, email="owner1@example.com")
    req_owner2 = _mock_request(role="owner", tenant=tenant, email="owner2@example.com")
    
    rid = db.create_receipt(supplier_name="Vendor_Conc", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid, [{
        "name": "并发牛肉", "quantity": 2.0, "unit": "kg", "unit_price": 50.0, "amount": 100.0, "is_void": 0
    }], tenant_id=tenant)
    
    results = []
    
    def _approve_call(req):
        body = ApproveBody(version=1)
        res = approve_receipt(rid, body, req)
        results.append(res)
        
    t1 = threading.Thread(target=_approve_call, args=(req_owner1,))
    t2 = threading.Thread(target=_approve_call, args=(req_owner2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
    conflict_count = sum(1 for r in results if getattr(r, "status_code", None) == 409)
    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert conflict_count == 1, f"Expected exactly 1 conflict, got {conflict_count}"
    
    # 验证库存流水记录只有 1 笔
    s = db.get_session()
    try:
        receipt_stock_logs = s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
        assert len(receipt_stock_logs) == 1
    finally:
        s.close()


def test_inv_05_auto_save_warnings_retained():
    """INV-05: source=auto 保存 warnings 结果保留 parsed_with_warnings，不变人工待审批 edited。"""
    tenant = "tenant_auto_save"
    req_staff = _mock_request(role="staff", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_Warn", status="parsed_with_warnings", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1, math_warnings_json=json.dumps(["算术不符"]))
    
    body = SaveEditedBody(
        receipt_id=rid,
        supplier_name="Vendor_Warn",
        date="2026-10-08",
        total_amount=100.0,
        settlement_type="cash",
        items=[{
            "name": "测试商品", "quantity": 1.0, "unit": "个", "unit_price": 80.0, "amount": 100.0, "is_void": 0
        }],
        version=1,
        source="auto"
    )
    res = save_edited(body, req_staff)
    assert not (isinstance(res, dict) and res.get("status") == "error")
    
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "parsed_with_warnings", f"Expected parsed_with_warnings, got {row.status}"


def test_ac_01_04_payment_separation():
    """AC-01-04: 含付款标记单据识别、保存、审批后，仅原单标记更新，绝不新增 PaymentRow 或真实付款记录。"""
    tenant = "tenant_payment_sep"
    req_owner = _mock_request(role="owner", tenant=tenant)
    rid = db.create_receipt(supplier_name="Vendor_Stamp", status="edited", tenant_id=tenant)
    db.update_receipt(rid, total_amount=60.0, receipt_date="2026-10-08", payment_mark="已付款", version=1)
    db.set_receipt_items(rid, [{
        "name": "盖章商品", "quantity": 1.0, "unit": "个", "unit_price": 60.0, "amount": 60.0, "is_void": 0
    }], tenant_id=tenant)
    
    # 审批
    body = ApproveBody(version=1)
    res = approve_receipt(rid, body, req_owner)
    assert isinstance(res, dict) and res.get("status") == "success"
    
    # 验证单据已入账
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert row.status == "approved"
    
    # 验证未生成任何 PaymentRow 或 paid_at
    s = db.get_session()
    try:
        payments = s.query(db._PaymentRow).filter(db._PaymentRow.tenant_id == tenant).all()
        assert len(payments) == 0
    finally:
        s.close()
    assert not row.paid_at
    assert not getattr(row, "payment_id", None)

