# -*- coding: utf-8 -*-
"""P2-T2：供应商×SKU 别名（vendor_sku_alias）回填 + 收益回放（避免重复建档 / 匹配率）。

为什么需要这个脚本：
- 别名表的**在线写入钩子**（approve 入账登记）只对"此后新入账的单据"生效；存量
  单据里那些已经人工确认过的写法（`receipt_items.matched=1`）是一批现成的强信号，
  直接回填比让系统慢慢重学一遍划算。
- 回填是**数据变更**，故与 `backfill_vendor_memory_supplier.py` 同款纪律：缺省只
  预演（只读、不写），`--apply` 才落笔；写入侧复用唯一实现
  `db.upsert_vendor_sku_alias`（不在脚本里再抄一份幂等/唯一键逻辑）。

三个模式：
  （缺省）   预演：扫描匹配成功的历史明细，打印将登记/跳过统计，**不写库**
  --apply    把预演结果写进 DB_PATH 指向的库（幂等：重跑零新增）
  --replay   度量别名的贡献。**在 DB_PATH 的临时副本上跑，绝不写源库**：
             先清空副本别名表跑基线，再回填后跑处理组。另外单独跑一次"空别名表 +
             传供应商维度"，用于证明提升来自**别名数据**而不是新形参本身。报告里同时
             给出"别名命中的行里有多少与全局口径给出的 SKU 不同"—— 只看命中率会把
             **纠正错配**（precision）这项真实收益算成 0。
             `--holdout` 时只从"前半段单据"回填、只在"后半段单据"上统计 —— 排除
             "自己已匹配过的行自己当然匹配得上"的循环。

**为什么 --replay 的主指标不是匹配率（验收口径修正）**：
匹配率的提升恒等于 0，这不是"接线坏了"而是**同义反复** —— 回填的别名只从
`receipt_items.matched=1` 的明细登记，而 `matched=1` 的定义就是"已经被全局口径
匹配上了"；于是别名编码的是**本来就匹配得上的映射**，对匹配率零贡献（holdout
实测 +0.00pp，且"空别名表 + 传供应商维度"的自校准组与基线完全相等）。
它真正改变的量是**同一供应商同一写法的重复出现：只建档一次**。故 --replay 增加
「避免重复建档数」口径（定义见 `_SAVINGS_DEF`），并同时给出**反事实对照**
（在独立副本上关掉/打开别名通道复放同一批重复组，各自实际新建了多少个 SKU）——
上界与实测一起给，避免读成"少建了 N 个"。
该口径自带**口径自校准**（`duplicate_savings` 的 `group_key_selfcheck`）：同一批明细
再按放宽键 (租户, 写法) 数一遍，两份数字必须不同，否则说明供应商维度没生效。

**口径命名（R9，与 memory_metrics.py 联动）**：`--replay` 报告里那个复放了匹配逻辑的
命中率，键名是 `simulated_match_rate`；`memory_metrics.py` 只数 `receipt_items.matched=1`
这个落库标记的那个，键名是 `matched_flag_rate`。两个数都常被叫作「SKU 匹配率」但口径
不同（实测 36.10% vs 23.73%），两侧的 `note` 互相指向对方并声明不可混读。

用法（**必须先显式指定 DB_PATH 与 RAG_DIR**；不带 DB_PATH 就是 live 库）：
  # why 不直接指着 live 跑：本模块 import app.db 时会在导入期执行 db._make_engine()
  # 的迁移块，按 DB_PATH 对目标库做 ALTER（不会自动跳过）。所以先在副本上跑，
  # 需要落库的 --apply 也先备份再执行。
  DB_PATH=/tmp/vm_alias.db RAG_DIR=/tmp/vm_alias_rag python demo/scripts/backfill_vendor_sku_alias.py --json
  DB_PATH=/tmp/vm_alias.db RAG_DIR=/tmp/vm_alias_rag python demo/scripts/backfill_vendor_sku_alias.py --apply
  DB_PATH=/tmp/vm_alias.db RAG_DIR=/tmp/vm_alias_rag python demo/scripts/backfill_vendor_sku_alias.py --replay --holdout
"""

import argparse
import importlib
import json
import logging
import os
import sqlite3
import sys
import tempfile

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证别名表存在）

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import residue_rules  # noqa: E402  纯函数模块（零 DB 依赖），残渣判别式只此一份

logger = logging.getLogger(__name__)

_SOURCE_DB_PATH = db.DB_PATH
_SELECT_MATCHED = (
    "SELECT i.sku_id, i.name, i.unit, r.id, r.tenant_id, r.supplier_id, r.supplier_name "
    "FROM receipt_items i JOIN receipts r ON r.id = i.receipt_id "
    "WHERE COALESCE(i.matched, 0) = 1 AND i.sku_id IS NOT NULL ORDER BY r.id"
)

