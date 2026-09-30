# -*- coding: utf-8 -*-
"""RAG 向量库落盘目录的**唯一**解析实现（以应用根为基准，与 cwd 无关）。

why（存在的理由）
----------------
`rag.PERSIST_DIR` 与巡检工具 `memory_consistency._resolve_rag_dir()` 原本**各写了一份**
「RAG_DIR 缺省 `./.rag_chroma`（cwd 相对）」的解析口径（P6·X2 审计 D1，severity high）：

- `rag.py` 只改这一份时，**向量真实落盘位置**随进程启动目录变化：生产从 `demo/` 起
  进程 → `demo/.rag_chroma`；从仓库根起的脚本 → `<repo>/.rag_chroma`。实测后果是
  「DB 有 5 条 active 行、生产向量库 0 条该 mid」的脱钩（R7 / R8）。
- 巡检工具若不跟着改，会在非 `demo/` cwd 下按另一条相对路径找库，给出「一致 / 缺失」
  的**假结论** —— 比不检查更危险。

按「同一口径只保留一份实现」，两处一律消费本模块：`rag.py` 取
`resolve_rag_dir()` 作为 `PERSIST_DIR`，`memory_consistency._resolve_rag_dir()` 直接委托
同一个函数（见两处引用点）。本模块**只 import os**，因此巡检工具仍可在不装配
langchain / ORM 的前提下复用它（这是 memory_consistency 刻意不 import `rag.py` 的原因）。

口径
----
优先级：**显式参数 > 环境变量 `RAG_DIR` > 缺省 `<demo>/.rag_chroma`**（末两者都归一为
绝对路径）。`RAG_DIR` 显式覆盖必须保留且优先 —— 测试隔离（conftest 的
`_isolate_rag_dir`）完全依赖它。

缺省以**本文件所在包向上定位 `demo/`** 计算，故任意 cwd 下 import 后都指向同一位置；
这正是「测试从仓库根跑就把向量写进另一个 store」这类事故（H3）的同源根因修复。
"""

import os


def app_dir() -> str:
    """应用根 `demo/` 的绝对路径（按本文件位置定位，不依赖 cwd）。

    本文件位于 `<repo>/demo/app/services/rag_paths.py`，向上三级即 `<repo>/demo`。
    """
    here = os.path.dirname(os.path.abspath(__file__))          # <repo>/demo/app/services
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def default_rag_dir() -> str:
    """缺省向量库目录：`<repo>/demo/.rag_chroma`（绝对路径）。"""
    return os.path.join(app_dir(), ".rag_chroma")


def resolve_rag_dir(rag_dir=None) -> str:
    """解析向量库落盘目录：显式参数 > `RAG_DIR` 环境变量 > 缺省（应用根基准）。

    三者都返回绝对路径，使「同一份配置在不同 cwd 下得到同一结论」——
    `rag.py` 与巡检工具因此不会再出现「一个看 demo/、一个看仓库根」的错位。

    空串（`""` / `"   "`）与未设置等价，按下一优先级处理；不把空串当成 cwd。
    """
    explicit = str(rag_dir or "").strip()
    if explicit:
        return os.path.abspath(explicit)
    env = (os.environ.get("RAG_DIR") or "").strip()
    if env:
        return os.path.abspath(env)
    return default_rag_dir()
