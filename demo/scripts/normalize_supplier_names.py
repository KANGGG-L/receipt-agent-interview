# -*- coding: utf-8 -*-
"""P9：供应商命名归一工具（缺省预演；`--apply` 才写库）。

背景（why 需要它）
------------------
live 的 `suppliers.name` 里混进了两类脏名：
1. **流程/状态后缀** —— `祥興凍肉(店员已核对)`（76 张单据）/ `金百加咖啡(已入账)`（18 张）/
   `九龍豆品廠(直通审批)`；
2. **括号内纯数字 id** —— `7-ELEVEN HK (1542)`。
这些后缀来自不同入口（人工核对标记、入账流程标记、登记时的临时序号），**不是供应商
身份的一部分**。留着会让同一个供应商在「按名字解析」（`db.resolve_supplier_id`）下走到
不同路径，并污染下拉列表与按供应商的统计。

处置纪律（三条，逐条对应任务要求）
--------------------------------
1. **先量化再动刀**：缺省预演，逐条给出「当前单据数 / 已生效记忆数 / 空闲档案数 /
   规范名提议 / 是否与既有档案冲突」，`--apply` 才落笔。
2. **重名冲突不得直接重命名**：剥掉后缀得到候选规范名后，若该名**已存在**同名档案
   （或与既有档案的归一 key 相同），直接改名会变成两条档案。此时报「应走合并」并给出
   合并建议（把脏名档案的单据与记忆改挂到既有干净档案）—— 本脚本**不自动合并**
   （改挂单据与记忆是架构级数据变更，须人工授权后再执行）。
3. **`--apply` 必须三表同步**：`suppliers.name` / `receipts.supplier_name` /
   `vendor_memory.vendor` 一起改。只改档案名会让 name-based 解析在旧名上断掉，
   落成「档案叫 A、单据叫 B」的错位。

名字归一：复用既有实现，不写第二套
---------------------------------
- 「归一 key」一律走 `app.services.supplier_normalizer.canonical_supplier_key`
  （与 `db.find_supplier_by_canonical_name` / `db.resolve_supplier_id` 同一份实现），
  本脚本不另写一份。
- 本脚本**新增**的只有「剥掉状态/数字后缀」这一步，理由见 `_strip_status_suffix`
  的 docstring：既有实现里**没有供应商侧的同类函数**，而 SKU 侧那个
  （`receipt_utils._normalize_sku_name`）是私有函数且会额外剥掉开头的数量/规格前缀
  —— 实测会把 `7-ELEVEN HK (1542)` 的前导数字当成数量剥成 `-ELEVEN HK (1542)`，
  供应商侧不能复用它。

向量提示（只提示，不自动重建）
----------------------------
`rag.ingest_memory` 写入的样本向量，其 `page_content` 里**含供应商名**
（`f"供应商 {vendor} 的已确认进货明细：..."`），metadata 也带 `vendor`。故改名之后
那批向量里的文本仍是旧名。本脚本调用既有巡检
`app.services.memory_consistency.check_memory_consistency` 报出「受影响的生效记忆里
有几条已有向量 / 几条缺向量」，并给出重建前的检查命令，**不代做重建**。

幂等
----
三条 UPDATE 都带「当前值 != 新值」与「当前名 == 旧名」的双重守卫，改写后即脱离判据；
连续跑两次 `--apply`，第二次改动 0 行。

用法（**必须先显式指定 DB_PATH 与 RAG_DIR**；不带就是 live 库/生产向量目录）
  # why 不直接指着 live 跑：本模块 import app.db 时会在导入期执行 db._make_engine()
  # 的迁移块，按 DB_PATH 对目标库做 ALTER。故先在副本上预演。
  DB_PATH=/tmp/x.db RAG_DIR=/tmp/x_rag python demo/scripts/normalize_supplier_names.py
  DB_PATH=/tmp/x.db RAG_DIR=/tmp/x_rag python demo/scripts/normalize_supplier_names.py --apply
  DB_PATH=/tmp/x.db RAG_DIR=/tmp/x_rag python demo/scripts/normalize_supplier_names.py --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证表存在）
from app.services.supplier_normalizer import canonical_supplier_key  # noqa: E402

# 状态/流程后缀词表：命中即视为「不是供应商身份的一部分」。
# why 是词表而不是从数据里推导：哪些后缀属流程产物是**策略判断**（如「(店员已核对)」
#   是人工核对标记、「(1542)」是登记序号），数据本身推不出来。词表越小越安全 ——
#   只有明确的后缀词才命中，像「(新都城)」「（將軍澳）」这类地点注不会被误判。
# 大小写不敏感（比对前统一 lower()）。
_STATUS_TOKENS = (
    "店员已核对", "店员修改", "ai自动入库", "待核对", "已核对", "已入账",
    "已解析", "已修改", "手工新建", "直通审批", "审核测试", "门禁警告", "parsed",
)

# 尾部括号组（半角/全角都认）；要求 base 非空（整名就是一个括号组时不算脏名）。
_TRAILING_PAREN_RE = re.compile(r"^(?P<base>.+?)\s*[（(](?P<inner>[^（）()]*)[）)]\s*$")


def _classify_paren(inner):
    """判定括号内容属哪一类脏后缀；都不是则返回 None（如地点注）。"""
    text = str(inner or "").strip()
    if not text:
        return None
    if text.isdigit():
        return "paren_id"
    low = text.lower()
    for token in _STATUS_TOKENS:
        if token in low:
            return "status_suffix"
    return None


def _strip_status_suffix(name):
    """剥掉尾部的「状态/流程后缀」与「括号纯数字 id」，返回 (base, stripped) 或 None。

    为什么自建而不复用既有实现（任务要求论证）：
    - 供应商侧的既有归一只有 `canonical_supplier_key`，它产出的是**分组 key**而不是
      展示名，规则上**刻意保留**括号内的中文注（只剔除含 ASCII 字母的英文注）
      —— 用它当规范名会原样留下 `(店员已核对)`，达不到本任务目的；
    - SKU 侧确有「剥尾部括号」的 `receipt_utils._normalize_sku_name`，但它是私有函数
      且额外剥掉**开头的数量/规格前缀**（`^\\d+...`），实测把 `7-ELEVEN HK (1542)`
      前导的 `7` 当数量剥掉 → `-ELEVEN HK (1542)`，供应商名不能走这条。
    故这里必须有一份供应商侧的剥离实现；「名字 → 档案 id / 归一 key」仍全部复用既有
    实现（见模块 docstring），不存在第二套归一。

    连续剥多层（`XXX(已核对) (1542)` → `XXX`）；括号内容不属于任何一类脏后缀时**停止**
    剥离（`七月烤魚 (新都城)` 不会被误剥）。
    """
    base = str(name or "").strip()
    stripped = []
    while True:
        m = _TRAILING_PAREN_RE.match(base)
        if not m:
            break
        inner = m.group("inner").strip()
        kind = _classify_paren(inner)
        if kind is None:
            break
        stripped.append({"kind": kind, "inner": inner})
        base = m.group("base").strip()
    if not stripped or not base:
        return None
    return base, stripped


def _embedded_status_token(name):
    """无括号但整名里嵌了状态/流程词（如 `直通审批 parsed 供应商`）→ 返回命中的词。

    这类名字**不提议规范名**（剥掉流程词后往往仍不是干净商号，如 `直通审批测试店`
    剥完只剩 `测试店`），故只报「需人工复核」，`--apply` 对它零改动。
    """
    low = str(name or "").lower()
    for token in _STATUS_TOKENS:
        if token in low:
            return token
    return None


def _counts(conn, supplier_id, name):
    """某一档案/名字的关联规模（单据、生效记忆）与本次将改动的行数。"""
    def one(sql, params):
        return int(conn.execute(sql, params).fetchone()[0])

    receipts_anchored = one("SELECT count(*) FROM receipts WHERE supplier_id = ?",
                            (supplier_id,))
    receipts_by_name_only = one(
        "SELECT count(*) FROM receipts WHERE supplier_id IS NULL AND supplier_name = ?",
        (name,))
    mem_active = one(
        "SELECT count(*) FROM vendor_memory WHERE status = 'active'"
        " AND (supplier_id = ? OR vendor = ?)", (supplier_id, name))
    mem_total = one(
        "SELECT count(*) FROM vendor_memory WHERE supplier_id = ? OR vendor = ?",
        (supplier_id, name))
    return {
        "receipts_anchored": receipts_anchored,
        "receipts_by_name_only": receipts_by_name_only,
        "receipts_total": receipts_anchored + receipts_by_name_only,
        "mem_active": mem_active,
        "mem_total": mem_total,
    }


def _change_scope(conn, supplier_id, name, proposal):
    """`--apply` 实际会改动的行数（三表分别计），供预演时如实展示爆炸半径。

    suppliers 1 行 + receipts 若干 + vendor_memory 若干；后两者按
    「supplier_id 锚到该档案」或「名字等于脏名」两条口径取并集（见 `apply_plan`）。
    why 必须单列「其中原名与脏名不同」的行数：live 上 `supplier_id=7` 锚着
    `祥興咖啡室` 等 60 行别的写法，按锚点回写会把它们一起改成规范名 —— 这是本项目
    「档案名是该供应商所有单据的展示名」的既定模型，但读报告的人必须看得见这个面。
    """
    def one(sql, params):
        return int(conn.execute(sql, params).fetchone()[0])

    receipts_change = one(
        "SELECT count(*) FROM receipts WHERE (supplier_id = ? OR supplier_name = ?)"
        " AND supplier_name <> ?", (supplier_id, name, proposal))
    receipts_change_by_other_name = one(
        "SELECT count(*) FROM receipts WHERE (supplier_id = ? OR supplier_name = ?)"
        " AND supplier_name <> ? AND supplier_name <> ?",
        (supplier_id, name, proposal, name))
    mem_change = one(
        "SELECT count(*) FROM vendor_memory WHERE (supplier_id = ? OR vendor = ?)"
        " AND vendor <> ?", (supplier_id, name, proposal))
    mem_change_by_other_name = one(
        "SELECT count(*) FROM vendor_memory WHERE (supplier_id = ? OR vendor = ?)"
        " AND vendor <> ? AND vendor <> ?", (supplier_id, name, proposal, name))
    return {
        "receipts_to_change": receipts_change,
        "receipts_to_change_by_other_name": receipts_change_by_other_name,
        "mem_to_change": mem_change,
        "mem_to_change_by_other_name": mem_change_by_other_name,
    }


def _affected_active_memory_ids(conn, supplier_id, name):
    """受改名影响的**生效**记忆 memory_id（按锚点或按名取并集，与写入口径一致）。"""
    rows = conn.execute(
        "SELECT memory_id FROM vendor_memory WHERE status = 'active'"
        " AND (supplier_id = ? OR vendor = ?)", (supplier_id, name)).fetchall()
    return sorted({str(r[0]) for r in rows if str(r[0] or "").strip()})


def _vector_report(affected_active_mids):
    """既有一致性巡检的只读调用：报出受影响生效记忆中有多少条已有向量。

    why 复用 `memory_consistency` 而不是自己读 Chroma：它的持久化表读取口径
    （sqlite3 `mode=ro`、目录不存在绝不创建）已经为「live 向量库必须逐字节不变」这条
    纪律设计过；自己再写一份既是①类缺陷（同一根因多份实现），也容易把 live 向量库
    "打开"出来产生写入。
    异常**不吞掉**：巡检失败时把错误原样回报（本项目③类缺陷就是异常被静默吞掉）。
    """
    from app.services import memory_consistency
    out = {
        "rebuild_hint": ("记忆文本（含供应商名）已变更，向量中的样本文本仍是旧名，"
                         "如受影响记忆已有向量，建议重建；本脚本不自动重建。"),
        "check_command": ("python -m app.services.memory_consistency --json"
                          "（RAG_DIR 指向生产向量目录）"),
        "affected_active_memories": len(affected_active_mids),
        "with_vector": 0,
        "missing_vector": None,
        "store_present": None,
        "error": None,
        "note": None,
    }
    try:
        report = memory_consistency.check_memory_consistency(db_path=db.DB_PATH)
    except Exception as e:                      # 巡检故障必须可见，不得静默
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    out["store_present"] = bool(report.get("store_present"))
    out["error"] = report.get("store_error")
    if not out["store_present"]:
        # 向量库不存在 → 「有没有向量」无从判断。**不得折成 0**：巡检把「库不在」
        # 判成「全缺向量」时口径仍是「缺」，读报告的人会误以为发现了不一致。
        out["note"] = ("向量库不存在（或缺 chroma.sqlite3），无法判断受影响记忆是否已有"
                       "向量；请把 RAG_DIR 指向生产向量目录后重跑检查命令。")
        return out
    have = set(report.get("intersection") or [])
    missing = set(report.get("missing_vector_mids") or [])
    out["with_vector"] = len([m for m in affected_active_mids if m in have])
    out["missing_vector"] = len([m for m in affected_active_mids if m in missing])
    return out


def scan():
    """扫描 suppliers 表产出脏名清单与动作建议；**不写库**。

    返回 stats dict；脏名逐条在 stats["items"]。每条含：
      supplier_id / name / tenant_id / active / kind / stripped / proposal /
      action（rename|merge|review）/ collision / merge_into / 关联规模 / 改动面
    """
    path = db.DB_PATH
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(suppliers)")}
        if not cols:
            raise RuntimeError("suppliers 表不存在: %s" % path)
        sups = conn.execute(
            "SELECT id, name, tenant_id, active FROM suppliers ORDER BY id").fetchall()
        rows = [{"supplier_id": int(sid), "name": str(name or ""),
                 "tenant_id": str(tid or "default"), "active": int(active or 0)}
                for sid, name, tid, active in sups]
        # 归一 key 一次算好（O(n^2) 比较用），复用唯一归一实现
        for r in rows:
            r["canonical"] = canonical_supplier_key(r["name"])

        items = []
        for r in rows:
            stripped = _strip_status_suffix(r["name"])
            if stripped is not None:
                base, parts = stripped
                kind = parts[0]["kind"]
                item = dict(r)
                item.update({
                    "kind": kind,
                    "stripped": parts,
                    "action": "rename",
                    "proposal": base,
                    "collision": None,
                    "merge_into": None,
                })
                # ②冲突：候选规范名已存在同名档案（物理 UNIQUE）或与既有档案归一 key 相同
                base_key = canonical_supplier_key(base)
                exact = [o for o in rows
                         if o["supplier_id"] != r["supplier_id"] and o["name"] == base]
                canon = [o for o in rows
                         if o["supplier_id"] != r["supplier_id"]
                         and base_key and o["canonical"] == base_key]
                if exact or canon:
                    keep = sorted(exact or canon, key=lambda o: o["supplier_id"])[0]
                    item["action"] = "merge"
                    item["collision"] = {
                        "type": "exact_name" if exact else "canonical_key",
                        "supplier_id": keep["supplier_id"],
                        "name": keep["name"],
                        "same_canonical_key": bool(canon),
                    }
                    item["merge_into"] = {
                        "keep_supplier_id": keep["supplier_id"],
                        "keep_name": keep["name"],
                        "drop_supplier_id": r["supplier_id"],
                        "drop_name": r["name"],
                    }
                item.update(_counts(conn, r["supplier_id"], r["name"]))
                item.update(_change_scope(conn, r["supplier_id"], r["name"], base))
                items.append(item)
                continue
            token = _embedded_status_token(r["name"])
            if token:
                item = dict(r)
                item.update({
                    "kind": "embedded_status",
                    "stripped": [],
                    "action": "review",
                    "proposal": None,
                    "collision": None,
                    "merge_into": None,
                    "matched_token": token,
                })
                item.update(_counts(conn, r["supplier_id"], r["name"]))
                items.append(item)
    finally:
        conn.close()

    # 向量提示（只对会被改写的项计算）
    affected = []
    conn = sqlite3.connect(path)
    try:
        for it in items:
            if it["action"] != "rename":
                continue
            it["affected_active_memory_ids"] = _affected_active_memory_ids(
                conn, it["supplier_id"], it["name"])
            affected.extend(it["affected_active_memory_ids"])
    finally:
        conn.close()
    vector = _vector_report(sorted(set(affected)))

    stats = {
        "db_path": path,
        "suppliers_scanned": len(rows),
        "dirty_total": len(items),
        "rename": len([i for i in items if i["action"] == "rename"]),
        "merge": len([i for i in items if i["action"] == "merge"]),
        "review": len([i for i in items if i["action"] == "review"]),
        "items": items,
        "vector_hint": vector,
        "changed": 0,
    }
    return stats


def apply_plan(items):
    """对 action='rename' 的项执行三表同步（单事务，失败整体回滚）；返回改动统计。

    三条 UPDATE（顺序与守卫见下）：
      1. suppliers.name      <- WHERE id=? AND name=?（改名后即不再命中 → 幂等）
      2. receipts.supplier_name  <- WHERE (supplier_id=? OR supplier_name=?) AND <> 新名
      3. vendor_memory.vendor    <- WHERE (supplier_id=? OR vendor=?) AND <> 新名
    why 2/3 用「锚点 OR 旧名」并集而不是只按名字：
      - 只按名字：`receipts.supplier_name` 已经是别的写法、但锚到该档案的行会漏改，
        档案名与单据名继续错位；
      - 只按锚点：**未锚定**却写着旧名的行（supplier_id IS NULL）会漏改 —— 这些行
        name-based 解析是唯一路径，正是任务里「解析会因此断掉」的受害者。
    why action='merge' 不在此执行：候选规范名已被占用，改挂单据与记忆属架构级变更，
    须人工授权；这里只报建议。
    """
    changed = {"suppliers": 0, "receipts": 0, "vendor_memory": 0}
    for it in items:
        if it["action"] != "rename":
            continue
        sid = int(it["supplier_id"])
        old = it["name"]
        new = it["proposal"]
        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE suppliers SET name = ? WHERE id = ? AND name = ?", (new, sid, old))
            changed["suppliers"] += int(cur.rowcount or 0)
            cur = conn.execute(
                "UPDATE receipts SET supplier_name = ? WHERE (supplier_id = ? OR"
                " supplier_name = ?) AND supplier_name <> ?", (new, sid, old, new))
            changed["receipts"] += int(cur.rowcount or 0)
            cur = conn.execute(
                "UPDATE vendor_memory SET vendor = ? WHERE (supplier_id = ? OR"
                " vendor = ?) AND vendor <> ?", (new, sid, old, new))
            changed["vendor_memory"] += int(cur.rowcount or 0)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return changed


def collect(write=False):
    """预演（write=False）/ 落笔（write=True）的统一入口。"""
    stats = scan()
    if write:
        changed = apply_plan(stats["items"])
        stats["changed"] = sum(changed.values())
        stats["changed_by_table"] = changed
    return stats


def _print_human(stats, write):
    mode = "落笔（--apply）" if write else "预演（不写库）"
    print("=== 供应商命名归一 · %s ===" % mode)
    print("目标库                : %s" % stats["db_path"])
    print("扫描供应商档案        : %d" % stats["suppliers_scanned"])
    print("脏名合计              : %d（可重命名 %d / 需合并 %d / 需人工复核 %d）"
          % (stats["dirty_total"], stats["rename"], stats["merge"], stats["review"]))
    if not stats["items"]:
        print("未发现脏名（无需处置）。")
        return
    print("")
    for it in stats["items"]:
        head = "[%d] %s（tenant=%s%s）" % (
            it["supplier_id"], it["name"], it["tenant_id"],
            "，已停用" if not it["active"] else "")
        print(head)
        print("    类型          : %s%s" % (
            it["kind"],
            ("（" + " / ".join(p["inner"] for p in it["stripped"]) + "）")
            if it["stripped"] else "（命中词：%s）" % it.get("matched_token", "")))
        if it["action"] == "review":
            print("    动作          : 需人工复核 —— 状态词嵌在名中且无括号，"
                  "剥掉后仍非干净商号，本脚本不改动")
        else:
            print("    单据数        : %d（按锚点 %d + 仅按旧名未锚定 %d）"
                  % (it["receipts_total"], it["receipts_anchored"],
                     it["receipts_by_name_only"]))
            print("    生效记忆数    : %d（含归档共 %d）"
                  % (it["mem_active"], it["mem_total"]))
            if it["action"] == "merge":
                c = it["collision"]
                print("    规范名提议    : %s —— **冲突，不得直接改名**"
                      % it["proposal"])
                print("    冲突          : 已存在档案 [%d] %s（判据=%s）"
                      % (c["supplier_id"], c["name"],
                         "同名" if c["type"] == "exact_name" else "归一 key 相同"))
                print("    合并建议      : 把 [%d] %s 的 %d 张单据与 %d 条生效记忆改挂到"
                      " [%d] %s，然后停用 [%d]（本脚本不自动执行）"
                      % (it["supplier_id"], it["name"], it["receipts_total"],
                         it["mem_active"], c["supplier_id"], c["name"],
                         it["supplier_id"]))
            else:
                print("    规范名提议    : %s -> 重命名" % it["proposal"])
                print("    改动面        : suppliers 1 行 / receipts %d 行"
                      "（其中原名与脏名不同 %d 行）/ vendor_memory %d 行"
                      "（其中原名与脏名不同 %d 行）"
                      % (it["receipts_to_change"],
                         it["receipts_to_change_by_other_name"],
                         it["mem_to_change"], it["mem_to_change_by_other_name"]))
    print("")
    v = stats["vector_hint"]
    print("向量提示              : %s" % v["rebuild_hint"])
    print("                        检查方式: %s" % v["check_command"])
    print("                        受影响生效记忆 %d 条 → 已有向量 %d 条 / 缺向量 %s"
          % (v["affected_active_memories"], v["with_vector"],
             "未知（向量库不存在）" if v["missing_vector"] is None
             else "%d 条" % v["missing_vector"]))
    if v["note"]:
        print("                        注: %s" % v["note"])
    if v["error"]:
        print("                        巡检异常: %s" % v["error"])
    if write:
        print("")
        print("已改动                : %d 行%s"
              % (stats["changed"],
                 ("（%s）" % json.dumps(stats.get("changed_by_table", {}),
                                       ensure_ascii=False)
                  if stats["changed"] else "")))
        if stats["merge"]:
            print("未执行合并            : %d 项（需人工授权，见上方合并建议）"
                  % stats["merge"])
        if stats["review"]:
            print("未执行人工复核项      : %d 项" % stats["review"])
    else:
        print("")
        print("（预演：未写库；加 --apply 才落笔）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="供应商命名归一（缺省预演，不写库）")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只预演）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    stats = collect(write=args.apply)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_human(stats, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