# -------------------------------------------------------------
# 口径命名（R9：同一指标名两个口径）
# -------------------------------------------------------------
# 本脚本 --replay 的「基线(全局口径)」是**复放了匹配逻辑**（_match_sku，含核心词归一）
# 之后的命中占比，键名 SIMULATED_MATCH_RATE_KEY；另一侧 memory_metrics.py 的
# SKU_MATCH_FLAG_KEY 只数 receipt_items.matched=1 这个**落库标记**。两个数此前都叫
# 「SKU 匹配率」却口径不同（实测 36.10% vs 23.73%），汇报时会被当成互相矛盾。
# 命名单独成常量，既让 note 的交叉引用可自证、也让
# tests/test_metric_wording_no_confusion.py 能按名断言。
SIMULATED_MATCH_RATE_KEY = "simulated_match_rate"
MATCHED_FLAG_RATE_KEY = "matched_flag_rate"
# --exclude-residue 新增口径键（R9：带口径名 + note，与上面两个键并列，不可混读）
SIMULATED_MATCH_RATE_EXCLUDED_KEY = "simulated_match_rate_excluded"

_EXCLUDE_RESIDUE_NOTE = (
    "本指标（%s）是分母剔除确定性残渣规则 7 类 %d 行（判别式唯一出处 residue_rules.py）后"
    "的 %s 同源口径：同样的 _match_sku 全局回放、同样的分子规则，仅残渣行不参与回放与统计。"
    "与不带后缀的 %s 不可混读（分母定义不同）；与 memory_metrics.py 的 matched_flag_rate_excluded"
    " 亦不可混读（分子口径不同：落库标记 vs 真跑匹配）。'0T' 型 OCR 废码另属单独类别，"
    "由 gen_sku_alias_candidates.py 的候选口径处理。评分口径（run_eval.py）不动——GT 侧残渣"
    "由 validate_gt_consistency.py 的复核清单承接，复标后走基线重冻结流程。"
)

# 交叉引用自述（对侧键名由常量插值保证，防「改了命名忘了改文案」）
_SIMULATED_MATCH_NOTE = (
    "本指标（%s）是**复放了匹配逻辑**（_match_sku，含核心词归一）之后的命中占比"
    "（报告里的「基线(全局口径)」）；它**不等于** receipt_items.matched=1 这个落库标记的"
    "占比 —— 后者见 memory_metrics.py 的 %s。两者都常被叫作「SKU 匹配率」但口径不同"
    "（差异来自分子口径：本指标真跑匹配，后者只认标记），不可混读、不可相互比较。"
    % (SIMULATED_MATCH_RATE_KEY, MATCHED_FLAG_RATE_KEY)
)


def _resolve_cached(name, tenant_id, cache):
    """`(租户, 名字) → supplier_id` 的带缓存解析 —— 本模块**唯一一份**缓存读写逻辑。

    why 抽成单点（D2）：`plan()` 与 `_load_items()` 此前各抄了一份等价实现（字典名还不
    一样），属于本仓高发的「同一根因多份实现只改一份」——改一处漏一处不会有任何报错。
    现在两处都调本函数，`tests/test_resolve_cache_single_impl.py` 用「两处调用点取到的
    是同一个函数对象」这一硬判据钉住（比「两边行为恰好一致」可靠）。

    why `cache` 由调用方传入、而不是模块级单例：本进程内 `db.DB_PATH` 会被改指向**不同的
    库副本**（`replay()` / `_simulate_creations()` 都会 `importlib.reload(db)` 切到临时副本）。
    模块级缓存会把「上一个库的 supplier_id」留给下一个库，同名不同库时直接给出错值。
    故缓存的生命周期必须与「一次操作」对齐：调用方建一个 dict，同一次操作内的多个调用点
    （`_load_items` 与 `plan`）**共用同一个** dict，操作结束即随作用域释放。

    why 异常降级为 None 而不是中断：历史数据里供应商名解析不出属正常（买方名、手写噪声、
    未建档商号），回填是离线批处理，一条失败不该让整批停下。但**降级必须留痕**（D10）：
    此前是裸 `except Exception: cache[key] = None`，解析长期失败时读数看着正常却查不到原因。
    注意 `db.resolve_supplier_id` 自身内部已有 catch + warning（`db.py:3043`），本函数的
    except 只有在该异常**逃出** db 层时才会命中（实现被替换、db 层 catch 被改动等），
    两者是不同失败面，故这里仍单独留痕，不视为重复日志。

    两处调用点的语义**刻意相同**（租户空值一律归一为 "default"、名字一律 `str(... or "")`
    建键、异常一律降级 None），故不需要用额外形参区分；如果将来某一侧确实要不同语义，
    必须在此处加显式形参并在 docstring 写明为何不同，不得靠两份实现隐式区分。
    """
    tid = str(tenant_id or "default")
    key = (tid, str(name or ""))
    if key in cache:
        return cache[key]
    try:
        cache[key] = db.resolve_supplier_id(name, tenant_id=tid)
    except Exception as e:                      # 降级可接受，静默不行
        logger.warning(
            "[alias-backfill] resolve_supplier_id 失败，降级为 None（不中断回填）："
            "name=%r tenant_id=%r %s: %s", name, tid, type(e).__name__, e)
        cache[key] = None
    return cache[key]


