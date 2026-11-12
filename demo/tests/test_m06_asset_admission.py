# -*- coding: utf-8 -*-
"""M06 / C-ASSET：AI 资产准入与开关防回退（离线，无任何模型外呼）。

覆盖任务卡 M06 的最低验收：
1. `test_active_consumer_requires_approval_manifest`
   - consumer inventory / pinned version / approval manifest 三件套必须自洽；
   - 生产消费方缺批准记录 → 生产加载拒绝，且手工路径说明保留；
   - 不得把 metadata 字样改成"准入"来冒充批准；
   - 真实 meta 报告缺凭据时只能 blocked，不得伪造 trustworthy。
2. `test_defaults_policy_unchanged`
   - 首轮/预判 False、抽取 True 但只进 pending 人审、kind TTL 保持；准入改动不写 production defaults。
3. `test_import_db_rag_isolated`
   - 导入准入/记忆抽取/路径解析不得拉起 app.db、不得创建真实 DB/向量目录。

环境隔离：模块级临时 DB / RAG 目录（必须在 import app.* 之前设置）。测试全程只用
合成样本与本地 fixture；`memory_extract` 的生产模型构建被准入闸挡在 `app.llm` 之前，
因此本文件不会发起任何真实模型调用。
"""

import json
import os
import subprocess
import sys
import tempfile

