# -*- coding: utf-8 -*-
"""T05-debug：复现 stack overflow，捕获完整堆栈。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home, ROOT

SAMPLE = os.path.join(ROOT, "demo", "samples", "20161001_711_thermal_receipt.jpg")


def main(page, qa):
    errs = []
    page.on("pageerror", lambda e: errs.append(getattr(e, "stack", None) or str(e)))
    goto_home(page)
    with page.expect_file_chooser(timeout=8000) as fc_info:
        page.click("text=选择收据图片")
    fc_info.value.set_files(SAMPLE)
    page.wait_for_timeout(3000)
    qa.log(f"上传后 pageerror 数: {len(errs)}")
    for st in errs[-2:]:
        qa.log("STACK>>> " + st[:1600].replace("\n", " | ")[:1600])
    qa.shot(page, "t05dbg_after_upload")
    if not errs:
        # 再点击旋转尝试触发
        rot = page.locator("#tab-scan button:visible", has_text="旋转")
        if rot.count():
            rot.first.click()
            page.wait_for_timeout(600)
            rot.first.click()
            page.wait_for_timeout(1500)
            qa.log(f"双击旋转后 pageerror 数: {len(errs)}")
            for st in errs[-2:]:
                qa.log("STACK>>> " + st[:1600].replace("\n", " | ")[:1600])
            qa.shot(page, "t05dbg_after_rotate2")
    for st in errs:
        print("FULLSTACK:\n", st[:3000], "\n----", flush=True)


if __name__ == "__main__":
    sys.exit(run("t05dbg", main))
