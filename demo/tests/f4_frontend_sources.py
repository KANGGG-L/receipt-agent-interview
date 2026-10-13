# -*- coding: utf-8 -*-
"""F4 S4-11 共享前端源码读取助手（pytest 侧）。

main.js 按域拆分后，源码级扫描类判据的守真不变量口径＝「main.js + 全部已抽离域文件」
联合全文（与浏览器实际加载的代码集合同构）。域文件清单与 demo/tests/f4_load_domains.js
（node harness 侧）保持同一顺序。
"""
import os

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 顺序＝index.html 中 main.js 之后的域文件加载顺序
# F5·S4-11（2026-10-13）：main.js 收口拆分，前 8 项为 F5 新增（common.js 内含
# apiFetch 装配块，在 index.html 中紧跟 main.js、先于其余域文件加载）。
F4_DOMAIN_FILES = [
    "static/js/common.js",
    "static/js/upload.js",
    "static/js/review_core.js",
    "static/js/workbench_queue.js",
    "static/js/review_modal.js",
    "static/js/version_conflict.js",
    "static/js/photo_sider.js",
    "static/js/finance_ops.js",
    "static/js/supplier_archive.js",
    "static/js/dish_consumption.js",
    "static/js/cost_report.js",
    "static/js/departments.js",
    "static/js/inventory_board.js",
    "static/js/archive_list.js",
    "static/js/kpi_trend.js",
    "static/js/admin_observation.js",
    "static/js/dish_bom.js",
    "static/js/parsing_logs.js",
    "static/js/engine_admin.js",
]


def read_main_js():
    with open(os.path.join(_DEMO_DIR, "static", "js", "main.js"), encoding="utf-8") as f:
        return f.read()


def read_domain(rel):
    with open(os.path.join(_DEMO_DIR, rel), encoding="utf-8") as f:
        return f.read()


def frontend_union():
    """main.js + 全部域文件联合全文。"""
    return "\n".join([read_main_js()] + [read_domain(r) for r in F4_DOMAIN_FILES])


def frontend_union_with(*rel_paths):
    """main.js + 指定域文件联合全文（判据明确知道目标在哪个域时用这个，扫描面更小）。"""
    parts = [read_main_js()]
    for rel in rel_paths:
        parts.append(read_domain(rel))
    return "\n".join(parts)