def plan(path=None, cache=None):
    """扫描"已匹配成功"的历史明细，产出待登记别名；**不写库**。

    返回 (candidates, stats)：
    - candidates: [{tenant_id, supplier_id, sku_id, raw_name, canonical_name, unit,
                    source_ref, receipt_id}]
    - stats:      各环节计数（含"供应商档案未锚定"的跳过数）
    同一 (tenant, supplier, 写法) 命中多个 SKU 时取**频次最高**者，同频次取**较新
    单据** —— 与更新四语义里的冲突优先级（人工修正 > 近期证据 > 频次）同向。

    `cache`：可选的 `(租户, 名字) → supplier_id` 缓存 dict，由调用方持有；`replay()`
    会与 `_load_items()` 共用同一个，避免同一次操作内对同一批名字解析两遍（见
    `_resolve_cached` 的 docstring：为何缓存不能做成模块级单例）。
    """
    from app.services.receipt_utils import _normalize_sku_name

    conn = sqlite3.connect(path or _SOURCE_DB_PATH)
    try:
        rows = conn.execute(_SELECT_MATCHED).fetchall()
        live_skus = {int(r[0]) for r in conn.execute("SELECT id FROM skus")}
    finally:
        conn.close()

    cache = cache if cache is not None else {}
    votes = {}
    stats = {"scanned": 0, "no_supplier": 0, "dead_sku": 0, "blank_name": 0,
             "multi_sku_forms": 0}
    for sku_id, name, unit, rid, tenant_id, supplier_id, supplier_name in rows:
        stats["scanned"] += 1
        raw = str(name or "").strip()
        if not raw:
            stats["blank_name"] += 1
            continue
        tenant_id = str(tenant_id or "default")
        if supplier_id is None:
            # 全模块唯一的带缓存解析（D2：不再内联第二份实现）
            supplier_id = _resolve_cached(supplier_name, tenant_id, cache)
        if not supplier_id:
            stats["no_supplier"] += 1
            continue
        if sku_id is None or int(sku_id) not in live_skus:
            stats["dead_sku"] += 1
            continue
        bucket = votes.setdefault((tenant_id, int(supplier_id), raw), {})
        slot = bucket.setdefault(int(sku_id), [0, 0, ""])
        slot[0] += 1
        slot[1] = max(slot[1], int(rid))
        if not slot[2] and str(unit or "").strip():
            slot[2] = str(unit).strip()      # 单位只作记录，取最近一次非空值

    candidates = []
    for (tenant_id, supplier_id, raw), bucket in votes.items():
        if len(bucket) > 1:
            stats["multi_sku_forms"] += 1
        sku_id, slot = sorted(bucket.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[0]
        candidates.append({
            "tenant_id": tenant_id, "supplier_id": supplier_id, "sku_id": sku_id,
            "raw_name": raw, "canonical_name": _normalize_sku_name(raw),
            "unit": slot[2], "source_ref": "backfill:matched",
            "receipt_id": slot[1],
        })
    stats["candidates"] = len(candidates)
    return candidates, stats


def apply_plan(candidates):
    """把候选别名写进当前 db.DB_PATH（幂等，复用唯一写入实现）。返回 (ok, failed)。"""
    ok = failed = 0
    for c in candidates:
        _alias_id, err = db.upsert_vendor_sku_alias(
            supplier_id=c["supplier_id"], sku_id=c["sku_id"],
            raw_name=c["raw_name"], canonical_name=c["canonical_name"],
            unit=c["unit"], tenant_id=c["tenant_id"], source_ref=c["source_ref"])
        if err:
            failed += 1
        else:
            ok += 1
    return ok, failed


# -------------------------------------------------------------
# 回放：度量别名对 SKU 匹配率的贡献
# -------------------------------------------------------------
def _load_items(path, cache=None):
    """回放样本：全部明细行 + 其单据的供应商上下文（只读）。

    `matched` 一并取出：新增的「避免重复建档数」口径只统计**未匹配**明细
    （已匹配的行入账时不会走建档分支，构不成"重复建档"）。

    `cache`：可选，与 `plan()` 共用的 `(租户, 名字) → supplier_id` 缓存
    （`replay()` 里两处传同一个 dict；见 `_resolve_cached`）。
    """
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT i.name, i.unit, i.unit_price, r.id, r.tenant_id, r.supplier_id,"
            " r.supplier_name, i.matched FROM receipt_items i"
            " JOIN receipts r ON r.id = i.receipt_id"
        ).fetchall()
    finally:
        conn.close()
    resolve_cache = cache if cache is not None else {}

    return [{
        "name": name, "unit": unit, "unit_price": unit_price,
        "receipt_id": int(rid), "tenant_id": str(tenant_id or "default"),
        # 行上已有 supplier_id 时不再解析；否则走全模块唯一的带缓存解析 helper
        "supplier_id": (int(supplier_id) if supplier_id
                        else _resolve_cached(supplier_name, tenant_id, resolve_cache)),
        "matched": int(matched or 0),
        # 原始供应商名：--exclude-residue 的 receipt 级残渣规则（默認來源測試/临时单）
        # 要按它判定；仅随行携带，不参与匹配与既有统计逻辑
        "supplier_name": supplier_name or "",
    } for name, unit, unit_price, rid, tenant_id, supplier_id, supplier_name, matched in rows]


