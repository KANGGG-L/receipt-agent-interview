# -*- coding: utf-8 -*-
"""P3-T4（B6）失败可重跑入口：给定 receipt_id / 失败运行记录，重放一次记忆抽取。

为什么需要这个入口：`memory_extract_runs`（P8b·AA1）把每次抽取的运行记录落了库，
于是"哪张单据、哪条入口、因为什么没产出候选"从此可查；但**只有留痕不叫闭环** ——
抽取失败（上游 5xx / 超时 / 模型输出不可解析）之后，操作者原先只能靠"再保存一次
单据"来碰运气（复核保存只在有字段差异时才抽取，且会顺带改单据版本）。本脚本是那条
**显式的重跑入口**：按 `--receipt-id` 或 `--failed-only` 选中目标，重放抽取。

与生产抽取的关系（本模块最关键的取舍：**绝不复制管线**）：
重放直接调 `api_receipts._extract_and_enqueue_memory_facts` —— 那是 `save_edited` /
`approve` 两个生产入口共用的**唯一实现**（见该函数 docstring 的"实现只此一份"）。
于是候选怎么算（`compute_field_diffs`）、怎么抽（`extract_candidate_facts`）、怎么定
动作（`decide_action`）、怎么入队（`db.enqueue_pending_memory`）、内容怎么拼
（`_pending_fact_content`）、被拒怎么留痕（`_record_refused_candidate`）、运行记录怎么写
（`_persist_extract_run`）全部与生产**逐字相同**：本模块**没有第二份管线**。
（本仓高发缺陷①"同一根因多份实现只改一份"，正是这样被避开的；若哪天有人把这段管线
抄进本文件，重放与生产就会开始分叉。）

**幂等是免费的，且不自建第二套判据**：去重走 `db.enqueue_pending_memory` 的
`(vendor, tenant_id, content, status)` 判据（全仓唯一那份）。同一单据连续重放两次，
第二次的候选全部命中既有行（只并入触发来源、不新增行），不需要本脚本做任何判断。
本脚本对"去重挡下多少"的读数也只是**观察**：入队调用数 - 新落地的 pending 行数。

预演（缺省）与落笔（--apply）：
- 缺省**预演**：把 DB_PATH 的快照复制到临时目录，整个重放在**副本**上进行，源库全程
  只读。因此预演报告里的每一个数字（含"去重挡下多少"）都是**真跑出来的**，而不是
  估算 —— 换成"干跑一遍管线、不写库"就必须自己重写一遍去重判定，那正是禁止的第二套。
- `--apply`：在 DB_PATH 指向的库上真写（新增 pending 候选、被拒审计行、运行记录）。
  按本仓写入类脚本的一致纪律：**先备份再 --apply**。

模式（两者必选其一）：
  --receipt-id N    重放单据 N 的抽取（租户取 `--tenant-id`，缺省从单据行推导）
  --failed-only     扫最近 `error` 非空的运行记录，按单据去重后批量重放

与开关的关系（`memory_extract_enabled`，缺省关闭）：
该开关管的是**请求路径上的自动抽取**（复核保存/入账不该悄悄多一次外呼）。本工具是
运维**显式**发起的重跑，若本开关在则照常生效、关闭则不执行；但**默认已强制开启**
（`--honor-switch` 可改回"跟随开关"）：理由是本工具的运行前提就是"当初开过抽取并
失败过"，为一次显式修复先去改 app_settings（一次真实写库）比在进程内强制更危险。
强制与否都会在报告里显式打印，**不是静默绕过**。

用法（**必须先显式指定 DB_PATH 与 RAG_DIR**；不带 DB_PATH 就是 live 库）：
  # why 不直接指着 live 跑：本模块 import app.db 时会在导入期执行 db._make_engine()
  # 的迁移块，按 DB_PATH 对目标库做 ALTER（不会自动跳过）。故先指向副本/临时库。
  DB_PATH=/tmp/vm_replay.db RAG_DIR=/tmp/vm_replay_rag python demo/scripts/memory_extract_replay.py \\
      --failed-only --limit 20 --json          # 预演：只读源库
  DB_PATH=/tmp/vm_replay.db RAG_DIR=/tmp/vm_replay_rag python demo/scripts/memory_extract_replay.py \\
      --receipt-id 1494 --apply                # 落笔（先备份目标库）
"""

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证新表存在）
import app.api_receipts as api_receipts  # noqa: E402

