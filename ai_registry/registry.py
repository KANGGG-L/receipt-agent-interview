"""
AI Registry Hub (ai_registry/registry.py)
统一资产注册表与动态加载中心，纳管 Prompts, Tools, Skills, MCP Servers, Plugins。
"""

import os
import json
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

BASE_DIR = Path(__file__).resolve().parent

# -------------------------------------------------------------
# 生产准入：consumer inventory / pinned version / approval manifest
# -------------------------------------------------------------
# why 单独落这两个文件、而不把「admitted」写进 prompts/*/metadata.json：
# metadata 是**资产自身**的版本说明，准入是**运行侧消费关系**的批准证据。把 admitted
# 补进 metadata 等于让资产自证可用（本仓已多次出现"登记了但没人读/没证据"），既无法
# 表达"同一版本被哪个消费者以什么方式使用"，也无法在缺凭据时留下 blocked。故：
#   - consumer_inventory.json：谁在消费、钉住哪个版本、生产/按需、失败时手工路径是什么；
#   - approval_manifest.json：该 (scene, version) 的批准记录与证据（可指向真实 meta 报告）。
# 两者分离后，资产 metadata 的字样保持原样，不会被改写来冒充准入。
APPROVALS_DIR = BASE_DIR / "approvals"
CONSUMER_INVENTORY_FILE = APPROVALS_DIR / "consumer_inventory.json"
APPROVAL_MANIFEST_FILE = APPROVALS_DIR / "approval_manifest.json"

ADMISSION_ADMITTED = "admitted"
ADMISSION_ADMITTED_WITH_CAVEAT = "admitted_with_caveat"
ADMISSION_BLOCKED = "blocked"
ADMISSION_PRODUCTION_MODE = "production"


class AdmissionDenied(RuntimeError):
    """生产加载被准入闸拒绝：明确拒绝受影响 AI 路径，同时保留手工路径说明。

    why 单独异常类型：调用方（如 memory_extract）需要区分"资产未准入"与"模型/IO 失败"，
    前者是可解释的治理状态（不是异常噪声），必须在运行记录里留 blocked 而不是混进
    "抽取失败"。
    """

    def __init__(self, consumer_id, scene, version, reasons, manual_path=""):
        self.consumer_id = consumer_id
        self.scene = scene
        self.version = version
        self.reasons = list(reasons or [])
        self.manual_path = manual_path or ""
        detail = "；".join(self.reasons) or "准入证据不足"
        tail = ("；手工路径保留：%s" % self.manual_path) if self.manual_path else ""
        super().__init__(
            "AI 资产准入拒绝 consumer=%s scene=%s version=%s：%s%s"
            % (consumer_id, scene, version, detail, tail))


def _json_list(value):
    """把清单文件里的集合字段归一成 list[dict]；非法结构一律当空（fail-closed）。"""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


