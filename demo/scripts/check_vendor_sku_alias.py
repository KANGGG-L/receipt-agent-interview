# -*- coding: utf-8 -*-
"""供应商×SKU 别名（vendor_sku_alias）一致性巡检：四类异常 + 回填预演（只读）。

why（存在的理由）
----------------
P2-T2 把「该供应商用这个写法指这个 SKU」沉淀成结构化事实，读取侧
（`receipt_utils._match_sku` → `db.find_vendor_sku_alias`）优先消费它。这张表一旦
被污染，**表现是"匹配悄悄变差"而不是报错**：
- 指向不存在 / 已停用 / 跨租户的 SKU → 读取侧判为死链，静默回落全局匹配；
- 同一 (租户, 供应商, 写法) 出现多条 active → 「这个写法指哪个 SKU」不再确定
  （读取侧靠唯一键才能确定命中哪一条）；
- canonical 与目标 SKU 归一后不一致 → 疑似被"后跑的实现"翻转指向了重复 SKU。
  P2a 审核 M1 实测过这个形态：解析期经别名确认「本吧 → 本地牛腩」，approve 时
  入账路径的第二份匹配实现又新建了重复 SKU「本吧」并把别名翻转指过去 —— 机制
  **自己触发**了它本来要消灭的 SKU 爆炸；
- 供应商维度缺失（NULL / 档案不存在 / 跨租户）→ 读取侧要求 supplier_id，这一行
  永远不被消费（登记了也没人用，属"只写不读"的静默残留）。

四类异常都不会让任何接口失败，只会让匹配率与命中率悄悄变差 —— 故需要一份可复算、
可进 CI 的巡检出口（对应收敛计划「五道防线」第 5 道：定期对账）。

巡检本身必须是只读的（live 库与 live 向量库都要求逐字节不变）
----------------------------------------------------------
- 数据面全程 sqlite3 `mode=ro` 读，**不 import app.db**（它在 import 期执行
  `_make_engine()` 的迁移块，按 DB_PATH 对目标库做 ALTER —— 本项目已因此两次让
  live 库发生加法迁移）。
- 唯一例外是归一实现：第 ③ 类需要与判定侧**同一份** `_normalize_sku_name`
  （自己抄一份就是本仓高发的「同一根因多份实现只改一份」）。该函数在
  `app.services.receipt_utils`，其顶层 `from app import db` → 会触发上述迁移。
  故 `_load_normalizer()` 在 import 前把 `DB_PATH` 指向**目标库的一次性副本**、
  `RAG_DIR` 指向临时空目录，import 完立即还原环境变量 —— 源库一个字节都不改。
  why 用副本而不是空库：回填预演里的 `db.resolve_supplier_id` 要读真实供应商
  档案，空库会让候选集凭空缩水（读数就假了）。
- 表 0 行时连归一实现都不加载（live 现为 0 行）—— 巡检零副作用、零重依赖。

四类判定与「谁是消费方」的口径
----------------------------
判定顺序与读取侧 `find_vendor_sku_alias` 的实际行为对齐（它命中不了 = 这行白登记）：
  ① 死链        —— 目标 SKU 不存在 / active=0（被合并漏改指）/ 跨租户；
  ② 唯一口径    —— 同 (tenant_id, supplier_id, raw_name) 多条 **active**；
                    SQLite 的唯一索引把 NULL 当互不相同，故 supplier_id 为 NULL
                    的行可以重复共存，是本类最容易漏的一种形态；
  ③ canonical   —— active 行且目标 SKU 存在时，`归一(目标 SKU 名)` 与
                    `归一(canonical_name)` 不一致。**本类是复核清单而不是定论**：
                    canonical_name 记录的是"供应商写法"的归一形，与 SKU 规范名
                    本就可能不同（缩写别名是合法形态）。故再给一个三角判据
                    `other_sku_ids`：同租户里存在**另一个** active SKU 的归一形
                    正好等于 canonical_name —— 命中它才是"疑似被翻转指向重复 SKU"。
  ④ 供应商缺失  —— supplier_id 为 NULL / 档案不存在 / 跨租户（读取侧直接返回 None）。

生命周期状态（status=retired、valid_to 已过期）不算异常：它们是刻意的失效态，
读取侧本就不命中；但死链（①）与供应商缺失（④）对**全部行**统计 —— 死链与状态无关
（retired 行一旦被 revive 会立刻变成悬空命中），且这些行不会被任何对账口径覆盖。

回填预演（`--simulate-backfill`，缺省开）
--------------------------------------
回答「若把已人工确认（`receipt_items.matched=1`）的写法回填进别名表，会落入哪几类」：
候选集直接用回填脚本自己的 `plan()`（唯一实现，不在本脚本重写投票/跳过逻辑），
判定与巡检同一套。候选天然不含①②④（plan 已过滤死 SKU 与未锚定供应商、并按唯一键
去重），唯一有量的是第 ③ 类 —— 即"需要人工复核"的清单。

用法（只读；默认巡 live 路径，但**不会**对它执行任何迁移或写入）
  python demo/scripts/check_vendor_sku_alias.py
  python demo/scripts/check_vendor_sku_alias.py --json
  python demo/scripts/check_vendor_sku_alias.py --db-path /tmp/live_copy.db --tenant default
  python demo/scripts/check_vendor_sku_alias.py --json --no-simulate-backfill --strict
"""

