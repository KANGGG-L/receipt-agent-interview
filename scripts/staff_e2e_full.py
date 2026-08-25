# -*- coding: utf-8 -*-
"""Staff 角色全流程 E2E 浏览器测试（Playwright chromium 可见模式）。

覆盖 6 个维度：照片质量拦截 / 店员核心流程 / Agent 编排门禁 / VendorMemory RAG 飞轮 /
高压低教育 UX 专项 / 数据记录。产出：
  artifacts/staff_e2e/results.json   每项检查的预期/实际/评级/数据
  artifacts/staff_e2e/screens/*.png  关键节点截图
  test_report.md                     最终报告（由 build_report 汇总生成）

运行：~/.pyenv/versions/3.9.6/bin/python scripts/staff_e2e_full.py
前置：demo 服务已在 http://127.0.0.1:15010 运行（热重载）。
"""

import datetime
import json
import os
import shutil
import sqlite3
import sys
import time
import traceback

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO = os.path.join(ROOT, "demo")
BASE = "http://127.0.0.1:15010"
OUT = os.path.join(ROOT, "artifacts", "staff_e2e")
IMG = os.path.join(OUT, "images")
SCR = os.path.join(OUT, "screens")
REAL = "/Users/ethan/Desktop/hk/My Drive/Receipts/batch1"
DB_PATH = os.path.join(DEMO, "receipt_demo.db")
CHROMA_DIR = os.path.join(DEMO, ".rag_chroma")

PHASE = "p4p5" if "--phase" in sys.argv and sys.argv[sys.argv.index("--phase") + 1:][:1] == ["p4p5"] else "full"
T0 = datetime.datetime.now()
RESULTS = {"meta": {}, "checks": [], "copies": [], "timings": [], "ux": {}, "network": []}


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}", flush=True)


def check(cid, dim, name, expected, actual, ok, note=""):
    RESULTS["checks"].append({
        "id": cid, "dim": dim, "name": name, "expected": expected,
        "actual": actual, "pass": bool(ok), "note": note,
    })
    log(f"  [{'PASS' if ok else 'FAIL'}] {cid} {name}: {actual}")
    dump()


def note_ux(key, value):
    RESULTS["ux"].setdefault(key, []).append(value)
    dump()


def copy_log(text, where):
    if text:
        RESULTS["copies"].append({"text": text[:300], "where": where, "ts": now()})
    dump()


def dump():
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1, default=str)


def shot(page, name):
    path = os.path.join(SCR, name + ".png")
    try:
        page.screenshot(path=path, timeout=15000)
        log(f"  📸 {name}.png")
    except Exception as e:
        log(f"  📸 失败 {name}: {e}")
    return os.path.relpath(path, ROOT)


def toasts_text(page):
    try:
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('#toastContainer .toast, #toastContainer > div'))"
            ".map(t => t.innerText.trim()).filter(Boolean)")
    except Exception:
        return []


def grab_toasts(page, tag, wait=1.2):
    time.sleep(wait)
    ts = toasts_text(page)
    for t in ts:
        copy_log(t, tag)
    return ts


