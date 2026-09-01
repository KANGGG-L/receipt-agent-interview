# -*- coding: utf-8 -*-
"""T05 收据识别主流程：文件选择器上传 → 图像编辑控件(旋转) → 解析 → 结果/错误观测。
真实调用一次识别引擎；若引擎失败则记录错误反馈 UX。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home, ROOT

SAMPLE = os.path.join(ROOT, "demo", "samples", "20161001_711_thermal_receipt.jpg")


def toast_texts(page):
    return page.eval_on_selector_all(
        ".app-toast", "els => els.map(e => e.innerText.trim().slice(0,120))")


def main(page, qa):
    assert os.path.exists(SAMPLE), "样本图不存在"
    hits = []
    page.on("response", lambda r: hits.append(f"{r.status} {r.url[-70:]}") if r.status >= 400 else None)

    goto_home(page)

    # 1) 文件选择器上传
    with page.expect_file_chooser(timeout=8000) as fc_info:
        page.click("text=选择收据图片")
    fc_info.value.set_files(SAMPLE)
    qa.log("已通过文件选择器选择样本图")
    page.wait_for_timeout(2500)
    qa.shot(page, "t05_uploaded_editor")

    # 编辑器控件枚举
    ctrls = page.eval_on_selector_all(
        "#tab-scan button:visible, #tab-scan input:visible, #tab-scan select:visible",
        "els => els.slice(0, 30).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, t: (e.innerText || e.placeholder || e.value || '').trim().slice(0, 14)}))",
    )
    qa.log("编辑器控件: " + json.dumps(ctrls, ensure_ascii=False))

    # 2) 旋转控件（点一次 90°，观察烘焙预览）
    rot = page.locator("#tab-scan button:visible", has_text="旋转")
    qa.log(f"旋转按钮数={rot.count()}")
    if rot.count():
        rot.first.click()
        page.wait_for_timeout(1800)
        qa.shot(page, "t05_after_rotate")

    # 3) 触发解析（勾选「立即开始分析」或点解析按钮）
    auto = page.locator("#tab-scan input[type=checkbox]:visible")
    analyze_btn = page.locator("#tab-scan button:visible", has_text="解析")
    qa.log(f"解析按钮: {analyze_btn.count()} 个; 首个文本={analyze_btn.first.inner_text() if analyze_btn.count() else '-'}")
    if analyze_btn.count():
        analyze_btn.first.click()
    page.wait_for_timeout(1200)
    qa.shot(page, "t05_analyzing")
    qa.log(f"解析中 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")

    # 4) 等待结果（最长 120s）
    t0 = time.time()
    result_seen = None
    while time.time() - t0 < 120:
        txts = page.eval_on_selector(
            "#tab-scan",
            "e => { const t = e.innerText; if (t.includes('解析完成') || t.includes('识别结果') || (t.includes('字段') && t.includes('供应商'))) return 'done'; if (t.includes('解析失败') || t.includes('失败')) return 'fail'; return null; }",
        )
        if txts:
            result_seen = txts
            break
        page.wait_for_timeout(1000)
    dt = time.time() - t0
    qa.log(f"解析等待 {dt:.0f}s → {result_seen}")
    qa.shot(page, "t05_result")
    qa.log(f"结束 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
    qa.log("4xx/5xx: " + (json.dumps(hits, ensure_ascii=False) if hits else "无"))


if __name__ == "__main__":
    sys.exit(run("t05_upload_flow", main))
