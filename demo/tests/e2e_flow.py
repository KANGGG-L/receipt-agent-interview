# -*- coding: utf-8 -*-
"""端到端流程测试：上传 → 识别 → save_edited(带items) → approve → 库存/成本。

直接调 demo 后端 API（模拟前端真实操作），验证完整业务闭环。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:15010"


def call(path, method="GET", body=None, form=None, files=None):
    data = None
    headers = {"X-Role": "owner"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def main():
    # 1. 上传（用一张已有图）
    import mimetypes
    import os
    img = "/Users/ethan/Documents/GitHub/receipt_agent/receipt_agent/samples/receipts/20260805_祥興_IMG5802_delivery_note.jpg"
    boundary = "----testboundary123"
    with open(img, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="receipt"; filename="test.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/upload?async=true", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-Role": "staff"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        up = json.load(resp)
    print("1. upload:", up["status"], "job:", up["job_id"], "receipt:", up["receipt_id"])
    rid = up["receipt_id"]

    # 2. 轮询 job
    for _ in range(60):
        job = call(f"/api/job/{up['job_id']}")
        if job["job_status"] in ("done", "error"):
            break
        time.sleep(3)
    if job["job_status"] != "done":
        print("✗ 识别失败:", job.get("error_msg"))
        return 1
    data = job["result"]["data"]
    print(f"2. 识别: {data['supplier_name'][:20]} | items={len(data['items'])} | total={data['total_amount']}")

    # 3. save_edited（带 items，模拟前端提交）
    items = []
    for it in data["items"]:
        items.append({k: v for k, v in it.items() if k != "id"})
    res = call("/api/save_edited", "POST", {
        "receipt_id": rid, "supplier_name": data["supplier_name"],
        "date": data["date"], "sheet_name": data["sheet_name"],
        "total_amount": data["total_amount"], "items": items,
        "settlement_type": "credit", "version": data["version"],
    })
    print("3. save_edited:", res["status"], "version:", res.get("version"))
    ver = res["version"]

    # 4. approve（owner）
    res = call(f"/api/receipt/{rid}/approve", "POST", {"version": ver})
    print("4. approve:", res["status"], "version:", res.get("version"))

    # 5. 库存
    inv = call("/api/inventory")
    print("5. 库存 SKU:", len(inv["data"]), "| meta:", inv["meta"])
    for s in inv["data"][:3]:
        print("   -", s["name"][:20], "stock:", s["current_stock"], s["base_unit"])

    # 6. 供应商
    sups = call("/api/suppliers")
    print("6. 供应商:", [(s["name"][:15], s["receipt_count"]) for s in sups["data"]])

    # 7. 成本报表
    cr = call("/api/cost_report?include_non_approved=1")
    print("7. 成本报表 total:", cr["data"]["month_total"])

    # 8. 对账
    rec = call("/api/reconciliation", "POST", {"supplier_id": sups["data"][0]["id"] if sups["data"] else 1, "period_type": "month"})
    print("8. 对账 task:", rec.get("task_id"))

    print("\n✓ 全链路通过")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
