# -*- coding: utf-8 -*-
"""query_sql_gen 工具守护回归测试（P2-3）。

背景：v1_0_0 经审计确认存在真实缺陷（租户隔离仅对大写 WHERE 生效、黑名单漏项、
注释绕过、多语句未拦截）。该工具当前无生产消费者，缺陷属潜伏；本文件既是回归
测试，也是 v1_1_0 与 v1_0_0 修正后指标的**实测出处**（metadata.json 的指标必须与
本文件实测一致，见 test_metadata_metrics_match_measured）。

覆盖：
  1. 占位符强制：缺失 {tenant_id}（含小写/混合大小写 where）一律 PermissionError；
  2. 注释绕过：`--` 行注释 / `/* */` 块注释剥离后再判定；
  3. 多语句拒绝；
  4. 危险关键字黑名单（写操作 + DDL/危险语句）；
  5. 正常聚合语句（含占位符）正确替换放行，且输出必含租户条件；
  6. v1_0_0 缺陷基线（如实登记，用于指标复算）；
  7. metadata.json 指标与实测一致、active 指向 v1_1_0。
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai_registry.registry import ai_registry
from ai_registry.tools.query_sql_gen import v1_0_0 as gen_v1_0_0
from ai_registry.tools.query_sql_gen import v1_1_0 as gen_v1_1_0

META_PATH = os.path.join(REPO_ROOT, "ai_registry", "tools", "query_sql_gen", "metadata.json")
TOOL_DIR = os.path.dirname(META_PATH)

TENANT = "tenantA"
TENANT_LITERAL = "'" + TENANT + "'"


# ---------------------------------------------------------------------------
# 语料：v1_1_0 必须拦截的攻击（模板缺失占位符 / 危险语句 / 多语句 / 注释绕过）
# ---------------------------------------------------------------------------
ATTACK_CORPUS = [
    ("lower_where_no_placeholder", "select * from receipts where id > 0"),
    ("mixed_where_no_placeholder", "select * from receipts Where id > 0"),
    ("mixed_where_no_placeholder_2", "select * from receipts wHeRe id > 0"),
    ("upper_where_no_placeholder", "select * from receipts WHERE id > 0"),
    ("tenant_in_line_comment", "select * from receipts -- tenant_id"),
    ("tenant_in_block_comment", "select * from receipts /* tenant_id */"),
    ("multi_statement", "select * from receipts; select * from suppliers"),
    ("multi_statement_trailing", "select * from receipts; select * from suppliers;"),
    ("create", "CREATE TABLE evil (id int)"),
    ("attach", "ATTACH DATABASE '/tmp/x.db' AS x"),
    ("pragma", "PRAGMA table_info(receipts)"),
    ("vacuum", "VACUUM"),
    ("reindex", "REINDEX"),
    ("replace", "REPLACE INTO receipts (id) VALUES (1)"),
    ("copy", "COPY receipts FROM '/tmp/x.csv'"),
    ("load", "LOAD EXTENSION 'evil'"),
    ("insert", "INSERT INTO receipts (id) VALUES (1)"),
    ("update", "UPDATE receipts SET total_amount = 0"),
    ("delete", "DELETE FROM receipts"),
    ("drop", "DROP TABLE receipts"),
    ("alter", "ALTER TABLE receipts ADD COLUMN x int"),
    ("truncate", "TRUNCATE TABLE receipts"),
]

# 危险关键字最小覆盖（每个关键字都是独立攻击载荷；v1_0_0 未覆盖的 DDL/危险项单列）
DANGER_KEYWORD_CORPUS = [
    "CREATE TABLE evil (id int)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA table_info(receipts)",
    "VACUUM",
    "REINDEX",
    "REPLACE INTO receipts (id) VALUES (1)",
    "COPY receipts FROM '/tmp/x.csv'",
    "LOAD EXTENSION 'evil'",
    "INSERT INTO receipts (id) VALUES (1)",
    "UPDATE receipts SET total_amount = 0",
    "DELETE FROM receipts",
    "DROP TABLE receipts",
    "ALTER TABLE receipts ADD COLUMN x int",
    "TRUNCATE TABLE receipts",
]

# 正常只读聚合查询（含占位符）：必须被正确替换放行，且输出包含租户条件
NORMAL_CORPUS = [
    ("count_with_placeholder",
     "select count(*) from receipts where tenant_id = {tenant_id}"),
    ("sum_with_date_filter",
     "select sum(total_amount) from receipts where tenant_id = {tenant_id} "
     "and receipt_date >= '2026-01-01'"),
    ("group_by_supplier",
     "select supplier_name, count(*) from receipts where tenant_id = {tenant_id} "
     "group by supplier_name"),
    ("quoted_placeholder_form",
     "select * from receipts where tenant_id = '{tenant_id}'"),
    ("trailing_semicolon",
     "select count(*) from receipts where tenant_id = {tenant_id};"),
    ("with_line_comment",
     "select count(*) from receipts where tenant_id = {tenant_id} -- only my tenant"),
    ("semicolon_inside_string",
     "select count(*) from receipts where tenant_id = {tenant_id} and note = 'a;b'"),
    ("keyword_inside_string",
     "select count(*) from receipts where tenant_id = {tenant_id} "
     "and note = 'drop table x'"),
]

V1_0_0_AUDIT_CORPUS = ATTACK_CORPUS


def _meta():
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _raises(sql: str, tenant: str = TENANT) -> bool:
    """v1_1_0 是否拦截（raise PermissionError）。"""
    try:
        gen_v1_1_0.Tool().execute(sql, tenant)
        return False
    except PermissionError:
        return True


# ---------------------------------------------------------------------------
# 1. 占位符强制：小写 / 混合大小写 where 一律拒绝
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id,sql", ATTACK_CORPUS, ids=[c[0] for c in ATTACK_CORPUS])
def test_attack_corpus_all_blocked(case_id, sql):
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute(sql, TENANT)


@pytest.mark.parametrize("sql", [
    "select * from receipts where id > 0",
    "select * from receipts Where id > 0",
    "select * from receipts wHeRe id > 0",
    "select * from receipts WHERE id > 0",
])
def test_lowercase_and_mixed_case_where_requires_placeholder(sql):
    """小写/混合大小写 where 不再原样放行：缺占位符即拒（v1_0_0 的实测缺陷）。"""
    with pytest.raises(PermissionError) as exc:
        gen_v1_1_0.Tool().execute(sql, TENANT)
    assert "tenant_id" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. 注释绕过
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "select * from receipts -- tenant_id",
    "select * from receipts /* tenant_id */",
    "select * from receipts where id > 0 -- {tenant_id}",
    "select * from receipts where id > 0 /* {tenant_id} */",
    "select * from receipts\n-- tenant_id\n",
    "select * from receipts\n/* tenant_id */",
])
def test_comment_cannot_smuggle_placeholder_or_keywords(sql):
    """注释里的 tenant_id / 占位符不算数：剥离注释后缺占位符即拒。"""
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute(sql, TENANT)


def test_comment_content_is_stripped_not_treated_as_statement():
    """注释内容被剥离：既不能充当占位符，也不会把注释里的危险关键字当成语句。"""
    # 注释里的危险关键字 + 缺占位符 -> 仍拒（原因是缺占位符）
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute("select 1 -- DROP TABLE receipts", TENANT)
    # 注释里的占位符不算数 -> 缺占位符即拒（防注释绕过）
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute("select * from receipts /* {tenant_id} */", TENANT)
    # 注释里的危险关键字被剥离 -> 正常查询照常放行，且产物不含该注释
    out = gen_v1_1_0.Tool().execute(
        "select count(*) from receipts where tenant_id = {tenant_id} "
        "/* DROP TABLE receipts */", TENANT)
    assert "DROP" not in out
    assert TENANT_LITERAL in out


# ---------------------------------------------------------------------------
# 3. 多语句
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "select * from receipts; select * from suppliers",
    "select * from receipts where tenant_id = {tenant_id}; drop table receipts",
    "select * from receipts where tenant_id = {tenant_id};select * from suppliers",
    "select * from receipts; select * from suppliers;",
])
def test_multi_statement_rejected(sql):
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute(sql, TENANT)


def test_single_trailing_semicolon_allowed():
    """单条语句的合法结尾分号不算多语句。"""
    out = gen_v1_1_0.Tool().execute(
        "select count(*) from receipts where tenant_id = {tenant_id};", TENANT)
    assert TENANT_LITERAL in out
    assert ";" not in out


# ---------------------------------------------------------------------------
# 4. 危险关键字黑名单
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sql", DANGER_KEYWORD_CORPUS)
def test_danger_keywords_blocked(sql):
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute(sql, TENANT)


# ---------------------------------------------------------------------------
# 5. 正常聚合语句放行 + 输出必含租户条件
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id,sql", NORMAL_CORPUS, ids=[c[0] for c in NORMAL_CORPUS])
def test_normal_readonly_query_allowed_with_tenant_condition(case_id, sql):
    out = gen_v1_1_0.Tool().execute(sql, TENANT)
    assert out is not None
    assert TENANT_LITERAL in out, f"{case_id}: 输出必须含租户条件"
    assert "tenant_id" in out
    assert "{tenant_id}" not in out, "占位符必须被替换干净"


def test_output_always_contains_tenant_condition():
    """正向断言：任一正常模板的产物都必须带租户隔离条件（不允许裸查询落库）。"""
    out = gen_v1_1_0.Tool().execute(
        "select count(*) from receipts where tenant_id = {tenant_id}", TENANT)
    assert out == "select count(*) from receipts where tenant_id = 'tenantA'"


def test_quoted_placeholder_not_double_quoted():
    """已带引号的占位符写法不得产生重复引号。"""
    out = gen_v1_1_0.Tool().execute(
        "select * from receipts where tenant_id = '{tenant_id}'", TENANT)
    assert out == "select * from receipts where tenant_id = 'tenantA'"


def test_tenant_value_is_escaped():
    """租户值里的单引号必须加倍转义，不得形成可注入的字符串断点。"""
    malicious = "a'; DROP TABLE receipts --"
    out = gen_v1_1_0.Tool().execute(
        "select count(*) from receipts where tenant_id = {tenant_id}", malicious)
    assert "'a''; DROP TABLE receipts --'" in out
    assert "a'; DROP TABLE receipts --" not in out or "a''; DROP" in out


def test_empty_tenant_rejected():
    with pytest.raises(PermissionError):
        gen_v1_1_0.Tool().execute(
            "select count(*) from receipts where tenant_id = {tenant_id}", "   ")


# ---------------------------------------------------------------------------
# 6. v1_0_0 缺陷基线（如实登记，用于指标复算）
# ---------------------------------------------------------------------------
def test_v1_0_0_lowercase_where_leaks_cross_tenant():
    """复现 v1_0_0 真实缺陷：小写 where 原样返回，无租户条件（跨租户 SQL）。"""
    out = gen_v1_0_0.Tool().execute("select * from receipts where id > 0", TENANT)
    assert out is not None
    assert "tenant_id" not in out, "缺陷复现：v1_0_0 对小写 where 未注入租户条件"
    assert TENANT_LITERAL not in out


def test_v1_0_0_comment_bypass():
    """复现 v1_0_0 注释绕过：-- tenant_id 让含 tenant_id 检查误判，跳过注入。"""
    out = gen_v1_0_0.Tool().execute("select * from receipts -- tenant_id", TENANT)
    assert out is not None
    assert "tenant_id = " not in out


# ---------------------------------------------------------------------------
# 7. metadata 指标与实测一致 + active 指向 v1_1_0
# ---------------------------------------------------------------------------
def test_metadata_active_points_to_v1_1_0_and_matches_files():
    meta = _meta()
    assert meta["active_version"] == "v1_1_0"
    py_versions = {f[:-3] for f in os.listdir(TOOL_DIR) if f.endswith(".py")}
    assert set(meta["versions"].keys()) == py_versions, "metadata versions 与目录 .py 文件不一致"
    assert os.path.exists(os.path.join(TOOL_DIR, "v1_0_0.py")), "旧版本文件必须保留"


def test_v1_0_0_false_metric_corrected():
    """v1_0_0 的 interception_rate=100.0 为无支撑的虚假指标，必须已纠偏。"""
    old = _meta()["versions"]["v1_0_0"]
    metrics = old.get("metrics", {})
    assert metrics.get("claim_unsupported") is True
    assert metrics.get("reproduce_command")
    assert old.get("changelog"), "必须补 changelog 记录缺陷"


def test_metadata_metrics_match_measured():
    """metadata 里的指标必须是实测值：与本次实测的语料规模/结果逐项对齐。"""
    meta = _meta()
    old_metrics = meta["versions"]["v1_0_0"]["metrics"]
    new_metrics = meta["versions"]["v1_1_0"]["metrics"]

    # v1_1_0：攻击语料全部拦截
    blocked = sum(1 for _, sql in ATTACK_CORPUS if _raises(sql))
    assert new_metrics["attack_corpus_total"] == len(ATTACK_CORPUS)
    assert new_metrics["attack_corpus_blocked"] == blocked == len(ATTACK_CORPUS)
    assert new_metrics["interception_rate"] == pytest.approx(100.0)
    assert new_metrics["normal_corpus_total"] == len(NORMAL_CORPUS)
    assert new_metrics["false_positive_rate"] == pytest.approx(0.0)
    assert new_metrics["reproduce_command"]

    # v1_0_0：以 raise PermissionError 为准的拦截口径（远低于原登记 100.0）
    blocked_old = sum(1 for _, sql in V1_0_0_AUDIT_CORPUS if _v1_0_0_raises(sql))
    total_old = len(V1_0_0_AUDIT_CORPUS)
    assert old_metrics["attack_corpus_total"] == total_old
    assert old_metrics["attack_corpus_blocked"] == blocked_old
    assert old_metrics["interception_rate"] == pytest.approx(
        100.0 * blocked_old / total_old, abs=0.1)
    assert old_metrics["interception_rate"] < 100.0


def _v1_0_0_raises(sql: str, tenant: str = TENANT) -> bool:
    try:
        gen_v1_0_0.Tool().execute(sql, tenant)
        return False
    except PermissionError:
        return True


def test_registry_get_tool_returns_v1_1_0():
    tool = ai_registry.get_tool("query_sql_gen")
    out = tool.execute("select count(*) from receipts where tenant_id = {tenant_id}", TENANT)
    assert TENANT_LITERAL in out
    with pytest.raises(PermissionError):
        tool.execute("select * from receipts where id > 0", TENANT)
