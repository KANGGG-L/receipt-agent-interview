#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具①：SKU 别名批量审批登记工具（SPEC_PHASE2A §2）—— 人工闸是产品红线。

**入口定夺（PLAN 遗留决策）：只做 CLI，不做 API。** 理由：api_evalset 是评分域
（阶段 0/1 口径冻结面），新增 API 面会拖入 auth/uvicorn/前端验证链，而批量导入的
一次性交互 CLI 完全够用；且 A5 守卫（test_memory_alias_linkage_l0l1.py）断言生产
别名写入只允许 db.py / services/inventory.py / services/rag.py 三处，本工具因此
必须放在 demo/scripts/（grep 范围外），任何 API 路径都天然与之冲突。

**人工闸红线（最高优先级约束）**：
1. 无审批列（``approval_status`` / ``approved_by`` 任一缺失，含拼错列名）→ **整批拒绝**
   （exit 2，零写入、零审计）；
2. 任一 approve 行缺审批人 → **整批拒绝**（同上）；
3. 非空但非法的审批状态值（如 "approved"）→ **整批拒绝**（拼写错静默按 pending 跳过
   会让"人以为批了"的行无声消失，格式完整性问题必须显式失败）；
4. reject/pending/空 status → 该行零写入零审计，仅计数；
5. **不存在任何绕过审批的自动登记路径**：本工具只消费带人工审批列的 CSV，无 CSV/
   无审批列跑不通；除 ``vendor_sku_alias`` 与系统审计外零写表。候选生成器、评分器
   等其余工具一律零写库（SPEC_PHASE1 §4.4 三道闸延续）。

空 status 按 pending 处理的理由（与红线 3 的区别）：Excel 编辑常见整行留空/表尾
空行，空值语义上明确是「未批」，跳过绝不登记、不越过红线，且摘要逐行可见
（skipped_by_status 计数与行号）；非空非法值没有这种「明确未批」语义。

输入 CSV schema（阶段 1 候选 CSV + 审批列，向后兼容：gen_sku_alias_candidates.py
的产物不做任何修改即可读取；基线十列只消费 ``canonical_writing``，其余透传忽略）：

  审批列            必填                语义
  approval_status   列必存在            approve/reject/pending（大小写不敏感、strip 后
                                        比对）；空值按 pending；其他非空值=整批拒绝
  approved_by       approve 行必填      审批人（写入审计 who；不编造缺省人）
  approval_note     可选                备注；透传进审计 JSON
  supplier_id       approve 行必填      供应商作用域权威锚点（整数）。不按供应商名解析
                                        —— OCR 变体供应商名正是锚点坏掉的根源
  target_sku_id     与 target_sku_name  目标 SKU 权威 id（approve 行二选一）
                    二选一
  target_sku_name   同上                目标 SKU 名（唯一性门解析：0→SKU_NOT_FOUND、
                                        ≥2→SKU_AMBIGUOUS，歧义拒绝有痕，不猜 first()）
  register_raw_name 可选                缺省登记 canonical_writing；填了则登记该写法
  tenant_id         可选                缺省 default

粒度契约：一行 = 一次登记 = (tenant_id, supplier_id, register_raw_name‖canonical_writing)。
阶段 1 候选行聚合了多供应商/多写法；人审拆行是预期操作，工具不拆、不猜。

幂等与并存语义（§2.5）：
- 幂等：同一 CSV 重复 --apply，别名表内容（id/sku_id/raw/canonical/hit_count）不变
  （幂等键 (tenant, supplier, raw)，upsert 更新分支不碰 hit_count/source_ref）；
  审计每次追加（审计是日志不是状态，预期行为）。
- 与 L1 钩子并存：同 (tenant, supplier, raw) 已有 L1 登记行（source_ref=memory:<mid>）
  时，upsert 走更新分支 —— source_ref **不被覆盖**（memory 回溯链保留）；改指语义与
  「同写法再次人工批准 → 改指」一致。source_ref="alias_import" 只落**新建行**。
  两来源在审计日志可区分（memory_alias_registered vs alias_import_registered）。

