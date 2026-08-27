# -*- coding: utf-8 -*-
"""
真实浏览器端到端实测：首选模型无响应触发回退的当下立即弹出通知。

步骤：
1. 通过管理 API 把识别引擎指向不可达黑洞地址（10.255.255.1）+ 5s 超时，
   首选引擎必然「无响应超时」，强制触发降级回退到 CodeBuddy 备用引擎。
2. Playwright 模拟真实用户上传单据并点击「开始 AI 智能解析」。
3. 断言：在解析 Loading 仍在进行期间（备用模型尚未出结果），
   屏幕即弹出「首选模型无响应，已自动为您切换备用模型继续解析，请稍候…」通知。
4. 实测结束后恢复原引擎配置，不留副作用。
"""

import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15010"
ADMIN_HEADERS = {"X-Role": "admin", "Content-Type": "application/json"}

BLACKHOLE_BASE_URL = "http://10.255.255.1/v1"


def api_get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Role": "admin"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def api_put(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=ADMIN_HEADERS, method="PUT")
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    print("=" * 80)
    print("【实测：首选模型无响应需回退时，回退当下立即弹出通知（不等解析完成）】")
    print("=" * 80)

    # ---- 备份原配置 ----
    orig = api_get("/api/admin/engine-config")
    orig_cfg = orig.get("data", orig)
    print("\n[Step 1] 已备份原引擎配置（识别引擎: %s）" % orig_cfg.get("recognition_engine"))

    sample_img = os.path.abspath("demo/samples/20161001_711_thermal_receipt.jpg")
    assert os.path.exists(sample_img), "缺少测试样图: %s" % sample_img

    try:
        # ---- 首选引擎置为黑洞 + 短超时：必然无响应超时 → 触发回退 ----
        api_put("/api/admin/engine-config", {
            "recognition_engine": "openai",
            "openai_rec_base_url": BLACKHOLE_BASE_URL,
            "openai_rec_api_key": "sk-placeholder-for-fallback-test",
            "openai_rec_model": "Qwen/Qwen2.5-VL-7B-Instruct",
            "call_timeout_seconds": 5,
        })
        print("[Step 2] 首选引擎已指向黑洞地址（%s），超时 5s → 必然无响应" % BLACKHOLE_BASE_URL)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script("localStorage.setItem('demo_role', 'admin');")
            page = context.new_page()

            print("\n[Step 3] 打开首页并真实上传单据图片...")
            page.goto(BASE, wait_until="domcontentloaded")
            time.sleep(1)
            page.set_input_files("#receiptFile", sample_img)
            time.sleep(1)
            assert page.locator("#preConfirmCard").is_visible(), "未进入收据确认卡片"

            print("[Step 4] 点击「开始 AI 智能解析」...")
            t0 = time.time()
            page.locator("#preConfirmCard button.btn-success").click()

            # ---- 关键断言：Loading 期间即弹出回退通知（首选引擎 ~5s 超时后立刻触发）----
            print("[Step 5] 等待回退即时通知（预期在首选引擎超时后、备用模型出结果前出现）...")
            toast_loc = page.locator("#toastContainer .app-toast")
            notified_at = None
            loading_still_on = None
            deadline = time.time() + 60
            while time.time() < deadline:
                texts = toast_loc.all_inner_texts()
                hit = [t for t in texts if "切换备用模型继续解析" in t]
                if hit:
                    notified_at = time.time() - t0
                    loading_still_on = page.locator("#loadingCard").is_visible()
                    print("      -> 即时回退通知内容: %s" % hit[0].replace("\n", " "))
                    print("      -> 通知出现耗时: %.1fs（首选引擎 5s 超时后即刻触发）" % notified_at)
                    print("      -> 通知出现时 Loading 仍在进行（备用模型尚未出结果）: %s" % loading_still_on)
                    assert "首选模型无响应" in hit[0], "超时回退措辞必须为「首选模型无响应」: %s" % hit[0]
                    break
                time.sleep(0.5)

            assert notified_at is not None, "60s 内未出现回退即时通知"
            assert loading_still_on is True, "通知必须在解析进行中（Loading 仍在）时弹出，而非解析完成后"
            assert notified_at < 30, "通知出现过晚（%.1fs），未达到「回退当下即提示」" % notified_at

            page.screenshot(path="scripts/test_fallback_realtime_notify_result.png")
            print("      -> 截图已保存: scripts/test_fallback_realtime_notify_result.png")
            browser.close()

        print("\n" + "=" * 80)
        print("【PASS：首选模型无响应触发回退时，回退当下即弹出即时通知】")
        print("=" * 80)
    finally:
        # ---- 恢复原配置 ----
        restore_key = orig_cfg.get("openai_rec_api_key") or ""
        if (not restore_key.strip()) or ("YOUR_" in restore_key.upper()):
            # 原密钥本身为占位符（正是 SiliconFlow 始终出错的根因），
            # 新保存守卫会拒绝写回占位符 → 用非占位符替身恢复，避免残留黑洞配置
            restore_key = "sk-needs-real-key"
            print("[清理] 注意：原配置密钥为空/占位符，已用替身密钥恢复；请到引擎配置填入真实密钥")
        restore = {
            "recognition_engine": orig_cfg.get("recognition_engine") or "openai",
            "openai_rec_base_url": orig_cfg.get("openai_rec_base_url") or "",
            "openai_rec_api_key": restore_key,
            "openai_rec_model": orig_cfg.get("openai_rec_model") or "",
            "call_timeout_seconds": orig_cfg.get("call_timeout_seconds") or 90,
        }
        api_put("/api/admin/engine-config", restore)
        print("[清理] 已恢复原引擎配置（识别引擎: %s）" % restore["recognition_engine"])


if __name__ == "__main__":
    main()
