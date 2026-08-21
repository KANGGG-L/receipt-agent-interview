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

class AIRegistry:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = base_dir
        self.prompts_dir = base_dir / "prompts"
        self.tools_dir = base_dir / "tools"
        self.skills_dir = base_dir / "skills"
        self.mcp_dir = base_dir / "mcp"
        self.plugins_dir = base_dir / "plugins"
        self.benchmarks_dir = base_dir / "benchmarks"

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


# 全局单例
ai_registry = AIRegistry()
