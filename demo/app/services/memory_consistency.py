# -*- coding: utf-8 -*-
"""VendorMemory 一致性巡检：比对 SQLite 记忆表与 Chroma 向量库的 memory_id 集合。

why（存在的理由）
----------------
Gap 05 §H2 实测：live `vendor_memory` 9 行 / Chroma 179 条向量 / **memory_id
交集 = 0**，且 155 条向量连 `memory_id` 键都没有 —— 于是检索通道里的
`allowed_mids` 门把 179/179 全拦掉、live 产出为 0，而这个过程**没有任何告警**，
静默失效可以长期存在。本模块是收敛计划里「五道防线」第 5 道（定期 DB↔向量
对账）的可复算出口：给出一份能进 CI / 能人工核对的巡检报告。

为什么读 Chroma 的持久化表，而不是开 PersistentClient
---------------------------------------------------
live 向量库（`demo/.rag_chroma`、`<repo>/.rag_chroma`）在验收口径上要求
**逐字节不变**（md5 对比）。而 `chromadb.PersistentClient(path=...)` 打开持久目录
即会执行内部 migrations、写 `acquire_write` / `maintenance_log` 等表，属于写操作。
因此这里直接以 sqlite3 `mode=ro` 读它的持久化表
（`collections` / `segments` / `embeddings` / `embedding_metadata`），
并且**目录或 chroma.sqlite3 不存在时绝不创建**（避免巡检本身把 live 库"打开"出来）。

路径口径（不 import 重依赖模块）
------------------------------
巡检工具要在「服务已停、引擎配置不应被装配」的前提下可跑，故不 import `app.db`
（会装配 ORM/engine）也不 import `app.services.rag`（会带 langchain 与 collection 缓存）。
但 **RAG 向量目录不再在此复刻**（P8a-R7 之前那第二份 cwd 相对口径正是 P6·X2 的 D1，
severity high）：`_resolve_rag_dir` 直接委托纯 stdlib 的
`app/services/rag_paths.py` 这一份唯一实现，与 `rag.PERSIST_DIR` 同源 —— 只改 `rag.py`
会让巡检在非 `demo/` cwd 下按另一条相对路径找库、给出「一致 / 缺失」的假结论。
- `RAG_DIR` 缺省 `<demo>/.rag_chroma`（应用根基准的绝对路径，任意 cwd 一致）；
- `DB_PATH` 缺省 `<demo>/receipt_demo.db`，且 `DB_PATH` 写成 `./receipt_demo.db`
  或 `receipt_demo.db` 时同样按缺省处理（≈ `app/db.py:47-53`）；DB 缺省未在本批收敛，
  仍在此复刻一份，由测试与 `db._default_db_path` 对拍以防漂移。

租户口径（H1 的教训）
------------------
`tenant_id=None` 表示**全租户巡检**；给值时 DB 侧按该租户过滤、向量侧只取该租户的
collection（`tenant_<tid>_vendor_memory[_<嵌入档 identity 后缀>]`）。**缺省绝不静默回退成
`default`** —— H1 的根因正是「读侧不传租户，于是恒落 default」，一致性检查若也这么做，
结论就是假的。
"""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse

# P8a-R7：向量目录解析口径的唯一实现（纯 stdlib，不引入 langchain / ORM）。
from app.services import rag_paths

logger = logging.getLogger(__name__)

# 与 rag.py 的 collection 命名约定一致（见 rag.py:_tenant_collection_name）。
# O1 起 rag 的集合名带嵌入档 identity 后缀（如 `..._vendor_memory_hash_char_ngram_hash_256`），
# 故这里必须同时认「带后缀」与「历史无后缀」两种形态 —— 否则租户视角会把该租户的
# 集合判成 unknown、整批 active 行被误报成「缺向量」（同一命名约定两份实现只改一份
# ＝本仓①类缺陷，rag 改名时必须同步这里）。
_DEFAULT_COLLECTION = "vendor_memory"
# tenant_<tid>_vendor_memory[_<identity 后缀>]：后缀只含 [a-z0-9_]（见
# EmbeddingIdentity.collection_suffix()），tid 用非贪婪以停在**第一个** _vendor_memory 处。
_TENANT_COLLECTION_RE = re.compile(r"^tenant_(?P<tid>.+?)_vendor_memory(?:_[a-z0-9_]+)?$")

# Chroma 的持久化文件名；缺省向量目录派生自唯一实现 rag_paths（P8a-R7，不再本地复刻口径）。
_CHROMA_DB_FILE = "chroma.sqlite3"
_DEFAULT_RAG_DIR = rag_paths.default_rag_dir()