# 运行记录里的入口分桶：重放独立成一个入口，复盘时能把"重放"与 save_edited/approve
# 分开看（trigger 只进运行记录、不参与业务判定，见 _extract_and_enqueue_memory_facts）。
TRIGGER_REPLAY = "replay"

# live 库绝对路径：--apply 直接打 live 是红线（本任务明令禁止），需显式 --allow-live。
_LIVE_DB = os.path.abspath(os.path.join(_DEMO_DIR, "receipt_demo.db"))

# --failed-only 缺省只扫最近多少条运行记录（避免一次把整库翻一遍）。
DEFAULT_LIMIT = 50


# -------------------------------------------------------------
# 库快照与"预演落在副本上"
# -------------------------------------------------------------
def _snapshot_db(src, dst):
    """把 src 的**一致快照**写到 dst（sqlite 在线备份 API）。

    why 不用 `shutil.copy2`：源库若处于 WAL 模式，主文件 + `-wal` 两份一起才是完整状态，
    只拷主文件会拿到过期快照 —— 预演数字会与实际不一致，且**没有任何信号**。备份 API
    由 sqlite 自己保证一致性，代价三行。
    """
    if not os.path.exists(src):
        raise SystemExit("源库不存在：%s（请检查 DB_PATH）" % src)
    src_con = sqlite3.connect(src)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


@contextlib.contextmanager
def _db_target(apply):
    """预演时把 db 指向源库副本；--apply 时才操作原库。

    换库手法与测试夹具一致：改 `db.DB_PATH` + `db._make_engine()`。**不用
    `importlib.reload`**：`api_receipts` 在 import 期就绑定了 `app.db` 这个模块对象，
    reload 会生成**新的模块对象**，生产函数仍走旧对象 → 写回源库（"改了一半"的静默
    失效）。改模块属性则所有 `db.xxx` 调用点同时生效。
    """
    src = db.DB_PATH
    if apply:
        yield src
        return
    tmpdir = tempfile.mkdtemp(prefix="mx_replay_")
    copy_path = os.path.join(tmpdir, "replay.db")
    _snapshot_db(src, copy_path)
    old_env = os.environ.get("DB_PATH")
    db.DB_PATH = copy_path
    os.environ["DB_PATH"] = copy_path
    db._make_engine()
    try:
        yield copy_path
    finally:
        db.DB_PATH = src
        if old_env is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_env
        db._make_engine()
        shutil.rmtree(tmpdir, ignore_errors=True)


# -------------------------------------------------------------
# 抽取开关：默认强制开启（显式运维动作），--honor-switch 才跟随
# -------------------------------------------------------------
@contextlib.contextmanager
def _extract_forced(force):
    """force=True 时在进程内临时把开关判据置为真，退出时逐字节还原。

    只覆盖 `_memory_extract_enabled` 这一个判定入口（覆盖的是"是否发起抽取"，不是任何
    数据安全闸门），且不落库、不改 app_settings。
    """
    if not force:
        yield
        return
    original = api_receipts._memory_extract_enabled
    api_receipts._memory_extract_enabled = lambda: True
    try:
        yield
    finally:
        api_receipts._memory_extract_enabled = original


# -------------------------------------------------------------
# 读侧快照（用于观察"新增/去重"，复用既有读 helper，不自己写 SQL）
# -------------------------------------------------------------
def _pending_rows(status):
    """按状态取全部待确认记忆行（`db.list_pending_memory` 是唯一读取实现）。"""
    return {int(r["id"]): r for r in db.list_pending_memory(
        status=status, tenant_id=None)}


def _new_rows(before, after):
    """after 里相对 before 新增的行（按 id）。"""
    return [r for i, r in after.items() if i not in before]