import argparse
import atexit
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_SCRIPTS_DIR)
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ALIAS_TABLE = "vendor_sku_alias"
_DEFAULT_DB_NAME = "receipt_demo.db"

# 与巡检口径相关的行数上限（JSON 里每类最多给这么多条明细，超出只报计数）
_DEFAULT_LIMIT = 50

# 归一实现的惰性缓存 + import 期沙箱（进程内只建一次）
_NORMALIZER = None
_SANDBOX = {"dir": None, "db_path": None, "rag_dir": None}


# -------------------------------------------------------------
# 路径与只读连接
# -------------------------------------------------------------
def _resolve_db_path(db_path=None):
    """目标库路径：显式参数 > DB_PATH > <demo>/receipt_demo.db（口径同 app/db.py:48-53）。

    `./receipt_demo.db` 与 `receipt_demo.db` 在 db.py 里被视作"没显式声明"，
    这里保持一致，避免同一份配置在两处得到不同结论。
    """
    raw = str(db_path or "").strip() or (os.environ.get("DB_PATH") or "").strip()
    if raw and raw not in ("./" + _DEFAULT_DB_NAME, _DEFAULT_DB_NAME):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(_DEMO_DIR, _DEFAULT_DB_NAME))


def _connect_ro(db_file):
    """只读连接：`mode=ro` 的 file: URI。

    why 不复制副本再读：巡检可能对着 live 库跑，"只读"必须是字面意义 ——
    ro 打开不改一个字节。连不上就直接失败（返回 None），由调用方记为错误，
    绝不为了"读得到"而改成可写打开。
    """
    uri = "file:" + urllib.parse.quote(os.path.abspath(db_file), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT 1").fetchone()
        return conn
    except sqlite3.Error:
        return None


# -------------------------------------------------------------
# 归一实现（与判定侧同源）+ import 期沙箱
# -------------------------------------------------------------
def _ensure_sandbox(target_db):
    """准备一次性沙箱目录（目标库副本 + 空 RAG 目录），返回其路径字典。

    幂等：进程内只建一次。
    """
    if _SANDBOX["db_path"]:
        return _SANDBOX
    sandbox_dir = tempfile.mkdtemp(prefix="alias_check_sandbox_")
    atexit.register(shutil.rmtree, sandbox_dir, True)
    copy_path = os.path.join(sandbox_dir, "sandbox.db")
    if os.path.exists(target_db):
        shutil.copy2(target_db, copy_path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(target_db + suffix):
                shutil.copy2(target_db + suffix, copy_path + suffix)
    else:
        open(copy_path, "a").close()
    _SANDBOX.update({"dir": sandbox_dir, "db_path": copy_path,
                     "rag_dir": os.path.join(sandbox_dir, "rag_chroma")})
    return _SANDBOX


@contextlib.contextmanager
def _import_env(target_db):
    """在 import 窗口内把 DB_PATH 指向沙箱副本、RAG_DIR 指向临时空目录，退出即还原。

    why 用副本：回填预演里的 `db.resolve_supplier_id` 要读真实供应商档案，
    空库会让候选集凭空缩水（读数就假了）。副本上的迁移无害；源库只读。
    why 用完还原：沙箱只需要覆盖 import 窗口，不污染调用进程（测试进程里尤其重要）。
    """
    sandbox = _ensure_sandbox(target_db)
    old_db = os.environ.get("DB_PATH")
    old_rag = os.environ.get("RAG_DIR")
    os.environ["DB_PATH"] = sandbox["db_path"]
    os.environ["RAG_DIR"] = sandbox["rag_dir"]
    try:
        yield sandbox
    finally:
        _restore_env("DB_PATH", old_db)
        _restore_env("RAG_DIR", old_rag)


def _load_normalizer(target_db):
    """惰性加载 `_normalize_sku_name`（唯一实现），并在 import 期用沙箱兜住迁移。

    只在真有行需要判定第 ③ 类时才被调用 —— 表 0 行时本脚本不 import 任何 app.*
    （live 现为 0 行，故常规巡检零副作用）。
    """
    global _NORMALIZER
    if _NORMALIZER is not None:
        return _NORMALIZER
    with _import_env(target_db):
        from app.services.receipt_utils import _normalize_sku_name  # noqa: E402
    _NORMALIZER = _normalize_sku_name
    return _NORMALIZER


def _restore_env(key, value):
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def _tenant_key(value):
    """租户归一：空/NULL 按 default 处理（与 db._tenant_ok 同口径）。"""
    return str(value or "").strip() or "default"


# -------------------------------------------------------------
# 读取
# -------------------------------------------------------------
def _table_present(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _read_alias_rows(conn):
    """读别名表全部行（含 status/valid_to，用于区分"刻意失效"与"异常"）。"""
    return conn.execute(
        "SELECT id, tenant_id, supplier_id, sku_id, raw_name, canonical_name,"
        " status, valid_to FROM " + _ALIAS_TABLE).fetchall()


def _read_index(conn, table):
    """把 skus / suppliers 读成 {id: {"name", "active", "tenant_id"}}（缺表返回空）。"""
    if not _table_present(conn, table):
        return {}
    out = {}
    for rid, name, active, tenant_id in conn.execute(
            "SELECT id, name, active, tenant_id FROM " + table):
        out[int(rid)] = {"name": str(name or ""), "active": int(active or 0),
                         "tenant_id": _tenant_key(tenant_id)}
    return out


def _is_expired(valid_to):
    """valid_to 非空且已过期 → 读取侧不命中（与 db.find_vendor_sku_alias 同口径）。

    解析口径与 `db._parse_iso_dt` 一致（`datetime.fromisoformat`），且**无法解析时按
    "未过期"**处理：与 db 同款取舍 —— 宁可把一条时间戳脏的行算作 active，
    也不因为格式问题把整批行误判成"刻意失效"从而漏检。
    """
    text = str(valid_to or "").strip()
    if not text:
        return False
    from datetime import datetime
    try:
        deadline = datetime.fromisoformat(text)
    except ValueError:
        return False
    return datetime.now() > deadline


def _classify(row, sku_index, supplier_index, norm_name):
    """单行的四类判定：返回 (dangling, supplier_reason, canonical_problem)。

    - dangling: None 或 {"reason": ...}（missing_sku / inactive_sku / tenant_mismatch）
    - supplier_reason: None 或 null_supplier / missing_supplier / tenant_mismatch
    - canonical_problem: None 或 {"sku_name", "canonical_name"}（仅 active 行且 SKU 存在）
    """
    alias_id, tenant_id, supplier_id, sku_id, raw_name, canonical_name, status, _vt = row
    tenant_id = _tenant_key(tenant_id)

    dangling = None
    sku = sku_index.get(int(sku_id)) if sku_id is not None else None
    if sku is None:
        dangling = {"reason": "missing_sku"}
    elif sku["tenant_id"] != tenant_id:
        dangling = {"reason": "tenant_mismatch"}
    elif sku["active"] != 1:
        dangling = {"reason": "inactive_sku"}

    if supplier_id is None:
        supplier_reason = "null_supplier"
    else:
        supplier = supplier_index.get(int(supplier_id))
        if supplier is None:
            supplier_reason = "missing_supplier"
        elif supplier["tenant_id"] != tenant_id:
            supplier_reason = "tenant_mismatch"
        else:
            supplier_reason = None

    canonical_problem = None
    if (sku is not None and str(status or "active") == "active"
            and sku["tenant_id"] == tenant_id):
        canonical = str(canonical_name or "").strip() or str(raw_name or "").strip()
        if norm_name(sku["name"]) != norm_name(canonical):
            canonical_problem = {"sku_name": sku["name"], "canonical_name": canonical}
    return dangling, supplier_reason, canonical_problem


def check_vendor_sku_alias(db_path=None, tenant_id=None, simulate_backfill=True,
                           limit=_DEFAULT_LIMIT):
    """巡检一次，返回可 JSON 序列化的报告 dict（**只读**，不产生任何写入）。

    参数：
      tenant_id —— None = 全租户；给值 = 只判该租户的别名行（SKU/供应商索引仍取全库，
                   否则跨租户错挂会被过滤掉、检测不出来）。
      simulate_backfill —— 是否附带"回填预演"（候选集复用回填脚本的 plan()）。
      limit —— 每类异常最多返回多少条明细（只影响输出体积，不影响计数）。
    """
    dbp = _resolve_db_path(db_path)
    scope = "all" if tenant_id is None else str(tenant_id)
    report = {
        "db_path": dbp,
        "tenant_scope": scope,
        "table_present": False,
        "db_error": None,
        "rows": {"total": 0, "active": 0, "inactive": 0, "expired": 0,
                 "checked": 0},
        "checks": {
            "dangling_sku": {"scope": "全部行", "count": 0, "by_reason": {}, "items": []},
            "duplicate_active_key": {"scope": "active 行", "count": 0, "groups": []},
            "canonical_mismatch": {"scope": "active 且目标 SKU 存在", "count": 0,
                                   "items": []},
            "missing_supplier": {"scope": "全部行", "count": 0, "by_reason": {},
                                 "items": []},
        },
        "findings_total": 0,
        "verdict": "clean",
        "verdict_text": "",
        "backfill_simulation": None,
    }

    if not os.path.exists(dbp):
        report["db_error"] = "目标库不存在"
        report["verdict"] = "no_db"
        report["verdict_text"] = "记忆库不存在（%s），无法巡检" % dbp
        return report

    conn = _connect_ro(dbp)
    if conn is None:
        report["db_error"] = "只读打开失败"
        report["verdict"] = "no_db"
        report["verdict_text"] = "记忆库只读打开失败（%s），无法巡检" % dbp
        return report
    try:
        if not _table_present(conn, _ALIAS_TABLE):
            report["verdict"] = "no_table"
            report["verdict_text"] = ("别名表不存在（P2-T2 迁移未执行），无法巡检 —— "
                                      "不是「无污染」")
            return report
        report["table_present"] = True
        rows = _read_alias_rows(conn)
        sku_index = _read_index(conn, "skus")
        supplier_index = _read_index(conn, "suppliers")
    except sqlite3.Error as e:
        report["db_error"] = "读取失败: %s" % e
        report["verdict"] = "no_db"
        report["verdict_text"] = "读取别名表失败（%s），无法巡检" % e
        return report
    finally:
        conn.close()

    if tenant_id is not None:
        rows = [r for r in rows if _tenant_key(r[1]) == scope]

    active, inactive, expired = [], 0, 0
    for row in rows:
        status = str(row[6] or "active")
        if status != "active":
            inactive += 1
        elif _is_expired(row[7]):
            expired += 1
        else:
            active.append(row)
    report["rows"].update({"total": len(rows), "active": len(active),
                           "inactive": inactive, "expired": expired})
    report["rows"]["checked"] = len(active)

    # 归一实现只在真有行需要第 ③ 类时才加载（0 行时不 import 任何 app.*）
    norm_memo = {}

    def norm_name(value):
        key = str(value or "")
        if key not in norm_memo:
            norm_memo[key] = str(_load_normalizer(dbp)(key) or "").strip()
        return norm_memo[key]

    # 第 ③ 类的三角判据：同租户 active SKU 里，归一形恰好等于 canonical_name 的
    # 其它 SKU（命中它 ⇒ 疑似"指向被翻转/存在重复 SKU"，而不是合法的缩写别名）
    sku_by_norm = {}
    if any(row[3] is not None and int(row[3]) in sku_index for row in active):
        for sid, sku in sku_index.items():
            if sku["active"] == 1:
                sku_by_norm.setdefault(norm_name(sku["name"]), []).append(sid)

    dangling_items, supplier_items, canonical_items = [], [], []
    dup_groups = {}
    for row in rows:
        dangling, supplier_reason, canonical_problem = _classify(
            row, sku_index, supplier_index, norm_name)
        alias_id, tenant_id_r, supplier_id, sku_id, raw_name = row[0], row[1], row[2], row[3], row[4]
        base = {"alias_id": int(alias_id), "tenant_id": _tenant_key(tenant_id_r),
                "supplier_id": None if supplier_id is None else int(supplier_id),
                "sku_id": None if sku_id is None else int(sku_id),
                "raw_name": str(raw_name or ""), "status": str(row[6] or "active")}
        if dangling:
            dangling_items.append(dict(base, reason=dangling["reason"]))
        if supplier_reason:
            supplier_items.append(dict(base, reason=supplier_reason))
        if canonical_problem:
            # 三角判据只取**同租户**的其它 active SKU（跨租户同名不构成"翻转指向"）
            others = [sid for sid in sku_by_norm.get(
                norm_name(canonical_problem["canonical_name"]), [])
                if (sku_id is None or sid != int(sku_id))
                and sku_index[sid]["tenant_id"] == _tenant_key(tenant_id_r)]
            canonical_items.append(dict(
                base, **canonical_problem, other_sku_ids=sorted(others),
                suspect_flipped_pointer=bool(others)))
        # 第 ② 类：唯一口径只看 active 行（retired 同键是合法的历史留存）
        if str(row[6] or "active") == "active":
            key = (_tenant_key(tenant_id_r),
                   None if supplier_id is None else int(supplier_id), str(raw_name or ""))
            dup_groups.setdefault(key, []).append(base)

    dup_list = [
        {"tenant_id": k[0], "supplier_id": k[1], "raw_name": k[2],
         "active_rows": len(v), "alias_ids": sorted(x["alias_id"] for x in v)}
        for k, v in sorted(dup_groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]),
                                                              kv[0][2]))
        if len(v) > 1
    ]

    def _by_reason(items):
        out = {}
        for it in items:
            out[it["reason"]] = out.get(it["reason"], 0) + 1
        return out

    c = report["checks"]
    c["dangling_sku"].update({"count": len(dangling_items),
                              "by_reason": _by_reason(dangling_items),
                              "items": dangling_items[:limit]})
    c["dangling_sku"]["truncated"] = len(dangling_items) > limit
    c["missing_supplier"].update({"count": len(supplier_items),
                                  "by_reason": _by_reason(supplier_items),
                                  "items": supplier_items[:limit]})
    c["missing_supplier"]["truncated"] = len(supplier_items) > limit
    c["canonical_mismatch"].update({"count": len(canonical_items),
                                    "items": canonical_items[:limit],
                                    "suspect_flipped_pointer": sum(
                                        1 for x in canonical_items
                                        if x["suspect_flipped_pointer"])})
    c["canonical_mismatch"]["truncated"] = len(canonical_items) > limit
    c["duplicate_active_key"].update({"count": len(dup_list), "groups": dup_list[:limit]})
    c["duplicate_active_key"]["truncated"] = len(dup_list) > limit

    total = (len(dangling_items) + len(dup_list) + len(canonical_items)
             + len(supplier_items))
    report["findings_total"] = total
    if total == 0:
        report["verdict"] = "clean"
        if not rows:
            report["verdict_text"] = "无污染可清（别名表 0 行）"
        else:
            report["verdict_text"] = ("无污染可清（%d 行 active，四类异常均为 0）"
                                      % len(active))
    else:
        report["verdict"] = "polluted"
        report["verdict_text"] = ("发现 %d 处异常：死链 %d / 重复键 %d / canonical 不一致 "
                                  "%d（其中疑似被翻转指向重复 SKU %d 条）/ 供应商缺失 %d"
                                  % (total, len(dangling_items), len(dup_list),
                                     len(canonical_items),
                                     c["canonical_mismatch"]["suspect_flipped_pointer"],
                                     len(supplier_items)))

    if simulate_backfill:
        report["backfill_simulation"] = simulate_backfill_impact(
            dbp, sku_index, supplier_index, norm_name)
    return report