def _run_match(items, use_supplier, collect=None):
    """回放并统计命中数。use_supplier=True 时走 P2-T2 新增的供应商维度。

    collect（可选 dict）额外收集别名通道的行为度量：
    - alias_fires：别名表命中次数
    - alias_same_as_global / alias_differs_from_global：别名给出的 SKU 与全局口径
      是否一致（**不同**＝别名把一行的 SKU 纠正了，这是别名表在"匹配率"之外的真实
      贡献；只看命中率会把这项算作 0 收益）
    """
    from app.services.receipt_utils import _match_sku, _normalize_sku_name
    matched = 0
    for it in items:
        name = it["name"]
        alias_sku = None
        if collect is not None and use_supplier and it["supplier_id"]:
            alias_sku = db.find_vendor_sku_alias(
                name, tenant_id=it["tenant_id"], supplier_id=it["supplier_id"],
                canonical_name=_normalize_sku_name(name))
        item = {
            "name": name, "unit": it["unit"], "unit_price": it["unit_price"],
            "sku_id": None, "matched": 0, "price_anomaly": 0,
            "price_anomaly_direction": "", "price_diff_percent": 0.0,
        }
        _match_sku(item, tenant_id=it["tenant_id"],
                   supplier_id=it["supplier_id"] if use_supplier else None)
        if item["matched"]:
            matched += 1
        if alias_sku is not None:
            collect["alias_fires"] += 1
            # 与**不带供应商维度**的口径对照（不是与本次结果对照 —— 本次结果必然等于
            # 别名给出的 SKU，那样比出来恒相等，度量等于没有）
            global_item = {
                "name": name, "unit": it["unit"], "unit_price": it["unit_price"],
                "sku_id": None, "matched": 0, "price_anomaly": 0,
                "price_anomaly_direction": "", "price_diff_percent": 0.0,
            }
            _match_sku(global_item, tenant_id=it["tenant_id"], supplier_id=None)
            if global_item["sku_id"] == alias_sku.id:
                collect["alias_same_as_global"] += 1
            else:
                collect["alias_differs_from_global"] += 1
    return matched


def _clear_aliases():
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute("DELETE FROM vendor_sku_alias")
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------
# P2-T2 验收口径修正：避免重复建档数（匹配率恒等的原因见模块 docstring）
# -------------------------------------------------------------
_SAVINGS_DEF = (
    "避免重复建档数：按 (tenant_id, supplier_id, 明细写法 name) 分组，取出现 >1 次的组，"
    "按单据顺序模拟「首次出现→登记别名→后续出现命中同一 SKU」，"
    "每个非首次出现计 1（即本会重复走一次建档分支的次数）。"
    "这是**机制上界**（假定非首次出现都会落到建档分支）；实测见 counterfactual —— "
    "同写法的重复在既有「精确 + 核心词归一」通道下本就会被去重，故建档数增量常为 0，"
    "该项机制真正改变的量是 counterfactual 的 name_conflicts（既匹配不上、又因同名"
    "建不了档的「无 SKU」行数）。"
)


def _dup_groups(items, group_by_supplier=True):
    """把明细按 (租户, 供应商, 写法) 分组，返回 (锚定组, 未锚定组, 跳过统计)。

    - 只统计 `matched=0` 的明细：已匹配的行入账时命中既有 SKU，不走建档分支，
      构不成"重复建档"。
    - 未锚定（`supplier_id` 解析不出）的明细**单列**：别名表按 supplier_id 建键，
      未锚定项根本无法登记别名（机制不覆盖这一部分），混进主口径会把机制的
      收益面算大。故主口径只报锚定组，未锚定组另报，差值可核对。
    - 组内按单据 id 升序（模拟"首次出现"用时间序；同一单据内的顺序不影响结论）。
    - `group_by_supplier=False`：把分组键**放宽**为 (租户, 写法)，**仅供口径自校准**
      （见 `duplicate_savings` 的 `group_key_selfcheck`）—— 明细集合不变、只换键，
      两种键下组数/出现次数若完全相等，说明主口径并未真正按供应商维度分组。
    """
    anchored, unanchored = {}, {}
    skipped = {"matched_items": 0, "unresolved_supplier_items": 0, "blank_name_items": 0}
    for it in items:
        raw = str(it["name"] or "").strip()
        if not raw:
            skipped["blank_name_items"] += 1
            continue
        if int(it.get("matched") or 0) != 0:
            skipped["matched_items"] += 1
            continue
        if it.get("supplier_id") is None:
            skipped["unresolved_supplier_items"] += 1
            unanchored.setdefault((it["tenant_id"], raw), []).append(it)
            continue
        anchored.setdefault(
            (it["tenant_id"], int(it["supplier_id"]), raw) if group_by_supplier
            else (it["tenant_id"], raw), []).append(it)
    for bucket in (anchored, unanchored):
        for key in [k for k, v in bucket.items() if len(v) < 2]:
            del bucket[key]
        for key in bucket:
            bucket[key] = sorted(bucket[key], key=lambda x: x["receipt_id"])
    return anchored, unanchored, skipped