def _ref_receipt_ids(row):
    """pending 行里记录的触发单据 id 集合（JSON 列；坏值容错为空集）。"""
    try:
        refs = json.loads(row.get("source_receipt_ids_json") or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(refs, list):
        return set()
    return {str(x) for x in refs}


def _run_ids_for(receipt_id, tenant_id):
    """某单据现有运行记录的 run_id 集合（重放后取差集即本次新增的那一行）。"""
    rows = db.list_memory_extract_runs(tenant_id=tenant_id, receipt_id=receipt_id)
    return {int(r["run_id"]) for r in rows}


def _latest_new_run(receipt_id, tenant_id, before_ids):
    """本次重放新写入的运行记录（按 run_id 差集取，最新一条）。"""
    rows = db.list_memory_extract_runs(tenant_id=tenant_id, receipt_id=receipt_id)
    fresh = [r for r in rows if int(r["run_id"]) not in before_ids]
    return fresh[0] if fresh else None


# -------------------------------------------------------------
# 单张重放
# -------------------------------------------------------------
def _resolve_target(receipt_id, tenant_id):
    """把 (receipt_id, tenant_id) 解析成 (row, 生效租户)。

    租户口径：显式给了就按它校验（跨租户视为不存在，与 `db.get_receipt_row` 一致）；
    没给则先按"不过滤"取行、再从行上取 tenant_id —— 复盘时手头往往只有 receipt_id。
    """
    row = db.get_receipt_row(receipt_id, tenant_id=tenant_id)
    if row is None:
        return None, (tenant_id or "default")
    tid = str(tenant_id or getattr(row, "tenant_id", None) or "default")
    return row, tid


def replay_receipt(receipt_id, tenant_id, force_switch=True):
    """重放一张单据的抽取，返回该次重放的结果 dict（**是否落库由调用方的库归属决定**）。

    为什么本函数自己不做 apply 判断：落笔与预演的区别只在于"db 当前指向哪个库"
    （见 `_db_target`），函数本身只做一次真实重放 —— 这样预演与落笔走的是**同一条
    代码路径**，预演不会因为"另一个分支"而与落笔分叉。
    """
    row, tid = _resolve_target(receipt_id, tenant_id)
    vendor = str(getattr(row, "supplier_name", "") or "").strip() if row is not None else ""
    result = {
        "receipt_id": receipt_id, "tenant_id": tid, "vendor": vendor,
        "status": "", "run_id": None, "diffs_count": 0, "candidates": 0,
        "enqueued_pre_dedup": 0, "enqueued_new": 0, "deduped": 0,
        "rejected": 0, "refused": 0, "prompt_source": "", "error": "",
        "reject_reasons": [], "refused_reasons": [],
    }
    if row is None:
        result["status"] = "not_found"
        return result
    if not force_switch and not api_receipts._memory_extract_enabled():
        # 跟随开关且开关关闭：生产函数会直接返回且**不写运行记录**，这里先判明原因，
        # 免得报告里出现"没有运行记录"这种看不出根因的结论（本仓高发的静默失效）。
        result["status"] = "switch_off"
        return result

    before_runs = _run_ids_for(receipt_id, tid)
    before_pending = _pending_rows("pending")
    before_rejected = _pending_rows("rejected")
    stats = None
    try:
        with _extract_forced(force_switch):
            stats = api_receipts._extract_and_enqueue_memory_facts(
                receipt_id, row, tid,
                api_receipts._ai_prefill_items(row),
                db.get_receipt_items(receipt_id, tenant_id=tid),
                trigger=TRIGGER_REPLAY)
    except Exception as e:                       # 生产函数自己已兜底，这里防意外升级
        result["status"] = "exception"
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return result

    run = _latest_new_run(receipt_id, tid, before_runs)
    if stats is not None:
        result["candidates"] = int(stats.get("candidates") or 0)
        result["rejected"] = int(stats.get("rejected") or 0)
        result["enqueued_pre_dedup"] = int(stats.get("enqueued") or 0)
        result["diffs_count"] = int(stats.get("diff_count") or 0)
    if run is not None:
        result["run_id"] = run["run_id"]
        result["prompt_source"] = run["prompt_source"]
        result["error"] = run["error"]
        result["reject_reasons"] = list(run["reject_reasons"])
        result["diffs_count"] = run["diffs_count"]
        result["rejected"] = max(result["rejected"], int(run["facts_rejected"] or 0))
    result["status"] = _classify(stats, run, result["error"])

    # 去重读数：新落地的 pending 行（并入既有行的去重命中不会新增行）
    rid_key = str(receipt_id)
    new_pending = [r for r in _new_rows(before_pending, _pending_rows("pending"))
                   if rid_key in _ref_receipt_ids(r)]
    new_rejected = [r for r in _new_rows(before_rejected, _pending_rows("rejected"))
                    if rid_key in _ref_receipt_ids(r)]
    result["enqueued_new"] = len(new_pending)
    result["deduped"] = max(0, result["enqueued_pre_dedup"] - result["enqueued_new"])
    result["refused"] = len(new_rejected)
    result["refused_reasons"] = [str(r.get("reject_reason") or "") for r in new_rejected
                                 if str(r.get("reject_reason") or "").strip()]
    return result


def _classify(stats, run, error):
    """把"这次算成功还是失败"按运行记录的语义判定（不另立口径）。

    `run.error` 非空即失败（与 `memory_extract_runs` 的列语义一致：空串 = 本次抽取
    成功执行）。没写进任何运行记录时按"没有可采信的记录"处理，不假装成功。
    """
    if run is None:
        return "no_run_record"
    if str(error or "").strip():
        return "failed"
    if int(run.get("diffs_count") or 0) == 0 and stats is None:
        return "no_diff"
    return "replayed"


# -------------------------------------------------------------
# 批量重放 + 报告
# -------------------------------------------------------------
def _failed_targets(limit, tenant_id):
    """扫最近失败运行记录 → 按单据去重后的 [(receipt_id, tenant_id)]。

    "失败"的判据就是运行记录自己的语义：`error` 非空（空串 = 成功执行）。
    同一单据多条失败只重放一次（重放是"按单据重跑抽取"，与失败了几次无关）。
    """
    runs = db.list_memory_extract_runs(tenant_id=tenant_id, limit=limit)
    failed = [r for r in runs if str(r.get("error") or "").strip()]
    targets, seen, skipped = [], set(), 0
    for r in failed:
        rid = r.get("receipt_id")
        if rid in (None, ""):
            skipped += 1
            continue
        key = (int(rid), str(r.get("tenant_id") or "default"))
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)
    return targets, failed, skipped