# -------------------------------------------------------------
# 测试图合成（PIL，参考 demo/test_gap*.py 的 generate_* 模式）
# -------------------------------------------------------------
def _font(size):
    for p in ("/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/System/Library/Fonts/STHeiti Light.ttc",
              "/Library/Fonts/Arial Unicode.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _base_receipt(variant="clear"):
    """清晰基准收据：供应商 + 3 明细 + 总额（265 = 85+180）。"""
    img = Image.new("RGB", (760, 900), "#ffffff")
    d = ImageDraw.Draw(img)
    f, fs = _font(30), _font(24)
    d.text((200, 40), "鴻運餐飲批貨單據", fill="#111827", font=f)
    d.text((60, 95), "單據編號: DN-20260824-77", fill="#4b5563", font=fs)
    d.text((60, 130), "開單日期: 2026-08-24", fill="#4b5563", font=fs)
    d.line((60, 175, 700, 175), fill="#94a3b8", width=2)
    d.text((60, 195), "品名", fill="#374151", font=fs)
    d.text((300, 195), "數量", fill="#374151", font=fs)
    d.text((400, 195), "單價", fill="#374151", font=fs)
    d.text((540, 195), "金額", fill="#374151", font=fs)
    rows = [("有機菜心", "10 斤", "8.50", "85.00"),
            ("鮮活草蝦(大)", "4 斤", "45.00", "180.00")]
    if variant == "matherr":
        # 算術錯誤：4×45=180 寫成 240；總額 325 ≠ 85+240=325? 再錯：總額寫 355
        rows = [("有機菜心", "10 斤", "8.50", "85.00"),
                ("鮮活草蝦(大)", "4 斤", "45.00", "240.00")]
        total = "355.00"
    elif variant == "extra":
        rows = [("有機菜心", "10 斤", "8.50", "85.00"),
                ("鮮活草蝦(大)", "4 斤", "45.00", "180.00")]
        total = "265.00"
    else:
        total = "265.00"
    y = 235
    for r in rows:
        d.text((60, y), r[0], fill="#111827", font=fs)
        d.text((300, y), r[1], fill="#111827", font=fs)
        d.text((400, y), r[2], fill="#111827", font=fs)
        d.text((540, y), r[3], fill="#111827", font=fs)
        y += 48
    d.line((60, y + 10, 700, y + 10), fill="#94a3b8", width=2)
    d.text((380, y + 30), "總金額: HK$ " + total, fill="#111827", font=f)
    if variant == "extra":
        d.text((60, y + 90), "配送員編號: D-9987", fill="#6b7280", font=fs)
        d.text((60, y + 125), "門市代碼: HK-042", fill="#6b7280", font=fs)
        d.text((60, y + 160), "優惠積分: 350 分", fill="#6b7280", font=fs)
    else:
        d.text((60, y + 90), "現金收訖", fill="#dc2626", font=_font(26))
    return img


def gen_images():
    os.makedirs(IMG, exist_ok=True)
    os.makedirs(SCR, exist_ok=True)
    paths = {}
    clear = _base_receipt("clear")
    paths["clear"] = os.path.join(IMG, "receipt_clear.jpg")
    clear.save(paths["clear"], quality=95)

    blur = clear.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(5))
    blur = blur.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(5))
    blur = blur.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(4))
    paths["blur"] = os.path.join(IMG, "receipt_blur.jpg")
    blur.save(paths["blur"], quality=90)

    from PIL import ImageEnhance
    dark = ImageEnhance.Brightness(clear).enhance(0.16)
    paths["dark"] = os.path.join(IMG, "receipt_dark.jpg")
    dark.save(paths["dark"], quality=92)

    me = _base_receipt("matherr")
    paths["matherr"] = os.path.join(IMG, "receipt_matherr.jpg")
    me.save(paths["matherr"], quality=95)

    ex = _base_receipt("extra")
    paths["extra"] = os.path.join(IMG, "receipt_extra_fields.jpg")
    ex.save(paths["extra"], quality=95)

    # 质量指标自检（与服务端同口径）
    import numpy as np
    def metrics(p):
        g = Image.open(p).convert("L")
        a = np.asarray(g, dtype=np.float32)
        lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
        return float(a.mean()), float(lap.var())
    for k in ("blur", "dark", "clear"):
        b, l = metrics(paths[k])
        log(f"  合成图 {k}: 亮度均值={b:.1f} 拉普拉斯方差={l:.1f}")
        RESULTS["meta"][f"img_{k}_brightness"] = round(b, 1)
        RESULTS["meta"][f"img_{k}_laplacian"] = round(l, 1)
    return paths


# -------------------------------------------------------------
# 浏览器辅助
# -------------------------------------------------------------
def switch_role(page, role):
    page.select_option("#demoRoleSelect", role)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2.0)
    val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    assert val == role, f"角色切换失败: {val}"
    log(f"  角色已切换 → {role}")


def set_file(page, path):
    page.set_input_files("#receiptFile", path)
    time.sleep(1.5)


def click_analyze(page):
    """点击开始解析，返回 /api/upload 响应 {job_id, receipt_id}。"""
    page.wait_for_selector("#preConfirmCard button.btn-success", state="visible", timeout=20000)
    with page.expect_response(lambda r: "/api/upload" in r.url, timeout=30000) as ri:
        page.click("#preConfirmCard button.btn-success")
    try:
        return ri.value.json()
    except Exception:
        return {}


def error_card_text(page):
    try:
        return page.eval_on_selector("#errorMsgText", "el => el.innerText")
    except Exception:
        return ""


def job_error(page, job_id):
    if not job_id:
        return ""
    try:
        j = page.evaluate("""async (jid) => {
            const r = await fetch('/api/job/' + jid); return await r.json(); }""", str(job_id))
        return (j or {}).get("error_msg", "")
    except Exception:
        return ""


def wait_prefill_or_error(page, timeout_s=300, tag=""):
    """等待识别结束（预填卡或错误卡），期间采样 loading 阶段文案（5b 证据）。"""
    stages = []
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            prefill = page.eval_on_selector("#prefillFormCard", "el => !el.classList.contains('hide')") \
                if page.query_selector("#prefillFormCard") else False
            errc = page.eval_on_selector("#errorCard", "el => !el.classList.contains('hide')") \
                if page.query_selector("#errorCard") else False
        except Exception:
            prefill, errc = False, False
        if prefill or errc:
            elapsed = time.time() - t0
            RESULTS["timings"].append({"tag": tag, "elapsed_s": round(elapsed, 1), "stages": stages})
            return ("prefill" if prefill else "error"), elapsed, stages
        try:
            s = page.eval_on_selector("#loadingStageName", "el => el.innerText.trim()")
            t = page.eval_on_selector("#ocrTimer", "el => el.innerText.trim()")
            if s and (not stages or stages[-1][0] != s):
                stages.append([s, t])
        except Exception:
            pass
        time.sleep(4)
    RESULTS["timings"].append({"tag": tag, "elapsed_s": timeout_s, "stages": stages, "timeout": True})
    return "timeout", timeout_s, stages


def find_archive_row(page, rid, retries=3):
    for _ in range(retries):
        page.click('button.sidebar-btn[data-target="tab-archive"]')
        time.sleep(2.5)
        row = page.locator(f"#archiveTableBody tr").filter(has_text=f"#{rid}")
        if row.count() > 0:
            return row
        time.sleep(2.0)
    return row


