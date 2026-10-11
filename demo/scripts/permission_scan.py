# -*- coding: utf-8 -*-
"""扫描所有 /api/* 路由的角色门槛，生成「端点 x 角色」权限矩阵。

门槛来自端点函数里的 require_role("角色") / require_admin 调用（静态读源码），
导出报表的逐张门槛来自 REPORTS_META 的 min_role。运行：
    python scripts/permission_scan.py --write      重新生成 docs/permission-matrix.md
测试 tests/test_b2_permission_matrix.py 对比代码与矩阵文件，两者不一致就失败。
"""

import importlib
import inspect
import os
import re
import sys

DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEMO_DIR)
MATRIX_PATH = os.path.join(os.path.dirname(DEMO_DIR), "docs", "permission-matrix.md")

_MODULES = ["api_admin", "api_auth", "api_dishes", "api_evalset", "api_export", "api_settings",
            "api_finance", "api_inventory", "api_memory", "api_phase2", "api_receipts",
            "api_receipt_lifecycle", "api_suppliers"]
_RANK = {"staff": 1, "owner": 2, "admin": 3}
ANON = "匿名可访问"


def _endpoint_table():
    from fastapi.routing import APIRoute
    from app.main import app
    table = {}
    for m in _MODULES:
        try:
            mod = importlib.import_module("app." + m)
        except ModuleNotFoundError:
            continue
        for r in mod.router.routes:
            if isinstance(r, APIRoute):
                for me in r.methods - {"HEAD", "OPTIONS"}:
                    table[(me, r.path)] = r
    for r in app.routes:
        if isinstance(r, APIRoute):
            for me in r.methods - {"HEAD", "OPTIONS"}:
                table.setdefault((me, r.path), r)
    return app, table


def scan():
    """返回 [(method, path, 角色门槛, 备注)]，按路径排序。"""
    app, table = _endpoint_table()
    paths = [(me.upper(), p) for p, v in app.openapi()["paths"].items() for me in v
             if p.startswith("/api/")]
    rows = []
    for key in sorted(set(paths), key=lambda k: (k[1], k[0])):
        route = table.get(key)
        if route is None:
            rows.append((key[0], key[1], "未知", "找不到端点函数"))
            continue
        src = inspect.getsource(route.endpoint)
        roles = []
        for r in re.findall(r'require_role\(\s*["\'](\w+)["\']', src):
            if r not in roles:
                roles.append(r)
        if "require_admin" in src:
            roles.append("admin")
        note = ""
        if "_enforce_report_role_gate" in src:
            note = "按报表分级，见下表"
        if not roles:
            rows.append((key[0], key[1], ANON, note or "不要求角色（登录接口、健康检查、签名链接）"))
            continue
        roles = sorted(set(roles), key=lambda r: _RANK.get(r, 9))
        label = roles[0] if len(roles) == 1 else "/".join(roles)
        if len(roles) > 1:
            note = (note + "；" if note else "") + "同一端点里按操作分级，最低 %s" % roles[0]
        rows.append((key[0], key[1], label, note))
    return rows


def report_rows():
    from app.api_export import REPORTS_META
    return [(r["id"], r["title"], r.get("min_role") or "staff") for r in REPORTS_META]


def render():
    rows = scan()
    lines = [
        "# 端点 x 角色权限矩阵",
        "",
        "本文件由 `demo/scripts/permission_scan.py --write` 生成，测试 `demo/tests/test_b2_permission_matrix.py` "
        "对比代码和本文件。改了端点的权限后，请重新运行生成命令并一起提交。",
        "",
        "角色从低到高：staff（店员）< owner（老板）< admin（管理员）。门槛是「至少需要的角色」，高角色自动包含低角色的权限。",
        "",
        "共 %d 个端点。" % len(rows),
        "",
        "| 方法 | 路径 | 最低角色 | 备注 |",
        "|---|---|---|---|",
    ]
    for m, p, role, note in rows:
        lines.append("| %s | `%s` | %s | %s |" % (m, p, role, note))
    lines += ["", "## 导出中心：逐张报表的最低角色", "",
              "成本、毛利、进价、货值、比价、含金额的台账一律老板及以上。", "",
              "| 报表 id | 名称 | 最低角色 |", "|---|---|---|"]
    for rid, title, role in report_rows():
        lines.append("| %s | %s | %s |" % (rid, title, role))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render()
    if "--write" in sys.argv:
        with open(MATRIX_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print("已写入", MATRIX_PATH)
    else:
        print(text)