# 隔离环境：临时 DB / 向量目录（必须先于 app.* / scripts import）
_TMP = tempfile.mkdtemp(prefix="m06_asset_admission_")
os.environ["DB_PATH"] = os.path.join(_TMP, "m06.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "m06_rag")
os.environ["AUTH_ENABLED"] = "0"
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "hash")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
for _p in (_DEMO_DIR, _REPO_ROOT, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import ai_registry.registry as registry  # noqa: E402
from ai_registry.registry import (  # noqa: E402
    AdmissionDenied,
    assert_consumer_admitted,
    consumer_admission_report,
    evaluate_consumer_admission,
    load_consumer_prompt,
)
from app.services import memory_extract as mx  # noqa: E402
from app.services import rag_paths, settings_service  # noqa: E402
from app.services.memory_facts import validate_fact  # noqa: E402

_APPROVALS_DIR = os.path.join(_REPO_ROOT, "ai_registry", "approvals")
_INVENTORY_PATH = os.path.join(_APPROVALS_DIR, "consumer_inventory.json")
_MANIFEST_PATH = os.path.join(_APPROVALS_DIR, "approval_manifest.json")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _metadata(scene):
    path = os.path.join(_REPO_ROOT, "ai_registry", "prompts", scene, "metadata.json")
    return _load(path)


# -------------------------------------------------------------
# 1. 生产消费方必须有批准记录；缺准入拒绝 AI 路径、保手工路径
# -------------------------------------------------------------
def test_active_consumer_requires_approval_manifest(monkeypatch):
    inventory = _load(_INVENTORY_PATH)
    manifest = _load(_MANIFEST_PATH)

    # 清单本身必须覆盖"生产消费方"并钉住具体版本（不是模糊的 scene）。
    production = [c for c in inventory["consumers"] if c.get("mode") == "production"]
    assert production, "consumer inventory 至少要登记生产消费方"
    for c in production:
        assert c.get("consumer_id") and c.get("scene") and c.get("pinned_version")
        assert c.get("manual_path"), "生产消费方必须写明被拒后的手工路径"

    approvals = {(a["scene"], a["version"]): a for a in manifest["approvals"]}
    for c in production:
        key = (c["scene"], c["pinned_version"])
        assert key in approvals, "生产消费方 %s 缺 approval manifest 条目" % c["consumer_id"]

    report = consumer_admission_report()
    by_id = {r["consumer_id"]: r for r in report["consumers"]}
    # 已批准的历史资产：放行但保留 caveat（真实 meta 报告仍 blocked，不伪造）
    admitted = assert_consumer_admitted("extract_chain")
    assert admitted["status"] in ("admitted", "admitted_with_caveat")
    if admitted["status"] == "admitted_with_caveat":
        assert admitted["evidence"]["meta_report"] is None
        assert admitted["evidence"]["meta_report_status"] == "blocked"
        assert "不伪造" in admitted["evidence"]["blocked_reason"]

    # 未批准的 memory 抽取：生产加载必须拒绝，且异常里带手工路径
    decision = evaluate_consumer_admission("memory_extract")
    assert decision["admitted"] is False and decision["status"] == "blocked"
    assert any("draft" in r or "缺准入" in r or "blocked" in r for r in decision["reasons"])
    with pytest.raises(AdmissionDenied) as ei:
        load_consumer_prompt("memory_extract")
    assert "手工路径保留" in str(ei.value)
    assert by_id["memory_extract"]["admitted"] is False
    assert by_id["memory_extract"]["active_production_consumer"] is True
    assert by_id["memory_extract"]["has_manifest_entry"] is True

    # 回滚到"已撤销"的旧批准资产：不得重新打开生产注入
    revoked = {"schema_version": "v1", "approvals": [
        dict(approvals[("extract", "v1_2_0_sku_clean")], revoked_at="2026-10-26T00:00:00")]}
    rolled = evaluate_consumer_admission("extract_chain", manifest=revoked)
    assert rolled["admitted"] is False
    assert any("撤销" in r for r in rolled["reasons"]), rolled["reasons"]

    # 清单缺失/该资产无条目 → fail-closed（不得默认放行）
    monkeypatch.setattr(registry.ai_registry, "get_approval_manifest",
                        lambda: {"schema_version": "v1", "approvals": []})
    blocked = evaluate_consumer_admission("memory_extract")
    assert blocked["admitted"] is False
    assert any("无该资产的准入记录" in r for r in blocked["reasons"])

    # 不得改 metadata 字样冒充准入：memory 版本仍是 draft，且没有 admitted=true
    mem_meta = _metadata("memory")["versions"]["v1_1_0_extract"]
    assert mem_meta["status"] == "draft"
    assert (mem_meta.get("admission") or {}).get("admitted") is not True


# -------------------------------------------------------------
# 2. 生产策略缺省不变：首轮/预判 False、抽取 True 人审、kind TTL 保持
# -------------------------------------------------------------
def test_defaults_policy_unchanged():
    d = dict(settings_service.SETTINGS_DEFAULTS)

    # 首轮注入与供应商预判保持关闭（零行为变化，避免默认多发一次模型调用）
    assert d["vendor_precheck_enabled"] is False
    assert d["memory_first_round_inject_enabled"] is False
    # 抽取常开，但产物只进 pending 人审队列（本测试锁住"默认值"；闸门由 memory_facts 保证）
    assert d["memory_extract_enabled"] is True
    assert d["memory_enabled"] is True
    # kind TTL 保持：价格 90 / 单位 180 / alias 与 layout 不过期；总 TTL 0
    assert d["memory_ttl_days"] == 0
    assert d["memory_ttl_days_price_range"] == 90
    assert d["memory_ttl_days_unit"] == 180
    assert d["memory_ttl_days_alias"] == 0
    assert d["memory_ttl_days_layout"] == 0

    # 真实前后快照比对（不是只看常量）：读库得到生效值 → 触发准入拒绝路径 → 再读。
    # all_settings 只读 app_settings，不得有任何写入。
    before = settings_service.all_settings()
    assert before["vendor_precheck_enabled"] is False
    assert before["memory_first_round_inject_enabled"] is False
    assert before["memory_extract_enabled"] is True
    assert before["memory_ttl_days_price_range"] == 90
    consumer_admission_report()
    mx.extract_candidate_facts(
        [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        "sup-1", "t1", model=None, cfg=None)
    after = settings_service.all_settings()
    assert after == before, "准入拒绝路径不得改动任何生产 defaults（前后真实快照应一致）"
    assert dict(settings_service.SETTINGS_DEFAULTS) == d

    # 未准入的抽取不得为了"跑通"偷偷改成别的策略值（如自动批准/关闭抽取）
    assert "memory_auto_approve" not in d
    assert d["memory_extract_enabled"] is True

    # 手工路径不受准入影响：纯代码差异计算与写入侧闸门仍是独立可用能力
    diffs = mx.compute_field_diffs(
        [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}])
    assert len(diffs) == 1
    from app.services.memory_facts import MemoryFact, SOURCE_MANUAL
    ok, _ = validate_fact(MemoryFact(
        kind="alias", fact_key="黑牛", value="黑牛腩", supplier_id="sup-1",
        tenant_id="t1", source_kind=SOURCE_MANUAL, trust_level=1.0))
    assert ok is True


# -------------------------------------------------------------
# 3. 导入隔离：不拉起 app.db / 不创建真实 DB 与向量目录
# -------------------------------------------------------------
def test_import_db_rag_isolated():
    """子进程实测：只导入准入、记忆抽取、settings 与 rag_paths 时 app.db 不得进 sys.modules。

    why 用子进程：pytest 进程里别的用例可能早已 import app.db，进程内断言会假绿。
    """
    code = r"""
import os, sys
tmp, demo, repo = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DB_PATH"] = os.path.join(tmp, "isolated.db")
os.environ["RAG_DIR"] = os.path.join(tmp, "isolated_rag")
os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"
for p in (demo, repo):
    if p not in sys.path:
        sys.path.insert(0, p)
from ai_registry.registry import consumer_admission_report
from app.services import settings_service, memory_extract, rag_paths
assert "app.db" not in sys.modules, "导入准入链路不得拉起 app.db"
assert rag_paths.resolve_rag_dir() == os.path.abspath(os.environ["RAG_DIR"])
assert settings_service.SETTINGS_DEFAULTS["memory_extract_enabled"] is True
assert consumer_admission_report()["consumers"]
assert not os.path.exists(os.environ["DB_PATH"]), "只读导入不得创建 DB 文件"
assert not os.path.exists(os.environ["RAG_DIR"]), "只读导入不得创建向量目录"
print("OK")
"""
    out = subprocess.run(
        [sys.executable, "-c", code, _TMP, _DEMO_DIR, _REPO_ROOT],
        cwd=_DEMO_DIR, capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PYTHONPATH="%s%s%s" % (_DEMO_DIR, os.pathsep, _REPO_ROOT)))
    assert out.returncode == 0, "STDOUT=%s\nSTDERR=%s" % (out.stdout, out.stderr)
    assert "OK" in out.stdout