def _group_stats(groups):
    """组数 / 组内出现次数 / 避免重复建档数(sum n-1) / 跨单据与同单据内的拆分。"""
    occurrences = sum(len(v) for v in groups.values())
    cross = sum(1 for v in groups.values() if len({x["receipt_id"] for x in v}) > 1)
    return {
        "groups": len(groups),
        "occurrences": occurrences,
        "avoided_duplicate_creations": occurrences - len(groups),
        "cross_receipt_groups": cross,
        "within_receipt_only_groups": len(groups) - cross,
    }


def duplicate_savings(items):
    """「避免重复建档数」主口径（定义见 `_SAVINGS_DEF`）。只读，不写库。

    主口径只报**已锚定供应商**的组（别名表按 supplier_id 建键，未锚定项登记不了
    别名）；未锚定的组与各项跳过计数另报，便于核对与解释组数差异。

    why 同时给出 `group_key_selfcheck`：这组数字只有在**真的按供应商分组**时才成立。
    分组键一旦退化成按名字分组（键写错、字典建键漏掉 supplier_id 都会），跨供应商的
    同名写法会被并成一组，"别的供应商首次出现的写法"也被算成避免重复建档 —— 实测在
    跨供应商同名样例上避免数由 1 虚高到 2 —— 且**没有任何信号**。故这里把同一批明细
    再按放宽键 (租户, 写法) 数一遍：两份数字不同即证明确实按供应商分组；相同则要么
    本批数据没有跨供应商同名写法（该维度此刻无从证伪），要么口径已退化。与 `--replay`
    里「空别名表 + 供应商维度」自校准组是同一手法。
    """
    anchored, unanchored, skipped = _dup_groups(items)
    stats = _group_stats(anchored)
    by_name = _group_stats(_dup_groups(items, group_by_supplier=False)[0])
    return {
        "definition": _SAVINGS_DEF,
        "groups_anchored": stats["groups"],
        "occurrences_anchored": stats["occurrences"],
        "avoided_duplicate_creations": stats["avoided_duplicate_creations"],
        "cross_receipt_groups": stats["cross_receipt_groups"],
        "within_receipt_only_groups": stats["within_receipt_only_groups"],
        "unanchored": _group_stats(unanchored),
        "skipped": skipped,
        "group_key_selfcheck": {
            "groups_by_name_only": by_name["groups"],
            "occurrences_by_name_only": by_name["occurrences"],
            "avoided_by_name_only": by_name["avoided_duplicate_creations"],
            "differs_from_supplier_key": (by_name["groups"] != stats["groups"]
                                          or by_name["occurrences"] != stats["occurrences"]),
            "note": ("放宽为 (租户, 写法) 后的两份数字必须不同（只要本批数据里有跨供应商的"
                     "同名写法）。相同 = 供应商维度没起作用（口径退化）或本批无从证伪，"
                     "此时不得把 avoided_duplicate_creations 当作已校验过的读数。"),
        },
    }


def _snapshot_sqlite(src, dst):
    """把 src 的已提交内容写成 dst。不写源库。

    shutil.copy2 在 WAL 下只拷主文件，副本缺行，回放读数变成 0。
    backup API 读到已提交快照。
    """
    src_con = sqlite3.connect(src)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def _simulate_creations(groups, use_alias):
    """反事实对照：在**独立副本**上按入账路径复放这些重复组，数实际新建几个 SKU。

    路径与 `services/inventory.apply_receipt_to_inventory` 一致：
    `_match_sku`（未命中）→ `create_sku(canonical_sku_name(name))`；`use_alias=True`
    时再补上 P2-T2 的写入钩子（登记别名），后续出现即命中别名通道。

    why 每次都要重开副本并 reload：上一次模拟新建的 SKU 会让下一次的"未命中"
    变成命中，两次读数就不在同一个世界里 —— 实测踩到过（C1 被 C0 的产物清零，
    边际少建被读成 14 而非 0）。
    why 单列 name_conflicts：`create_sku` 同名时返回 SKU_NAME_CONFLICT（不建档），
    这时该行落入"没有 SKU"的另一类缺陷，若混进 created 会把读数解释错，
    若默默丢掉又是本仓高发的"异常被静默吞掉"，故分开计数并回报。
    """
    from app.services.receipt_utils import _match_sku, _normalize_sku_name, canonical_sku_name

    tmpdir = tempfile.mkdtemp(prefix="sku_alias_cf_")
    path = os.path.join(tmpdir, "cf.db")
    _snapshot_sqlite(_SOURCE_DB_PATH, path)
    os.environ["DB_PATH"] = path
    importlib.reload(db)

    created = conflicts = 0
    for (tenant_id, supplier_id, raw), occ in sorted(
            groups.items(), key=lambda kv: kv[1][0]["receipt_id"]):
        for it in occ:
            probe = {
                "name": it["name"], "unit": it["unit"], "unit_price": it["unit_price"],
                "sku_id": None, "matched": 0, "price_anomaly": 0,
                "price_anomaly_direction": "", "price_diff_percent": 0.0,
            }
            _match_sku(probe, tenant_id=tenant_id,
                       supplier_id=supplier_id if use_alias else None)
            sku_id = probe["sku_id"]
            if not sku_id:
                sku_id, err = db.create_sku(canonical_sku_name(it["name"]),
                                            base_unit=it["unit"] or "",
                                            tenant_id=tenant_id)
                if sku_id:
                    created += 1
                elif err == "SKU_NAME_CONFLICT":
                    conflicts += 1
            if use_alias and sku_id:
                db.upsert_vendor_sku_alias(
                    supplier_id=supplier_id, sku_id=sku_id, raw_name=raw,
                    canonical_name=_normalize_sku_name(raw), unit=it["unit"] or "",
                    tenant_id=tenant_id, source_ref="cf:sim")
    return {"created": created, "name_conflicts": conflicts}