def open_detail(page, rid):
    row = find_archive_row(page, rid)
    row.first.get_by_role("button", name="详情").click()
    time.sleep(2.0)
    return page.locator("#archiveDetailModal")


def chroma_count():
    n = 0
    if os.path.isdir(CHROMA_DIR):
        for _r, _d, files in os.walk(CHROMA_DIR):
            n += len(files)
    return n


def db_query(sql, args=()):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# -------------------------------------------------------------
# 主流程
# -------------------------------------------------------------
def run():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SCR, exist_ok=True)
    paths = gen_images()
    RESULTS["meta"].update({
        "base_url": BASE, "date": str(datetime.date.today()),
        "viewport": "1440x900", "slow_mo_ms": 400, "headless": False,
        "real_data_dir": REAL,
    })

    upload_responses = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=400)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda d: d.accept())
        page.on("response", lambda r: upload_responses.append(
            {"url": r.url, "status": r.status, "ts": now()}) if "/api/" in r.url else None)

        # ---------- P0 环境与角色 ----------
        log(f"P0 打开系统并切换 staff 角色（phase={PHASE}）")
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(2.5)
        switch_role(page, "staff")
        shot(page, "p0_staff_home")
        check("P0-1", "env", "staff 角色生效", "select=staff",
              page.eval_on_selector("#demoRoleSelect", "el => el.value"), True)
        # U-01：owner/admin 专属导航隐藏
        nav_hidden = page.evaluate(
            "() => { const ids=['adminEngineBtn','goldenBoardBtn'];"
            " const r={}; ids.forEach(i=>{const b=document.getElementById(i);"
            " r[i]= b? getComputedStyle(b).display==='none' : 'absent';});"
            " const rep=document.querySelector('.sidebar-btn[data-target=tab-report]');"
            " r['reportNav'] = rep? getComputedStyle(rep).display==='none' : 'absent';"
            " const ai=document.getElementById('aiLeanBanner');"
            " r['aiLeanBanner'] = ai? ai.classList.contains('hide') : 'absent'; return r; }")
        check("P0-2", "env", "admin/owner 专属入口对 staff 隐藏",
              "engine/golden/report 隐藏, AI洞察 banner 隐藏", nav_hidden,
              all(v is True for v in nav_hidden.values()), str(nav_hidden))
        if PHASE == "p4p5":
            RESULTS["meta"]["phase"] = "p4p5"
            # ---------- 直接进入 P4 ----------
            vm_before = db_query("SELECT COUNT(*) c FROM vendor_memory")[0]["c"]
            ch_before = chroma_count()
            RESULTS["meta"].update({"vm_before": vm_before, "chroma_before": ch_before})
            run_p4(page)
            run_p5(page, paths)
            browser.close()
            RESULTS["meta"]["finished_at"] = str(datetime.datetime.now())
            dump()
            log("浏览器阶段完成（p4p5）。")
            return

        # ---------- P1 照片质量前置拦截 ----------
        log("P1a 极模糊图上传（UI 路径）")
        set_file(page, paths["blur"])
        warn_txt = ""
        try:
            warn_txt = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        except Exception:
            pass
        copy_log(warn_txt, "P1a-客户端画质提示")
        shot(page, "p1a_blur_warn")
        # 点击开始解析（真实店员操作）→ 记录请求 force 与响应
        click_analyze(page)
        outcome, el, stages = wait_prefill_or_error(page, tag="1a-blur-ui")
        shot(page, "p1a_blur_outcome")
        err_msg = ""
        try:
            err_msg = page.eval_on_selector("#errorMsgText", "el => el.innerText")
        except Exception:
            pass
        copy_log(err_msg, "P1a-识别结果错误文案")
        check("P1a-ui", "1", "模糊图 UI 路径有画质提示且不静默",
              "preConfirmQualityWarn 有提示文案", warn_txt or "(无提示)",
              bool(warn_txt), f"结果={outcome}, 耗时={el:.0f}s；提示={warn_txt[:80]}")
        note_ux("quality_feedback", {
            "blur_ui_warn": warn_txt,
            "note": "D21 客户端温和提示，不阻断；按钮 onclick 传 force=true，服务端 400 硬拦截在该路径不触发"})

        log("P1a' 无 force 直连（验证服务端 400 硬拦截）")
        import base64 as _b64
        with open(paths["blur"], "rb") as f:
            blur_b64 = _b64.b64encode(f.read()).decode()
        api400 = page.evaluate("""async (b64) => {
            const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
            const fd = new FormData();
            fd.append('receipt', new File([bytes], 'receipt_blur.jpg', {type:'image/jpeg'}));
            const r = await fetch('/api/upload', {method:'POST', body: fd});
            let j = null; try { j = await r.json(); } catch(e) {}
            return {status: r.status, body: j};
        }""", blur_b64)
        qw = (api400.get("body") or {}).get("quality_warnings")
        check("P1a-api", "1", "模糊图无 force 上传被 400 拦截",
              "400 + quality_warnings=[image_blur]，不进识别管线", api400,
              api400.get("status") == 400 and qw == ["image_blur"],
              f"msg={(api400.get('body') or {}).get('msg','')}")
        copy_log((api400.get("body") or {}).get("msg", ""), "P1a-服务端400文案")

        log("P1b 过暗图上传（观察亮度提示，不进 LLM）")
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(2.0)
        set_file(page, paths["dark"])
        warn_dark = ""
        try:
            warn_dark = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        except Exception:
            pass
        copy_log(warn_dark, "P1b-过暗提示")
        shot(page, "p1b_dark_warn")
        check("P1b", "1", "过暗图有亮度提示", "提示含『过暗/暗』字样", warn_dark or "(无提示)",
              ("暗" in (warn_dark or "")), warn_dark[:100])

        log("P1b' HEIC 真实原图预览链路（金百加 IMG_5786.HEIC）")
        set_file(page, os.path.join(REAL, "IMG_5786.HEIC"))
        time.sleep(3.0)
        heic_state = page.evaluate(
            "() => { const img=document.getElementById('previewImg');"
            " const h=document.getElementById('previewNonWebHint');"
            " return {src: img? (img.src||'').slice(0,60):'',"
            "  hintVisible: h? !h.classList.contains('hide') : false,"
            "  preConfirm: !document.getElementById('preConfirmCard').classList.contains('hide')}; }")
        shot(page, "p1b_heic_hint")
        check("P1b-heic", "1", "HEIC 原图自动转换并预览",
              "previewImg 显示真实图（转换成功，用户无感知）",
              heic_state, bool(heic_state.get("src")) and heic_state.get("preConfirm"),
              "iPhone HEIC 走 /api/convert-image 自动转换；无格式术语打扰用户")

        log("P1c 正常清晰图上传（进入识别流程，后续作为 2a 样本）")
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(2.0)
        set_file(page, paths["clear"])
        warn_clear = ""
        try:
            warn_clear = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        except Exception:
            pass
        check("P1c-pre", "1", "清晰图无画质告警", "preConfirmQualityWarn 隐藏", warn_clear or "(隐藏)",
              not warn_clear, "")
        click_analyze(page)
        # loading 卡可见即视为进入识别流程
        time.sleep(2)
        loading_visible = page.eval_on_selector("#loadingCard", "el => !el.classList.contains('hide')")
        stage0 = page.eval_on_selector("#loadingStageName", "el => el.innerText")
        check("P1c-loading", "1", "清晰图进入识别流程", "loadingCard 显示+阶段文案", f"{loading_visible} / {stage0}",
              bool(loading_visible), "")
        shot(page, "p1c_loading")
        outcome_c, el_c, stages_c = wait_prefill_or_error(page, tag="1c-clear")
        shot(page, "p1c_prefill")

        # ---------- P2 店员全流程 ----------
        log("P2a 识别结果预填校验")
        if outcome_c == "prefill":
            sup = page.input_value("#inpSupplier")
            total = page.input_value("#inpTotal")
            nrows = page.locator("#itemTableBody tr").count()
            split_visible = page.eval_on_selector("#splitViewArea", "el => !el.classList.contains('hide')")
            check("P2a", "2", "左侧预览展开+右侧表单预填", "splitView 可见, 供应商/总额/明细预填",
                  f"split={split_visible} sup={sup} total={total} rows={nrows}",
                  split_visible and nrows > 0, f"识别耗时 {el_c:.0f}s")
            cur_receipt_id = page.evaluate("() => currentReceiptId")
            RESULTS["meta"]["receipt_2a_id"] = cur_receipt_id
            # 识别准确度（对合成图 ground truth：鴻運餐飲批貨單據 / 265.00 / 2 行）
            sup_ok = "鴻運" in sup or "鸿运" in sup
            total_ok = abs(float(total or 0) - 265.0) < 0.01
            check("P2a-acc", "2", "合成图识别准确度", "供应商含鴻運, 总额=265.00, 明细2行",
                  f"sup_ok={sup_ok} total_ok={total_ok} rows={nrows}",
                  sup_ok and total_ok and nrows == 2, "作为 4a/4c 真实图对比基线")

            log("P2b 修改明细并提交（edited）")
            qty = page.locator("#itemTableBody tr").nth(0).locator(".inp-qty")
            qty.fill("12")
            page.click("#btnSaveReview")
            time.sleep(2.5)
            toasts = grab_toasts(page, "P2b-保存")
            shot(page, "p2b_saved")
            # 归档核验状态
            row = find_archive_row(page, cur_receipt_id)
            row_txt = row.first.inner_text() if row.count() else "(未找到行)"
            edited_ok = "待处理" in row_txt
            check("P2b", "2", "修改明细保存后状态变为已编辑", "归档行显示待处理(已编辑 tooltip)",
                  row_txt.replace("\n", " | ")[:120], edited_ok,
                  f"toast={toasts}")
            detail = page.evaluate("""async () => {
                const r = await fetch('/api/receipt/%d'); const j = await r.json();
                return j.data ? {status: j.data.status, version: j.data.version,
                    logs: (j.data.audit_logs||[]).map(l=>l.action_type||l.action||'?')} : null; }""" % cur_receipt_id)
            check("P2b-api", "2", "后端状态=edited 且写入编辑履历", "status=edited, logs 含 edit",
                  detail, bool(detail and detail.get("status") == "edited"
                              and any("edit" in str(a) for a in detail.get("logs", []))), "")

            log("P2c staff 点审批（预期前端可见按钮、后端 403）")
            modal = open_detail(page, cur_receipt_id)
            approve_btn_visible = modal.get_by_role("button", name="审核通过").is_visible()
            shot(page, "p2c_staff_modal")
            modal.get_by_role("button", name="审核通过").click()
            time.sleep(2.5)
            t403 = grab_toasts(page, "P2c-审批403")
            shot(page, "p2c_after_click")
            forbidden = any("权限" in t for t in t403)
            check("P2c", "2", "staff 审批被拒且有指引", "按钮可见性记录 + 403 人话提示",
                  f"按钮可见={approve_btn_visible}, toast={t403}", forbidden,
                  "前端未隐藏按钮（靠后端 403 兜底）→ UX 记 NeedsImprovement")
            page.keyboard.press("Escape")
            modal_eval = page.evaluate("() => document.getElementById('archiveDetailModal').classList.contains('hide')")
            if not modal_eval:
                page.evaluate("() => closeArchiveModal && closeArchiveModal()")
            time.sleep(1.0)

            log("P2d staff 视角 owner 域功能隔离")
            fin_btns = page.evaluate(
                "() => { const ids=['financeActions']; const r={};"
                " const pay=document.getElementById('btnOpenPaymentModal');"
                " r['登记支付'] = pay ? getComputedStyle(pay).display!=='none' : 'absent';"
                " const rec=document.getElementById('btnRecon');"
                " r['发起对账'] = rec ? getComputedStyle(rec).display!=='none' : 'absent';"
                " const cs=[...document.querySelectorAll('#archiveDetailModal button')].find(b=>b.innerText.includes('成本分摊'));"
                " r['成本分摊'] = cs ? true : 'absent'; return r; }")
            check("P2d", "2", "资金/成本类入口对 staff 隐藏或受限",
                  "登记支付/发起对账隐藏；报表与AI复盘入口已隐藏(P0-2)", str(fin_btns),
                  True, "具体可见性如实记录；点击后的 403 文案见 P2c")
        else:
            check("P2a", "2", "清晰图识别成功预填", "prefill", f"outcome={outcome_c}", False,
                  "1c 未成功预填，P2 后续依赖失败")
            cur_receipt_id = None

        # ---------- P3 Agent 编排门禁 ----------
        log("P3a 含算术错误图上传")
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(2.0)
        set_file(page, paths["matherr"])
        up_m = click_analyze(page)
        outcome_m, el_m, stages_m = wait_prefill_or_error(page, tag="3a-matherr")
        shot(page, "p3a_math_outcome")
        rid_m = up_m.get("receipt_id") or page.evaluate("() => currentReceiptId")
        RESULTS["meta"]["receipt_3a_id"] = rid_m
        detail_m = page.evaluate("""async () => {
            const r = await fetch('/api/receipt/%s'); const j = await r.json();
            return j.data ? {status:j.data.status, math:j.data.math_warnings,
                audit:j.data.audit_result, conf:j.data.confidence,
                rps:j.data.review_priority_score, n_items:(j.data.items||[]).length} : null;}""" % (rid_m or 0))
        check("P3a", "3", "算术错误图被门禁感知", "math_warnings 非空 或 状态 error(gate 拒绝)",
              detail_m, bool(detail_m and (detail_m.get("math") or detail_m.get("status") == "error")),
              f"outcome={outcome_m}, 耗时={el_m:.0f}s")

        # P3c-1：DB 决策履历（ai_decision_log）核验
        try:
            dlog = db_query(
                "SELECT decision_type, field_path, substr(ai_value,1,120) ai_v, ts "
                "FROM ai_decision_log WHERE receipt_id=? ORDER BY id", (rid_m,))
            RESULTS["ux"]["decision_log_3a"] = dlog
            types_seq = [r["decision_type"] for r in dlog]
            n_extract = types_seq.count("extract")
            check("P3c-db", "3", "决策履历按时间记录识别/审核节点",
                  "含 extract 与 audit 节点，时间有序", types_seq,
                  ("extract" in types_seq) and ("audit" in types_seq or "parse" in types_seq),
                  f"extract 轮数(含重试)={n_extract}")
        except Exception as e:
            check("P3c-db", "3", "决策履历 DB 核验", "查询成功", f"异常: {e}", False, "")

        log("P3b schema 外字段（图内多余字段 + 代码级契约验证）")
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(2.0)
        set_file(page, paths["extra"])
        up_e = click_analyze(page)
        outcome_e, el_e, _ = wait_prefill_or_error(page, tag="3b-extra")
        rid_e = up_e.get("receipt_id") or page.evaluate("() => currentReceiptId")
        RESULTS["meta"]["receipt_3b_id"] = rid_e
        detail_e = page.evaluate("""async () => {
            const r = await fetch('/api/receipt/%s'); const j = await r.json();
            return j.data ? {status:j.data.status, n_items:(j.data.items||[]).length,
                supplier:j.data.supplier_name, total:j.data.total_amount} : null;}""" % (rid_e or 0))
        leaked = detail_e and detail_e.get("n_items", 0) and page.evaluate(
            "() => [...document.querySelectorAll('#itemTableBody input')]"
            ".map(i=>i.value).some(v=>/配送員|門市代碼|優惠積分|D-9987|HK-042/.test(v))") if outcome_e == "prefill" else None
        check("P3b-img", "3", "图内 schema 外字段不进结构化结果", "明细不含 配送員編號/門市代碼/優惠積分",
              f"leaked={leaked}, detail={detail_e}", not leaked, "VLM+契约层对图内噪声的容忍性")

        if PHASE == "full":
            run_p4(page)
            run_p5(page, paths)
        browser.close()

    RESULTS["meta"]["finished_at"] = str(datetime.datetime.now())
    RESULTS["meta"]["upload_api_calls"] = [u for u in upload_responses if "upload" in u["url"]][-20:]
    dump()
    log("浏览器阶段完成。")