def simulate_backfill_impact(db_path, sku_index, supplier_index, norm_name):
    """回填预演：把候选别名按同一套判定跑一遍，返回各预计落入数（只读）。

    候选集复用回填脚本的 `plan()`（唯一实现）—— 它的扫描逻辑（频次投票、跳过未锚定
    供应商/死 SKU）不在本脚本重写，避免"同一根因多份实现"。plan() 只 SELECT。

    任何异常都吞成 {"error": ...}：回填预演是**附加信息**，不能让巡检主结论翻车
    （本项目已出现"辅助能力拖垮主链路"的形态）。
    """
    out = {"candidate_count": 0, "predicted": {"dangling_sku": 0, "duplicate_active_key": 0,
                                               "canonical_mismatch": 0,
                                               "missing_supplier": 0},
           "canonical_mismatch_samples": [], "note": "", "error": None}
    try:
        # 顺序不可颠倒：`backfill_vendor_sku_alias` 顶层 `import app.db`，会按 DB_PATH
        # 执行 import 期迁移。必须先立沙箱（DB_PATH 指向目标库副本），再 import。
        with _import_env(db_path):
            from backfill_vendor_sku_alias import plan as _plan  # 同目录脚本（唯一实现）
        candidates, stats = _plan(db_path)
    except Exception as e:                                  # noqa: BLE001
        out["error"] = "回填预演失败: %s" % e
        return out

    out["candidate_count"] = len(candidates)
    out["plan_stats"] = stats
    seen_keys = set()
    for c in candidates:
        tenant_id = _tenant_key(c.get("tenant_id"))
        sku = sku_index.get(int(c["sku_id"]))
        supplier = supplier_index.get(int(c["supplier_id"]))
        if sku is None or sku["tenant_id"] != tenant_id or sku["active"] != 1:
            out["predicted"]["dangling_sku"] += 1
        if supplier is None or supplier["tenant_id"] != tenant_id:
            out["predicted"]["missing_supplier"] += 1
        key = (tenant_id, int(c["supplier_id"]), str(c["raw_name"] or ""))
        if key in seen_keys:
            out["predicted"]["duplicate_active_key"] += 1
        seen_keys.add(key)
        if sku is not None:
            canonical = str(c.get("canonical_name") or "").strip()
            if norm_name(sku["name"]) != norm_name(canonical):
                out["predicted"]["canonical_mismatch"] += 1
                if len(out["canonical_mismatch_samples"]) < 10:
                    out["canonical_mismatch_samples"].append(
                        {"supplier_id": int(c["supplier_id"]), "raw_name": c["raw_name"],
                         "canonical_name": canonical, "sku_name": sku["name"]})
    out["note"] = (
        "候选由回填脚本的 plan() 产出（已过滤死 SKU / 未锚定供应商并按唯一键去重），"
        "故①②④天然为 0；有量的第 ③ 类是**人工复核清单** —— canonical_name 记录的"
        "是供应商写法的归一形，与 SKU 规范名本就可能不同（缩写别名是合法形态）。")
    return out


