# -*- coding: utf-8 -*-
"""Q1 回归守卫：杜绝「同名重复实现」与「被遮蔽的不可达路由」再次出现。

why（缺陷回顾）：本仓库曾同时存在两类肉眼难查的同源缺陷——
1. 同名重复实现：db.py 里同一函数被先后定义两次，后一份遮蔽前一份，前一份
   沦为死代码（历史：create_experiment / start_experiment / stop_experiment /
   conclude_experiment 各两份，其中 create_experiment 返回值契约还不同：
   旧份返回 int、新份返回 dict）；
2. 不可达路由：api_phase2.py 与 api_admin.py 注册了完全相同的 (method, path)，
   而 main.py 先 include api_admin，导致 api_phase2 的同名路由永远匹配不到。

两类缺陷都不会让 import 失败、也不会让接口报错（返回 200 的是先注册的那份），
因此只能靠静态断言锁住：前者用 AST 扫顶层定义重名，后者用 app.routes 扫
(method, path) 重复。
"""

import ast
import os

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_DIR = os.path.join(_DEMO_DIR, "app")


def _iter_app_py():
    for root, _dirs, files in os.walk(_APP_DIR):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _duplicate_top_level_defs(path):
    """返回 {名字: [行号, ...]}，仅含在同一文件顶层重复定义的函数/类。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    seen = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen.setdefault(node.name, []).append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def test_no_duplicate_top_level_defs_in_app():
    """app/ 下任何模块都不得出现同名顶层函数/类（后一份会静默遮蔽前一份）。"""
    offenders = {}
    for path in _iter_app_py():
        dups = _duplicate_top_level_defs(path)
        if dups:
            offenders[os.path.relpath(path, _DEMO_DIR)] = dups
    assert offenders == {}, f"存在被遮蔽的同名重复定义：{offenders}"


def test_no_duplicate_route_method_path_pairs():
    """app.routes 中不得存在重复的 (method, path)：先注册者生效，后者不可达。"""
    from app.main import app

    seen = {}
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or ()
        if not path or not methods:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            seen.setdefault((m, path), []).append(
                getattr(getattr(r, "endpoint", None), "__module__", "?"))
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert dups == {}, f"存在被遮蔽的不可达路由：{dups}"


def test_experiments_post_route_is_bound_once():
    """POST /api/admin/experiments 必须有且仅有一个注册，且绑定到 api_admin。"""
    from app.main import app

    hits = [
        r for r in app.routes
        if getattr(r, "path", None) == "/api/admin/experiments"
        and "POST" in (getattr(r, "methods", None) or ())
    ]
    assert len(hits) == 1, f"POST /api/admin/experiments 注册数应为 1，实际 {len(hits)}"
    assert hits[0].endpoint.__module__ == "app.api_admin", \
        f"应绑定到 app.api_admin，实际 {hits[0].endpoint.__module__}"