def replay(targets, apply, limit=DEFAULT_LIMIT, tenant_id=None, force_switch=True):
    """批量重放 report。targets=None 表示 `--failed-only`（自行扫描失败记录）。"""
    report = {
        "mode": "failed_only" if targets is None else "receipt",
        "applied": bool(apply),
        "force_switch": bool(force_switch),
        "db_path": "", "switch_on_before": None,
        "scanned_failed_runs": 0, "skipped_runs_without_receipt": 0,
        "targets": 0, "replayed": 0, "success": 0, "failed": 0,
        "candidates_produced": 0, "enqueued_pre_dedup": 0, "enqueued_new": 0,
        "deduped": 0, "rejected": 0, "refused": 0,
        "reject_reason_distribution": {}, "skipped": {}, "results": [],
    }
    with _db_target(apply) as effective_db:
        report["db_path"] = effective_db
        report["switch_on_before"] = api_receipts._memory_extract_enabled()
        if targets is None:
            targets, failed, skipped = _failed_targets(limit, tenant_id)
            report["scanned_failed_runs"] = len(failed)
            report["skipped_runs_without_receipt"] = skipped
        report["targets"] = len(targets)
        for receipt_id, tid in targets:
            res = replay_receipt(receipt_id, tid, force_switch=force_switch)
            report["results"].append(res)
            if res["status"] == "replayed":
                report["replayed"] += 1
                report["success"] += 1
            elif res["status"] in ("failed", "exception"):
                report["replayed"] += 1
                report["failed"] += 1
            else:
                report["skipped"][res["status"]] = \
                    report["skipped"].get(res["status"], 0) + 1
            report["candidates_produced"] += res["candidates"]
            report["enqueued_pre_dedup"] += res["enqueued_pre_dedup"]
            report["enqueued_new"] += res["enqueued_new"]
            report["deduped"] += res["deduped"]
            report["rejected"] += res["rejected"]
            report["refused"] += res["refused"]
            for reason in res["reject_reasons"] + res["refused_reasons"]:
                key = _reason_bucket(reason)
                report["reject_reason_distribution"][key] = \
                    report["reject_reason_distribution"].get(key, 0) + 1
    return report


def _reason_bucket(reason):
    """丢弃理由归桶：取首句（理由文本各带上下文，原样列出会碎成几十个键）。"""
    text = str(reason or "").strip()
    if not text:
        return ""
    for sep in ("；", ";", "\n"):
        text = text.split(sep)[0]
    return text.strip()