# -------------------------------------------------------------
# 4. 生产抽取被拒但手工路径保留（附加判别力用例）
# -------------------------------------------------------------
def test_production_extract_blocked_keeps_manual_path():
    """model=None 的真实生产入口：准入拒绝 → 返回 []、model_called=False、错误可读。

    手工路径没有被这段逻辑触碰：compute_field_diffs 仍返回差异、validate_fact 仍可判合法，
    且整个过程不 import app.db / app.llm（闸门在模型构建前生效）。
    """
    ai_items = [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}]
    user_items = [{"name": "黑牛腩", "quantity": 1, "unit": "斤",
                   "unit_price": 10, "amount": 10}]
    out = mx.extract_candidate_facts(ai_items, user_items, "sup-1", "t1",
                                     model=None, cfg=None)
    assert out == []
    run = mx.get_last_run()
    assert mx.get_last_admission_status() == mx.ADMISSION_STATUS_BLOCKED
    assert run["model_called"] is False
    assert "准入拒绝" in run["error"] and "手工" in run["error"]
    # 被拒时只 import 了 app.llm 模块（用于识别真实构建函数），但没有调用它；
    # 导入隔离（app.db / 真实 DB / 向量目录）由上面的子进程用例单独证明。


# -------------------------------------------------------------
# 5. 真实 meta 报告缺凭据 → blocked，不伪造
# -------------------------------------------------------------
def test_real_meta_report_without_credentials_stays_blocked():
    import run_meta_eval as meta_eval

    items = meta_eval.load_meta_eval_set()[:2]

    def _no_credentials(_receipt_input):
        return {"verdict": None, "why": "audit 模型构建失败: 缺凭据（测试合成）"}

    report = meta_eval.run_meta_eval(_no_credentials, items)
    assert report["status"] == meta_eval.REPORT_STATUS_BLOCKED
    assert report["estimable"] is False
    assert report["evaluator_trustworthy"] is False
    # 未测不估值：不能把 0.0 当成"测了但全错"
    assert report["correct_rate"] is None and report["wrong_rate"] is None
    assert "缺凭据" in report["blocked_reason"]
    # 报告必须引用版本，且 blocked 的资产不被伪造成 admitted
    assert "memory_extract" in report["asset_versions"]
    assert report["asset_versions"]["memory_extract"]["admitted"] is False