class AIRegistry:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = base_dir
        self.prompts_dir = base_dir / "prompts"
        self.tools_dir = base_dir / "tools"
        self.skills_dir = base_dir / "skills"
        self.mcp_dir = base_dir / "mcp"
        self.plugins_dir = base_dir / "plugins"
        self.benchmarks_dir = base_dir / "benchmarks"
        self.approvals_dir = base_dir / "approvals"

    # =========================================================================
    # 1. Prompts 资产管理
    # =========================================================================
    def get_prompt(self, scene: str, version: Optional[str] = None, with_metadata: bool = False) -> Any:
        scene_dir = self.prompts_dir / scene
        if not scene_dir.exists():
            raise KeyError(f"Prompt 场景不存在: '{scene}'. 可选场景: {self.list_prompt_scenes()}")

        meta_file = scene_dir / "metadata.json"
        meta = {}
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

        target_version = version or meta.get("active_version")
        if not target_version:
            raise ValueError(f"场景 '{scene}' 未指定版本且 metadata.json 中无 active_version")

        prompt_file = scene_dir / f"{target_version}.py"
        if not prompt_file.exists():
            raise FileNotFoundError(f"未找到版本文件: {prompt_file}")

        # 动态导入模块
        module_path = f"ai_registry.prompts.{scene}.{target_version}"
        mod = importlib.import_module(module_path)
        prompt_text = getattr(mod, "PROMPT", getattr(mod, "SYSTEM_PROMPT", str(mod)))

        if with_metadata:
            version_meta = meta.get("versions", {}).get(target_version, {})
            return prompt_text, version_meta
        return prompt_text

    def list_prompt_scenes(self) -> List[str]:
        return [d.name for d in self.prompts_dir.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]

    def get_prompt_metadata(self, scene: str) -> Dict[str, Any]:
        meta_file = self.prompts_dir / scene / "metadata.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # =========================================================================
    # 2. Tools 资产管理
    # =========================================================================
    def get_tool(self, tool_name: str, version: Optional[str] = None) -> Any:
        tool_dir = self.tools_dir / tool_name
        if not tool_dir.exists():
            raise KeyError(f"Tool 工具不存在: '{tool_name}'. 可选工具: {self.list_tools()}")

        meta_file = tool_dir / "metadata.json"
        meta = {}
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

        target_version = version or meta.get("active_version", "v1_0_0")
        module_path = f"ai_registry.tools.{tool_name}.{target_version}"
        mod = importlib.import_module(module_path)
        
        # 返回工具实例或执行函数
        tool_class = getattr(mod, "Tool", getattr(mod, f"{tool_name.capitalize()}Tool", None))
        if tool_class and isinstance(tool_class, type):
            return tool_class()
        return mod

    def list_tools(self) -> List[str]:
        return [d.name for d in self.tools_dir.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]

    # =========================================================================
    # 3. Skills 资产管理
    # =========================================================================
    def get_skill(self, skill_name: str) -> Dict[str, Any]:
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            raise KeyError(f"Skill 技能不存在: '{skill_name}'. 可选技能: {self.list_skills()}")

        skill_md = skill_dir / "SKILL.md"
        eval_json = skill_dir / "eval.json"
        
        content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
        eval_data = json.loads(eval_json.read_text(encoding="utf-8")) if eval_json.exists() else {}

        return {
            "name": skill_name,
            "instruction": content,
            "evaluation": eval_data,
            "path": str(skill_dir)
        }

    def list_skills(self) -> List[str]:
        return [d.name for d in self.skills_dir.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]

    # =========================================================================
    # 4. MCP Servers 管理
    # =========================================================================
    def get_mcp_config(self) -> Dict[str, Any]:
        cfg_file = self.mcp_dir / "configs" / "mcp_settings.json"
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def list_mcp_servers(self) -> List[str]:
        servers_dir = self.mcp_dir / "servers"
        if servers_dir.exists():
            return [f.stem for f in servers_dir.glob("*.py") if not f.name.startswith((".", "_"))]
        return []

    # =========================================================================
    # 5. Plugins 扩展插件管理
    # =========================================================================
    def list_plugins(self) -> Dict[str, List[str]]:
        result = {}
        for cat in self.plugins_dir.iterdir():
            if cat.is_dir() and not cat.name.startswith((".", "_")):
                result[cat.name] = [p.stem for p in cat.glob("*.py") if not p.name.startswith((".", "_"))]
        return result

    # =========================================================================
    # 6. Benchmark 与效果汇总查询
    # =========================================================================
    def get_benchmark_matrix(self) -> Dict[str, Any]:
        mat_file = self.benchmarks_dir / "benchmark_matrix.json"
        if mat_file.exists():
            with open(mat_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # =========================================================================
    # 7. 生产准入清单（consumer inventory / approval manifest）
    # =========================================================================
    def get_consumer_inventory(self) -> Dict[str, Any]:
        """读取消费方清单；文件缺失/损坏返回空清单（由准入判定 fail-closed 拒绝）。"""
        path = self.approvals_dir / "consumer_inventory.json"
        if not path.exists():
            return {"schema_version": "", "consumers": [], "_path": str(path)}
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            raise ValueError("consumer_inventory 必须是 JSON 对象")
        doc = dict(doc)
        doc["_path"] = str(path)
        doc["consumers"] = _json_list(doc.get("consumers"))
        return doc

    def get_approval_manifest(self) -> Dict[str, Any]:
        """读取批准清单；文件缺失/损坏返回空清单（由准入判定 fail-closed 拒绝）。"""
        path = self.approvals_dir / "approval_manifest.json"
        if not path.exists():
            return {"schema_version": "", "approvals": [], "_path": str(path)}
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            raise ValueError("approval_manifest 必须是 JSON 对象")
        doc = dict(doc)
        doc["_path"] = str(path)
        doc["approvals"] = _json_list(doc.get("approvals"))
        return doc


# 全局单例
ai_registry = AIRegistry()


# -------------------------------------------------------------
# 准入判定（唯一实现；所有消费方只调这里，不在各自的加载器里重抄一份）
# -------------------------------------------------------------
def _find_consumer(inventory, consumer_id):
    for item in inventory.get("consumers") or []:
        if str(item.get("consumer_id") or "") == str(consumer_id):
            return item
    return None


def _find_approval(manifest, scene, version):
    for item in manifest.get("approvals") or []:
        if (str(item.get("scene") or "") == str(scene)
                and str(item.get("version") or "") == str(version)):
            return item
    return None


def _resolve_evidence_path(raw):
    """evidence.meta_report 的路径解析：相对路径以仓库根为基准。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if not os.path.isabs(text):
        text = os.path.join(str(BASE_DIR.parent), text)
    return os.path.abspath(text)


def _metadata_admission(scene, version):
    """读取资产自身 metadata 的既有 admission（只读，绝不修改 metadata 字样）。"""
    path = ai_registry.prompts_dir / str(scene) / "metadata.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return False, "metadata 读取失败：%s" % e
    info = ((meta.get("versions") or {}).get(str(version)) or {})
    admitted = (info.get("admission") or {}).get("admitted") is True
    return admitted, "" if admitted else "metadata 未登记 admission.admitted=true"


def _validate_evidence(approval, consumer_id, scene, version):
    """校验准入证据：真实 meta 报告必须存在且 trustworthy=true；否则只允许显式 caveat。

    返回 (ok, reasons, status)。
    why 不能只校验 evidence 非空：手改 manifest 填一个 `{}` 或指向不存在的报告即可
    放行。这里有两条严格路径：
      1. 声明了 `meta_report` → 文件必须存在、`evaluator_trustworthy=true`、
         非 blocked/failed；若报告带 `asset_versions`，版本必须与本资产一致。
      2. 无报告 → 仅允许 `admitted_with_caveat` + `meta_report_status=blocked`，
         且资产 metadata 必须已有 `admission.admitted=true`（历史已批准资产），
         不得仅凭 manifest 手改把 draft 版本放行。
    """
    evidence = approval.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return False, ["approval manifest 已标 admitted 但缺 evidence（不得无证据准入）"], ADMISSION_BLOCKED

    report_ref = evidence.get("meta_report")
    if report_ref:
        path = _resolve_evidence_path(report_ref)
        if not path or not os.path.exists(path):
            return False, ["evidence.meta_report 指向的报告不存在：%s" % (path or report_ref)], ADMISSION_BLOCKED
        try:
            with open(path, "r", encoding="utf-8") as f:
                report = json.load(f)
        except Exception as e:
            return False, ["evidence.meta_report 无法解析：%s" % e], ADMISSION_BLOCKED
        if not isinstance(report, dict):
            return False, ["evidence.meta_report 必须是 JSON 对象"], ADMISSION_BLOCKED
        if report.get("evaluator_trustworthy") is not True:
            return False, ["meta 报告 evaluator_trustworthy != true（不得作为准入证据）"], ADMISSION_BLOCKED
        if report.get("status") == ADMISSION_BLOCKED or report.get("estimable") is False:
            return False, ["meta 报告为 blocked/不可估值（未测不得准入）"], ADMISSION_BLOCKED
        versions = report.get("asset_versions")
        if isinstance(versions, dict) and consumer_id in versions:
            entry = versions.get(consumer_id) or {}
            if entry.get("version") and str(entry.get("version")) != str(version):
                return False, ["meta 报告评的版本与本资产不一致：%s != %s"
                               % (entry.get("version"), version)], ADMISSION_BLOCKED
            if entry.get("admitted") is False:
                return False, ["meta 报告的 asset_versions 标记该消费者未准入"], ADMISSION_BLOCKED
        return True, [], ADMISSION_ADMITTED

    # 无真实报告：只允许历史已批准资产的显式 caveat，且 metadata 必须真有 admission。
    if evidence.get("meta_report_status") != ADMISSION_BLOCKED:
        return False, ["缺真实 meta 报告且未显式标 blocked（不得默认准入）"], ADMISSION_BLOCKED
    if approval.get("status") != ADMISSION_ADMITTED_WITH_CAVEAT:
        return False, ["无真实 meta 报告时只允许 admitted_with_caveat 显式降级"], ADMISSION_BLOCKED
    if not str(approval.get("caveat") or "").strip():
        return False, ["admitted_with_caveat 缺 caveat 说明"], ADMISSION_BLOCKED
    admitted_meta, reason = _metadata_admission(scene, version)
    if not admitted_meta:
        return False, ["仅靠 manifest 无法准入：%s" % reason], ADMISSION_BLOCKED
    return True, [str(evidence.get("blocked_reason")
                       or "真实 meta 报告缺凭据/未达 trustworthy，留 blocked（未伪造）")], ADMISSION_ADMITTED_WITH_CAVEAT


def _blocked_decision(consumer_id, scene, version, reasons, consumer=None):
    consumer = consumer or {}
    return {
        "consumer_id": consumer_id,
        "scene": scene,
        "version": version,
        "pinned_version": consumer.get("pinned_version") or "",
        "mode": consumer.get("mode") or "",
        "admitted": False,
        "status": ADMISSION_BLOCKED,
        "reasons": list(reasons or []),
        "manual_path": consumer.get("manual_path") or "",
        "approval": None,
        "evidence": {},
    }


def evaluate_consumer_admission(consumer_id, scene=None, version=None,
                                inventory=None, manifest=None) -> Dict[str, Any]:
    """判定某消费方在指定 (scene, version) 上是否已获准入。

    判定顺序（任一不满足即 blocked，绝不默认放行）：
      1. consumer inventory 必须登记该消费方（未登记 = 无法证明谁在用）；
      2. 请求的 (scene, version) 必须与清单钉住版本一致（不许运行时漂移到别的版本）；
      3. approval manifest 必须有该资产条目，且 `admitted is True`、未撤销；
      4. 批准记录必须有 evidence（可为"真实 meta 报告 blocked"这一诚实状态，但不能为空）；
      5. evidence 若声明 `meta_report_status=blocked`，仍可准入但状态降为
         `admitted_with_caveat`（历史已批准资产的既有准入依据保留，不伪造新报告）。

    返回 dict；调用方据 `admitted` 决定拒绝/放行。本函数只读，不写任何清单。
    """
    consumer_id = str(consumer_id or "")
    try:
        inventory = inventory if inventory is not None else ai_registry.get_consumer_inventory()
    except Exception as e:
        return _blocked_decision(consumer_id, scene, version,
                                 ["consumer inventory 读取失败：%s" % e])
    consumer = _find_consumer(inventory, consumer_id)
    if consumer is None:
        return _blocked_decision(
            consumer_id, scene, version,
            ["consumer inventory 未登记该消费者（无法证明消费关系与批准版本）"])

    pinned_scene = str(consumer.get("scene") or "")
    pinned_version = str(consumer.get("pinned_version") or "")
    req_scene = str(scene or pinned_scene)
    req_version = str(version or pinned_version)
    if req_scene != pinned_scene or req_version != pinned_version:
        return _blocked_decision(
            consumer_id, req_scene, req_version,
            ["请求版本与 consumer inventory 钉住版本不一致：pinned=%s/%s requested=%s/%s"
             % (pinned_scene, pinned_version, req_scene, req_version)], consumer)

    try:
        manifest = manifest if manifest is not None else ai_registry.get_approval_manifest()
    except Exception as e:
        return _blocked_decision(consumer_id, req_scene, req_version,
                                 ["approval manifest 读取失败：%s" % e], consumer)
    approval = _find_approval(manifest, req_scene, req_version)
    if approval is None:
        return _blocked_decision(
            consumer_id, req_scene, req_version,
            ["approval manifest 无该资产的准入记录（缺准入）"], consumer)

    if approval.get("revoked_at"):
        return _blocked_decision(
            consumer_id, req_scene, req_version,
            ["该资产准入已被撤销：%s" % approval.get("revoked_at")], consumer)
    if approval.get("admitted") is not True:
        reasons = [str(approval.get("blocked_reason") or "approval manifest 标记为未准入（blocked）")]
        return _blocked_decision(consumer_id, req_scene, req_version, reasons, consumer)

    ok, reasons, status = _validate_evidence(
        approval, consumer_id, req_scene, req_version)
    if not ok:
        return _blocked_decision(consumer_id, req_scene, req_version, reasons, consumer)
    evidence = dict(approval.get("evidence") or {})
    return {
        "consumer_id": consumer_id,
        "scene": req_scene,
        "version": req_version,
        "pinned_version": pinned_version,
        "mode": consumer.get("mode") or "",
        "admitted": True,
        "status": status,
        "reasons": reasons,
        "manual_path": consumer.get("manual_path") or "",
        "approval": dict(approval),
        "evidence": dict(evidence),
    }


def assert_consumer_admitted(consumer_id, scene=None, version=None):
    """生产加载前的硬闸：未准入直接抛 AdmissionDenied（含手工路径说明）。

    why 返回 decision 而不是只抛：允许调用方把 status/evidence 写进运行记录，使
    "被拒"与"跑了但质量差"在指标上可分。admitted=false 时绝不降级到别的版本/别的
    文案——那会把治理失效伪装成一次正常调用。
    """
    decision = evaluate_consumer_admission(consumer_id, scene, version)
    if not decision.get("admitted"):
        raise AdmissionDenied(
            decision.get("consumer_id") or consumer_id,
            decision.get("scene"), decision.get("version"),
            decision.get("reasons"), decision.get("manual_path"))
    return decision


def consumer_admission_report() -> Dict[str, Any]:
    """全量消费方准入视图（供 meta 报告引用版本 / 巡检 / 测试断言）。

    `production_blocked` 只统计生产模式消费方：它是"生产路径是否被拒绝"的计数，
    不含按需/离线消费方（那些不阻断生产）。任何生产消费方缺 manifest 条目都会被
    如实列为 blocked，不会被"未登记 = 默认可用"吞掉。
    """
    inventory = ai_registry.get_consumer_inventory()
    manifest = ai_registry.get_approval_manifest()
    consumers = []
    for consumer in inventory.get("consumers") or []:
        decision = evaluate_consumer_admission(
            consumer.get("consumer_id"),
            inventory=inventory, manifest=manifest)
        decision["active_production_consumer"] = (
            consumer.get("mode") == ADMISSION_PRODUCTION_MODE)
        decision["has_manifest_entry"] = _find_approval(
            manifest, consumer.get("scene"), consumer.get("pinned_version")) is not None
        consumers.append(decision)
    production = [c for c in consumers if c.get("active_production_consumer")]
    return {
        "inventory_path": inventory.get("_path", ""),
        "manifest_path": manifest.get("_path", ""),
        "consumers": consumers,
        "production_consumers": len(production),
        "production_blocked": sum(1 for c in production if not c.get("admitted")),
        "blocked": sum(1 for c in consumers if not c.get("admitted")),
    }


def load_consumer_prompt(consumer_id, production=True, version=None):
    """按消费方清单钉住版本加载 prompt；生产模式先过准入闸。

    production=False 是运维/离线复盘用的显式降级口：仍按**钉住版本**加载（不许回落到
    active 或别的版本），但不要求准入——这条路径不产生生产注入，不能借它绕过生产闸。
    """
    decision = evaluate_consumer_admission(consumer_id, version=version)
    if production and not decision.get("admitted"):
        raise AdmissionDenied(
            decision.get("consumer_id") or consumer_id,
            decision.get("scene"), decision.get("version"),
            decision.get("reasons"), decision.get("manual_path"))
    return load_prompt(decision["scene"], version or decision["pinned_version"])


def load_prompt(scene: str, version: Optional[str] = None) -> str:
    """按 scene + version 载入 prompt 文本；version 缺省取 metadata.json 的 active_version。

    why 需要这个模块级入口（而不是让消费方自己去拿 `ai_registry.get_prompt`）：
    1. **版本必须已登记**。`AIRegistry.get_prompt` 只校验「版本文件存在」——目录里放一个
       `v9_9_9.py` 而 metadata 里没登记，它照样能加载出来，于是 prompt 版本治理被绕过
       （「文件在、登记没有」正是 tests/test_registry_ssot.py AC-10 要拦的漂移）。本函数
       额外要求版本出现在 `versions` 里，未登记即抛 KeyError 并列出可用版本。
    2. **失败可见、绝不静默回退**。找不到场景/版本/文件时一律抛错，不返回空串、不退回
       别的版本、不退回内置文案 —— 「注册表读不到就悄悄用旧文案」会让版本治理整体失效，
       属本仓高发的「异常被静默吞掉导致功能静默失效」。需要降级的调用方自己 except 并
       显式 warning（如 `app/services/memory_extract._load_registry_prompt`）。

    实现只委托单例，是一个**唯一实现**：不要在图里或调用方另抄一份「拼模块路径 + 读
    metadata」的逻辑。
    """
    meta = ai_registry.get_prompt_metadata(scene)
    if not isinstance(meta, dict) or not meta.get("versions"):
        raise KeyError(
            f"Prompt 场景不存在或 metadata.json 无 versions: '{scene}'. "
            f"可选场景: {ai_registry.list_prompt_scenes()}")
    target_version = version or meta.get("active_version")
    if not target_version:
        raise ValueError(f"场景 '{scene}' 未指定版本且 metadata.json 中无 active_version")
    versions = meta["versions"]
    if target_version not in versions:
        raise KeyError(
            f"场景 '{scene}' 的版本 '{target_version}' 未在 metadata.json 登记"
            f"（可用版本: {sorted(versions)}）。未登记的版本文件不得被加载。")
    return ai_registry.get_prompt(scene, target_version)
