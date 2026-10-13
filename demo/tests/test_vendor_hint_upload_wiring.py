# -*- coding: utf-8 -*-
"""B3「上传前可选供应商（vendor_hint）」的前端接线守卫。

why：本项目高发缺陷之一是「定义了没接线」——后端 `/api/upload` 早有
`vendor_hint: str = Form("")` 形参、识别链路也早有首轮供应商记忆检索分支，但前端
从不发送该字段（`grep vendor_hint demo/static demo/templates` 零命中），整条注入
通道在生产上不可达。这里把「下拉存在 + 单张上传真的带上 vendor_hint + 该设备上次
选择被记住」三件事钉住，避免它再次静默断线。

消费方（断言之外的事实，供核对）：
- `app/api_receipts.py` 的 `upload_receipt(vendor_hint: str = Form(""))` 读该字段，
  传 `start_recognition_job(image_path, vendor_hint=...)`，并把 `has_vendor_hint`
  布尔位落 user_event（PDPO：不落供应商提示原文）。
- `app/chains/extract_chain.py` 在首轮按 `if vendor_hint:` 发起记忆检索。

只读文本断言，不导入 app、不碰 DB / 向量库 / 网络。
路径用 __file__ 定位（历史教训：写死 "demo/..." 只在 cwd=仓库根 时通过，从 demo/
下跑会产生假回归）。
"""
import re
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parents[1]
_INDEX_HTML = _DEMO_DIR / "templates" / "index.html"
_MAIN_JS = _DEMO_DIR / "static" / "js" / "main.js"


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _main_js_src():
    """F5 S4-11：main.js 已拆为组装层 + 域文件；源码级守卫用联合全文
    （main.js+全部域文件，与浏览器实际加载的代码集合同构）。"""
    import sys as _sys
    _tests_dir = str(_DEMO_DIR / "tests")
    if _tests_dir not in _sys.path:
        _sys.path.insert(0, _tests_dir)
    from f4_frontend_sources import frontend_union
    return frontend_union()


# ------------------------------------------------------------------
# 1. 上传卡片里有供应商下拉，默认空选项
# ------------------------------------------------------------------
def test_vendor_hint_select_exists_in_upload_card():
    html = _read(_INDEX_HTML)
    m = re.search(r'<select id="vendorHintSelect".*?</select>', html, re.S)
    assert m is not None, "上传卡片必须存在 #vendorHintSelect 下拉"
    block = m.group(0)
    assert '<option value="">未选择（自动识别）</option>' in block, \
        "默认必须是无值空选项：未选择时提交空串，行为与接线前一致"

    # 必须落在上传卡片内（#uploadArea 到 splitViewArea 之间）
    upload_area = html.split('id="uploadArea"', 1)[1].split('id="splitViewArea"', 1)[0]
    assert 'id="vendorHintSelect"' in upload_area, \
        "下拉必须在上传卡片内（用户上传前就能选）"


# ------------------------------------------------------------------
# 2. 单张上传真的把 vendor_hint 放进 FormData
# ------------------------------------------------------------------
def test_single_upload_formdata_carries_vendor_hint():
    js = _main_js_src()
    assert re.search(r"formData\.append\(\s*'vendor_hint'\s*,\s*getVendorHintValue\(\)\s*\)", js), \
        "单张上传的 FormData 必须 append('vendor_hint', getVendorHintValue())"

    # 该 append 必须落在 triggerAnalysisNow 的 /api/upload 请求体里，
    # 而不是别处（避免「写了但不在真正发请求的那个 FormData 上」）
    fn = js.split("function triggerAnalysisNow(", 1)[1].split("\nfunction ", 1)[0]
    assert "formData.append('vendor_hint', getVendorHintValue());" in fn
    assert "/api/upload?" in fn, "triggerAnalysisNow 应仍是单张上传（/api/upload）的发起处"


# ------------------------------------------------------------------
# 3. 下拉的取值 / 填充 / 记忆三条接线都在
# ------------------------------------------------------------------
def test_vendor_hint_helpers_defined():
    js = _main_js_src()
    for fn_name in ("getVendorHintValue", "populateVendorHintOptions", "initVendorHintSelect"):
        assert re.search(r"function\s+%s\s*\(" % fn_name, js), \
            "缺少函数定义：%s" % fn_name
    assert re.search(r"const VENDOR_HINT_STORAGE_KEY\s*=\s*'vendor_hint_last'", js), \
        "localStorage key 必须是 snake_case 的 vendor_hint_last"


def test_vendor_hint_options_wired_to_existing_supplier_source():
    js = _main_js_src()
    # 选项数据源必须复用既有 loadSuppliersData（不额外发请求、随既有刷新点一起更新）
    body = js.split("function loadSuppliersData()", 1)[1].split("\n}\n", 1)[0]
    assert "populateVendorHintOptions(suppliers);" in body, \
        "loadSuppliersData 必须用同一份供应商列表填充下拉"


def test_vendor_hint_init_called_on_dom_ready():
    js = _read(_MAIN_JS)
    dom_ready = js.split("addEventListener('DOMContentLoaded'", 1)[1].split("});", 1)[0]
    assert "initVendorHintSelect();" in dom_ready, \
        "initVendorHintSelect 必须在 DOMContentLoaded 里被调用（否则下拉不会被恢复）"
