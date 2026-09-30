# -*- coding: utf-8 -*-
"""记忆存储层编排（P1-T1 / R11）：状态变更时保证 DB 行与向量层一致。

为什么需要单独一层：一条记忆同时落在两张介质 —— `vendor_memory` 表（DB）与
Chroma 向量库（按 memory_id 作为 metadata）。SQL 事务管不到向量层，若只改其中
一侧就产生"层级脱钩"：DB 行没了、向量仍在（H2 实测的那批孤儿向量就是同一形态，
179 条向量无人消费且无人察觉），或反之留下检索不到却永驻的残留。

把「DB 动作 + 按 memory_id 级联向量」收在这一处的另一个理由：同一个动作现在有多个
入口（单条删除、按供应商批量删除、批量归档、单条恢复），后续维护脚本也会用；实现
只有一份，才不会出现本仓高发的"同一根因多份实现，只改了一份"。

覆盖的状态迁移（全部对称，见下面各函数的 docstring）：
- 删除（硬删）：`delete_memory` / `delete_memory_vectors`；
- 归档（active → 非 active）：`archive_memory_vectors`（R11，此前只有删除有级联）；
- 取代（active → 非 active）：同一个 `archive_memory_vectors`（R13，归档同族的第二个
  不对称：supersede 此前也只改 status、不动向量层）；它的名字来自 R11 首次落点，
  现在承担的是通用的「非 active 迁移级联」——不另立第二个函数名，避免两份实现并存。
- 恢复（非 active → active）：`restore_memory_vectors`（R11，委托 rag 的「确保向量存在」）。

调用方：app/api_memory.py 的两个 DELETE 端点（删除路径）、app/db.py 的两处状态迁移
（`apply_vendor_memory_override_penalty` 归档、`revive_vendor_memory` 恢复，R11 接线）、
以及 app/db.py 的 `save_memory_fact`（supersede，R13 接线）。
状态变更本身在 db 层完成，本层只负责"状态变了以后向量层该怎样"。
"""

import logging
import os
from typing import Optional

from app import db

logger = logging.getLogger(__name__)


class MemoryVectorDeleteError(RuntimeError):
    """FP-2c：删除路径的向量级联失败（on_error="raise" 时抛出）。

    为什么要有专用异常类型：端点要区分「向量删不动（本次删除未生效，可重试）」与
    其它 500，逐条失败信息都在消息里。DB 行尚未删除（删除路径已重排为向量先行），
    抛出时两侧介质仍处于一致态 —— 不做跨介质补偿（失败后反向补写向量属自愈而非
    补偿，超出最小修，见 SPEC FP-2c「明确不做」）。
    """