用法（必须先显式指定 DB_PATH 与 RAG_DIR；live 防护见 guard_db_path）：
  # 缺省预演（dry-run，零写库含零审计）
  DB_PATH=/tmp/phase2a_spec.db RAG_DIR=/tmp/phase2a_spec_rag \\
      python demo/scripts/import_sku_alias_approvals.py --csv /tmp/approvals.csv
  #   --apply   实际登记（含系统审计）
  #   --json    机器可读摘要

退出码：0 正常完成（含有 skip 的批次）；2 整批拒绝（红线闸，零写入零审计）；
1 其他错误（CSV 不存在、解析失败等）。
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# live 防护必须先于任何 app.* import（import app.db 即对 DB_PATH 执行迁移块）；
# 复用候选生成器的单一实现（D2 纪律），不在本文件抄第二份。
from gen_sku_alias_candidates import guard_db_path  # noqa: E402

# 阶段 1 候选 CSV 基线十列（向后兼容判据）：只消费 canonical_writing，其余列存在与否
# 不影响读取（人工拆行/精简表头都允许），此处仅作文档锚点。
BASELINE_HEADERS = ["canonical_writing", "raw_writings", "row_count", "supplier_names",
                    "receipt_id_samples", "replay_match", "candidate_sku", "confidence",
                    "evidence", "suggested_action"]

APPROVAL_COLUMNS = ["approval_status", "approved_by"]
VALID_STATUSES = {"approve", "reject", "pending"}
SOURCE_REF = "alias_import"

# 逐行结局（确定性；§2.4）
OUTCOME_REGISTERED = "registered"
OUTCOME_REFRESHED = "refreshed"
OUTCOME_SKIPPED = "skipped"


# ------------------------------------------------------------------
# 输入解析与整批拒绝闸（G1-G4，任何一条触发 → exit 2，零写入零审计）
# ------------------------------------------------------------------
def _norm_status(value):
    s = str(value or "").strip().lower()
    return s