# 向量条数：一个 collection 有 VECTOR / METADATA 两个 segment，只有其中一个持有
# embeddings 行（chromadb 1.5.9 实测：METADATA 段持有）；用 DISTINCT 行 id 计数，
# 两种形态都不会重复计。
_SQL_COLLECTION_VECTORS = (
    "SELECT c.name, COUNT(DISTINCT e.id) "
    "FROM embeddings e "
    "JOIN segments s ON e.segment_id = s.id "
    "JOIN collections c ON s.collection = c.id "
    "GROUP BY c.name"
)
# 带 memory_id 的向量（按 mid 聚合，SUM 即「可回溯向量数」）
_SQL_COLLECTION_MIDS = (
    "SELECT c.name, m.string_value, COUNT(DISTINCT e.id) "
    "FROM embedding_metadata m "
    "JOIN embeddings e ON e.id = m.id "
    "JOIN segments s ON e.segment_id = s.id "
    "JOIN collections c ON s.collection = c.id "
    "WHERE m.key = 'memory_id' AND m.string_value IS NOT NULL AND m.string_value != '' "
    "GROUP BY c.name, m.string_value"
)
_SQL_COLLECTIONS = "SELECT name FROM collections"


def _resolve_rag_dir(rag_dir=None):
    """向量库落盘目录：显式参数 > 环境变量 RAG_DIR > 缺省 `<demo>/.rag_chroma`（绝对路径）。

    P8a-R7：直接委托 `app.services.rag_paths.resolve_rag_dir` —— 与 `rag.PERSIST_DIR`
    **共用同一实现**。此前这里复刻了一份 cwd 相对口径（P6·X2 的 D1，severity high）：
    只改 `rag.py` 的话，巡检会在非 `demo/` cwd 下按另一条相对路径找库，给出「一致 /
    缺失」的**假结论**（比不巡检更危险）。
    """
    return rag_paths.resolve_rag_dir(rag_dir)


def _resolve_db_path(db_path=None):
    """记忆库路径：显式参数 > DB_PATH > <demo>/receipt_demo.db（口径同 app/db.py:47-53）。

    `./receipt_demo.db` 与 `receipt_demo.db` 在 db.py 里被视作"没显式声明"，
    这里保持一致，避免同一份配置在两处得到不同结论。
    """
    raw = str(db_path or "").strip() or (os.environ.get("DB_PATH") or "").strip()
    if raw and raw not in ("./receipt_demo.db", "receipt_demo.db"):
        return os.path.abspath(raw)
    here = os.path.dirname(os.path.abspath(__file__))          # demo/app/services
    return os.path.abspath(os.path.join(here, "..", "..", "receipt_demo.db"))


def _collection_tenant(collection_name):
    """由 collection 名反推租户（启发式，见 rag.py:_tenant_collection_name）。

    rag.py 把租户 id 里非 `[a-zA-Z0-9_-]` 的字符替换成 `_` 并截断 64，
    所以这个反推是**有损**的；它只用于「按租户收窄巡检范围」，不用于安全判定。
    非本模块命名约定的 collection 归为 "unknown"（仍统计，仅不被租户过滤选中）。

    O1 起 rag 的集合名尾随嵌入档 identity 后缀（`hash_char_ngram_hash_256` /
    `siliconflow_bge_m3_1024` …），因此这里必须**同时认**带后缀与历史无后缀两种形态：
    只认无后缀的话，切档后所有租户集合都会落到 "unknown"，租户视角巡检会把整批
    active 行误报成缺向量 —— 命名约定改了而消费方没跟着改，正是本仓①类缺陷。
    """
    name = str(collection_name or "")
    if name == _DEFAULT_COLLECTION or name.startswith(_DEFAULT_COLLECTION + "_"):
        return "default"
    m = _TENANT_COLLECTION_RE.match(name)
    if m:
        return m.group("tid") or "unknown"
    return "unknown"


def _file_uri(path):
    """sqlite3 的 file: URI（只读模式由调用方追加 ?mode=ro）。"""
    return "file:" + urllib.parse.quote(os.path.abspath(path), safe="/")


def _copy_db_to_tmp(db_file):
    """把库文件（含 -wal / -shm）复制到临时目录后返回副本路径。

    why：只读打开 WAL 形态的库时，若 `-shm` 不可写，SQLite 会拒绝打开
    （SQLITE_READONLY_RECOVERY / CANTOPEN）。巡检不能因此失败，也不能为了读
    而去动原库 —— 复制一份再读，原库一个字节都不改。
    """
    tmp_dir = tempfile.mkdtemp(prefix="vm_consistency_")
    base = os.path.basename(db_file)
    for suffix in ("", "-wal", "-shm"):
        src = db_file + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp_dir, base + suffix))
    return os.path.join(tmp_dir, base)