def delete_memory_vectors(tenant_id, memory_ids, on_error="degrade"):
    """按 memory_id 级联删除向量层分块，返回成功处理的 memory_id 条数。

    为什么用 rag._store(tenant_id)._collection.delete(where=...)：langchain 的
    Chroma 包装层没有"按 metadata 删"的公开方法，而底层 Collection 的 delete
    支持 metadata where（实测 chromadb 1.5.9 生效：3 条 → 1 条），且删除不存在的
    memory_id 是 no-op、不抛错。

    on_error（FP-2c）：失败时如何处置。
    - "degrade"（缺省）：逐条 warning 后 continue，不向上抛 —— 归档（R11）、取代
      （R13）与容量淘汰（W-4）三条路径的状态变更此刻已经提交，向量层故障再抛只会
      让调用方误以为"没成功"而重试，同时把一次可降级的失败升级成端点故障。
      缺省值就是 degrade：三个既有内部调用点零改动。
    - "raise"：任一条失败都收集后抛 MemoryVectorDeleteError（逐条失败信息进异常
      消息）。仅供**删除路径**使用 —— 那条路径的 DB 行还没删（vector-first），
      失败上抛才能保住"DB 完好、可重试、无孤儿"的失败态，而不是静默留下孤儿向量。

    本函数被三条路径共用：删除（delete_memory）、归档与取代
    （archive_memory_vectors，R11 / R13）—— collection 操作全仓只有这一处（D8）。
    """
    ids = [str(m) for m in (memory_ids or []) if m]
    if not ids:
        return 0
    from app.services import rag

    persist_dir = str(getattr(rag, "PERSIST_DIR", "") or "")
    if persist_dir and not os.path.isdir(persist_dir):
        # 向量库整目录不存在（记忆从未写入过向量；或已按 D5 清退）→ 无向量可删。
        # 此处刻意提前返回：否则 _store() 会为了"删一个不存在的库"而把空库建出来。
        # 两种 on_error 都不抛：无库就是"无可删"，不是失败。
        return 0
    deleted = 0
    failures = []
    for mid in ids:
        try:
            rag._store(tenant_id)._collection.delete(where={"memory_id": mid})
        except Exception as e:
            if on_error == "raise":
                failures.append((mid, f"{type(e).__name__}: {e}"))
                continue
            logger.warning(
                "[memory_store] 向量级联删除失败（主存储的状态已变更，向量可能残留）"
                "memory_id=%s tenant=%s: %s", mid, tenant_id, e)
            continue
        deleted += 1
    if failures:
        detail = "; ".join(f"memory_id={mid}: {msg}" for mid, msg in failures)
        raise MemoryVectorDeleteError(
            f"向量级联删除失败（{len(failures)}/{len(ids)} 条，本次删除未生效，"
            f"DB 行未被改动，可重试）：{detail}")
    return deleted


def delete_memory(memory_id: Optional[str] = None, vendor: Optional[str] = None,
                  tenant_id: Optional[str] = None,
                  supplier_id: Optional[int] = None):
    """删除一条或按供应商批量删除记忆：**先删向量（失败即抛）、后删 DB 行**（FP-2c）。

    返回 {"affected": 删除的 DB 行数, "memory_ids": [...],
          "vectors_deleted": 成功级联删除的 memory_id 条数}。
    affected == 0 表示该租户下没有匹配行（端点据此回 404，跨租户也走这条路径）。

    为什么 vector-first：SQLite 单条 DELETE 失败概率远低于向量库故障，把**不可回滚
    的一侧（DB 已提交）放到最后**，失败态就落在"DB 完好、可重试、无孤儿"——若保持
    DB 先删 + 失败上抛，向量一旦删失败就是孤儿向量（H2 形态、consistent:false）。
    实现上先用 `db.delete_vendor_memory(dry_run=True)` 的**同一份过滤**取 mid 清单
    （只读、不删行），向量删净后再实删 DB 行；两步之间行被并发删掉时实删 affected=0
    （端点回 404），已删的向量属无害 no-op。

    supplier_id（FP-2a）：vendor_memory 的权威主体锚点；与 vendor 名过滤是 OR 关系
    （同名性不可靠，锚定到同一 supplier 但写法不同的行也必须删净）。
    """
    affected, mids = db.delete_vendor_memory(
        memory_id=memory_id, vendor=vendor, tenant_id=tenant_id,
        supplier_id=supplier_id, dry_run=True)
    if not mids:
        return {"affected": 0, "memory_ids": [], "vectors_deleted": 0}
    vectors_deleted = delete_memory_vectors(tenant_id, mids, on_error="raise")
    affected, _mids = db.delete_vendor_memory(
        memory_id=memory_id, vendor=vendor, tenant_id=tenant_id,
        supplier_id=supplier_id)
    return {"affected": affected, "memory_ids": mids,
            "vectors_deleted": vectors_deleted}


