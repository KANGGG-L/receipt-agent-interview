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

def test_all_wrapped_audit_call_sites_log_failures():
    """凡是被 try 包裹的审计写入点，其 except 必须留 warning（不得再是裸 pass）。

    做法：先建父节点映射，对每个 `append_system_audit_log(...)` 调用找它最内层的
    包围 Try 与其 except；这样既覆盖全部被包裹站点，也不会把外层大 try（如
    guardian.check_once 的兜底 try）误算进来。

    口径说明：另有 3 处调用（api_admin.py 的 promote_grey_config / set_engine_config /
    reset_engine_config）**没有** try 包裹，失败会直接 500（响亮失败，不是静默丢弃），
    且「审计写不进就不该让改配成功」在审计完整性上更保守 —— 故本用例不为它们要求
    try/except，只要求「一旦包了就必须留日志」。
    """
    demo_dir = pathlib.Path(__file__).resolve().parents[1]
    files = ["app/api_admin.py", "app/api_memory.py", "app/services/guardian.py"]
    log_methods = ("warning", "error", "exception", "critical")
    wrapped = 0
    unwrapped = []
    for rel in files:
        tree = ast.parse((demo_dir / rel).read_text(encoding="utf-8"))
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            fname = getattr(call.func, "attr", getattr(call.func, "id", ""))
            if fname != "append_system_audit_log":
                continue
            # 向上找最内层 Try，且该调用必须位于其 body（不是 except/finally 里）
            node, handler = call, None
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.Try) and any(
                        call in ast.walk(stmt) for stmt in node.body):
                    handler = node.handlers[0] if node.handlers else None
                    break
            if handler is None:
                unwrapped.append("%s::%s" % (rel, _enclosing_func_name(call, parents)))
                continue
            wrapped += 1
            has_log = any(
                isinstance(c.func, ast.Attribute) and c.func.attr in log_methods
                for c in ast.walk(handler) if isinstance(c, ast.Call)
            )
            assert has_log, (
                "%s:%d 审计写入失败被静默吞掉（except 内无 logging 调用）"
                % (rel, handler.lineno))
    assert wrapped >= 7, "被 try 包裹的审计写入点应至少 7 个，实际 %d 个" % wrapped
    # 断言按「所在函数名」而非「绝对行号」定位这 3 个**故意不加 try** 的调用点：
    # 行号会随任何无关编辑漂移（本用例已因上游改动失败过一次），函数名才是语义锚点。
    # 仍精确锁定这 3 处（不是放宽成子集/非空），漏改或多一处都会失败。
    assert set(unwrapped) == {
        "app/api_admin.py::promote_grey_config",
        "app/api_admin.py::set_engine_config",
        "app/api_admin.py::reset_engine_config",
    }, "未被 try 包裹的审计写入点发生了变化，请复核其失败语义（响亮 500 还是静默丢弃）：%s" % unwrapped


def _enclosing_func_name(call, parents):
    """返回包住该调用节点的最内层函数/方法的限定名（含类名前缀，若有）。"""
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


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