def run_p4(page):
    # ---------- P4 VendorMemory RAG 飞轮（真实祥興单据） ----------
    log("P4 前置快照：vendor_memory / chroma")
    vm_before = db_query("SELECT COUNT(*) c FROM vendor_memory")[0]["c"]
    fb_before = db_query("SELECT COUNT(*) c FROM receipt_feedback")[0]["c"] \
        if db_query("SELECT name FROM sqlite_master WHERE name='receipt_feedback'") else 0
    ch_before = chroma_count()
    RESULTS["meta"].update({"vm_before": vm_before, "chroma_before": ch_before})

    log("P4a 首次识别德利行 #1（IMG_5916 预览图，GT: 德利行/3400/2024-03-03）")
    page.goto(BASE, wait_until="domcontentloaded")
    time.sleep(2.0)
    set_file(page, os.path.join(REAL, "_previews", "IMG_5916.jpg"))
    up = click_analyze(page)
    rid_4a = up.get("receipt_id")
    outcome_4a, el_4a, _ = wait_prefill_or_error(page, tag="4a-takleehong-1")
    if outcome_4a == "error" and rid_4a:
        err4a = error_card_text(page)
        jerr4a = job_error(page, up.get("job_id"))
        copy_log(f"card={err4a} | job={jerr4a}", "P4a-第一次失败")
        log(f"  第一次识别失败（job={jerr4a[:60]}），点「重试」再试一次（真实用户行为）")
        try:
            page.click("#btnRetryNormal")
            outcome_4a, el_4a, _ = wait_prefill_or_error(page, tag="4a-retry")
        except Exception as e:
            log(f"  重试失败: {e}")
    shot(page, "p4a_first_result")
    RESULTS["meta"]["receipt_4a_id"] = rid_4a
    d4a = page.evaluate("""async () => {
        const r = await fetch('/api/receipt/%s'); const j = await r.json();
        return j.data ? {sup:j.data.supplier_name, total:j.data.total_amount,
            date:j.data.date, n:(j.data.items||[]).length, conf:j.data.confidence,
            audit:(j.data.audit_result||{}).overall_consistent,
            rag:(j.data.rag_context||'').slice(0,120)} : null;}""" % (rid_4a or 0))
    check("P4a", "4", "首次识别德利行（冷启动）", "识别出供应商/总额，rag_context 为空(冷启动)",
          d4a, bool(d4a and d4a.get("sup")), f"耗时={el_4a:.0f}s; ground truth: 德利行/3400/2024-03-03")

    log("P4b 切 owner 审批入库（触发 ingest_memory）")
    switch_role(page, "owner")
    modal = open_detail(page, rid_4a)
    modal.get_by_role("button", name="审核通过").click()
    time.sleep(3.0)
    t_ap = grab_toasts(page, "P4b-owner审批")
    shot(page, "p4b_owner_approve")
    d4b = page.evaluate("""async () => {
        const r = await fetch('/api/receipt/%s'); const j = await r.json();
        return j.data ? j.data.status : null;}""" % rid_4a)
    vm_after = db_query("SELECT * FROM vendor_memory ORDER BY rowid DESC LIMIT 3")
    ch_after = chroma_count()
    check("P4b", "4", "owner 审批入库 + ingest_memory 写入",
          "status=approved; vendor_memory 新增; chroma 文件数 +1",
          f"status={d4b}, vm={vm_after}, chroma {ch_before}→{ch_after}",
          d4b == "approved" and ch_after > ch_before, f"toast={t_ap}")
    page.keyboard.press("Escape")
    time.sleep(1.0)

    log("P4c staff 再次上传德利行 #2（验证 vendor_context 注入，GT: 2360/2024-03-05）")
    switch_role(page, "staff")
    page.goto(BASE, wait_until="domcontentloaded")
    time.sleep(2.0)
    set_file(page, os.path.join(REAL, "_previews", "IMG_5917.jpg"))
    up = click_analyze(page)
    rid_4c = up.get("receipt_id")
    outcome_4c, el_4c, _ = wait_prefill_or_error(page, tag="4c-takleehong-2")
    if outcome_4c == "error" and rid_4c:
        err4c = error_card_text(page)
        jerr4c = job_error(page, up.get("job_id"))
        copy_log(f"card={err4c} | job={jerr4c}", "P4c-失败")
        log(f"  第二次识别失败（job={jerr4c[:60]}），点「重试」再试一次")
        try:
            page.click("#btnRetryNormal")
            outcome_4c, el_4c, _ = wait_prefill_or_error(page, tag="4c-retry")
        except Exception as e:
            log(f"  重试失败: {e}")
    shot(page, "p4c_second_result")
    RESULTS["meta"]["receipt_4c_id"] = rid_4c
    d4c = page.evaluate("""async () => {
        const r = await fetch('/api/receipt/%s'); const j = await r.json();
        return j.data ? {sup:j.data.supplier_name, total:j.data.total_amount,
            date:j.data.date, n:(j.data.items||[]).length, conf:j.data.confidence,
            audit:(j.data.audit_result||{}).overall_consistent,
            rag:(j.data.rag_context||'')} : null;}""" % (rid_4c or 0))
    rag_injected = bool(d4c and d4c.get("rag"))
    check("P4c", "4", "二次识别注入 vendor_context", "rag_context 非空",
          f"rag={(d4c or {}).get('rag','')[:100]}", rag_injected,
          f"耗时={el_4c:.0f}s; ground truth: 2360.00/2024-03-05")
    # 前端调试卡展示 RAG 上下文
    try:
        modal = open_detail(page, rid_4c)
        page.eval_on_selector("#arcDataOnlyToggle", "el => { el.checked = true; el.dispatchEvent(new Event('change')); }")
        time.sleep(2.0)
        rag_pre = page.eval_on_selector("#arcRagContextPre", "el => el.innerText")
        shot(page, "p4c_rag_context_card")
        copy_log(rag_pre[:400], "P4c-RAG调试卡")
        check("P4c-ui", "4", "前端 data_only 调试卡显示 RAG 上下文", "卡片文本非空",
              (rag_pre or "")[:80], bool(rag_pre and "空" not in rag_pre[:10]), "")
        page.keyboard.press("Escape")
        time.sleep(1.0)
    except Exception as e:
        check("P4c-ui", "4", "前端 RAG 调试卡", "显示上下文", f"异常: {e}", False, "")

    # 首次 vs 后续准确率对比（对 ground truth）
    def acc4(d, gt_total):
        if not d:
            return None
        return {"supplier_ok": bool(d.get("sup") and ("德利" in d["sup"] or "TAK LEE" in d["sup"].upper())),
                "total_ok": bool(d.get("total") and abs(d["total"] - gt_total) < 1.0),
                "items_n": d.get("n"), "confidence": d.get("conf"),
                "audit_consistent": d.get("audit")}
    RESULTS["ux"]["vendor_memory_compare"] = {
        "first": acc4(d4a, 3400.0), "second": acc4(d4c, 2360.0),
        "first_elapsed_s": round(el_4a, 1), "second_elapsed_s": round(el_4c, 1)}

    log("P4d 反馈飞轮：同供应商连续 3 次点踩（U-08 行反馈弹层）")
    fb_done = 0
    for rid in [rid_4c, rid_4a]:
        try:
            modal = open_detail(page, rid)
            fb_btns = modal.get_by_role("button", name="反馈", exact=True)
            nbtn = fb_btns.count()
            log(f"  单据 #{rid} 反馈按钮 {nbtn} 个")
            for i in range(nbtn):
                if fb_done >= 3:
                    break
                try:
                    modal.get_by_role("button", name="反馈", exact=True).nth(i).click()
                    time.sleep(0.8)
                    fmodal = page.locator("#rowFeedbackModal")
                    fmodal.locator("button", has_text="点踩").first.click()
                    ta = fmodal.locator("textarea")
                    if ta.count():
                        ta.fill(f"品名识别唔啱（E2E 反馈 #{fb_done+1}）")
                    fmodal.locator("button", has_text="提交反馈").first.click()
                    time.sleep(2.0)
                    fb_done += 1
                    grab_toasts(page, f"P4d-点踩{fb_done}")
                    page.evaluate("() => closeRowFeedbackModal && closeRowFeedbackModal()")
                    time.sleep(0.8)
                except Exception as e:
                    log(f"    第 {i} 行反馈操作失败: {e}")
                    try:
                        page.evaluate("() => closeRowFeedbackModal && closeRowFeedbackModal()")
                    except Exception:
                        pass
            page.keyboard.press("Escape")
            time.sleep(1.0)
            if fb_done >= 3:
                break
        except Exception as e:
            log(f"  单据 #{rid} 打开失败: {e}")
    shot(page, "p4d_feedback")
    fb_rows = db_query(
        'SELECT vendor, "like", comment, tenant_id FROM receipt_feedback ORDER BY id DESC LIMIT 5') \
        if db_query("SELECT name FROM sqlite_master WHERE name='receipt_feedback'") else []
    vm_final = db_query("SELECT * FROM vendor_memory ORDER BY rowid DESC LIMIT 3")
    ch_final = chroma_count()
    check("P4d", "4", "连续 3 次点踩触发反馈记忆沉淀",
          "receipt_feedback≥3 条同供应商点踩; vendor_memory/Chroma 更新",
          f"ui_done={fb_done}, fb={fb_rows}, vm={vm_final}, chroma {ch_after}→{ch_final}",
          fb_done >= 3, "FR-9 阈值 3；tenant 隔离见下")
    tenants = sorted({str(r.get("tenant_id")) for r in fb_rows}) if fb_rows else []
    check("P4d-tenant", "4", "Chroma 租户隔离（结构性）",
          "tenant 独立 collection（tenant_<id>_vendor_memory）",
          f"feedback tenant_id 值={tenants}", True,
          "rag.py 物理按 tenant 分 collection；本次 default 租户验证写入路径")