def _connect_ro(db_file):
    """只读连接；只读打开受阻时退回复制副本，返回 (conn, opened_via_note)。"""
    try:
        conn = sqlite3.connect(_file_uri(db_file) + "?mode=ro", uri=True)
        conn.execute("SELECT 1").fetchone()
        return conn, None
    except sqlite3.Error as e:
        note = "只读打开失败（%s），改用临时副本读取" % e
        logger.warning("[memory_consistency] %s: %s", db_file, note)
        return sqlite3.connect(_copy_db_to_tmp(db_file)), note


def _read_store(rag_dir, tenant_id):
    """读向量库：返回 (store_present, stores, store_error)。

    stores 元素：{collection, tenant_id, vectors, distinct_memory_id, memory_ids,
                  vectors_without_memory_id}
    目录 / chroma.sqlite3 不存在 → (False, [], None)，**不创建任何目录**。
    """
    db_file = os.path.join(rag_dir, _CHROMA_DB_FILE)
    if not os.path.exists(db_file):
        return False, [], None
    try:
        conn, _note = _connect_ro(db_file)
        try:
            names = [r[0] for r in conn.execute(_SQL_COLLECTIONS).fetchall()]
            vectors = {r[0]: int(r[1]) for r in conn.execute(_SQL_COLLECTION_VECTORS).fetchall()}
            mids = {}
            for name, mid, cnt in conn.execute(_SQL_COLLECTION_MIDS).fetchall():
                mids.setdefault(name, {})[str(mid)] = int(cnt)
        finally:
            conn.close()
    except sqlite3.Error as e:
        # 表结构不认识（Chroma 版本差异/半损坏）时如实上报，绝不静默当成「0 条、一致」
        return True, [], "读取 Chroma 持久化表失败: %s" % e

    stores = []
    for name in sorted(set(names) | set(vectors)):
        mid_map = mids.get(name, {})
        total = vectors.get(name, 0)
        with_mid = sum(mid_map.values())
        stores.append({
            "collection": name,
            "tenant_id": _collection_tenant(name),
            "vectors": total,
            "distinct_memory_id": len(mid_map),
            "vectors_without_memory_id": max(0, total - with_mid),
            "memory_ids": sorted(mid_map.keys()),
        })
    if tenant_id is not None:
        stores = [s for s in stores if s["tenant_id"] == str(tenant_id)]
    return True, stores, None


