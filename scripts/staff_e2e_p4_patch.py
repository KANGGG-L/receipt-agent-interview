# -*- coding: utf-8 -*-
"""P4 补测：
- P4d 反馈飞轮：单据 #40（德利行 Tak Lee Hong）连续 3 行点踩（同供应商满 FR-9 阈值）
- P4c2 带 vendor_hint 上传（API 层）：验证 RAG 读路径在 hint 存在时可用，
      并记录「前端无 hint 入口 → 默认路径飞轮断链」发现
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import staff_e2e_full as m


def close_modal(page):
    try:
        page.evaluate("() => { if (typeof closeArchiveModal==='function') closeArchiveModal(true); }")
    except Exception:
        pass
    time.sleep(0.8)


def open_detail(page, rid):
    close_modal(page)
    row = m.find_archive_row(page, rid)
    assert row.count() > 0, f"row #{rid} not found"
    row.first.get_by_role("button", name="详情").click()
    time.sleep(2.0)
    return page.locator("#archiveDetailModal")


def run():
    m.RESULTS["meta"]["patch_phase"] = "p4d+p4c2"
    with __import__("playwright.sync_api", fromlist=["sync_api"]).sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=400)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda d: d.accept())
        page.goto(m.BASE, wait_until="domcontentloaded")
        time.sleep(2.0)
        m.switch_role(page, "staff")

        # ---------- P4d 反馈飞轮（#40 德利行 Tak Lee Hong 三行点踩） ----------
        m.log("P4d 单据 #40 连续 3 行点踩（同供应商）")
        fb_done = 0
        try:
            modal = open_detail(page, 40)
            btns = modal.get_by_role("button", name="反馈", exact=True)
            n = btns.count()
            m.log(f"  #40 反馈按钮 {n} 个")
            for i in range(min(n, 3)):
                modal.get_by_role("button", name="反馈", exact=True).nth(i).click()
                time.sleep(0.8)
                fm = page.locator("#rowFeedbackModal")
                fm.locator("button", has_text="点踩").first.click()
                ta = fm.locator("textarea")
                if ta.count():
                    ta.fill(f"呢行品名認錯咗（補測 #{fb_done+1}）")
                fm.locator("button", has_text="提交反馈").first.click()
                time.sleep(2.0)
                fb_done += 1
                m.grab_toasts(page, f"P4d-点踩{fb_done}")
                try:
                    page.evaluate("() => closeRowFeedbackModal && closeRowFeedbackModal()")
                except Exception:
                    pass
                time.sleep(0.8)
            m.shot(page, "p4d_feedback_modal")
        except Exception as e:
            m.log(f"  P4d 异常: {e}")
            m.check("P4d", "4", "反馈 UI 操作", "3 次点踩提交", f"异常: {e}", False, "")
        close_modal(page)

        fb_rows = m.db_query(
            'SELECT vendor, "like", comment, tenant_id, created_at FROM receipt_feedback '
            "WHERE vendor LIKE '%德利%' ORDER BY id DESC LIMIT 5")
        vm_after_fb = m.db_query("SELECT * FROM vendor_memory ORDER BY rowid DESC LIMIT 3")
        m.check("P4d", "4", "连续 3 次同供应商点踩触发反馈记忆沉淀(FR-9)",
                "receipt_feedback 3 条 dislike + vendor_memory notes 含反馈沉淀",
                {"fb": fb_rows, "vm": [(r.get("vendor"), (r.get("notes") or "")[:60]) for r in vm_after_fb]},
                fb_done >= 3 and len(fb_rows) >= 3,
                f"UI 点踩 {fb_done} 次")

        # ---------- P4c2 带 hint 上传：验证 RAG 读路径 ----------
        m.log("P4c2 带 vendor_hint 上传德利行 #3（API 层，验证读路径）")
        img = os.path.join(m.REAL, "_previews", "IMG_5918.jpg")
        with open(img, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        up = page.evaluate("""async (b64) => {
            const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
            const fd = new FormData();
            fd.append('receipt', new File([bytes], 'taklee3.jpg', {type:'image/jpeg'}));
            fd.append('vendor_hint', '德利行 Tak Lee Hong');
            const r = await fetch('/api/upload', {method:'POST', body: fd});
            return await r.json();
        }""", b64)
        rid = (up or {}).get("receipt_id")
        job_id = (up or {}).get("job_id")
        m.log(f"  上传 resp: {up}")
        detail = None
        t0 = time.time()
        while time.time() - t0 < 300:
            j = page.evaluate(
                "async (jid) => { const r = await fetch('/api/job/'+jid); return await r.json(); }",
                str(job_id))
            if (j or {}).get("job_status") == "done":
                time.sleep(1)
                detail = page.evaluate("""async (rid) => {
                    const r = await fetch('/api/receipt/'+rid); const jj = await r.json();
                    return jj.data ? {sup: jj.data.supplier_name, total: jj.data.total_amount,
                        rag: (jj.data.rag_context||'').slice(0,150), n: (jj.data.items||[]).length} : null; }""",
                    int(rid))
                break
            if (j or {}).get("job_status") == "error":
                m.check("P4c2", "4", "带 hint 识别完成", "done", f"job error: {j.get('error_msg','')}", False, "")
                break
            time.sleep(5)
        m.RESULTS["meta"]["receipt_4c2_id"] = rid
        rag_ok = bool(detail and detail.get("rag"))
        m.check("P4c2", "4", "RAG 读路径（带 vendor_hint 时注入 vendor_context）",
                "rag_context 非空", detail, rag_ok,
                "前端上传无 vendor_hint 入口 → 默认路径飞轮断链（extract_chain L125）")
        m.shot(page, "p4c2_hint_rag") if False else None

        browser.close()
    m.dump()
    m.log("补测完成")


if __name__ == "__main__":
    run()
    print("PATCH-RUN-DONE")