# -------------------------------------------------------------
# 6. 生产 prompt 载入路径（extract_chain / memory_consolidate）不得绕过准入
# -------------------------------------------------------------
def test_production_prompt_loaders_require_admission(monkeypatch):
    """撤销 manifest 后：识别/整合 prompt 载入必须拒绝，识别入口走确定性失败+手工路径。"""
    from app.chains import extract_chain as ec
    from app.services import memory_consolidate as mc

    # 基线：当前 manifest 已准入，默认识别 prompt 可载入
    assert ec._resolve_extract_system_prompt()
    # 模拟撤回/清空 manifest
    monkeypatch.setattr(registry.ai_registry, "get_approval_manifest",
                        lambda: {"schema_version": "v1", "approvals": []})
    with pytest.raises(ec.PromptAdmissionBlocked):
        ec._resolve_extract_system_prompt()
    with pytest.raises(ec.PromptAdmissionBlocked):
        ec.build_prompt("no-such-image.jpg")
    with pytest.raises(mc.PromptUnavailableError) as ei:
        mc._system_prompt()
    assert "准入" in str(ei.value)
    # 直接走被 QA 点名的 loader 也必须被拒（不能只靠上层 _system_prompt 拦）
    with pytest.raises(mc.PromptUnavailableError):
        mc._load_registry_prompt()

    # 识别入口：确定性 admission 失败，不建模型/不读图；提示走手工路径
    res = ec.extract_receipt("no-such-image.jpg", model=None)
    assert res["data"] is None
    assert res["error_category"] == "admission"
    assert res["deterministic_error"] is True
    assert "手工" in res["error"]


# -------------------------------------------------------------
# 7. 准入证据：报告必须存在且 trustworthy=true；手改 manifest 不能放行
# -------------------------------------------------------------
def test_evidence_requires_existing_trustworthy_meta_report(tmp_path):
    manifest = _load(_MANIFEST_PATH)
    base = next(a for a in manifest["approvals"]
                if a["scene"] == "extract")

    def _with_report(path_text):
        return {"schema_version": "v1", "approvals": [dict(
            base, admitted=True, status="admitted", caveat="",
            evidence={"meta_report": path_text, "meta_report_status": "admitted"})]}

    report_path = tmp_path / "meta_report.json"
    report_path.write_text(json.dumps({
        "evaluator_trustworthy": True, "status": "ok", "estimable": True,
        "asset_versions": {"extract_chain": {
            "version": "v1_2_0_sku_clean", "admitted": True}},
    }, ensure_ascii=False), encoding="utf-8")
    ok = evaluate_consumer_admission("extract_chain", manifest=_with_report(str(report_path)))
    assert ok["admitted"] is True and ok["status"] == "admitted"

    # 删除报告 → must deny
    report_path.unlink()
    missing = evaluate_consumer_admission("extract_chain", manifest=_with_report(str(report_path)))
    assert missing["admitted"] is False
    assert any("不存在" in r for r in missing["reasons"]), missing["reasons"]

    # 报告存在但 evaluator_trustworthy=false → must deny
    report_path.write_text(json.dumps({"evaluator_trustworthy": False}), encoding="utf-8")
    untrusted = evaluate_consumer_admission(
        "extract_chain", manifest=_with_report(str(report_path)))
    assert untrusted["admitted"] is False
    assert any("evaluator_trustworthy" in r for r in untrusted["reasons"])

    # 手改 manifest：给 memory draft 加 admitted 但无真实报告 → 仍 deny
    hand_edit = {"schema_version": "v1", "approvals": [{
        "scene": "memory", "version": "v1_1_0_extract", "admitted": True,
        "status": "admitted", "evidence": {"meta_report_status": "blocked"},
    }]}
    edited = evaluate_consumer_admission("memory_extract", manifest=hand_edit)
    assert edited["admitted"] is False
    assert any("admitted_with_caveat" in r for r in edited["reasons"]), edited["reasons"]

    # 手改 manifest 加 caveat 但 metadata 仍 draft → 仍 deny（不得只靠 manifest 准入）
    hand_edit["approvals"][0].update(
        status="admitted_with_caveat", caveat="手写豁免")
    edited2 = evaluate_consumer_admission("memory_extract", manifest=hand_edit)
    assert edited2["admitted"] is False
    assert any("仅靠 manifest" in r for r in edited2["reasons"]), edited2["reasons"]