def _holdout_savings(full_items, eval_items, mid):
    """严格 holdout 版口径：只认「前半段单据登记过的写法」，在后半段上统计。

    why 不只看 `--replay` 的在线口径：在线口径里首次出现自己登记、后续自己命中，
    等于让评测集自己教自己。这里把"学到的写法"限制在 rid <= mid 的窗口，
    后半段里凡是该写法已在前半段出现过 → 这次不必再建档（计 1）；
    没出现过 → 属首次出现，正常建档，不计数（另报 first_seen）。
    """
    def _key(it):
        raw = str(it["name"] or "").strip()
        if not raw or int(it.get("matched") or 0) != 0 or it.get("supplier_id") is None:
            return None
        return (it["tenant_id"], int(it["supplier_id"]), raw)

    learn_keys = {k for k in (_key(it) for it in full_items
                              if it["receipt_id"] <= mid) if k}
    subject = {}          # 全样本里出现 >1 次的写法 = 本指标的对象集
    for it in full_items:
        k = _key(it)
        if k:
            subject[k] = subject.get(k, 0) + 1
    subject = {k for k, n in subject.items() if n > 1}

    avoided = first_seen = 0
    new_keys = set()
    for it in eval_items:
        k = _key(it)
        if k is None or k not in subject:
            continue
        if k in learn_keys:
            avoided += 1
        else:
            first_seen += 1
            new_keys.add(k)
    return {
        "definition": ("严格 holdout：只把 rid<=%d 的写法视为已登记，在后半段上统计"
                       "「该写法已登记所以本次不必建档」的次数" % mid),
        "learn_keys": len(learn_keys), "subject_keys": len(subject),
        "avoided_by_learned_keys": avoided, "first_seen_occurrences": first_seen,
        "first_seen_keys": len(new_keys),
    }