def format_report(report):
    """人类可读摘要（简体中文，无 emoji）。"""
    lines = [
        "供应商×SKU 别名一致性巡检（只读）",
        "  记忆库: %s（租户范围=%s）" % (report["db_path"], report["tenant_scope"]),
    ]
    if report["db_error"]:
        lines.append("  异常: %s" % report["db_error"])
    if not report["table_present"]:
        lines.append("  别名表: 不存在")
    else:
        r = report["rows"]
        lines.append("  别名行: 合计 %d（active %d / 非 active %d / 已过期 %d）"
                     % (r["total"], r["active"], r["inactive"], r["expired"]))
        c = report["checks"]
        lines.append("  ① 死链（SKU 缺失/停用/跨租户）: %d %s"
                     % (c["dangling_sku"]["count"], c["dangling_sku"]["by_reason"] or ""))
        lines.append("  ② 同键多条 active（违反唯一口径）: %d"
                     % c["duplicate_active_key"]["count"])
        lines.append("  ③ canonical 与目标 SKU 归一后不一致: %d（其中疑似被翻转指向"
                     "重复 SKU %d 条）"
                     % (c["canonical_mismatch"]["count"],
                        c["canonical_mismatch"]["suspect_flipped_pointer"]))
        lines.append("  ④ 供应商缺失/NULL/跨租户: %d %s"
                     % (c["missing_supplier"]["count"],
                        c["missing_supplier"]["by_reason"] or ""))
        sim = report.get("backfill_simulation")
        if sim and not sim.get("error"):
            lines.append("  回填预演: 候选 %d 条 → 预计 ① %d / ② %d / ③ %d / ④ %d"
                         % (sim["candidate_count"], sim["predicted"]["dangling_sku"],
                            sim["predicted"]["duplicate_active_key"],
                            sim["predicted"]["canonical_mismatch"],
                            sim["predicted"]["missing_supplier"]))
        elif sim:
            lines.append("  回填预演: %s" % sim["error"])
    lines.append("  结论: %s" % report["verdict_text"])
    return "\n".join(lines)


