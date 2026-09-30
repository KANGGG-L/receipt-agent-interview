# -*- coding: utf-8 -*-
"""P2-T1：vendor_memory.supplier_id（主体锚点）幂等回填 —— 缺省预演，--apply 才写。

为什么单独成脚本、而不是放进 app/db.py 的迁移块：
- 迁移块加列是**结构变更**（幂等、无数据语义），回填是**数据变更**（改的是每条记忆
  的归属），必须显式授权才落笔。若塞进迁移，任何一次 `import app.db`（含测试自检、
  管理台启动、CLI）都会静默改写存量记忆归属 —— 这正是本项目「import 期迁移打到 live
  库」那次事故的形态。
- 读侧/写侧的名字→id 解析只有**一份实现**（`db.resolve_supplier_id`），本脚本复用它，
  不自己抄一遍归一逻辑（本仓高发的「同一根因多份实现只改一份」）。

口径：
- 只处理 `supplier_id IS NULL` 的行，**含 archived / superseded 行**（当前 live 的 9 行
  全部 archived；归档行将来会被 revive 恢复注入，没有锚点就恢复不出正确归属）。
- 匹配不到就**留空**，绝不新建供应商档案（拿不准就不锚定）。
- 按行的 tenant_id 在**同租户内**匹配，防跨租户错挂。
- 幂等：改写后该行脱离 `supplier_id IS NULL` 判据，二次执行零行更新。

用法（务必先把 DB_PATH 指向目标库；不带 DB_PATH 就是 live 库）：
  DB_PATH=/tmp/x.db python demo/scripts/backfill_vendor_memory_supplier.py           # 预演
  DB_PATH=/tmp/x.db python demo/scripts/backfill_vendor_memory_supplier.py --apply   # 落笔
  DB_PATH=/tmp/x.db python demo/scripts/backfill_vendor_memory_supplier.py --json    # 机器可读
"""

import argparse
import json
import os
import sqlite3
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证 supplier_id 列存在）


def plan():
    """扫描未锚定行并解析归属，返回 (matched, unmatched, stats)；**不写库**。

    matched 项 = {"memory_id", "vendor", "tenant_id", "status", "supplier_id"}
    unmatched 项 = 同上但无 supplier_id（名字解析不出档案）
    """
    path = db.DB_PATH
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vendor_memory)")}
        if "supplier_id" not in cols:
            raise RuntimeError(
                "vendor_memory 缺 supplier_id 列（迁移未生效）: %s" % path)
        rows = conn.execute(
            "SELECT memory_id, vendor, tenant_id, status FROM vendor_memory "
            "WHERE supplier_id IS NULL ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()

    matched, unmatched = [], []
    for mid, vendor, tid, status in rows:
        item = {
            "memory_id": mid,
            "vendor": vendor or "",
            "tenant_id": str(tid or "").strip() or "default",
            "status": status or "active",
        }
        sid = db.resolve_supplier_id(item["vendor"], item["tenant_id"])
        if sid is None:
            unmatched.append(item)
        else:
            item["supplier_id"] = int(sid)
            matched.append(item)
    stats = {
        "db_path": path,
        "pending_rows": len(rows),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "updated": 0,
    }
    return matched, unmatched, stats


def apply_backfill(matched):
    """把 plan 的 matched 项写入（只更新仍为 NULL 的行）；返回实际更新行数。

    WHERE 里再带一次 supplier_id IS NULL：即使 plan 与写入之间有并发写入（人工在
    管理台补了锚点/新增了同名行），也不会被本脚本覆盖成旧判定 —— 幂等的第二道防线。
    """
    if not matched:
        return 0
    conn = sqlite3.connect(db.DB_PATH)
    try:
        cur = conn.executemany(
            "UPDATE vendor_memory SET supplier_id = ? "
            "WHERE memory_id = ? AND supplier_id IS NULL",
            [(m["supplier_id"], m["memory_id"]) for m in matched],
        )
        conn.commit()
        return int(cur.rowcount if cur.rowcount is not None else 0)
    finally:
        conn.close()


def collect(write=False):
    """预演（write=False）/ 落笔（write=True）的统一入口，返回 stats。"""
    matched, unmatched, stats = plan()
    if write:
        stats["updated"] = apply_backfill(matched)
    stats["rows"] = matched + unmatched
    return stats


def _print_human(stats, write):
    mode = "落笔（--apply）" if write else "预演（不写库）"
    print("=== vendor_memory.supplier_id 回填 · %s ===" % mode)
    print("目标库            : %s" % stats["db_path"])
    print("待锚定行（NULL）  : %d" % stats["pending_rows"])
    print("匹配到档案        : %d" % stats["matched"])
    print("匹配不到（留空）  : %d" % stats["unmatched"])
    if write:
        print("本次更新行数      : %d" % stats["updated"])
    else:
        print("本次更新行数      : 0（预演，加 --apply 才写）")
    for item in stats["rows"]:
        sid = item.get("supplier_id")
        print("  %-8s %-28s tenant=%-8s -> supplier_id=%s" % (
            item["status"], item["vendor"], item["tenant_id"],
            sid if sid is not None else "（未匹配，保持 NULL）"))
    if stats["unmatched"]:
        print("提示：未匹配的行保持 NULL，读侧按 vendor 名回退匹配；"
              "如需锚定请先在管理台建立供应商档案，再重跑本脚本。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="vendor_memory.supplier_id 回填（缺省预演）")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只预演）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)
    stats = collect(write=a.apply)
    if a.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_human(stats, a.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
