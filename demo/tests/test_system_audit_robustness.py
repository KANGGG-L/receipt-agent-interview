# -*- coding: utf-8 -*-
"""系统审计链路健壮性回归用例（审计能力不得静默缺失）。

why：`db.append_system_audit_log` 原先用 `json.loads(row.value or "[]")` 解析历史值，
而 7 个调用点（api_admin 2 / api_memory 2 / guardian 3）全部是
`try: ... except Exception: pass`。两者叠加的后果是：只要 `system_audit_json` 被写坏一次，
`json.loads` 每次调用都会抛错 → 被 7 个调用点静默吞掉 → **审计链路外观完全正常、
实际一条也写不进去，且没有任何日志可查**（财务/复核系统最不该静默的一类）。

本文件守住三层：
1. 写入侧：坏值（非 JSON / 非数组）必须显式重建 + 告警，后续审计照常落库；
2. 读取侧：坏值不得让审计面板整页 500，按空返回并告警；
3. 调用点：所有 try/except 必须留 warning（AST 结构守卫，防「修 A 漏 B」）。
"""

import ast
import logging
import pathlib

import pytest

from app import db


# ---------------- 第一层：写入侧自愈 ----------------

def test_append_rebuilds_corrupted_value_and_warns(caplog):
    """历史值不是合法 JSON -> 重建为空数组 + 告警 + 本次审计仍成功落库。"""
    _seed_raw_value("{这不是 JSON")

    with caplog.at_level(logging.WARNING):
        db.append_system_audit_log("tester", "unit_action", "some_field", "a", "b")

    logs = db.read_system_audit_log()
    assert len(logs) == 1
    assert logs[0]["action"] == "unit_action"
    assert logs[0]["who"] == "tester"
    assert any("损坏无法解析" in str(r.getMessage()) for r in caplog.records), \
        "重建必须留 warning，否则审计缺失不可观测"


def test_append_rebuilds_non_list_value_and_warns(caplog):
    """历史值是合法 JSON 但不是数组 -> 同样重建 + 告警。"""
    _seed_raw_value('{"unexpected": "object"}')

    with caplog.at_level(logging.WARNING):
        db.append_system_audit_log("tester", "unit_action2", "f", "", "x")

    logs = db.read_system_audit_log()
    assert len(logs) == 1
    assert logs[0]["action"] == "unit_action2"
    assert any("不是数组" in str(r.getMessage()) for r in caplog.records)


def test_append_appends_to_existing_list():
    """正常路径：已有历史条目必须保留（不得重建覆盖）。"""
    _seed_raw_value('[{"who": "old", "action": "legacy", "field": "", "old": "", "new": "", "ts": "t"}]')

    db.append_system_audit_log("tester", "unit_action3", "f", "", "x")

    logs = db.read_system_audit_log()
    assert [x["action"] for x in logs] == ["legacy", "unit_action3"], \
        "坏值才重建，好值必须原样追加（不得改变正常路径语义）"


# ---------------- 第二层：读取侧不得 500 ----------------

def test_read_returns_empty_on_corrupted_value(caplog):
    _seed_raw_value("<xml>nope</xml>")
    with caplog.at_level(logging.WARNING):
        assert db.read_system_audit_log() == []
    assert any("按空返回" in str(r.getMessage()) for r in caplog.records)


# ---------------- 第三层：调用点结构守卫 ----------------

_LOG_METHODS = ("warning", "error", "exception", "critical")


def _call_name(call):
    """调用节点的函数名（attr 优先，兼容 db.append_system_audit_log / 本地助手调用）。"""
    return getattr(call.func, "attr", getattr(call.func, "id", ""))


def _build_parents(tree):
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_func_name(call, parents):
    """返回包住该调用节点的最内层函数/方法的限定名（含类名前缀，若有）。"""
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def _innermost_enclosing_try(call, parents):
    """向上找最内层 Try，且该调用必须位于其 body（不是 except/finally 里）。"""
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Try) and any(
                call in ast.walk(stmt) for stmt in node.body):
            return node
    return None


def _handler_logs(handler):
    """该 except 分支里是否存在 logging 级别的调用（warning/error/exception/critical）。"""
    return any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        and c.func.attr in _LOG_METHODS
        for c in ast.walk(handler) if isinstance(c, ast.Call)
    )