def main(argv=None):
    """CLI：只读巡检；`--strict` 时**发现四类异常**以退出码 2 结束（供 CI/定时对账）。

    「无法巡检」（库不存在 / 表未迁移 / 打开失败）不是污染，故 `--strict` 下仍返回 0，
    但 `verdict` 会明确写成 no_db / no_table —— 不把"巡检没跑成"含混成"无污染"，
    也不与"发现污染"共用同一个退出码（两种故障的处置动作不同）。
    """
    ap = argparse.ArgumentParser(description="vendor_sku_alias 一致性巡检（只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--db-path", default=None, help="覆盖目标库路径（缺省走 DB_PATH/默认）")
    ap.add_argument("--tenant", default=None, help="只巡检该租户（缺省为全租户）")
    ap.add_argument("--no-simulate-backfill", action="store_true",
                    help="跳过回填预演（不加载回填脚本，最快）")
    ap.add_argument("--limit", type=int, default=_DEFAULT_LIMIT,
                    help="每类异常最多输出的明细条数（缺省 %d）" % _DEFAULT_LIMIT)
    ap.add_argument("--strict", action="store_true", help="发现异常时退出码为 2")
    args = ap.parse_args(argv)

    report = check_vendor_sku_alias(
        db_path=args.db_path, tenant_id=args.tenant,
        simulate_backfill=not args.no_simulate_backfill, limit=args.limit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.strict and report["verdict"] == "polluted":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
