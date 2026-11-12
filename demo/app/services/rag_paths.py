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
优先级（`resolve_rag_dir()`，从高到低）：

1. **显式参数** `rag_dir`（调用方直接指定，测试与脚本的显式覆盖口）；
2. 环境变量 **`CHROMA_PATH`**（新键，语义与 `RAG_DIR` 相同、优先级更高，供「只想改向量
   落点、不动 `RAG_DIR` 历史值」的部署使用）；
3. 环境变量 **`RAG_DIR`**（历史键，**必须继续优先于派生分支** —— 测试隔离
   （conftest 的 `_isolate_rag_dir`）完全依赖它）；
4. **随 `DB_PATH` 派生**：`DB_PATH` 已显式外置（非空、非 `receipt_demo.db` 相对名写法、
   且 realpath 不等于 `<demo>/receipt_demo.db`）时，派生为
   `<DB 同目录>/<DB 文件名去扩展名>.rag_chroma`；
5. 缺省 `<demo>/.rag_chroma`。

空串 / 全空白的环境变量值与「未设置」等价（与 `db.py` 对 `DB_PATH` 的处理同口径）。

why（第 4 档为什么必须存在）
------------------------
**DB 与向量必须同源**：一次识别写入既落 `vendor_memory` 行（按 `DB_PATH`），又落向量
（按本模块解析出的目录）。第 4 档之前，解析优先级里没有「随 DB 派生」这一档，于是
「只外置 DB、不设 RAG_DIR」的运行 —— TestClient 用例、E2E 脚本、迁移演练 —— 会把
**向量写进生产目录 `demo/.rag_chroma`**，而 DB 行落在临时库：巡检随即报孤儿，且没人
知道是谁写的。2026-10-15 那批 `tenant_uat_a_*` / `tenant_uat_b_*` 夹具向量（live 实测
52 条向量里占 40 条、vendor 全是夹具名 `Vendor_A` / `Vendor_B`）的来历即此：
`demo/tests/test_uat_031_sku_merge_batch_migration.py:29` 这类文件在**模块 import 期**就
设 `os.environ["DB_PATH"]` 指向临时库，早于 conftest 的 per-test `_isolate_rag_dir`
夹具，于是向量落进生产目录（G1，H3/R7 换了形态复发）。

派生分支不改变任何「已设 `RAG_DIR` / `CHROMA_PATH`」或「未外置 DB」的运行：
生产从任意 cwd 起进程仍指 `demo/.rag_chroma`，把 `DB_PATH` 显式写成 live 库的绝对路径
也算「未外置」（仍指 `demo/.rag_chroma`，§9 的巡检复现命令依赖这一点）。

缺省以**本文件所在包向上定位 `demo/`** 计算，故任意 cwd 下 import 后都指向同一位置；
这正是「测试从仓库根跑就把向量写进另一个 store」这类事故（H3）的同源根因修复。
"""

import os

# 环境变量键名集中在此，避免消费方各写一份字符串常量。
_ENV_CHROMA_PATH = "CHROMA_PATH"
_ENV_RAG_DIR = "RAG_DIR"
_ENV_DB_PATH = "DB_PATH"

# 与 `app/db.py:61-66` 同口径：`DB_PATH` 写成相对名时视同「没显式声明」，不派生。
_DB_BASENAME = "receipt_demo.db"
_DB_NON_DECLARED_VALUES = ("./" + _DB_BASENAME, _DB_BASENAME)


def app_dir() -> str:
    """应用根 `demo/` 的绝对路径（按本文件位置定位，不依赖 cwd）。

    本文件位于 `<repo>/demo/app/services/rag_paths.py`，向上三级即 `<repo>/demo`。
    """
    here = os.path.dirname(os.path.abspath(__file__))          # <repo>/demo/app/services
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def default_rag_dir() -> str:
    """缺省向量库目录：`<repo>/demo/.rag_chroma`（绝对路径）。"""
    return os.path.join(app_dir(), ".rag_chroma")


def default_db_path() -> str:
    """缺省记忆库路径：`<repo>/demo/receipt_demo.db`（绝对路径）。

    与 `app/db.py` 的 `_default_db_path` 同值；本模块只 import os，不能去 import db
    （否则巡检工具会被迫装配 ORM / 触发 import 期迁移），故这里是**路径常量**而非
    解析逻辑的第二份实现 —— 派生判据（是否「外置」）只依赖它做比较。
    """
    return os.path.join(app_dir(), _DB_BASENAME)


def _env(name: str) -> str:
    """读环境变量并归一：空串 / 全空白 == 未设置。"""
    return (os.environ.get(name) or "").strip()


def externalized_db_path() -> str:
    """已显式外置的 `DB_PATH`（绝对路径）；未外置返回空串。

    「未外置」的三种形态（与 `app/db.py:61-66` 的判据逐项一致）：
    - 未设置 / 空串 / 全空白；
    - 写成相对名 `receipt_demo.db` 或 `./receipt_demo.db`（dotenv 从 `demo/.env` 注入的
      正是后者，历史上被 db.py 视为「没声明」，派生分支不得把它当外置库）；
    - realpath 等于 `<demo>/receipt_demo.db`（显式指向 live 库的绝对路径写法 —— 巡检
      复现命令 `DB_PATH=$PWD/receipt_demo.db` 依赖这一条：DB 就是 live 库时向量仍必须
      落 `demo/.rag_chroma`，不得派生出 `receipt_demo.rag_chroma` 这个新库）。
    """
    raw = _env(_ENV_DB_PATH)
    if not raw or raw in _DB_NON_DECLARED_VALUES:
        return ""
    abspath = os.path.abspath(raw)
    if os.path.realpath(abspath) == os.path.realpath(default_db_path()):
        return ""
    return abspath


def derived_rag_dir_from_db() -> str:
    """按 `DB_PATH` 派生的向量目录：`<DB 同目录>/<DB 文件名去扩展名>.rag_chroma`。

    未外置 `DB_PATH` 时返回空串（由 `resolve_rag_dir` 继续回落到缺省）。
    返回绝对路径，使派生结果同样与 cwd 无关。
    """
    db = externalized_db_path()
    if not db:
        return ""
    stem = os.path.splitext(os.path.basename(db))[0] or _DB_BASENAME
    return os.path.abspath(os.path.join(os.path.dirname(db), stem + ".rag_chroma"))


def resolve_rag_dir(rag_dir=None) -> str:
    """解析向量库落盘目录（**唯一实现**，口径详见模块 docstring）。

    优先级：显式参数 > `CHROMA_PATH` > `RAG_DIR` > 随外置 `DB_PATH` 派生 > 缺省
    `<demo>/.rag_chroma`。全部返回绝对路径，使「同一份配置在不同 cwd 下得到同一结论」
    —— `rag.py` 与巡检工具因此不会再出现「一个看 demo/、一个看仓库根」的错位。

    空串（`""` / `"   "`）与未设置等价，按下一优先级处理；不把空串当成 cwd。
    """
    explicit = str(rag_dir or "").strip()
    if explicit:
        return os.path.abspath(explicit)
    for name in (_ENV_CHROMA_PATH, _ENV_RAG_DIR):
        value = _env(name)
        if value:
            return os.path.abspath(value)
    derived = derived_rag_dir_from_db()
    if derived:
        return derived
    return default_rag_dir()