# -------------------------------------------------------------
# R11 / R13：状态迁移（归档 / 取代 / 恢复）与向量层的对称
#
# 为什么归在本模块：本模块的职责就是「一条记忆落在两张介质（vendor_memory 行 + Chroma
# 向量）时保证两侧一致」。删除路径的一致（delete_memory）早已在这里，而**归档**此前
# 只改 DB 行的 status、完全不动向量层 —— 删除有级联、归档没有，于是每归档一次就在向量库
# 留一份孤儿（实测：归档 4 条 → 巡检立刻报 `consistent: False` + 恰好那 4 个 orphan_mids）。
# R13 是同一不对称的第二例：**取代**（supersede，active -> superseded）起初也没有级联。
#
# 两个方向都收在下面两个函数里（而不是各自散在归档/取代/恢复的调用点上）：
# 「同一状态迁移的对称动作只写一份」正是本仓①类缺陷（改一处漏一处）的防线。
# 状态变更本身仍在 db 层（apply_vendor_memory_override_penalty / save_memory_fact /
# revive_vendor_memory），本层只负责「状态变了以后向量层该怎样」——调用方在**状态已提交
# 之后**调用。
# -------------------------------------------------------------
def archive_memory_vectors(tenant_id, memory_ids, vendor=None, reason="archived"):
    """非 active 迁移路径的向量级联：删掉**刚离开 active**那些 mid 的向量。

    两个调用方共用（同一实现在两处接线，不另写一份 collection 操作）：
    - R11 归档：`db.apply_vendor_memory_override_penalty`（active → archived）；
    - R13 取代：`db.save_memory_fact`（active → superseded）。
    名字沿用 R11 首次落点，语义已是通用的「非 active 迁移级联」；`reason` 只用于日志
    区分来源（缺省 "archived"），不影响行为。

    与删除路径共用同一个删除实现（`delete_memory_vectors`），不另写一份 collection 操作。

    调用契约（幂等）：只对**本次真的发生 active → 非 active 迁移**的 memory_id 调用。
    重复迁移不会重复删 —— 第二次调用时那些行已不是 active，调用方根本拿不到这批 mid；
    即便传入，删除一个不存在的 memory_id 也是 no-op（见 delete_memory_vectors 的说明）。

    向量库不可用/单条删除失败：逐条 warning 降级、不向上抛 —— 状态变更此刻已经
    提交，把向量层故障升级成异常只会让调用方误以为"没迁移成功"（与 delete_memory 同口径）。

    返回成功处理的 memory_id 条数（不等于"实际删掉的向量数"：一枚 mid 可能对应多条向量块）。
    """
    ids = [str(m) for m in (memory_ids or []) if m]
    if not ids:
        return 0
    for mid in ids:
        logger.info(
            "[memory_store] 非 active 迁移级联删向量（reason=%s）："
            "memory_id=%s vendor=%s tenant=%s",
            reason, mid, vendor, tenant_id)
    return delete_memory_vectors(tenant_id, ids)


def restore_memory_vectors(tenant_id, memory_id, vendor=None):
    """R11 恢复路径的向量补写：状态回到 active 时把该 mid 的向量补回。

    委托 `rag.ensure_memory_vectors`（单点实现：文本来自 `_row_payload`、写入走 AK2 的
    `_sync_memory_vectors`）—— 本函数只负责调用与兜底，**不留成功日志**：成功那一条
    info 由 `rag.ensure_memory_vectors` 打（同一次动作两行日志会淹没运维视角）。

    返回 True = 向量已在库中（含本次补写）；False = 未补写（无可嵌入内容 / 行不存在 /
    完整性闸门拒绝 / 向量库不可用而降级），此时状态变更仍然生效，只留 warning 可查。

    调用契约：只在**真的发生非 active → active 迁移**时调用（本来就 active 的行不补，
    避免"第二次点击恢复"顺手多写一枚重复向量）。
    """
    from app.services import rag

    try:
        return rag.ensure_memory_vectors(tenant_id, memory_id, vendor=vendor)
    except Exception as e:
        # ensure_memory_vectors 的契约是不抛；这里兜底是为了"实现漂移"时不把恢复动作
        # 拖成失败（状态已提交），同时避免异常被静默吞掉（本仓③类缺陷）。
        logger.warning(
            "[memory_store] R11 恢复补向量异常（状态变更已生效，向量可能缺失）："
            "memory_id=%s vendor=%s tenant=%s: %s",
            memory_id, vendor, tenant_id, e)
        return False