def _body_without_docstring(node):
    """函数体去掉 docstring 后的语句列表（用于识别「薄包装」助手）。"""
    return [
        s for s in node.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]


def _safe_audit_wrapper_names(tree, parents):
    """识别「安全审计助手」：函数体（去 docstring）恰为单个 Try，其 body 内直接调用
    append_system_audit_log，且每个 except 分支内都有 logging 调用。

    这类助手（如 api_memory._safe_audit）本身就是一个「审计写入 + try + warning」的
    封装，它的**调用点**因此等价于一个被 try 包裹的审计写入点。识别要求函数体是
    单 Try 的薄包装形态：大端点（如 api_admin 的改配函数）虽然也含 try+审计，但其
    函数体远不止一个 Try，不会被误认成助手。
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = _body_without_docstring(node)
        if len(body) != 1 or not isinstance(body[0], ast.Try):
            continue
        tr = body[0]
        direct = [
            c for c in ast.walk(tr)
            if isinstance(c, ast.Call) and _call_name(c) == "append_system_audit_log"
            and any(c in ast.walk(stmt) for stmt in tr.body)
        ]
        if not direct:
            continue
        if tr.handlers and all(_handler_logs(h) for h in tr.handlers):
            names.add(node.name)
    return names


def test_all_wrapped_audit_call_sites_log_failures():
    """凡是被 try 包裹的审计写入点，其 except 必须留 warning（不得再是裸 pass）。

    做法：先建父节点映射，对每个 `append_system_audit_log(...)` 调用找它最内层的
    包围 Try 与其 except；这样既覆盖全部被包裹站点，也不会把外层大 try（如
    guardian.check_once 的兜底 try）误算进来。

    覆盖口径演进（本轮结构同步，覆盖语义不变）：
    api_memory.py 的 approve/reject 两处**各自内联**的 try+warning 审计写入，已被
    并发重构合并为一个安全助手 `_safe_audit(...)`（自身 try + except 内 logging），
    端点改为调用该助手，后续删除/恢复端点也复用同一助手。若不识别助手，扫描器眼里
    「被 try 包裹的写入点」会从 7 掉到 6 —— 但覆盖**语义**并未退化（每条写入路径仍被
    try+warning 保护）。故本用例额外识别「经过 try+warning 安全助手的审计写入路径」，
    把助手的调用点等价计为一个已包裹的写入点，从而恢复 >= 7 的覆盖口径；这比原来更强
    （新增「助手自身必须保持 try + except logging」的结构断言，防止助手被改成裸调用
    后调用点悄悄失去保护）。

    口径说明：另有 3 处调用（api_admin.py 的 promote_grey_config / set_engine_config /
    reset_engine_config）**没有** try 包裹，失败会直接 500（响亮失败，不是静默丢弃），
    且「审计写不进就不该让改配成功」在审计完整性上更保守 —— 故本用例不为它们要求
    try/except，只要求「一旦包了就必须留日志」。这 3 处仍被精确锁定，不是放宽成子集。
    """
    demo_dir = pathlib.Path(__file__).resolve().parents[1]
    files = ["app/api_admin.py", "app/api_memory.py", "app/services/guardian.py"]
    wrapped = 0
    unwrapped = []
    wrappers_by_file = {}
    wrapped_by_file = {}
    for rel in files:
        tree = ast.parse((demo_dir / rel).read_text(encoding="utf-8"))
        parents = _build_parents(tree)
        wrappers = _safe_audit_wrapper_names(tree, parents)
        wrappers_by_file[rel] = wrappers
        file_wrapped = 0

        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            fname = _call_name(call)
            if fname == "append_system_audit_log":
                # 安全助手体内的实现调用：其覆盖在助手的调用点统一计一次，避免重复计数；
                # 助手自身是否为 try+logging 由下面 wrappers_by_file 的结构断言单独守。
                if _enclosing_func_name(call, parents) in wrappers:
                    continue
                try_node = _innermost_enclosing_try(call, parents)
                if try_node is None:
                    unwrapped.append("%s::%s" % (rel, _enclosing_func_name(call, parents)))
                    continue
                wrapped += 1
                file_wrapped += 1
                assert try_node.handlers and all(_handler_logs(h) for h in try_node.handlers), (
                    "%s:%d 审计写入失败被静默吞掉（except 内无 logging 调用）"
                    % (rel, try_node.lineno))
            elif fname in wrappers:
                # 经过 try+warning 安全助手的审计写入路径：等价于一个已包裹的写入点。
                wrapped += 1
                file_wrapped += 1

        wrapped_by_file[rel] = file_wrapped

    # 新增结构断言：api_memory.py 必须保留 try + except(logging) 的安全审计助手。
    # 若助手被改成裸调用或删除，识别结果为空 -> 这里失败；同时其内部裸调用会落入
    # unwrapped（下面锁定集合随之失败），构成双重兜底。
    assert wrappers_by_file.get("app/api_memory.py"), (
        "app/api_memory.py 未识别到 try+except(logging) 的审计助手（_safe_audit 类）："
        "助手被改造成裸调用或删除，审计安全包装语义已失效")

    assert wrapped >= 7, "被 try 包裹的审计写入点应至少 7 个，实际 %d 个" % wrapped
    # 覆盖口径拆解（api_admin 2 + api_memory 助手调用 + guardian 3），逐文件设下限，
    # 防止某一路径被整体删掉却因总量仍达标而漏网（比单一总量断言更强）。
    assert wrapped_by_file["app/api_admin.py"] >= 2, wrapped_by_file
    assert wrapped_by_file["app/api_memory.py"] >= 2, wrapped_by_file
    assert wrapped_by_file["app/services/guardian.py"] >= 3, wrapped_by_file

    # 断言按「所在函数名」而非「绝对行号」定位这 3 个**故意不加 try** 的调用点：
    # 行号会随任何无关编辑漂移（本用例已因上游改动失败过一次），函数名才是语义锚点。
    # 仍精确锁定这 3 处（不是放宽成子集/非空），漏改或多一处都会失败。
    assert set(unwrapped) == {
        "app/api_admin.py::promote_grey_config",
        "app/api_admin.py::set_engine_config",
        "app/api_admin.py::reset_engine_config",
        # 2026-10-10 复核纳入：save/delete_engine_preset 随自定义预设特性
        # （bc260e7）新增，沿用与上述三点一致的「响亮 500」语义 —— 审计写入
        # 失败不得静默丢弃管理台操作留痕，故与引擎配置三点同列豁免。
        "app/api_admin.py::save_engine_preset",
        "app/api_admin.py::delete_engine_preset",
    }, "未被 try 包裹的审计写入点发生了变化，请复核其失败语义（响亮 500 还是静默丢弃）：%s" % unwrapped


@pytest.fixture(autouse=True)
def _restore_audit_row():
    """用例前后把 system_audit_json 还原为进入时的状态，绝不向库留残留。

    why 必须做：本文件可能落到一个**被 env 声明、跨 run 持久**的测试库（同批多个文件在
    导入期用 `os.environ.setdefault("DB_PATH", "/tmp/...")` 声明固定路径；conftest 对
    「等于 env 声明的环境库」按 ambient 处理、不做隔离，且 pytest 在 collection 期就导入
    全部模块，先到先得）。向共享库写死键而不还原，会让**后续任何一次复用该库的 run**
    读到本用例留下的值 —— 这正是本文件早期版本与兄弟文件互相干扰的一半原因。
    """
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "system_audit_json")
        before = row.value if row is not None else None
    finally:
        s.close()
    yield
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "system_audit_json")
        if before is None:
            if row is not None:
                s.delete(row)
        elif row is None:
            s.add(db._AppSettingRow(key="system_audit_json", value=before))
        else:
            row.value = before
        s.commit()
    finally:
        s.close()


def _seed_raw_value(raw: str):
    """把 app_settings.system_audit_json 置为给定原始串（模拟历史坏值）。

    why 用 upsert 而不是 `s.add(...)`：`app_settings.key` 是主键，而本文件可能落到上面
    那种**跨 run 持久**的共享测试库。盲插在主键已存在时必抛
    `IntegrityError: UNIQUE constraint failed: app_settings.key` —— 实测「同一组合第二次
    跑」即稳定 3 failed（单跑绿只是因为那一次该行尚不存在）。故此处幂等 upsert，
    并由 `_restore_audit_row` 收尾还原。
    """
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "system_audit_json")
        if row is None:
            s.add(db._AppSettingRow(key="system_audit_json", value=raw))
        else:
            row.value = raw
        s.commit()
    finally:
        s.close()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