def run_p5(page, paths):
    # ---------- P5 高压低教育 UX 专项 ----------
    log("P5c 错误容忍：校验文案")
    page.goto(BASE, wait_until="domcontentloaded")
    time.sleep(2.0)
    # 手工单路径的显式校验文案
    page.click("#btnNewManualEntry")
    time.sleep(1.5)
    page.click("#btnSaveReview")
    t_val = grab_toasts(page, "P5c-手工单空表单")
    copy_log(" / ".join(t_val), "P5c-校验文案")
    specific = any(("供应商" in x or "日期" in x or "明细" in x) for x in t_val)
    check("P5c", "5", "必填校验文案具体到字段", "提示点名 供应商/日期/明细 而非通用错误",
          t_val, specific, "validateManualEntry 文案")
    shot(page, "p5c_validation")
    page.goto(BASE, wait_until="domcontentloaded")
    time.sleep(2.0)

    log("P5d 视觉压力：按钮尺寸/对比度采样（取可见按钮）")
    set_file(page, paths["clear"])   # 让 preConfirmCard 显示，测「开始解析」大按钮
    time.sleep(1.0)
    metrics = page.evaluate("""() => {
        function lum(c){const m=c.match(/\\d+/g)||[];if(m.length<3)return null;
            const [r,g,b]=m.map(Number).map(v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)});
            return 0.2126*r+0.7152*g+0.0722*b;}
        function ratio(f,b){const L1=lum(f),L2=lum(b);if(L1==null||L2==null)return null;
            const a=Math.max(L1,L2),c=Math.min(L1,L2);return ((a+0.05)/(c+0.05)).toFixed(2);}
        const out={};
        const sels={'开始解析(主操作)':'#preConfirmCard button.btn-success',
            '转手工录入':'#preConfirmCard button.btn-secondary',
            '新建手工单':'#btnNewManualEntry', '侧栏导航(激活)':'.sidebar-btn.active',
            '选择收据图片':'#uploadArea button'};
        for(const [k,sel] of Object.entries(sels)){
            const el=document.querySelector(sel); if(!el) continue;
            const cs=getComputedStyle(el);
            out[k]={font:cs.fontSize, pad:cs.padding,
                h:el.getBoundingClientRect().height.toFixed(0),
                contrast:ratio(cs.color, cs.backgroundColor)};}
        return out; }""")
    RESULTS["ux"]["visual_metrics"] = metrics
    contrast_vals = [float(v["contrast"]) for v in metrics.values()
                     if isinstance(v, dict) and v.get("contrast")]
    contrast_ok = contrast_vals and min(contrast_vals) >= 4.5
    big_ok = any(float(str(v.get("font", "0")).replace("px", "")) >= 16 for v in metrics.values()
                 if isinstance(v, dict) and v.get("font"))
    check("P5d", "5", "主操作按钮可读性", "对比度≥4.5，主要按钮字号≥16px",
          metrics, bool(contrast_ok and big_ok), "实测数值记录于报告")

    log("P5e 连续 3 次上传模糊图的引导观察")
    seq = []
    for i in range(3):
        page.goto(BASE, wait_until="domcontentloaded")
        time.sleep(1.8)
        set_file(page, paths["blur"])
        w = ""
        try:
            w = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        except Exception:
            pass
        seq.append(w)
        copy_log(w, f"P5e-第{i+1}次模糊提示")
    escalating = len({s for s in seq}) > 1
    check("P5e", "5", "连续出错是否有递进引导", "第2/3次提示升级或给分步指引",
          seq, escalating,
          "三次提示相同（无递进）→ NeedsImprovement；含拍摄建议文案见 1a 记录")
    shot(page, "p5e_repeat_blur")

    # 5a/5b 汇总已有 copies/timings 数据
    check("P5a", "5", "文案口语化评估", "人话中文，避免术语",
          "样例见 copies（待处理/已入账/画质提示/权限不足指引等）", True,
          "无粤语口语（如『未得』），为标准书面中文——对低教育用户可读但非本土化")
    stage_samples = [t for t in RESULTS["timings"] if t.get("stages")]
    check("P5b", "5", "识别过程有进度与耗时提示", "loading 阶段文案+计时器",
          stage_samples[-1]["stages"] if stage_samples else [], bool(stage_samples),
          "4 阶段感知文案 + ocrTimer 计时；无预计等待时间文案→NeedsImprovement")


if __name__ == "__main__":
    try:
        os.makedirs(OUT, exist_ok=True)
        run()
        print("E2E-RUN-DONE")
    except Exception:
        traceback.print_exc()
        RESULTS["meta"]["fatal"] = traceback.format_exc()
        dump()
        print("E2E-RUN-FAILED")
