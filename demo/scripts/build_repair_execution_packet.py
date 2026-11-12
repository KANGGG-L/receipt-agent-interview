# -*- coding: utf-8 -*-
"""H06 历史修正执行审批包（只出包，不执行）。

硬门禁：授权引用、审批人、操作人、恢复证据、对象白名单、写窗口、计划快照 hash、
H04 演练对账（0 未解释差异）任一缺失/不一致 → `ready=false`，不下发可执行脚本。
本模块**不生成 apply 脚本**（`apply_script` 恒为 null）；真实执行属另行书面授权。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

PACKET_VERSION = "h06.v1"

REQUIRED_META = ("authorization_ref", "approved_by", "operator",
                 "restore_evidence", "write_window_start", "write_window_end")


def _missing_meta(meta):
    return [k for k in REQUIRED_META if not str(meta.get(k) or "").strip()]


def build_packet(plan, drill, meta):
    """返回审批包与门禁结果；不执行任何修正。"""
    missing = _missing_meta(meta)
    plan_objects = {(d["object_kind"], int(d["object_id"])) for d in plan["decisions"]}
    whitelist = set()
    for item in (meta.get("whitelist") or []):
        if isinstance(item, str) and ":" in item:
            kind, oid = item.split(":", 1)
            whitelist.add((kind.strip(), int(oid)))
    if not whitelist:
        missing.append("object_whitelist")
    elif whitelist != plan_objects:
        missing.append("object_whitelist_mismatch")
    if not drill.get("ok"):
        missing.append("drill_reconcile_not_ok")
    if plan.get("snapshot", {}).get("sha256") != drill.get("snapshot_sha256"):
        missing.append("snapshot_mismatch")
    start, end = meta.get("write_window_start"), meta.get("write_window_end")
    if start and end and str(start) >= str(end):
        missing.append("write_window_invalid")
    ready = not missing
    packet = {
        "schema": "h06-repair-execution-packet.v1",
        "packet_version": PACKET_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": ready,
        "blocking_reasons": sorted(set(missing)),
        "apply_script": None,  # 明确：本包不携带可执行修库脚本
        "authorization": {
            "authorization_ref": meta.get("authorization_ref") or "",
            "approved_by": meta.get("approved_by") or "",
            "operator": meta.get("operator") or "",
            "write_window": {"start": start or "", "end": end or ""},
        },
        "restore_evidence": meta.get("restore_evidence") or "",
        "snapshot_sha256": plan.get("snapshot", {}).get("sha256", ""),
        "drill": {"ok": bool(drill.get("ok")),
                  "unexplained": drill.get("unexplained"),
                  "snapshot_sha256": drill.get("snapshot_sha256", "")},
        "whitelist": [{"object_kind": k, "object_id": i} for k, i in sorted(whitelist)],
        "objects": [{"object_kind": d["object_kind"], "object_id": d["object_id"],
                     "action": d["action"], "reason": d["reason"],
                     "requires_owner": d["requires_owner"],
                     "status": d["status"]}
                    for d in plan["decisions"]],
        "stop_conditions": ["任何未解释差异 > 0", "快照 hash 漂移",
                            "对象超出白名单", "操作人/授权与包不一致"],
        "rollback": "按 H04 副本演练协议回滚演练账本；原审计与不可逆事实不覆盖",
    }
    return packet


def main(argv=None):
    ap = argparse.ArgumentParser(description="H06 修正执行审批包（只出包）")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--drill", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    with open(args.plan, "r", encoding="utf-8") as f:
        plan = json.load(f)
    with open(args.drill, "r", encoding="utf-8") as f:
        drill = json.load(f)
    with open(args.meta, "r", encoding="utf-8") as f:
        meta = json.load(f)
    packet = build_packet(plan, drill, meta)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2, sort_keys=True)
    return 0 if packet["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