def _read_db(db_path, tenant_id):
    """读记忆表：返回 {rows, rows_without_memory_id, memory_ids, error}。

    库文件不存在 → 0 行（全新环境，不是异常）；库在但表不存在 → 记 error，
    因为那意味着记忆层没建起来，报「0 行、一致」会掩盖真实故障。
    """
    if not os.path.exists(db_path):
        return {"rows": 0, "rows_without_memory_id": 0, "rows_inactive": 0,
                "memory_ids": [], "error": None}
    # 必须带上 status：检索侧只注入 status='active' 的行（db.list_vendor_memory
    # 默认过滤），归档/superseded 的行**本来就不该有向量** —— 若把它们算进「缺向量」，
    # 巡检会永远报不一致（live 首次跑就是 9 条遗留行全报 missing），把真问题淹没。
    sql = "SELECT memory_id, tenant_id, status FROM vendor_memory"
    params = ()
    if tenant_id is not None:
        sql += " WHERE tenant_id = ?"
        params = (str(tenant_id),)
    try:
        conn, _note = _connect_ro(db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return {"rows": 0, "rows_without_memory_id": 0, "rows_inactive": 0,
                "memory_ids": [], "error": "读取 vendor_memory 表失败: %s" % e}
    except sqlite3.Error as e:
        return {"rows": 0, "rows_without_memory_id": 0, "rows_inactive": 0,
                "memory_ids": [], "error": "打开记忆库失败: %s" % e}

    mids, blank, inactive = [], 0, 0
    for mid, _tid, status in rows:
        mid = str(mid or "")
        if str(status or "") != "active":
            inactive += 1          # 归档/superseded：不参与「是否需要向量」的判定
            continue
        if mid:
            mids.append(mid)
        else:
            blank += 1
    return {"rows": len(rows), "rows_without_memory_id": blank,
            "rows_inactive": inactive,
            "memory_ids": sorted(set(mids)), "error": None}


def check_memory_consistency(tenant_id=None, db_path=None, rag_dir=None):
    """巡检一次，返回可 JSON 序列化的报告 dict。

    参数：
      tenant_id —— None = 全租户；给值 = DB 按该租户过滤 + 只取该租户 collection。
      db_path / rag_dir —— 显式覆盖（缺省走 DB_PATH / RAG_DIR / 各自缺省口径）。

    返回键：
      db_path / rag_dir / store_present / tenant_scope / db_error / store_error
      db_rows / db_rows_without_memory_id            —— DB 侧（按租户范围）
      vectors                                        —— 范围内向量总数
      stores[]                                       —— 每个 collection 的
          vectors / distinct_memory_id / vectors_without_memory_id / memory_ids
      intersection                                   —— 两侧都有的 memory_id
      orphan_mids                                    —— 有向量但 DB 无行（H2 那类孤儿）
      missing_vector_mids                            —— **active** 行有但无向量（H4 那类缺向量；
                                                        归档/superseded 行不计入，见 _read_db 注释）
      db_rows_active / db_rows_inactive              —— active 行数 / 归档等非 active 行数
      consistent                                     —— 双向为空且两侧均无 error

    判定方向说明：`orphan_mids` / `missing_vector_mids` 都是**问题清单**；
    `vectors_without_memory_id` 是第三类问题（向量无法按 memory_id 回溯删除/失效）。
    """
    scope = "all" if tenant_id is None else str(tenant_id)
    dbp = _resolve_db_path(db_path)
    ragd = _resolve_rag_dir(rag_dir)

    db_info = _read_db(dbp, tenant_id)
    store_present, stores, store_error = _read_store(ragd, tenant_id)

    db_mids = set(db_info["memory_ids"])
    vec_mids = set()
    for s in stores:
        vec_mids.update(s["memory_ids"])

    orphan = sorted(vec_mids - db_mids)
    missing = sorted(db_mids - vec_mids)
    consistent = (not orphan and not missing
                  and not db_info["error"] and not store_error)
    return {
        "db_path": dbp,
        "rag_dir": ragd,
        "store_present": store_present,
        "tenant_scope": scope,
        "db_rows": db_info["rows"],
        "db_rows_active": len(db_info["memory_ids"]),
        "db_rows_inactive": db_info.get("rows_inactive", 0),
        "db_rows_without_memory_id": db_info["rows_without_memory_id"],
        "db_error": db_info["error"],
        "store_error": store_error,
        "vectors": sum(s["vectors"] for s in stores),
        "stores": stores,
        "intersection": sorted(db_mids & vec_mids),
        "orphan_mids": orphan,
        "missing_vector_mids": missing,
        "consistent": consistent,
    }


def format_report(report):
    """人类可读摘要（CLI 非 --json 时用；措辞只用简体中文，无 emoji）。"""
    lines = [
        "VendorMemory 一致性巡检",
        "  记忆库: %s（租户范围=%s，行数=%d，无 memory_id 行=%d）"
        % (report["db_path"], report["tenant_scope"], report["db_rows"],
           report["db_rows_without_memory_id"]),
        "  向量库: %s（%s）" % (report["rag_dir"],
                            "存在" if report["store_present"] else "不存在"),
    ]
    for s in report["stores"]:
        lines.append(
            "    - %s: 向量=%d 可回溯 memory_id=%d 无 memory_id 向量=%d"
            % (s["collection"], s["vectors"], s["distinct_memory_id"],
               s["vectors_without_memory_id"]))
    lines.append(
        "  交集=%d 孤儿向量=%d 缺向量行=%d"
        % (len(report["intersection"]), len(report["orphan_mids"]),
           len(report["missing_vector_mids"])))
    if report["db_error"]:
        lines.append("  记忆库异常: %s" % report["db_error"])
    if report["store_error"]:
        lines.append("  向量库异常: %s" % report["store_error"])
    lines.append("  结论: %s" % ("一致" if report["consistent"] else "不一致"))
    return "\n".join(lines)


def main(argv=None):
    """CLI：`python -m app.services.memory_consistency [--json] [--tenant T]`。

    --strict 时不一致返回退出码 2 —— 供定时对账/CI 直接当告警用；缺省恒为 0，
    避免人工看一眼巡检就把流水线判红。
    """
    parser = argparse.ArgumentParser(
        description="VendorMemory 记忆表与向量库的 memory_id 一致性巡检")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--tenant", default=None, help="只巡检该租户（缺省为全租户）")
    parser.add_argument("--db-path", default=None, help="覆盖记忆库路径")
    parser.add_argument("--rag-dir", default=None, help="覆盖向量库目录")
    parser.add_argument("--strict", action="store_true",
                        help="发现不一致时以退出码 2 结束")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    report = check_memory_consistency(
        tenant_id=args.tenant, db_path=args.db_path, rag_dir=args.rag_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.strict and not report["consistent"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
