# -*- coding: utf-8 -*-
"""`.env.example` 与代码实际读取的环境变量必须对得上（E 项）。

两边的错误都会静默伤人：
- 模板里写着但代码零读取的键 → 运维按模板设了完全无效（本次确认的两个历史残留：
  `OPENCODE_RECOGNITION_MODEL` / `OPENCODE_AUDIT_MODEL`，全仓只出现在 .env* 里）；
- 代码读取但模板没声明的键 → 运维不知道这些开关存在，只能靠读源码发现。

本文件把「读取面」钉成一份可复算的清单，并双向核对：
1. 清单里每个键都真的在 demo/app 源码里出现（防止清单本身腐烂）；
2. 清单 ⊆ 模板声明（补声明）；
3. 模板里**生效**（非注释）的键 ⊆ 代码里出现过的键（防死键回归）。
"""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

DEMO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_EXAMPLE = os.path.join(DEMO, ".env.example")
ENV_FILE = os.path.join(DEMO, ".env")

# 代码实际读取的环境变量（2026-09-20 用 grep `os.environ` / `os.getenv` 全量比对得出；
# 下面 test_every_listed_key_appears_in_source 会对本清单逐项复核，防止它腐烂）。
ENV_READ_KEYS = frozenset({
    "AUDIT_ENGINE", "AUDIT_MODEL",
    "APP_ENV", "ENV",
    "AUTH_ENABLED", "BACKUP_DIR",
    "DASHSCOPE_API_KEY", "DASHSCOPE_AUDIT_MODEL", "DASHSCOPE_BASE_URL",
    "DB_PATH", "DEMO_TOKEN_SECRET",
    "ENGINE_CALL_TIMEOUT", "EVALSET_DIR", "EXTRACT_PROMPT_VERSION",
    "GUARDIAN_INTERVAL_MINUTES", "MOCK_QWEN_FLASH",
    "OPENAI_API_KEY", "OPENAI_AUD_API_KEY", "OPENAI_AUD_BASE_URL", "OPENAI_AUD_MODEL",
    "OPENAI_BASE_URL", "OPENAI_MODEL",
    "OPENAI_PARSE_API_KEY", "OPENAI_PARSE_BASE_URL", "OPENAI_PARSE_MODEL",
    "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_MODEL",
    "QWEN_VL_MODEL", "RAG_DIR",
    "RECOGNITION_ENGINE", "RECOGNITION_MODEL",
    "SILICONFLOW_API_KEY", "SILICONFLOW_AUDIT_MODEL", "SILICONFLOW_BASE_URL",
    "SILICONFLOW_MODEL",
})

# 已确认零读取、已从模板移除的历史残留键。
# 前两个是历史重命名残留；后四个随「本机 CLI 引擎（opencode / codebuddy）2026-09-02 弃用」
# 一并删除 —— 对应的模型类、常驻进程管理器、插件与 EngineKind 成员都已从代码中删除，
# 代码再也不会读这四个键。
DEAD_KEYS = (
    "OPENCODE_RECOGNITION_MODEL", "OPENCODE_AUDIT_MODEL",
    "OPENCODE_BIN", "CODEBUDDY_BIN", "CODEBUDDY_MODEL", "CODEBUDDY_PERMISSION_MODE",
)

_ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")
_COMMENTED_ASSIGN_RE = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=")


def _app_sources():
    for path in glob.glob(os.path.join(DEMO, "app", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            yield path, f.read()


def _parse_env_file(path):
    """返回 (生效键, 被注释掉的键)。"""
    active, commented = set(), set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _COMMENTED_ASSIGN_RE.match(line)
            if m:
                commented.add(m.group(1))
                continue
            m = _ASSIGN_RE.match(line)
            if m:
                active.add(m.group(1))
    return active, commented


def test_every_listed_key_appears_in_source():
    """清单里的每个键必须真的在 demo/app 源码里出现（防清单腐烂）。"""
    all_src = "\n".join(src for _, src in _app_sources())
    missing = [k for k in sorted(ENV_READ_KEYS) if f'"{k}"' not in all_src]
    assert not missing, f"这些键已不再被代码读取，请从清单移除: {missing}"


def test_all_read_keys_are_declared_in_example():
    """代码读取的键必须在 .env.example 里声明（生效或注释均可）。"""
    active, commented = _parse_env_file(ENV_EXAMPLE)
    declared = active | commented
    missing = sorted(ENV_READ_KEYS - declared)
    assert not missing, f".env.example 漏声明这些代码实际读取的键: {missing}"


def test_no_dead_keys_in_example():
    """模板里**生效**的键必须真被代码读取，否则就是又一个「设了没用」的键。"""
    active, _ = _parse_env_file(ENV_EXAMPLE)
    all_src = "\n".join(src for _, src in _app_sources())
    dead = [k for k in sorted(active) if f'"{k}"' not in all_src]
    assert not dead, f".env.example 里这些生效键代码零读取: {dead}"


def test_deprecated_open_code_keys_are_not_active():
    """两个历史残留键不得再以生效形式出现在 .env / .env.example。"""
    active, _ = _parse_env_file(ENV_EXAMPLE)
    assert not [k for k in DEAD_KEYS if k in active]
    if os.path.exists(ENV_FILE):
        live_active, _ = _parse_env_file(ENV_FILE)
        assert not [k for k in DEAD_KEYS if k in live_active], (
            ".env 里仍生效的 OPENCODE_* 模型键零读取，应移除或注释")


@pytest.mark.parametrize("key", DEAD_KEYS)
def test_dead_keys_are_really_unread(key):
    """前提复核：这两个键确实零读取（不允许出现在 app 源码里）。"""
    hits = [path for path, src in _app_sources() if f'"{key}"' in src or f"'{key}'" in src]
    assert not hits, f"这些文件竟引用了 {key}，本项「零读取」结论需重新评估: {hits}"