def replay(holdout=False, exclude_residue=False):
    """在 DB_PATH 的临时副本上度量三组口径，返回报告 dict（源库全程只读）。

    新增「避免重复建档数」主口径 + 反事实对照（放最后跑：反事实会在独立副本上
    执行 create_sku，不能污染前面的匹配率读数）。

    exclude_residue（默认关）：把残渣行（residue_rules.classify 命中）剔出回放样本——
    只剔除**统计分母**，不写库、不删行（操作对象本就是临时副本，源库全程只读）。
    关闭时样本集与报告与既有口径完全一致（逐字节回归门禁）。
    """
    tmpdir = tempfile.mkdtemp(prefix="sku_alias_replay_")
    copy_path = os.path.join(tmpdir, "replay.db")
    _snapshot_sqlite(_SOURCE_DB_PATH, copy_path)
    os.environ["DB_PATH"] = copy_path
    importlib.reload(db)

    # 两处调用点共用**同一个**缓存 dict（D2「不要两个字典」）：本函数内
    # `_load_items` 与 `plan` 解析的是同一份副本内容，共享缓存只在结果上省一次解析，
    # 不改变任何读数。
    resolve_cache = {}
    all_items = _load_items(copy_path, cache=resolve_cache)
    excluded_residue_rows = 0
    if exclude_residue:
        def _keep(it):
            is_res, _rule = residue_rules.classify(it["name"], it.get("supplier_name"))
            return not is_res

        excluded_residue_rows = len(all_items) - sum(1 for it in all_items if _keep(it))
        all_items = [it for it in all_items if _keep(it)]
    items = all_items
    candidates, stats = plan(_SOURCE_DB_PATH, cache=resolve_cache)
    holdout_mid = None
    if holdout:
        rid_sorted = sorted({it["receipt_id"] for it in all_items})
        mid = rid_sorted[len(rid_sorted) // 2]
        holdout_mid = mid
        candidates = [c for c in candidates if c["receipt_id"] <= mid]
        items = [it for it in all_items if it["receipt_id"] > mid]

    _clear_aliases()
    baseline = _run_match(items, use_supplier=False)      # 既有全局口径
    supplier_only = _run_match(items, use_supplier=True)  # 空别名表 + 供应商维度
    ok, failed = apply_plan(candidates)
    collect = {"alias_fires": 0, "alias_same_as_global": 0, "alias_differs_from_global": 0}
    treatment = _run_match(items, use_supplier=True, collect=collect)   # 回填别名后

    # 避免重复建档数（主口径）+ 反事实对照（必须在匹配率读数之后）
    savings = duplicate_savings(items)
    if holdout_mid is not None:
        savings["holdout"] = _holdout_savings(all_items, items, holdout_mid)
    g_anchored, _unanchored, _skipped = _dup_groups(items)
    cf_no = _simulate_creations(g_anchored, use_alias=False)
    cf_yes = _simulate_creations(g_anchored, use_alias=True)
    savings["counterfactual"] = {
        "creations_no_alias": cf_no["created"],
        "creations_with_alias": cf_yes["created"],
        "marginal_saved": cf_no["created"] - cf_yes["created"],
        "name_conflicts_no_alias": cf_no["name_conflicts"],
        "name_conflicts_with_alias": cf_yes["name_conflicts"],
        "note": ("在同批重复组上按入账路径复放（_match_sku → create_sku）。"
                 "marginal_saved 是「少建了几个重复 SKU」：同写法的重复在既有"
                 "「精确 + 核心词归一」通道下本就被去重，这里通常是 0 —— 即该口径的"
                 "收益并不在「少建 SKU」上。"
                 "真正被这项机制改变的是 name_conflicts：`_match_sku` 认不出、"
                 "`create_sku` 又因同名被拒的行（该行最终没有 SKU）；别名把首次建档时"
                 "算出来的那个 SKU 记下来，后续出现直接命中，于是这批行从「无 SKU」"
                 "变成「有正确 SKU」（对比前后两列即为其减少量）。"
                 "剩下的 conflicts 属「首次出现自己也没拿到 SKU」那一类，写入钩子只在"
                 "拿到 sku_id 时登记，故救不回来（需预置别名，或修归一实现的分叉）。"),
    }

    total = len(items)
    pct = (lambda n: round(100.0 * n / total, 2)) if total else (lambda n: 0.0)
    return {
        "db_copy": copy_path, "holdout": bool(holdout), "items": total,
        "baseline_matched": baseline, "baseline_rate": pct(baseline),
        "supplier_dim_only_matched": supplier_only, "supplier_dim_only_rate": pct(supplier_only),
        "treatment_matched": treatment, "treatment_rate": pct(treatment),
        "delta_pp": round(pct(treatment) - pct(baseline), 2),
        # 显式口径名（值取自既有 baseline 读数，不改任何计算逻辑）：与 memory_metrics.py 的
        # matched_flag_rate 是**两个口径**，两个 note 交叉引用并声明不可混读。
        SIMULATED_MATCH_RATE_KEY: {
            "numerator": baseline,
            "denominator": total,
            "rate_pct": pct(baseline),
            "incomparable_with": MATCHED_FLAG_RATE_KEY,
            "note": _SIMULATED_MATCH_NOTE,
        },
        **(
            {
                "excluded_residue_rows": excluded_residue_rows,
                SIMULATED_MATCH_RATE_EXCLUDED_KEY: {
                    "numerator": baseline,
                    "denominator": total,
                    "rate_pct": pct(baseline),
                    "incomparable_with": (MATCHED_FLAG_RATE_KEY, SIMULATED_MATCH_RATE_KEY),
                    "note": _EXCLUDE_RESIDUE_NOTE % (
                        SIMULATED_MATCH_RATE_EXCLUDED_KEY, excluded_residue_rows,
                        SIMULATED_MATCH_RATE_KEY, SIMULATED_MATCH_RATE_KEY),
                },
            }
            if exclude_residue else {}
        ),
        "alias_fires": collect["alias_fires"],
        "alias_same_as_global": collect["alias_same_as_global"],
        "alias_differs_from_global": collect["alias_differs_from_global"],
        "aliases_written": ok, "aliases_failed": failed, "plan_stats": stats,
        "duplicate_savings": savings,
    }


def main():
    ap = argparse.ArgumentParser(description="vendor_sku_alias 回填（缺省预演，不写库）")
    ap.add_argument("--apply", action="store_true", help="把预演结果写进 DB_PATH 库")
    ap.add_argument("--replay", action="store_true", help="在临时副本上回放 SKU 匹配率")
    ap.add_argument("--holdout", action="store_true", help="回放时用前半段训练/后半段评估")
    ap.add_argument("--exclude-residue", action="store_true",
                    help="replay 时把残渣行（residue_rules.py 判别式）剔出回放分母；"
                         "默认关，关闭时输出与既有口径逐字节一致")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    if args.replay:
        report = replay(holdout=args.holdout, exclude_residue=args.exclude_residue)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print("源库(只读)      : %s" % _SOURCE_DB_PATH)
        print("副本            : %s" % report["db_copy"])
        print("评估明细行      : %d%s" % (report["items"],
                                       "（holdout：仅后半段单据）" if report["holdout"] else ""))
        if report.get("excluded_residue_rows"):
            _ex = report[SIMULATED_MATCH_RATE_EXCLUDED_KEY]
            print("残渣剔除        : %d 行（判别式 residue_rules.py）；%s = %d/%d (%.2f%%)"
                  % (report["excluded_residue_rows"], SIMULATED_MATCH_RATE_EXCLUDED_KEY,
                     _ex["numerator"], _ex["denominator"], _ex["rate_pct"]))
        print("基线(全局口径)  : %d (%.2f%%)  [口径名 %s：分子 %d/分母 %d；"
              "非落库标记口径，后者见 memory_metrics.py 的 %s，不可混读]"
              % (report["baseline_matched"], report["baseline_rate"],
                 SIMULATED_MATCH_RATE_KEY,
                 report[SIMULATED_MATCH_RATE_KEY]["numerator"],
                 report[SIMULATED_MATCH_RATE_KEY]["denominator"], MATCHED_FLAG_RATE_KEY))
        print("空表+供应商维度 : %d (%.2f%%)" % (report["supplier_dim_only_matched"],
                                                 report["supplier_dim_only_rate"]))
        print("回填别名后      : %d (%.2f%%)" % (report["treatment_matched"],
                                                 report["treatment_rate"]))
        print("提升            : %+.2f pp" % report["delta_pp"])
        print("  注：该提升恒为 0，属**同义反复**而非接线故障 —— 回填的别名只从"
              " matched=1 的明细登记，而 matched=1 就等于「已被全局口径匹配上」，"
              "故别名编码的是本来就匹配得上的映射；自校准组（空别名表 + 供应商维度）"
              "与基线完全相等即为此证。该机制的量在下面的「避免重复建档数」。")
        print("别名通道命中    : %d 行（其中与全局口径不同 %d 行＝被纠正的错配）"
              % (report["alias_fires"], report["alias_differs_from_global"]))
        print("登记别名        : %d 条（失败 %d）" % (report["aliases_written"],
                                                    report["aliases_failed"]))
        s = report["duplicate_savings"]
        print("")
        print("【避免重复建档数】口径定义：%s" % s["definition"])
        print("  重复组(已锚定)  : %d 组 / 组内出现 %d 次（跨单据 %d 组、仅同单据内 %d 组）"
              % (s["groups_anchored"], s["occurrences_anchored"],
                 s["cross_receipt_groups"], s["within_receipt_only_groups"]))
        print("  避免重复建档数  : %d  ← 主口径（机制上界，见下）"
              % s["avoided_duplicate_creations"])
        sc = s["group_key_selfcheck"]
        print("  口径自校准      : 放宽为 (租户, 写法) → %d 组 / %d 次（按供应商 %d 组 / %d 次）"
              "＝%s" % (sc["groups_by_name_only"], sc["occurrences_by_name_only"],
                        s["groups_anchored"], s["occurrences_anchored"],
                        "有差异，供应商维度生效" if sc["differs_from_supplier_key"]
                        else "无差异（本批无跨供应商同名写法，或分组键已退化，读数待核）"))
        u = s["unanchored"]
        print("  未锚定供应商    : %d 组 / %d 次（机制不覆盖：别名按 supplier_id 建键，"
              "解析不出供应商就无法登记）" % (u["groups"], u["occurrences"]))
        print("  跳过            : 已匹配明细 %d / 供应商解析不出 %d / 写法为空 %d"
              % (s["skipped"]["matched_items"], s["skipped"]["unresolved_supplier_items"],
                 s["skipped"]["blank_name_items"]))
        cf = s["counterfactual"]
        print("  反事实对照      : 关别名建档 %d 个 SKU（同名被拒 %d）/ 开别名建档 %d 个"
              "（同名被拒 %d）→ 实测增量 %+d"
              % (cf["creations_no_alias"], cf["name_conflicts_no_alias"],
                 cf["creations_with_alias"], cf["name_conflicts_with_alias"],
                 cf["marginal_saved"]))
        print("                    （%s）" % cf["note"])
        if s.get("holdout"):
            h = s["holdout"]
            print("  holdout（%s）" % h["definition"])
            print("                    learn 写法 %d / 对象集 %d → 后半段少建档 %d 次、"
                  "首次出现 %d 次（新写法 %d 个）"
                  % (h["learn_keys"], h["subject_keys"], h["avoided_by_learned_keys"],
                     h["first_seen_occurrences"], h["first_seen_keys"]))
        return 0

    candidates, stats = plan()
    if args.apply:
        ok, failed = apply_plan(candidates)
        stats["applied"] = ok
        stats["apply_failed"] = failed
    if args.json:
        print(json.dumps({"stats": stats, "candidates": candidates},
                         ensure_ascii=False, indent=2))
        return 0
    print("DB_PATH         : %s" % db.DB_PATH)
    print("扫描已匹配明细  : %d" % stats["scanned"])
    print("待登记别名      : %d" % stats["candidates"])
    print("供应商未锚定    : %d（跳过，绝不凭空建档）" % stats["no_supplier"])
    print("指向死 SKU      : %d（跳过）" % stats["dead_sku"])
    print("同写法多 SKU    : %d（取频次最高 / 较新单据）" % stats["multi_sku_forms"])
    if args.apply:
        print("已写入          : %d（失败 %d）" % (stats["applied"], stats["apply_failed"]))
    else:
        print("（预演：未写库；加 --apply 才落笔）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