def parse_and_validate(csv_path):
    """读入 CSV 并跑 G1-G4 整批拒绝闸。

    返回 (rows, violations)：
    - rows: [dict]（DictReader 产物 + ``_row_no`` 数据行序号，1 起且含表头行计数）
    - violations: [{gate, row_no, detail}]；非空即整批拒绝。
    G1-G4 全部基于 CSV 内容本身，不触碰 DB —— 整批拒绝发生在任何 DB 写之前。
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [fn for fn in (reader.fieldnames or [])]
        rows = []
        for i, row in enumerate(reader, start=2):   # 行号按文件行计：表头是第 1 行
            row = dict(row)
            row["_row_no"] = i
            rows.append(row)

    violations = []
    # G1 缺审批列（含拼错列名）：红线「无审批列=拒绝整批」
    for col in APPROVAL_COLUMNS:
        if col not in fieldnames:
            violations.append({"gate": "G1", "row_no": None,
                               "detail": "表头缺审批列 %r（实有表头：%s）"
                                         % (col, ",".join(fieldnames) or "（空）")})
    if violations:
        return rows, violations

    for row in rows:
        no = row["_row_no"]
        status = _norm_status(row.get("approval_status"))
        if status and status not in VALID_STATUSES:
            # G3 非法状态值：拼写错（"approved"）若静默按 pending 跳过，人以为批了的
            # 行不会登记且无人知晓 —— 格式完整性问题必须显式失败
            violations.append({"gate": "G3", "row_no": no,
                               "detail": "非法 approval_status=%r（合法值：approve/reject/pending）"
                                         % row.get("approval_status")})
            continue
        if status != "approve":
            continue
        # G2 缺审批人（仅 approve 行要求；reject/pending 行不要求）
        if not str(row.get("approved_by") or "").strip():
            violations.append({"gate": "G2", "row_no": no,
                               "detail": "approval_status=approve 但 approved_by 为空"})
        # G4 身份缺失：批的是什么无法确定
        if not str(row.get("canonical_writing") or "").strip() \
                and not str(row.get("register_raw_name") or "").strip():
            violations.append({"gate": "G4", "row_no": no,
                               "detail": "approve 行 canonical_writing 与 register_raw_name 同空"})
    return rows, violations


# ------------------------------------------------------------------
# 逐行登记流水线（§2.4；skip 有痕：系统审计，模式沿 rag.py _audit_alias_skip）
# ------------------------------------------------------------------
def _existing_alias_row(db_path, tid, supplier_id, raw_name):
    """只读查询既有别名行（判定 registered/refreshed；dry-run 也用它，零写库）。

    why 不用 db.find_vendor_sku_alias：读取通道命中会累加 hit_count（写语义），
    dry-run 零写库红线不容忍。
    """
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        cur = conn.execute(
            "SELECT id, sku_id, canonical_name, hit_count, source_ref FROM vendor_sku_alias"
            " WHERE tenant_id = ? AND supplier_id = ? AND raw_name = ?",
            (tid, int(supplier_id), raw_name))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"id": row[0], "sku_id": row[1], "canonical_name": row[2],
            "hit_count": row[3], "source_ref": row[4]}


def _audit(db, who, action, payload):
    db.append_system_audit_log(who or "alias_import", action, "vendor_sku_alias", "",
                               json.dumps(payload, ensure_ascii=False))


def process_row(row, db, db_path, apply):
    """处理一行，返回 (outcome, detail)。

    outcome ∈ {registered, refreshed, skipped}；detail 为 skip reason 或登记摘要。
    前置门全部镜像 rag.py L1 钩子（门 4b 供应商作用域 / 门 5 SKU 唯一性），
    歧义拒绝有痕、不猜 first()。
    """
    no = row["_row_no"]
    tid = str(row.get("tenant_id") or "").strip() or "default"
    status = _norm_status(row.get("approval_status"))
    raw_name = str(row.get("register_raw_name") or "").strip() \
        or str(row.get("canonical_writing") or "").strip()
    approved_by = str(row.get("approved_by") or "").strip()
    csv_file = os.path.basename(str(row.get("_csv_file") or ""))
    note = str(row.get("approval_note") or "").strip()

    # 步骤 5：reject/pending/空 → skip（零写入零审计），按状态计数
    if status != "approve":
        return OUTCOME_SKIPPED, "STATUS_%s" % (status or "empty")

    def _skip(reason, candidates=0):
        # skip 有痕（模式沿 rag.py _audit_alias_skip）；但 dry-run 对库零写入**含零审计**
        # （§6 静默回归 1），留痕只发生在 --apply。
        if apply:
            _audit(db, approved_by or "alias_import", "alias_import_skip", {
                "tenant": tid,
                "supplier_id": _sup_id_or_none(row),
                "raw_name": raw_name,
                "sku_id": None,
                "csv_row_no": no,
                "csv_file": csv_file,
                "reason": reason,
                "candidates": candidates,
            })
        return OUTCOME_SKIPPED, reason

    # 步骤 2：raw 为空（G4 已整批拦截"两者同空"，此处防 register_raw_name 全空白等残形）
    if not raw_name:
        return _skip("INVALID_RAW_NAME")
    # 步骤 3：供应商作用域门（镜像 rag 门 4b）
    try:
        sid = int(str(row.get("supplier_id") or "").strip())
    except (TypeError, ValueError):
        return _skip("NO_SUPPLIER")
    if db.get_supplier(sid, tenant_id=tid) is None:
        return _skip("SUPPLIER_NOT_FOUND")

    # 步骤 4：目标 SKU 门（镜像 rag 门 5；list_skus 缺省 active=1 同租户）
    skus = db.list_skus(tenant_id=tid)
    id_s = str(row.get("target_sku_id") or "").strip()
    name_s = str(row.get("target_sku_name") or "").strip()
    sku = None
    if id_s:
        try:
            sid_target = int(id_s)
        except ValueError:
            return _skip("SKU_NOT_FOUND")
        matches = [s for s in skus if int(s.id) == sid_target]
        if not matches:
            return _skip("SKU_NOT_FOUND")
        sku = matches[0]
        if name_s and name_s != str(sku.name or "").strip():
            # 防人审点错行：id 与名字交叉校验不一致 → 拒绝该行
            return _skip("SKU_NAME_MISMATCH")
    elif name_s:
        matches = [s for s in skus if str(s.name or "").strip() == name_s]
        if not matches:
            return _skip("SKU_NOT_FOUND")
        if len(matches) > 1:
            # 歧义拒绝有痕，不猜 first()
            return _skip("SKU_AMBIGUOUS", candidates=len(matches))
        sku = matches[0]
    else:
        return _skip("TARGET_MISSING")

    existing = _existing_alias_row(db_path, tid, sid, raw_name)
    expected = OUTCOME_REFRESHED if existing is not None else OUTCOME_REGISTERED

    def _registered_payload(alias_id):
        payload = {
            "tenant": tid, "supplier_id": sid, "raw_name": raw_name,
            "sku_id": int(sku.id), "canonical_name": row.get("_canonical_name"),
            "csv_row_no": no, "csv_file": csv_file, "source_ref": SOURCE_REF,
            "alias_id": alias_id,
        }
        if note:
            payload["note"] = note
        return payload

    # 步骤 6：dry-run 只打印预期结局，不落库；--apply 才写
    if not apply:
        return expected, {
            "supplier_id": sid, "sku_id": int(sku.id), "raw_name": raw_name,
            "tenant_id": tid, "existing_source_ref":
                (existing or {}).get("source_ref", ""),
        }
    from app.services.receipt_utils import _normalize_sku_name
    canonical = _normalize_sku_name(raw_name)
    row["_canonical_name"] = canonical
    alias_id, err = db.upsert_vendor_sku_alias(
        supplier_id=sid, sku_id=int(sku.id), raw_name=raw_name,
        canonical_name=canonical, unit="", tenant_id=tid,
        source_ref=SOURCE_REF, protect_live_target=False)
    if err is not None:
        return _skip(err)
    _audit(db, approved_by, "alias_import_registered", _registered_payload(alias_id))
    return expected, {"supplier_id": sid, "sku_id": int(sku.id), "raw_name": raw_name,
                      "tenant_id": tid, "alias_id": alias_id,
                      "previous_source_ref": (existing or {}).get("source_ref", "")}


def _sup_id_or_none(row):
    try:
        return int(str(row.get("supplier_id") or "").strip())
    except (TypeError, ValueError):
        return None


def _row_line(row, outcome, detail):
    """逐行人读摘要：登记行打印将执行（或已执行）的 upsert 参数与结局。"""
    raw = str(row.get("register_raw_name") or "").strip() \
        or str(row.get("canonical_writing") or "").strip()
    if outcome == OUTCOME_SKIPPED:
        reason = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        return "skip(%s) raw=%r" % (reason, raw)
    d = detail if isinstance(detail, dict) else {}
    return "%s supplier_id=%s sku_id=%s raw=%r tenant_id=%s%s" % (
        outcome, d.get("supplier_id"), d.get("sku_id"), raw, d.get("tenant_id"),
        "" if outcome == OUTCOME_REGISTERED and not d.get("previous_source_ref")
        else " prev_source_ref=%r" % d.get("previous_source_ref", ""))


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="SKU 别名批量审批登记（缺省预演不写库；人工闸红线：无审批列/缺审批人/"
                    "非法状态值整批拒绝）")
    ap.add_argument("--csv", required=True, help="审批 CSV（阶段 1 候选 CSV + 审批列）")
    ap.add_argument("--apply", action="store_true", help="实际登记（缺省 dry-run 零写库）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 摘要")
    args = ap.parse_args()

    # live 防护先于任何 app.* import
    db_path = guard_db_path()
    os.environ["DB_PATH"] = db_path
    _demo_dir = os.path.dirname(_SCRIPTS_DIR)
    if _demo_dir not in sys.path:
        sys.path.insert(0, _demo_dir)
    import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证别名表存在）

    if not os.path.exists(args.csv):
        print("CSV 不存在：%s" % args.csv)
        return 1

    rows, violations = parse_and_validate(args.csv)
    for row in rows:
        row["_csv_file"] = args.csv

    # 整批拒绝闸（§2.3）：发生在任何 DB 写之前 → 拒绝 = 完全无痕于库
    if violations:
        for v in violations:
            print("整批拒绝 %s 行%s：%s" % (
                v["gate"], " %s" % v["row_no"] if v["row_no"] else "（表头）", v["detail"]))
        print("总计：%d 条违规（无审批列/缺审批人/非法状态值/身份缺失 → 零写入零审计）"
              % len(violations))
        return 2

    summary = {
        "csv_rows": len(rows),
        "approve": 0, "reject": 0, "pending": 0, "empty_status": 0,
        "registered": 0, "refreshed": 0,
        "skipped_by_status": 0, "skipped_by_status_rows": [],
        "skipped_by_reason": {},
        "skipped_by_reason_rows": {},
        "apply": bool(args.apply),
    }
    for row in rows:
        status = _norm_status(row.get("approval_status"))
        if status == "approve":
            summary["approve"] += 1
        elif status == "reject":
            summary["reject"] += 1
        elif status == "pending":
            summary["pending"] += 1
        else:
            summary["empty_status"] += 1
        outcome, detail = process_row(row, db, db_path, apply=args.apply)
        if not args.json:
            # 步骤 6：dry-run 逐行打印将执行的 upsert 参数与预期结局（人审复核面）
            print("行 %d [%s] %s" % (row["_row_no"],
                                     _norm_status(row.get("approval_status")) or "（空→pending）",
                                     _row_line(row, outcome, detail)))
        if outcome == OUTCOME_REGISTERED:
            summary["registered"] += 1
        elif outcome == OUTCOME_REFRESHED:
            summary["refreshed"] += 1
        elif outcome == OUTCOME_SKIPPED:
            reason = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
            if reason.startswith("STATUS_"):
                summary["skipped_by_status"] += 1
                summary["skipped_by_status_rows"].append(row["_row_no"])
            else:
                summary["skipped_by_reason"].setdefault(reason, 0)
                summary["skipped_by_reason"][reason] += 1
                summary["skipped_by_reason_rows"].setdefault(reason, [])
                summary["skipped_by_reason_rows"][reason].append(row["_row_no"])

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        mode = "apply" if args.apply else "dry-run（未写库；加 --apply 才落笔）"
        print("CSV 行数 %d | approve %d / reject %d / pending %d / 空 status %d"
              % (summary["csv_rows"], summary["approve"], summary["reject"],
                 summary["pending"], summary["empty_status"]))
        print("registered %d / refreshed %d / skipped_by_status %d / skipped_by_reason %s"
              % (summary["registered"], summary["refreshed"], summary["skipped_by_status"],
                 json.dumps(summary["skipped_by_reason"], ensure_ascii=False, sort_keys=True)))
        print("skipped_by_status 行号: %s" % (summary["skipped_by_status_rows"] or "无"))
        for reason in sorted(summary["skipped_by_reason_rows"]):
            print("skip %s 行号: %s" % (reason, summary["skipped_by_reason_rows"][reason]))
        print("模式：%s" % mode)
        print("注：重复 --apply 幂等（别名表内容不变、hit_count 保留）；审计每次追加（日志非状态）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
