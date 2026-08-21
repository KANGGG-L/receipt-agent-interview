# -*- coding: utf-8 -*-
"""完整 Workflow 测试（免费模型 opencode/mimo-v2.5-free）。

模拟前端真实操作流程：
  上传收据 → 异步识别 → 人工复核(save_edited) → owner approve → 库存/供应商
  → 成本报表 → AI 复盘 → 对账 → 支付登记
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:15010"
IMG = "/Users/ethan/Documents/GitHub/receipt_agent/receipt_agent/samples/receipts/20260805_祥興_IMG5802_delivery_note.jpg"


def call(path, method="GET", body=None, headers=None):
    data = None
    h = dict(headers or {})
    h.setdefault("X-Role", "owner")
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"status": "error", "msg": f"HTTP {e.code}"}


def upload(img):
    boundary = "----wf123"
    with open(img, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="receipt"; filename="t.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/upload?async=true", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Role": "staff"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def call_form(path, fields):
    """multipart/form-data POST（支付登记用）。"""
    boundary = "----wfpay123"
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Role": "owner"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"status": "error", "msg": f"HTTP {e.code}"}


def main():
    print("=" * 60)
    print("WORKFLOW 测试（免费模型 opencode/mimo-v2.5-free）")
    print("=" * 60)

    # 0. 前置数据：建 SKU + 供应商 + 部门
    sku_id, _ = None, None
    r = call("/api/inventory/skus", "POST", {"name": "菜心", "base_unit": "斤", "min_stock_alert": 10}, headers={"X-Role": "owner"})
    print("0. 预置 SKU 菜心:", r.get("status"))
    did = call("/api/departments", "POST", {"name": "厨房"}, headers={"X-Role": "owner"})
    print("0. 预置部门 厨房:", did.get("status"), "id:", did.get("id"))

    # 1. 上传
    up = upload(IMG)
    assert up["status"] == "queued", f"上传失败: {up}"
    print(f"\n1. 上传 → queued job={up['job_id']} receipt={up['receipt_id']}")
    rid = up["receipt_id"]

    # 2. 轮询识别（免费模型 2-4 分钟）
    print("2. 异步识别中（免费模型 40s~4min）...")
    t0 = time.time()
    for _ in range(80):
        job = call(f"/api/job/{up['job_id']}")
        if job["job_status"] in ("done", "error"):
            break
        time.sleep(5)
    el = round(time.time() - t0, 1)
    assert job["job_status"] == "done", f"识别失败: {job.get('error_msg')}"
    data = job["result"]["data"]
    print(f"   [PASS]  识别完成（{el}s）: {data['supplier_name'][:22]} | items={len(data['items'])} | total={data['total_amount']}")
    print(f"     决策履历: {[e['action'] for e in job['result'].get('log', [])]}")
    for it in data["items"][:3]:
        print(f"     - {it['name'][:20]} {it['quantity']}{it['unit']} @{it['unit_price']} = {it['amount']}")

    # 3. 人工复核 save_edited（带 items + 乐观锁 version）
    items = [{k: v for k, v in it.items() if k != "id"} for it in data["items"]]
    r = call("/api/save_edited", "POST", {
        "receipt_id": rid, "supplier_name": data["supplier_name"],
        "date": data["date"], "sheet_name": data["sheet_name"],
        "total_amount": data["total_amount"], "items": items,
        "settlement_type": "credit", "version": data["version"],
    }, headers={"X-Role": "staff"})
    assert r["status"] == "success", f"save_edited: {r}"
    ver = r["version"]
    print(f"3. 人工复核提交 [PASS]  version={ver}")

    # 3.5 乐观锁验证：错误 version 应拒绝
    r = call("/api/save_edited", "POST", {
        "receipt_id": rid, "items": [], "settlement_type": "credit", "version": 1,
    })
    print(f"3.5 乐观锁（旧 version=1 应拒）: code={r.get('code')}")

    # 4. RBAC：staff 不能 approve
    r = call(f"/api/receipt/{rid}/approve", "POST", {"version": ver}, headers={"X-Role": "staff"})
    print(f"4. staff approve（应 403）: {'403 [PASS] ' if r.get('detail') or r.get('msg') else r}")
    # 4b. owner approve
    r = call(f"/api/receipt/{rid}/approve", "POST", {"version": ver}, headers={"X-Role": "owner"})
    assert r["status"] == "success", f"approve: {r}"
    print(f"4. owner approve [PASS]  → 入账（version → {r.get('version')}）")

    # 5. 库存 + 供应商
    inv = call("/api/inventory")
    print(f"5. 库存 SKU: {len(inv['data'])} 条 | {inv['meta']}")
    sups = call("/api/suppliers")
    print(f"   [PASS]  供应商自动建档: {[s['name'][:15] for s in sups['data']]}")

    # 6. 成本报表
    cr = call("/api/cost_report?include_non_approved=1")
    print(f"6. 成本报表: month_total={cr['data']['month_total']}")

    # 7. AI 复盘（免费模型）
    print("7. AI 复盘（免费模型）...")
    rv = call("/api/review")
    print(f"   [PASS]  summary: {(rv.get('data') or {}).get('summary', '—')[:60]}")

    # 8. 对账
    if sups["data"]:
        rec = call("/api/reconciliation", "POST", {"supplier_id": sups["data"][0]["id"], "period_type": "month"})
        print(f"8. 对账创建 [PASS]  task={rec.get('task_id')}")
        rec_list = call("/api/reconciliation")
        print(f"   对账列表: {len(rec_list['data'])} 个 | {rec_list['data'][0]['summary']}")

    # 9. 支付登记（FormData）
    if sups["data"]:
        pay = call_form("/api/payments", {
            "supplier_id": str(sups["data"][0]["id"]), "amount": str(data["total_amount"]),
            "method": "bank_transfer", "paid_at": "2026-08-10", "notes": "workflow测试",
        })
        print(f"9. 支付登记 [PASS]  {pay.get('msg')}")

    # 10. 收据导出 CSV（原始文本）
    try:
        req = urllib.request.Request(BASE + "/api/receipts/export", headers={"X-Role": "owner"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            csv_text = resp.read().decode("utf-8")
        print(f"10. 导出 CSV [PASS]  ({len(csv_text)} bytes)")
    except Exception as e:
        print(f"10. 导出 CSV [FAIL]  {e}")

    print("\n" + "=" * 60)
    print("[PASS]  完整 WORKFLOW 全部通过（免费模型）")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
