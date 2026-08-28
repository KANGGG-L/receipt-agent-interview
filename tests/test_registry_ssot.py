# -*- coding: utf-8 -*-
"""T12 ai_registry 唯一来源收敛（SSOT）验收测试（产品 AC-01~AC-20）。

原则：register-then-switch + golden 逐字节等价为行为一致性证明。

断言分组：
  A 链路加载唯一性    AC-01~AC-05
  B 内联零残留        AC-06
  C 镜像退役形态      AC-07, AC-08（含 /api/admin/prompts 形状）
  D metadata 自洽     AC-09~AC-12
  E math_engine 单实现 AC-13~AC-15
  F mcp 如实          AC-16
  G pii_masker 可导入 AC-17
  H 离线冒烟          AC-18, AC-19
  I 零回归            AC-20（math 消费测试零改动守卫）
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO_DIR = os.path.join(REPO_ROOT, "demo")
for _p in (DEMO_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["AUTH_ENABLED"] = "0"

GOLDEN_DIR = os.path.join(REPO_ROOT, "tests", "golden")


def _golden(name):
    with open(os.path.join(GOLDEN_DIR, name), "rb") as f:
        return f.read().decode("utf-8")


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def _meta(scene):
    path = os.path.join(REPO_ROOT, "ai_registry", "prompts", scene, "metadata.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# A 链路加载唯一性（AC-01~AC-05）
# ============================================================

def test_ac01_extract_system_prompt_from_registry():
    """AC-01: extract_chain.SYSTEM_PROMPT 经 ai_registry 加载,与 golden v1_2_8 逐字节等价。"""
    from ai_registry.registry import ai_registry
    from app.chains import extract_chain
    assert extract_chain.SYSTEM_PROMPT == _golden("extract_v1_2_8_anti_injection.txt")
    assert extract_chain.SYSTEM_PROMPT == ai_registry.get_prompt("extract")
    # 源码不再硬编码 import 具体版本文件
    src = _read(os.path.join(DEMO_DIR, "app", "chains", "extract_chain.py"))
    assert "from ai_registry.prompts.extract.v1_2_8_anti_injection import" not in src


def test_ac02_parse_prompt_explicit_version_active_unchanged():
    """AC-02: PARSE_SYSTEM_PROMPT 经 registry 显式版本加载,parse active 保持 v2_1_0_sku_clean。"""
    from ai_registry.registry import ai_registry
    from app.chains import extract_chain
    golden = _golden("extract_parse_system_prompt.txt")
    assert extract_chain.PARSE_SYSTEM_PROMPT == golden
    assert extract_chain.PARSE_SYSTEM_PROMPT == ai_registry.get_prompt("parse", "v2_2_0_structured_json")
    meta = _meta("parse")
    assert meta["active_version"] == "v2_1_0_sku_clean"
    assert meta["versions"]["v2_2_0_structured_json"]["status"] == "archived"


def test_ac03_audit_system_equals_mirror_golden():
    """AC-03: audit_chain.AUDIT_SYSTEM 与 golden 镜像文本(收据审核员/overall_consistent)逐字节等价。"""
    from ai_registry.registry import ai_registry
    from app.chains import audit_chain
    golden = _golden("audit_active_mirror.txt")
    assert audit_chain.AUDIT_SYSTEM == golden
    assert audit_chain.AUDIT_SYSTEM == ai_registry.get_prompt("audit")
    assert "overall_consistent" in audit_chain.AUDIT_SYSTEM
    assert "收据审核员" in audit_chain.AUDIT_SYSTEM


def test_ac04_review_system_explicit_version_active_unchanged():
    """AC-04: REVIEW_SYSTEM 经 registry 显式版本加载,review active 保持 v2_0_0_cards。"""
    from ai_registry.registry import ai_registry
    from app.chains import review_chain
    golden = _golden("review_system.txt")
    assert review_chain.REVIEW_SYSTEM == golden
    assert review_chain.REVIEW_SYSTEM == ai_registry.get_prompt("review", "v2_0_1_inline_parity")
    meta = _meta("review")
    assert meta["active_version"] == "v2_0_0_cards"
    assert meta["versions"]["v2_0_1_inline_parity"]["status"] == "archived"


def test_ac05_correct_prompt_registered_scene():
    """AC-05: CORRECT_SYSTEM_PROMPT 经 registry correct 场景 v1_0_0 加载。"""
    from ai_registry.registry import ai_registry
    from app.chains import extract_chain
    golden = _golden("extract_correct_system_prompt.txt")
    assert extract_chain.CORRECT_SYSTEM_PROMPT == golden
    assert extract_chain.CORRECT_SYSTEM_PROMPT == ai_registry.get_prompt("correct", "v1_0_0")
    meta = _meta("correct")
    assert meta["active_version"] == "v1_0_0"


# ============================================================
# B 内联零残留（AC-06）
# ============================================================

def test_ac06_no_inline_prompt_literals_in_demo_chains():
    """AC-06: demo 链路文件零内联 prompt 文本残留（唯一来源为 ai_registry）。"""
    extract_src = _read(os.path.join(DEMO_DIR, "app", "chains", "extract_chain.py"))
    review_src = _read(os.path.join(DEMO_DIR, "app", "chains", "review_chain.py"))
    audit_src = _read(os.path.join(DEMO_DIR, "app", "chains", "audit_chain.py"))
    # extract PARSE 内联
    assert "你是收据结构化解析助手" not in extract_src
    # extract CORRECT 内联
    assert "你是收据结构化修正助手" not in extract_src
    # review 内联
    assert "你是香港餐饮的采购复盘助手" not in review_src
    # audit 镜像内联
    assert "收据审核员" not in audit_src


# ============================================================
# C 镜像退役形态（AC-07, AC-08）
# ============================================================

def test_ac07_admin_prompts_api_shape_unchanged():
    """AC-07: /api/admin/prompts 响应保持 status/versions.active/versions.available/benchmark 四键,
    versions.available.extract 含 11 个版本号。"""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/admin/prompts", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "versions", "benchmark"}
    assert body["status"] == "success"
    assert set(body["versions"].keys()) == {"active", "available"}
    assert isinstance(body["benchmark"], dict)
    avail_extract = body["versions"]["available"]["extract"]
    assert len(avail_extract) == 11
    assert "v1_2_8_anti_injection" in avail_extract


def test_ac08_extract_metadata_complete_and_active_switched():
    """AC-08: extract metadata active=v1_2_8_anti_injection,versions 键集==目录 11 个 .py,
    v1_2_0_sku_clean 置 archived。"""
    scene_dir = os.path.join(REPO_ROOT, "ai_registry", "prompts", "extract")
    py_versions = {f[:-3] for f in os.listdir(scene_dir) if f.endswith(".py")}
    assert len(py_versions) == 11
    meta = _meta("extract")
    assert meta["active_version"] == "v1_2_8_anti_injection"
    assert set(meta["versions"].keys()) == py_versions
    assert meta["versions"]["v1_2_0_sku_clean"]["status"] == "archived"
    assert meta["versions"]["v1_2_8_anti_injection"]["status"] == "production"


def test_ac08b_audit_mirror_registered_as_new_version():
    """AC-08(补): 镜像生效文本登记为 audit v2_1_0_overall_schema 且 active;v2_0_0_reason 归档但文件保留。"""
    scene_dir = os.path.join(REPO_ROOT, "ai_registry", "prompts", "audit")
    meta = _meta("audit")
    assert meta["active_version"] == "v2_1_0_overall_schema"
    assert meta["versions"]["v2_1_0_overall_schema"]["status"] == "production"
    assert meta["versions"]["v2_0_0_reason"]["status"] == "archived"
    assert os.path.exists(os.path.join(scene_dir, "v2_0_0_reason.py"))
    assert os.path.exists(os.path.join(scene_dir, "v2_1_0_overall_schema.py"))
    from ai_registry.registry import ai_registry
    assert ai_registry.get_prompt("audit", "v2_1_0_overall_schema") == _golden("audit_active_mirror.txt")


# ============================================================
# D metadata 自洽（AC-09~AC-12）
# ============================================================

def _prompt_scenes():
    scenes_dir = os.path.join(REPO_ROOT, "ai_registry", "prompts")
    return sorted(d for d in os.listdir(scenes_dir)
                  if os.path.isdir(os.path.join(scenes_dir, d)) and not d.startswith((".", "_")))


def test_ac09_every_scene_metadata_active_exists():
    """AC-09: 每个场景 metadata 的 active_version 必须存在于 versions 且状态为 production。"""
    for scene in _prompt_scenes():
        meta = _meta(scene)
        assert "active_version" in meta, f"{scene}: 缺 active_version"
        assert meta["active_version"] in meta["versions"], \
            f"{scene}: active_version {meta['active_version']} 未登记"
        assert meta["versions"][meta["active_version"]]["status"] == "production", \
            f"{scene}: active 版本状态非 production"


def test_ac10_every_scene_metadata_versions_match_files():
    """AC-10: 每个场景 metadata versions 键集 == 目录内 .py 文件集（无悬空、无未登记）。"""
    for scene in _prompt_scenes():
        scene_dir = os.path.join(REPO_ROOT, "ai_registry", "prompts", scene)
        py_versions = {f[:-3] for f in os.listdir(scene_dir) if f.endswith(".py")}
        meta = _meta(scene)
        assert set(meta["versions"].keys()) == py_versions, \
            f"{scene}: metadata versions 与目录文件不一致: metadata={sorted(meta['versions'])} files={sorted(py_versions)}"


def test_ac11_every_version_has_release_date_status_and_changelog_for_inherited():
    """AC-11: 每个登记版本必有 release_date 与 status;新登记的继承版本 changelog 标注
    字节等价豁免评测说明。"""
    for scene in _prompt_scenes():
        meta = _meta(scene)
        for v, info in meta["versions"].items():
            assert info.get("release_date"), f"{scene}/{v}: 缺 release_date"
            assert info.get("status") in ("production", "archived", "experimental"), \
                f"{scene}/{v}: 非法 status {info.get('status')}"
    for scene, ver in [
        ("parse", "v2_2_0_structured_json"),
        ("correct", "v1_0_0"),
        ("review", "v2_0_1_inline_parity"),
        ("audit", "v2_1_0_overall_schema"),
    ]:
        changelog = " ".join(_meta(scene)["versions"][ver].get("changelog", []))
        assert "继承自内联前身的生产行为" in changelog, f"{scene}/{ver}: changelog 缺继承说明"
        assert "字节等价豁免评测" in changelog, f"{scene}/{ver}: changelog 缺豁免评测说明"


def test_ac12_review_changelog_records_divergence_from_cards():
    """AC-12: review v2_0_1_inline_parity 的 changelog 记录与 v2_0_0_cards 的分歧。"""
    info = _meta("review")["versions"]["v2_0_1_inline_parity"]
    changelog = " ".join(info.get("changelog", []))
    assert "v2_0_0_cards" in changelog, "changelog 未记录与 v2_0_0_cards 的分歧"


# ============================================================
# E math_engine 单实现（AC-13~AC-15）
# ============================================================

def _git_diff_empty(rel_path):
    out = subprocess.run(
        ["git", "diff", "--", rel_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return out.stdout.strip() == ""


MATH_CONSUMER_TEST_FILES = [
    "tests/test_u13_u14_localization_anomaly.py",
    "tests/test_u5_real_handwritten_long_receipts.py",
    "tests/test_fix_p1_governance.py",
    "tests/test_gap3_discount_deposit_math.py",
    "tests/test_gap6_strikethrough_notes.py",
    "tests/test_hallucination_adversarial.py",
    "tests/test_fix_p1_1_service_tax_rounding.py",
    "tests/test_fix_p0_2_audit_trail_void.py",
    "demo/tests/test_demo.py",
]


def test_ac13_math_consumer_tests_untouched():
    """AC-13: 9 个 math_engine 消费测试文件零改动（git diff 为空）。"""
    for rel in MATH_CONSUMER_TEST_FILES:
        assert _git_diff_empty(rel), f"{rel} 被改动,违反 AC-13"


def test_ac14_math_engine_single_implementation_in_registry():
    """AC-14: ai_registry/tools/math_engine/v2_1_0.py 为唯一实现（容差 0.01、is_void/actual_qty/
    赠品/折让/押金/服务费/税/抹零、validate_and_report + audit_trail）,active=production;
    demo/app/services/math_engine.py 为 re-export facade,同一函数对象。"""
    from ai_registry.registry import ai_registry
    mod = ai_registry.get_tool("math_engine", "v2_1_0")
    assert hasattr(mod, "validate_and_report")
    assert hasattr(mod, "audit_trail")
    src = _read(os.path.join(REPO_ROOT, "ai_registry", "tools", "math_engine", "v2_1_0.py"))
    assert "0.01" in src
    assert "is_void" in src and "actual_qty" in src
    for kw in ["赠", "押金", "服务费", "税", "抹零", "rounding", "0.01"]:
        assert kw in src, f"v2_1_0 缺关键字段: {kw}"
    # 折让语义由 discount_amount 承担（实现文案用「折扣」，字段语义等价）
    assert "折让" in src or "折扣" in src
    assert "discount" in src

    meta_path = os.path.join(REPO_ROOT, "ai_registry", "tools", "math_engine", "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["active_version"] == "v2_1_0"
    assert meta["versions"]["v2_1_0"]["status"] == "production"
    assert meta["versions"]["v2_0_0"]["status"] == "archived"
    assert "v1_0_0" not in meta["versions"], "v1_0_0 悬空条目未删除"
    assert os.path.exists(os.path.join(REPO_ROOT, "ai_registry", "tools", "math_engine", "v2_0_0.py"))

    # demo facade 与 registry v2_1_0 是同一实现（同一函数对象）
    from app.services import math_engine as facade
    from ai_registry.tools.math_engine import v2_1_0 as impl
    assert facade.validate_and_report is impl.validate_and_report
    assert facade.audit_trail is impl.audit_trail


def test_ac15_math_engine_behavior_parity():
    """AC-15: facade 路径行为与注册实现一致（离线冒烟样本上 problems 完全一致）。"""
    from app.services import math_engine as facade
    from ai_registry.tools.math_engine import v2_1_0 as impl
    from app.models import ReceiptData
    data = ReceiptData(
        doc_form="printed_delivery_note",
        vendor="测试供应商",
        date="2026-08-06",
        items=[
            {"name": "菜心", "qty": 3.0, "unit": "斤", "unit_price": 5.0, "amount": 20.0},
        ],
        total=105.0,
        discount_amount=20.0,
        payment_marked=False,
        confidence=0.8,
    )
    assert facade.validate_and_report(data) == impl.validate_and_report(data)
    assert facade.audit_trail(data) == impl.audit_trail(data)


# ============================================================
# F mcp 如实（AC-16）
# ============================================================

def test_ac16_mcp_settings_honest_and_version_registry_no_fiction():
    """AC-16: mcp_settings.json 3 server active=false;version_registry.json 删虚构
    healthy/avg_latency_ms,保留 version 与未接入说明。"""
    with open(os.path.join(REPO_ROOT, "ai_registry", "mcp", "configs", "mcp_settings.json"),
              "r", encoding="utf-8") as f:
        settings = json.load(f)
    servers = settings["servers"]
    assert set(servers.keys()) == {"chroma_vendor_memory", "db_readonly_query", "receipt_vision_preprocess"}
    for name, cfg in servers.items():
        assert cfg.get("active") is False, f"{name} 未置 active:false"

    with open(os.path.join(REPO_ROOT, "ai_registry", "mcp", "configs", "version_registry.json"),
              "r", encoding="utf-8") as f:
        vreg_text = f.read()
    vreg = json.loads(vreg_text)
    assert "healthy" not in vreg_text
    assert "avg_latency_ms" not in vreg_text
    assert len(vreg["endpoints"]) == 3
    for ep in vreg["endpoints"]:
        assert ep.get("version"), f"{ep.get('server')}: 缺 version"
    assert "未接入" in vreg_text


# ============================================================
# G pii_masker 可导入（AC-17）
# ============================================================

def test_ac17_pii_masker_importable():
    """AC-17: pii_masker/v1_0_0.py 修复 from typing import str 后可导入且功能正确。"""
    from ai_registry.tools.pii_masker.v1_0_0 import PIIMaskerTool
    tool = PIIMaskerTool()
    masked = tool.execute("客户张三(A123456(7)) 电话 +852 9123 4567 卡号 1234-5678-9012-3456")
    assert "A123456(7)" not in masked
    assert "9123 4567" not in masked
    assert "1234-5678-9012-3456" not in masked


# ============================================================
# H 离线冒烟（AC-18, AC-19）
# ============================================================

def _fixture():
    with open(os.path.join(GOLDEN_DIR, "offline_stub_fixture.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def test_ac18_offline_stub_parse_smoke_identical():
    """AC-18: _parse_to_receipt 对固定样本输出与 golden fixture 完全一致。"""
    from app.chains import extract_chain
    for sample in _fixture()["samples"]:
        data, err = extract_chain._parse_to_receipt(sample["raw"])
        got = data.model_dump() if data is not None else None
        assert got == sample["data"], f"{sample['name']}: data 不一致"
        assert err == sample["err"], f"{sample['name']}: err 不一致"


def test_ac19_offline_stub_math_smoke_identical():
    """AC-19: validate_and_report / audit_trail 对 fixture 样本输出与 golden fixture 完全一致。"""
    from app.services import math_engine
    from app.models import ReceiptData
    for sample in _fixture()["samples"]:
        if sample["data"] is None:
            continue
        data = ReceiptData(**sample["data"])
        assert math_engine.validate_and_report(data) == sample["math_problems"], \
            f"{sample['name']}: math_problems 不一致"
        assert math_engine.audit_trail(data) == sample["audit_trail"], \
            f"{sample['name']}: audit_trail 不一致"


# ============================================================
# I 零回归（AC-20）
# ============================================================

def test_ac20_app_prompts_delegate_shape_preserved():
    """AC-20: app.prompts 为 ai_registry 的 re-export 委托,get_prompt/get_metrics/
    list_prompt_versions/get_benchmark_report 函数名与返回形状不变,分叉副本文件已删除。"""
    from app import prompts as app_prompts
    for fn in ("get_prompt", "get_metrics", "list_prompt_versions", "get_benchmark_report"):
        assert callable(getattr(app_prompts, fn, None)), f"app.prompts 缺 {fn}"

    versions = app_prompts.list_prompt_versions()
    assert set(versions.keys()) == {"active", "available"}
    assert set(versions["active"].keys()) == set(versions["available"].keys())

    # get_prompt 逐组件可加载
    for comp in versions["active"]:
        text = app_prompts.get_prompt(comp)
        assert isinstance(text, str) and text, f"{comp}: get_prompt 空文本"

    report = app_prompts.get_benchmark_report()
    assert set(report.keys()) == set(versions["available"].keys())

    metrics = app_prompts.get_metrics("extract")
    assert isinstance(metrics, dict)

    # demo/app/prompts 分叉副本已删除（audit/memory/query 目录不再持有 .py 副本）
    for scene in ("audit", "memory", "query"):
        scene_dir = os.path.join(DEMO_DIR, "app", "prompts", scene)
        if os.path.isdir(scene_dir):
            leftover = [f for f in os.listdir(scene_dir) if f.endswith(".py")]
            assert not leftover, f"app/prompts/{scene} 仍残留副本: {leftover}"