def print_report(report, force_switch):
    print("模式            : %s" % ("failed-only（扫失败运行记录）"
                                   if report["mode"] == "failed_only" else "receipt-id"))
    print("库              : %s%s" % (report["db_path"],
                                   "" if report["applied"] else "（副本；源库只读）"))
    print("抽取开关        : 当前=%s；本次%s"
          % (report.get("switch_on_before"),
             "强制开启（显式运维重跑）" if force_switch else "跟随开关（--honor-switch）"))
    if report["mode"] == "failed_only":
        print("扫到失败运行    : %d（缺 receipt_id 跳过 %d）"
              % (report["scanned_failed_runs"], report["skipped_runs_without_receipt"]))
    print("目标单据        : %d（按单据去重）" % report["targets"])
    print("实际重放        : %d（成功 %d / 失败 %d）"
          % (report["replayed"], report["success"], report["failed"]))
    print("产出候选        : %d" % report["candidates_produced"])
    print("入队调用        : %d" % report["enqueued_pre_dedup"])
    print("新增待批        : %d" % report["enqueued_new"])
    print("去重挡下        : %d（同 vendor+内容+状态 已存在→并入触发来源，不新增行）"
          % report["deduped"])
    print("闸门拒绝        : %d（写入侧计数）" % report["rejected"])
    print("落选审计行      : %d（status=rejected，理由可查）" % report["refused"])
    if report["skipped"]:
        print("未重放          : %s" % ", ".join(
            "%s=%d" % kv for kv in sorted(report["skipped"].items())))
    dist = report["reject_reason_distribution"]
    print("丢弃原因分布    :")
    if not dist:
        print("  （无）")
    for reason, cnt in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  %d  %s" % (cnt, reason or "(空理由)"))
    print("-" * 60)
    for r in report["results"]:
        print("  #%s tenant=%s status=%s 候选=%d 新增=%d 去重=%d 拒绝=%d 磁盘差异=%d %s"
              % (r["receipt_id"], r["tenant_id"], r["status"], r["candidates"],
                 r["enqueued_new"], r["deduped"], r["rejected"], r["diffs_count"],
                 ("error=%s" % r["error"]) if r["error"] else ""))
    if not report["applied"]:
        print("（预演：源库零写入；加 --apply 才落笔，落笔前请先备份目标库）")
    else:
        print("（已落笔：新增 pending 候选 / 落选审计行 / 运行记录）")


# -------------------------------------------------------------
# CLI
# -------------------------------------------------------------
def _assert_apply_target(apply, allow_live):
    """--apply 打 live 库需显式 --allow-live（本任务红线：不得改 live 库）。

    why 设闸而不是只打警告：DB_PATH 缺省就指向 live，一个手滑的 `--apply` 会直接写生产
    库 —— 本仓已有多次「误跑写入 live」的前例，多一个 flag 的成本远低于一次事故。
    """
    if not apply:
        return
    if os.path.abspath(db.DB_PATH) == _LIVE_DB and not allow_live:
        raise SystemExit(
            "拒绝在 live 库上 --apply：%s\n"
            "请把 DB_PATH 指向副本/临时库（例如 DB_PATH=/tmp/vm_replay.db），"
            "或显式加 --allow-live（请先备份）。" % db.DB_PATH)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="记忆抽取失败重跑（缺省预演：在副本上真跑一遍，源库零写入）")
    ap.add_argument("--receipt-id", type=int, default=None,
                    help="重放某张单据的抽取")
    ap.add_argument("--failed-only", action="store_true",
                    help="扫最近 error 非空的运行记录，按单据去重后批量重放")
    ap.add_argument("--apply", action="store_true", help="真写；缺省只预演（不写源库）")
    ap.add_argument("--tenant-id", default=None, help="限定租户（缺省：全部/从单据推导）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="--failed-only 最多扫多少条最近运行记录（缺省 %d）" % DEFAULT_LIMIT)
    ap.add_argument("--honor-switch", action="store_true",
                    help="跟随 memory_extract_enabled 开关；缺省强制开启（显式重跑）")
    ap.add_argument("--allow-live", action="store_true",
                    help="允许 --apply 直接写 live 库（红线例外，需自行备份）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    if (args.receipt_id is None) == (not args.failed_only):
        raise SystemExit("必须且只能指定 --receipt-id 或 --failed-only 之一")
    _assert_apply_target(args.apply, args.allow_live)

    targets = None
    if args.receipt_id is not None:
        targets = [(args.receipt_id, args.tenant_id)]
    report = replay(targets, apply=args.apply, limit=max(0, args.limit),
                    tenant_id=args.tenant_id, force_switch=not args.honor_switch)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, force_switch=not args.honor_switch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
