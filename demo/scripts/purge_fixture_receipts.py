# -*- coding: utf-8 -*-
"""夹具单据清退工具（缺省预演：源库零写入；``--apply`` 才落笔）。

**为什么需要它**：live 的 ``receipts`` 表里残留着一批**程序化写入的夹具/流程单据**
（供应商名带「测试 / 直通审批 / 已核对 / 已入账 / 已解析」这类标记，且``image_path``
为空 —— 即从未有过真实拍摄的原图）。它们不属于真实业务，却让 ``receipts`` 总数偏大。
清退属**不可逆**操作，因此本项目把它拆成两步走：**先把工具写对并预演**（本文件），
再由人拍板后跑一次 ``--apply``。

**识别判据（两个条件必须同时成立，宁可少杀、不可误杀）**：
  ① 供应商名命中夹具/流程标记（``测试`` / ``直通审批`` / ``已核对`` / ``已入账`` /
     ``已解析``）**或**名字里带**括号内纯数字 id**（半角 ``(123)`` 或全角 ``（123）``）；
  ② 该单据 ``image_path`` 为空（无原图）。
只满足名字（但**有**原图）的一律**不**进候选 —— 这正是「宁可少杀」的关键：
判据①会命中像 ``7-ELEVEN (1542)`` 这样的真实单据名（括号里是门店号），
靠②把它挡在外面。同理「真实供应商名 + 无原图」也不进候选（名字不含任何标记）。
两个条件缺一，就会把真实单据误删；改动这两条时**必须**同时跑
``demo/tests/test_purge_fixture_receipts.py``（防误杀对照用例会失败）。

**``--apply`` 的纪律**（与本仓其余写数据脚本一致，逐条对应事故教训）：
  1. **先整库备份**（``shutil.copy2`` 到 ``--backup-to``，缺省 ``/tmp/``，文件名带时间戳），
     并打印备份路径与 md5；输出里给「从备份恢复」的命令行。
     （本项目 live 库是 ``journal_mode=delete``，无 ``-wal`` 旁文件时整文件复制即一致快照；
     若检测到 ``-wal``/``-shm`` 会一并复制以免拿到过期快照。）
  2. **按 id 集合分批（每批 ≤ ``--batch-size``，缺省 10），每批一个事务** ——
     单批失败不会留下半删状态，且逐批有 changes 读数可审计。
  3. **先删子表再删主表**：先删所有含 ``receipt_id`` 列的子表（至少含 ``receipt_items``），
     最后才删 ``receipts``。子表清单**由 ``sqlite_master`` + ``PRAGMA table_info`` 动态发现**，
     不硬编码一份名单（硬编码必然漏掉后加的表 —— 本仓高发的「同一根因多份实现只改一份」）。
  4. **清理其它引用**：``vendor_memory.source_ref`` 等「JSON 列表式引用」会把被删的
     receipt_id 摘掉，**保留记忆行本身**（记忆是独立资产，删单据不该连带删记忆），
     每条变更都打印 before → after 留痕。见 ``REFERENCE_JSON_COLUMNS``。
     ``vendor_sku_alias.source_ref`` 是 ``receipt:<id>`` **单串**文本形态（不是列表）且
     live 当前 0 行，本工具只**检测并报告**、不改写（理由见 ``report_text_references``）。
  5. **幂等**：已删的 id 重跑时 ``DELETE`` 命中 0 行、``total_changes`` 为 0，不报错。
  6. **不得触碰任何非候选行**：写前写后对**非候选区域**取指纹
     （非候选 receipts 的 id 集合 + 逐行 ``quote(*)`` 拼串的 md5），前后不一致即中止报错。

**「已读清单」纪律（P2seed3 审核 mustFix 2）**：用户是按计划 §11.0 的**一份具体清单**
拍板的，而判据会命中清单**之外**的单据（实测 live 上清单外还有 4 张：ids 9/10/68/296）。
平铺输出会让人看不出「实际删除集合 != 已读清单」，于是：
  - 内置 ``PLAN_SET_IDS``（§11.0 里能被判据命中的 **27 张**；来源与拆分见该常量注释），
    可用 ``--plan-ids`` 显式覆盖（清单变动时改输入、不改判据）；
  - 预演**逐条**标注「计划内 / 计划外」，并给「计划内 N 张 / 计划外 M 张」分段汇总；
  - **不加 ``--only-plan-set`` 时**：删除集 = 全部候选，只要存在计划外候选，预演就打醒目提示
    「本次实际删除集合 != 已读清单，清单外还有 M 张」（不静默扩大授权范围）；
  - **加 ``--only-plan-set`` 时**：``--only-plan-set`` 是**真过滤器**（2026-09-23 修正）——
    删除集 = ``plan_in``（**只删计划内**），计划外候选**原样留在库里**，并打出一条明确提示
    「候选里还有 N 张计划外未删」。
    修正前它是**闸门**（发现计划外即报错退出、不做任何删除），导致「清单内外都有候选时」
    只能拒绝执行、无法完成「精确删除已批准清单」——这正是本次要修的缺口。改成过滤器后
    「实际删除集合 == 已读清单」由**删除集本身**保证（而不是靠拒绝），且**未删的**计划外项
    会被逐条点名，仍可人工复核；
  - **纪律：未经用户再次确认，不得用``--apply`` 删计划外候选。**
  - ``九龍豆品廠`` 名下 10 张（原名 ``九龍豆品廠(直通审批)``，标记已被 P9 命名归一剥掉
    → 判据结构上抓不到）**不进候选、不参与删除**，只在报告里单独列一段标注「需单独拍板」，
    要删它们必须走下面的 ``--ids`` 显式入口。

**``--ids`` 显式入口（2026-09-23 新增，补「无法删非候选行」的缺口）**：判据结构上抓不到的行
（如 ``九龍豆品廠`` 那 10 张）只有**按 id 点名**才能精确清退。``--ids`` 接受一个显式 id 列表
（逗号/空白分隔），**允许包含非候选行**，但**缺省**必须逐行满足**安全双条件**
（``image_path`` 为空 **且** 名字命中夹具标记），三件事缺一不可：
  1. **每个 id 都必须存在，或可证明「已被删除」**（查不到的 id 分两类口径，见
     ``classify_missing_ids``）：**从不存在**（编号 > 当前 ``MAX(id)``，编号尚未被分配 →
     输入有误）**报错退出、零写入**；**已被删除**（编号已分配过、或在已读清单内 →
     已批准清退的幂等重跑）**不报错**，但必须在报告里逐条点名、并计入「已被删除」类别
     （``total_changes=0``）。why 要分两类：两种成因的处置完全相反（笔误要拦住、
     任务早做完了要能读出来），混成一条「不存在」会让人无法判断本次是打错字还是重跑；
  2. **每行都过安全双条件**：**缺省不得删无标记的行**（那正是「误杀真实供应商」的形态）；
     确需清退未过条件的行（例如标记被命名归一剥掉的夹具）时，加**危险开关**
     ``--allow-ids-unsafe``（2026-09-23 由 ``--allow-ids-outside-plan`` 改名而来，
     旧名已彻底删除、不留别名）—— 它**同时关闭两道闸：「① 名字未命中夹具标记」与
     「② 有原图」**（不是「只放宽清单范围」），help / 错误文案 / 报告三处都逐条点名，
     报告还会逐行打出「越权放行」及理由；
  3. **与既有路径共用同一套机制**：整库备份 / 按 id 分批（每批一事务）/ 先子表后主表 /
     引用清理 / **非候选区域指纹前后一致** —— 不另写一份删除实现（本仓高发「同一根因多份实现」）。
     ``--ids`` 与 ``--only-plan-set`` **互斥**（前者已是精确集合，无需再按清单过滤）。

**幂等（2026-09-23 已知事实校验）**：用户已批准的那次清退**已执行完毕**，内置 ``PLAN_SET_IDS``
里的 27 个 id **现已不存在**。工具在这种情形下必须**幂等且不报错**（重跑 ``--apply`` 时
``total_changes=0``、不产备份），并在预演输出里明说「这些 id 已不存在」（见 ``plan_set_existence``）。

用法（**必须先显式指定 DB_PATH 与 RAG_DIR**；不带 DB_PATH 就是 live 库）：

    # why 先指向副本：本模块 import app.db 时会在导入期执行 db._make_engine() 的迁移块，
    # 按 DB_PATH 对目标库做 ALTER（不会自动跳过）。预演同样适用。
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py                  # 预演（缺省）
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --dry-run        # 预演（显式写法）
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --only-plan-set  # 只删「已读清单」内（真过滤）
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --plan-ids 6,7,8 # 显式覆盖清单
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --ids 300,301    # 显式 id（须过安全双条件）
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --ids 300,301 \\
        --allow-ids-unsafe                                             # 危险：越权放行（同时关两道闸）
    DB_PATH=/tmp/purge_demo.db RAG_DIR=/tmp/purge_rag \\
        python demo/scripts/purge_fixture_receipts.py --apply \\
        --only-plan-set --backup-to /tmp --allow-live                  # 落笔（先备份）

**在 live 上 ``--apply`` 需显式 ``--allow-live``**（与 ``memory_extract_replay.py``
同款闸门）：``DB_PATH`` 缺省就指向 live，一个手滑的 ``--apply`` 会直接写生产库，
本仓已有多次「误跑写入 live」的前例，多一个 flag 的成本远低于一次事故。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402  （import 即执行幂等迁移；目标库由 DB_PATH 决定）

# live 库绝对路径：--apply 直接打 live 是红线，需显式 --allow-live。
LIVE_DB = os.path.abspath(os.path.join(_DEMO_DIR, "receipt_demo.db"))

# 判据①之一：夹具/流程标记（命中任一即算名字达标）。这些都是「程序化写入」留下的
# 痕迹：测试、审批直通、以及流程状态词被写进了供应商名。
FIXTURE_MARKERS = ("测试", "直通审批", "已核对", "已入账", "已解析")

# 判据①之二：括号内纯数字 id（半角/全角括号都认）。用 search 而非 match —— 数字括号
# 可能出现在名字中段（如 "7-ELEVEN (1542)"）。
_PAREN_DIGIT_RE = re.compile(r"[（(]\s*\d+\s*[）)]")

# 引用型 JSON 列：(表, 列, 主键列)。这些列存的是「receipt_id 列表」，
# 删单据时应把 id 从列表里摘掉、保留行本身。主键列用于定位要改哪一行。
REFERENCE_JSON_COLUMNS = (
    ("vendor_memory", "source_ref", "memory_id"),
    ("pending_memory", "source_receipt_ids_json", "id"),
    ("payments", "linked_receipt_ids_json", "id"),
)

# 单串文本式引用（形如 "receipt:1455"）：只报告、不改写。见 report_text_references。
TEXT_REFERENCE_COLUMNS = (
    ("vendor_sku_alias", "source_ref"),
)

# 分批大小缺省值（每批一个事务）。
DEFAULT_BATCH_SIZE = 10

# -------------------------------------------------------------
# 「已读清单」：用户按计划 §11.0 拍板的那 27 张
# -------------------------------------------------------------
# 来源：docs/superpowers/plans/2026-09-22-vendormemory-convergence.md 的 §11.0。
# 该节列出 4 家夹具供应商共 37 张单据，其中 **27 张**能被本工具的判据命中（无原图 +
# 名字带标记），按供应商拆分为：测试供应商 3 + 直通审批 parsed 供应商 16 +
# 直通审批测试店 8 = 27。清单外另有 4 张同样被判据命中但**不在** §11.0 清单里
# （ids 9/10/68/296），它们才是「实际删除集合 != 已读清单」的来源。
#
# why 用显式 id 而不是「按供应商名推导」：用户是按**一份具体的单据清单**拍板的，
# 「实际删除集合 == 已读清单」必须能被逐条核对；按名字推导会让「清单」随库内容漂移，
# 失去可审计性（新出现的同名单据会被无声纳入授权范围）。供应商名只用于分组阅读。
# 清单变动（新增/撤回授权）时用 `--plan-ids 6,7,8,...` 显式覆盖，**不要改判据**。
PLAN_SET_IDS = frozenset({
    6, 7, 8,                                                        # 测试供应商 3 张
    188, 196, 203, 210, 217, 224, 231, 239, 252, 265, 278, 291,     # 直通审批 parsed 供应商 16 张
    305, 318, 337, 630,
    248, 261, 274, 287, 301, 314, 333, 626,                         # 直通审批测试店 8 张
})

# 「需单独拍板」的供应商：§11.0 的 37 张里有 10 张属 `九龍豆品廠`，其原名
# `九龍豆品廠(直通审批)` 的流程标记已被 P9 的命名归一剥掉 → 本工具判据**结构上抓不到**。
# 这批**不进候选、不参与删除**，只在报告里单独列一段（含 id 明细）提示「需单独拍板」，
# 以免「工具只覆盖 27 张」与「计划写 37 张」两个数字各说各话（§11.0 的口径修正）。
PENDING_DECISION_VENDORS = ("九龍豆品廠",)


# -------------------------------------------------------------
# 标识符与连接
# -------------------------------------------------------------
def _qi(ident):
    """把标识符安全地包成 SQLite 双引号形式。

    why 不用参数绑定：SQLite 的标识符（表名/列名）不能用 ``?`` 占位，只能拼串；
    这里的标识符全部来自 ``sqlite_master``/``PRAGMA``（不是用户输入），仍按规范转义。
    """
    return '"%s"' % str(ident).replace('"', '""')


def _connect(db_path):
    """打开连接并置 Row 工厂（dict 式按列名取值，避免按位置取值错列）。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def md5_file(path):
    """文件 md5（备份核对与「预演零写入」自证都用它）。"""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -------------------------------------------------------------
# 判据：候选识别（唯一实现，供预演/落笔/测试共用）
# -------------------------------------------------------------
def classify_name(name):
    """返回名字命中的夹具判据列表（空列表 = 名字不命中任何标记）。"""
    hits = []
    text = str(name or "")
    for marker in FIXTURE_MARKERS:
        if marker in text:
            hits.append("marker:%s" % marker)
    m = _PAREN_DIGIT_RE.search(text)
    if m:
        hits.append("digit:%s" % m.group(0))
    return hits


def is_candidate(name, image_path):
    """两条判据**同时**成立才算候选；返回 ``(是否候选, 排除原因, 命中的判据)``。

    排除原因只在「名字命中但被②挡下」时非空，便于预演时解释「为什么它没进候选」。
    """
    hits = classify_name(name)
    if not hits:
        return False, "名字未命中夹具标记", hits
    if str(image_path or "").strip():
        return False, "有原图", hits
    return True, "", hits


def find_candidates(con):
    """扫全表按判据选出候选（只读，返回逐条 dict 供打印/审计）。"""
    out = []
    for r in con.execute(
            "SELECT id, supplier_name, status, image_path FROM receipts ORDER BY id"):
        ok, excluded, hits = is_candidate(r["supplier_name"], r["image_path"])
        if not ok:
            continue
        out.append({
            "id": int(r["id"]),
            "supplier_name": str(r["supplier_name"] or ""),
            "status": str(r["status"] or ""),
            "image_path": str(r["image_path"] or ""),
            "hits": hits,
        })
    return out


# -------------------------------------------------------------
# 「已读清单」切分、存在性校验与显式 id 校验（报告与删除集共用同一份切分）
# -------------------------------------------------------------
def resolve_plan_set(plan_ids=None):
    """返回本次生效的「计划内」id 集合。

    ``plan_ids`` 非空时用它（``--plan-ids`` 显式覆盖），否则用内置 ``PLAN_SET_IDS``。
    why 允许覆盖：清单是「当前已批准的一版」；将来新增/撤回授权时不应改代码判据，
    而应把新清单显式传进来 —— 这样「实际删除集合」始终可追溯到一个显式输入。
    """
    if plan_ids:
        return frozenset(int(x) for x in plan_ids)
    return PLAN_SET_IDS


def partition_candidates(candidates, plan_set):
    """把候选按「是否在已读清单内」切成两组，返回 ``(plan_in, plan_out)``（保持原顺序）。

    计划外的定义即「判据命中、但用户没在 §11.0 那一版清单里读过」——它们**不是**判据错，
    只是授权范围没覆盖，因此必须显式呈现并交人再确认，不得静默删。预演分段与
    ``--only-plan-set`` 过滤共用本函数，避免「报告说的是 A、删除集判的是 B」的分叉。
    """
    plan = {int(x) for x in plan_set}
    return ([c for c in candidates if int(c["id"]) in plan],
            [c for c in candidates if int(c["id"]) not in plan])


def find_pending_decision(con):
    """列出「需单独拍板」的供应商名下单据（**只报告，永不进候选、永不删**）。

    why 单独找而不是靠判据：``九龍豆品廠`` 的流程标记已被命名归一剥掉（P9），判据结构上
    抓不到；但实测其名下 10 张全部无原图、确属夹具性质。把这批**列出来**才能让「本工具
    覆盖 27 张」与「计划写 37 张」这两个数字对得上（§11.0 口径修正），并把处置权交回人。
    """
    out = []
    rows = con.execute(
        "SELECT id, supplier_name, status, image_path FROM receipts ORDER BY id")
    for r in rows:
        name = str(r["supplier_name"] or "")
        if not any(v in name for v in PENDING_DECISION_VENDORS):
            continue
        out.append({
            "id": int(r["id"]),
            "supplier_name": name,
            "status": str(r["status"] or ""),
            "image_path": str(r["image_path"] or ""),
        })
    return out


def plan_set_existence(con, plan_set):
    """返回 ``(仍在库的 id 列表, 已不存在的 id 列表)``（只读）。

    why 需要它：用户已批准的那次清退**已执行完毕**，``PLAN_SET_IDS`` 里的 27 个 id 现在
    都不在库里。此时工具必须能**幂等重跑**（``total_changes=0``、不产备份、不报错），
    并在预演里明说「这些 id 已不存在」—— 否则「重跑无输出」会被误读成「工具坏了」或
    「清单写错了」，而真相是「任务已完成」。这份读数也是 `--plan-ids` 输入正确性的自检。
    """
    existing, missing = [], []
    for rid in sorted(int(x) for x in plan_set):
        if con.execute("SELECT 1 FROM receipts WHERE id=?", (rid,)).fetchone():
            existing.append(rid)
        else:
            missing.append(rid)
    return existing, missing


def describe_row(con, rid):
    """按 id 读一行并算出「是否过安全双条件」，返回 dict；行不存在时返回 None。

    安全双条件 = ``image_path`` 为空 **且** 名字命中夹具标记（与 ``is_candidate`` 同一判据，
    不另写一份）。``--ids`` 的逐行校验与报告共用本函数。
    """
    r = con.execute(
        "SELECT id, supplier_name, status, image_path FROM receipts WHERE id=?",
        (int(rid),)).fetchone()
    if r is None:
        return None
    name = str(r["supplier_name"] or "")
    img = str(r["image_path"] or "")
    hits = classify_name(name)
    reasons = []
    if img.strip():
        reasons.append("有原图")
    if not hits:
        reasons.append("名字未命中夹具标记")
    return {
        "id": int(r["id"]),
        "supplier_name": name,
        "status": str(r["status"] or ""),
        "image_path": img,
        "hits": hits,
        "safe": not reasons,
        "unsafe_reason": "、".join(reasons),
    }


def classify_missing_ids(con, missing_ids, plan_set=None):
    """把「在 ``receipts`` 里查不到行」的 id 分成 ``(已被删除, 从不存在)`` 两组（只读）。

    判据（都是库内可复核的结构性证据，不臆测）：
      - **已被删除 / 幂等重跑**：id 落在**已读清单**（用户批准过的清退集合）内；或
        ``id <= 当前 MAX(id)`` —— 该编号落在已分配区间里，只可能是「曾写入后被删」；
      - **从不存在**：``id > 当前 MAX(id)``（编号尚未被分配）→ 属输入笔误，必须拦住。
        库中一条单据都没有（``MAX(id)`` 为空）时也无从判定已分配区间，同样归入本类，
        因为**拦住比放行安全**。

    why 要分两类：``--ids`` 是显式入口，「点名的 id 查不到」有两种成因 —— 笔误（该报错）
    vs 已批准清退的幂等重跑（应安静空转、把读数交回人）。混成一条「不存在」的文案会让人
    无法判断本次到底该去核对 id 还是该收工（P23 审核 mustFix 4）。
    """
    plan = {int(x) for x in (plan_set if plan_set is not None else PLAN_SET_IDS)}
    max_id = con.execute("SELECT MAX(id) FROM receipts").fetchone()[0]
    max_id = int(max_id) if max_id is not None else None
    already_deleted, never_existed = [], []
    for rid in missing_ids:
        rid = int(rid)
        if rid in plan or (max_id is not None and rid <= max_id):
            already_deleted.append(rid)
        else:
            never_existed.append(rid)
    return already_deleted, never_existed


def resolve_explicit_ids(con, requested_ids, allow_unsafe=False, plan_set=None):
    """``--ids`` 的逐行校验：返回 ``(rows, unsafe_rows, already_deleted)``。

    两条硬判据（缺一不可，均在任何写入与备份之前）：
      ① **每个 id 都必须存在，或可证明「已被删除」**（``classify_missing_ids``）——
         「从不存在」（编号超出已分配区间）一律**报错退出**（显式点名一个编号却什么都不删，
         若属笔误而静默通过，是比报错更危险的**静默失败**）；「已被删除」**不报错**，作为
         幂等重跑返回给报告逐条点名并计数（``total_changes=0``）；
      ② **每行都过安全双条件** —— 缺省不得删无标记/有原图的行（那是「误杀真实供应商」的形态）。
         确需清退未过条件的行时，加 ``--allow-ids-unsafe`` 显式越权，报告逐行留痕 ——
         该开关**同时关闭「① 名字未命中夹具标记」与「② 有原图」两道闸**。
    """
    rows, missing = [], []
    for rid in requested_ids:
        d = describe_row(con, rid)
        if d is None:
            missing.append(int(rid))
        else:
            rows.append(d)
    already_deleted, never_existed = classify_missing_ids(con, missing, plan_set)
    if never_existed:
        raise SystemExit(
            "拒绝执行 --ids：以下 id **从不存在**（编号超出当前已分配区间，"
            "零写入，未做任何删除/备份）：%s\n"
            "这一类不做静默跳过也不需要「已被删除」口径 —— 它几乎总是输入笔误，"
            "静默跳过会让人以为删过了。请核对 id 后重跑。"
            % " ".join(str(x) for x in never_existed))
    unsafe = [d for d in rows if not d["safe"]]
    if unsafe and not allow_unsafe:
        lines = ["拒绝执行 --ids：以下 %d 行未过安全双条件（``image_path`` 为空 且 "
                 "名字命中夹具标记）：" % len(unsafe)]
        for d in unsafe:
            lines.append("  单据 id=%s 供应商=%s 状态=%s 有原图=%s 命中=%s -> 未过：%s"
                         % (d["id"], d["supplier_name"], d["status"],
                            "是" if d["image_path"] else "否",
                            ",".join(d["hits"]) or "无", d["unsafe_reason"]))
        lines.append("缺省不得删未过条件的行（防误杀真实单据）。确需清退请显式加**危险开关** "
                     "--allow-ids-unsafe —— 它同时关闭两道闸："
                     "① 名字未命中夹具标记（缺省不得删无标记的行）"
                     "② 有原图（缺省不得删有原图的行）；报告会逐行留痕。")
        raise SystemExit("\n".join(lines))
    return rows, unsafe, already_deleted


# -------------------------------------------------------------
# 子表发现与删除计划（先子表、后主表）
# -------------------------------------------------------------
def child_tables(con):
    """动态发现所有含 ``receipt_id`` 列的表（不含 ``receipts`` 本身）。

    动态发现而非硬编码：本项目「漏改一份」是最常见的缺陷形态，硬编码名单在
    未来新增子表时必然漏删、留下悬空引用。
    """
    out = []
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for (name,) in rows:
        if name == "receipts" or str(name).startswith("sqlite_"):
            continue
        cols = [c[1] for c in con.execute("PRAGMA table_info(%s)" % _qi(name))]
        if "receipt_id" in cols:
            out.append(str(name))
    return sorted(out)


def purge_statements(children, ids):
    """给出某一批 id 的删除语句序列（**顺序即契约**：子表在前、``receipts`` 在最后）。

    单独抽成函数是为了让「先删子表再删主表」这条顺序可被用例断言，而不是只能靠
    跑完看结果倒推。
    """
    placeholders = ",".join("?" for _ in ids)
    stmts = []
    for t in children:
        stmts.append(("DELETE FROM %s WHERE receipt_id IN (%s)"
                      % (_qi(t), placeholders), list(ids)))
    stmts.append(("DELETE FROM %s WHERE id IN (%s)"
                  % (_qi("receipts"), placeholders), list(ids)))
    return stmts


def count_rows(con, table, ids):
    """某子表里引用了这批 id 的行数（预演报告与前后对比用）。"""
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    return int(con.execute(
        "SELECT COUNT(*) FROM %s WHERE receipt_id IN (%s)"
        % (_qi(table), placeholders), list(ids)).fetchone()[0])


# -------------------------------------------------------------
# 引用清理（保留行，只摘除被删 id）
# -------------------------------------------------------------
def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def reference_scrub_changes(con, ids):
    """算出「JSON 列表引用」里需要摘除哪些 id（**只读**，不写库）。

    返回逐条变更 ``{table, column, pk, before, after, removed}``。落笔侧复用同一份
    结果做 UPDATE（``apply_reference_scrub``），预演侧直接打印 —— 一份实现两处用，
    不会出现「预演说的是 A、落笔做的是 B」的分叉。
    """
    want = {str(i) for i in ids}
    if not want:
        return []
    changes = []
    for table, column, pk in REFERENCE_JSON_COLUMNS:
        if not _table_exists(con, table):
            continue
        try:
            rows = con.execute("SELECT %s AS pk, %s AS val FROM %s"
                               % (_qi(pk), _qi(column), _qi(table))).fetchall()
        except sqlite3.OperationalError:
            continue
        for r in rows:
            raw = r["val"] or "[]"
            try:
                refs = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(refs, list):
                continue
            kept = [x for x in refs if str(x) not in want]
            if len(kept) == len(refs):
                continue
            removed = [x for x in refs if str(x) in want]
            changes.append({
                "table": table, "column": column, "pk_col": pk, "pk": r["pk"],
                "before": raw,
                "after": json.dumps(kept, ensure_ascii=False),
                "removed": removed,
            })
    return changes


def apply_reference_scrub(con, changes):
    """执行 ``reference_scrub_changes`` 给出的变更（在调用方的事务内）。"""
    for ch in changes:
        con.execute("UPDATE %s SET %s=? WHERE %s=?"
                    % (_qi(ch["table"]), _qi(ch["column"]), _qi(ch["pk_col"])),
                    (ch["after"], ch["pk"]))


def report_text_references(con, ids):
    """检测「单串文本式引用」（如 ``vendor_sku_alias.source_ref = "receipt:1455"``）。

    **只报告、不改写**，理由：该列语义是「这条别名从哪张单据学来的」的**来源留痕**，
    而别名行本身是独立资产（供应商×SKU 映射不因来源单据被清退而失效）。删引用会
    抹掉溯源、删行会丢映射，两者都比「留一条已知指向已删单据的留痕」更差。故交给
    人判。返回逐条 ``{table, column, pk, value}``。
    """
    want = {str(i) for i in ids}
    if not want:
        return []
    found = []
    for table, column in TEXT_REFERENCE_COLUMNS:
        if not _table_exists(con, table):
            continue
        try:
            rows = con.execute("SELECT rowid AS pk, %s AS val FROM %s"
                               % (_qi(column), _qi(table))).fetchall()
        except sqlite3.OperationalError:
            continue
        for r in rows:
            m = re.search(r"receipt:\s*(\d+)", str(r["val"] or ""))
            if m and m.group(1) in want:
                found.append({"table": table, "column": column,
                              "pk": r["pk"], "value": r["val"]})
    return found


# -------------------------------------------------------------
# 非候选区域指纹（不得触碰任何非候选行）
# -------------------------------------------------------------
def non_candidate_fingerprint(con, candidate_ids):
    """非候选 receipts 的「id 集合 + 逐行 quote(*) 拼串 md5」。

    只覆盖非候选行：候选行本来就要删/改，拿它们做指纹会自相矛盾。返回 ``ids`` 与
    ``md5``，落笔前后各取一次逐项比对。
    """
    cand = {int(x) for x in candidate_ids}
    cols = [c[1] for c in con.execute("PRAGMA table_info(%s)" % _qi("receipts"))]
    row_expr = "||'|'||".join("quote(%s)" % _qi(c) for c in cols)
    rows = con.execute("SELECT id AS rid, (%s) AS rowtext FROM receipts ORDER BY id"
                       % row_expr).fetchall()
    ids = []
    h = hashlib.md5()
    for r in rows:
        rid = int(r["rid"])
        if rid in cand:
            continue
        ids.append(rid)
        h.update(("%d|%s\n" % (rid, r["rowtext"] or "")).encode("utf-8"))
    return {"ids": ids, "md5": h.hexdigest(), "count": len(ids)}


# -------------------------------------------------------------
# 备份
# -------------------------------------------------------------
def backup_db(db_path, backup_to):
    """整库备份到 ``backup_to``（文件名带时间戳），返回 ``(路径, md5, 旁文件列表)``。

    用 ``shutil.copy2``（含元数据）覆盖主文件；若存在 ``-wal``/``-shm`` 旁文件
    则一并复制 —— 否则 WAL 模式下只拷主文件会拿到过期快照且**没有任何信号**。
    """
    os.makedirs(backup_to, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(db_path) or "receipt_demo.db"
    dest = os.path.join(backup_to, "%s.bak_%s" % (base, stamp))
    shutil.copy2(db_path, dest)
    sidecars = []
    for suffix in ("-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            shutil.copy2(src, dest + suffix)
            sidecars.append(dest + suffix)
    return dest, md5_file(dest), sidecars


# -------------------------------------------------------------
# 落笔：分批事务
# -------------------------------------------------------------
def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


def execute_batches(db_path, ids, children, batch_size=DEFAULT_BATCH_SIZE):
    """按 id 分批删除（每批一个事务）+ 引用清理，返回 ``(总 changes, 逐批读数)``。

    幂等：对已不存在的 id 重复调用时，各 ``DELETE`` 命中 0 行，``total_changes`` 为 0。
    """
    ids = [int(x) for x in ids]
    total = 0
    batches = []
    con = _connect(db_path)
    try:
        for batch in _chunks(ids, max(1, int(batch_size))):
            before_changes = con.total_changes
            with con:                                   # 每批一个事务
                for sql, params in purge_statements(children, batch):
                    con.execute(sql, params)
                changes = reference_scrub_changes(con, batch)
                apply_reference_scrub(con, changes)
            delta = con.total_changes - before_changes
            total += delta
            batches.append({"ids": batch, "changes": delta,
                            "ref_rows_touched": len(changes)})
    finally:
        con.close()
    return total, batches


# -------------------------------------------------------------
# 主流程
# -------------------------------------------------------------
def _assert_apply_target(db_path, apply, allow_live):
    """--apply 打 live 库需显式 --allow-live（本仓红线：不误写生产库）。"""
    if not apply:
        return
    if os.path.abspath(str(db_path)) == LIVE_DB and not allow_live:
        raise SystemExit(
            "拒绝在 live 库上 --apply：%s\n"
            "请把 DB_PATH 指向副本/临时库（例如 DB_PATH=/tmp/purge_demo.db），"
            "或显式加 --allow-live（请先确认已备份）。" % db_path)


def _snapshot(con, ids):
    """预演/落笔共用的读数快照（前后对比的分子分母）。"""
    max_id = con.execute("SELECT MAX(id) FROM receipts").fetchone()[0]
    return {
        "receipts": int(con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]),
        "receipt_items": int(con.execute("SELECT COUNT(*) FROM receipt_items").fetchone()[0]),
        "max_id": int(max_id) if max_id is not None else None,
        "child_counts": {t: count_rows(con, t, ids) for t in child_tables(con)}
        if ids else {},
    }


def run(db_path, apply=False, backup_to="/tmp", batch_size=DEFAULT_BATCH_SIZE,
        allow_live=False, only_plan_set=False, plan_ids=None,
        ids=None, allow_ids_unsafe=False):
    """执行一次（预演或落笔），返回报告 dict。**预演零写入**。

    删除集有两条入口（``ids`` 非 None 走显式入口，与 ``only_plan_set`` 互斥，互斥由 CLI 保证）：

    - **候选判据**（``ids=None``）：删除集 = 全部候选；``only_plan_set=True`` 时**真过滤**为
      ``plan_in``（只删计划内），计划外候选**原样留在库里**并在报告里点名「还有 N 张计划外未删」。
    - **显式 id**（``ids=[...]``）：逐行校验「存在（或已被删除）+ 安全双条件」
      （``resolve_explicit_ids``），不合格即在**任何写入（含备份）之前** ``SystemExit``，源库零改动。
      ``allow_ids_unsafe=True``（危险开关，旧名 ``--allow-ids-outside-plan`` 已删除）会**同时**
      放行「名字未命中夹具标记」与「有原图」两类行，报告逐行留痕并点名关掉的是哪两道闸。

    两条入口共用同一套备份 / 分批事务 / 先子表后主表 / 引用清理 / 非候选指纹。
    """
    _assert_apply_target(db_path, apply, allow_live)

    plan_set = resolve_plan_set(plan_ids)

    con = _connect(db_path)
    try:
        candidates = find_candidates(con)
        plan_in, plan_out = partition_candidates(candidates, plan_set)
        plan_existing, plan_missing = plan_set_existence(con, plan_set)

        if ids is not None:
            selection = "explicit_ids"
            # 校验必须在任何写入（含备份）之前完成 → 不合格时源库零改动。
            delete_rows, unsafe_rows, already_deleted_ids = resolve_explicit_ids(
                con, ids, allow_ids_unsafe, plan_set)
        else:
            selection = "candidates"
            unsafe_rows = []
            already_deleted_ids = []
            # --only-plan-set 是**真过滤器**（2026-09-23 修正）：只删计划内，计划外留在库里。
            delete_rows = plan_in if only_plan_set else candidates

        pending_decision = find_pending_decision(con)
        children = child_tables(con)
        delete_ids = [int(c["id"]) for c in delete_rows]
        ref_changes = reference_scrub_changes(con, delete_ids)
        text_refs = report_text_references(con, delete_ids)
        fp_before = non_candidate_fingerprint(con, delete_ids)
        before = _snapshot(con, delete_ids)
    finally:
        con.close()

    vendors = sorted({c["supplier_name"] for c in candidates})
    report = {
        "mode": "apply" if apply else "dry_run",
        "db_path": db_path,
        "selection": selection,
        "criteria": ("名字命中 [%s] 或括号内纯数字 id；且 image_path 为空（两条同时成立）"
                     % "|".join(FIXTURE_MARKERS)),
        "children": children,
        # 候选（判据命中集）：始终全量呈现，作为「计划内/计划外」切分的基数。
        "candidates": candidates,
        "candidate_count": len(candidates),
        "candidate_ids": [int(c["id"]) for c in candidates],
        # 本次**实际删除集**：候选路径 = 候选（或 only_plan_set 下的计划内）；显式路径 = --ids。
        "delete_rows": delete_rows,
        "delete_ids": delete_ids,
        "delete_count": len(delete_ids),
        "delete_items": before["child_counts"].get("receipt_items", 0),
        "explicit_ids": [int(x) for x in (ids or [])],
        "allow_ids_unsafe": bool(allow_ids_unsafe),
        "ids_unsafe_rows": unsafe_rows,
        # `--ids` 查不到行的两类口径（P23 mustFix 4）：分别计数，避免「打错字」与
        # 「已批准清退的幂等重跑」被混成一条无法判断的「不存在」。
        "ids_already_deleted": already_deleted_ids,
        "ids_already_deleted_count": len(already_deleted_ids),
        "ids_never_existed": [],
        "ids_never_existed_count": 0,
        "ids_resolved_count": len(delete_rows),
        "ids_unsafe_count": len(unsafe_rows),
        "vendors": vendors,
        # 「已读清单」对齐：报告与 --only-plan-set 过滤共用同一份切分（partition_candidates）。
        "plan_set": sorted(int(x) for x in plan_set),
        "plan_set_existing_ids": plan_existing,
        "plan_set_missing_ids": plan_missing,
        "plan_set_missing_count": len(plan_missing),
        "plan_in": plan_in,
        "plan_out": plan_out,
        "plan_in_count": len(plan_in),
        "plan_out_count": len(plan_out),
        "only_plan_set": bool(only_plan_set),
        # --only-plan-set 真过滤时，被**留在库里**的计划外候选（报告要逐条点名）。
        "out_of_plan_skipped": (plan_out if (only_plan_set and selection == "candidates")
                                else []),
        "pending_decision": pending_decision,
        "other_child_counts": {k: v for k, v in before["child_counts"].items()
                               if k != "receipt_items"},
        "reference_json_changes": ref_changes,
        "text_references": text_refs,
        "fingerprint_before": fp_before,
        "before": before,
        "backup_path": "", "backup_md5": "", "backup_sidecars": [],
        "total_changes": 0, "batches": [],
        "after": None, "fingerprint_after": None, "fingerprint_ok": None,
    }

    if not apply or not delete_ids:
        # 预演：到此为止（源库零写入）。删除集为空的 --apply 同样不做任何写入、不产备份
        # （没有破坏动作就没有需要备份的东西，避免每次空跑都留一堆备份文件）。
        report["after"] = before
        report["fingerprint_after"] = fp_before
        report["fingerprint_ok"] = True
        return report

    # 落笔：先备份，再分批写。
    backup_path, backup_md5, sidecars = backup_db(db_path, backup_to)
    report["backup_path"] = backup_path
    report["backup_md5"] = backup_md5
    report["backup_sidecars"] = sidecars

    total, batches = execute_batches(db_path, delete_ids, children, batch_size)
    report["total_changes"] = total
    report["batches"] = batches

    con = _connect(db_path)
    try:
        report["after"] = _snapshot(con, delete_ids)
        report["fingerprint_after"] = non_candidate_fingerprint(con, delete_ids)
    finally:
        con.close()
    report["fingerprint_ok"] = (
        report["fingerprint_after"]["md5"] == fp_before["md5"]
        and report["fingerprint_after"]["ids"] == fp_before["ids"])
    return report


def _recovery_command(report):
    """从备份恢复的命令行（预演时给的是「落笔后会生成的」模板）。"""
    return "cp %s %s" % (report["backup_path"] or "<备份文件>", report["db_path"])


def print_report(report):
    """把报告打成可逐条复核的清单（人看这一屏就能决定要不要 --apply）。"""
    print("模式            : %s" % ("落笔（--apply）" if report["mode"] == "apply"
                                   else "预演（源库零写入）"))
    print("库              : %s" % report["db_path"])
    print("候选判据        : %s" % report["criteria"])
    print("候选取字段      : %s" % ", ".join(report["children"]))
    print("候选单据        : %d 张，涉及 %d 家供应商"
          % (report["candidate_count"], len(report["vendors"])))
    print("选择方式        : %s" % (
        "显式 --ids（逐行核「存在或已被删除」+ 安全双条件，允许非候选行）"
        if report["selection"] == "explicit_ids"
        else ("候选判据 + --only-plan-set 真过滤（只删计划内）" if report["only_plan_set"]
              else "候选判据（全部候选）")))
    print("本次删除集合    : %d 张%s"
          % (report["delete_count"],
             "（%s）" % " ".join(str(x) for x in report["delete_ids"])
             if report["delete_count"] else ""))
    # 已读清单存在性：用户已批准的清退应已执行完毕时，明说「这些 id 已不存在」（幂等依据）。
    # 否则「重跑无输出」会被误读成「工具坏了」或「清单写错了」。
    total_plan = len(report["plan_set"])
    if report["plan_set_missing_count"] and report["plan_set_missing_count"] == total_plan:
        print("已读清单校验    : %d 张**已不存在**（用户已批准的清退应已执行完毕；"
              "重跑幂等，total_changes=0）" % total_plan)
    elif report["plan_set_missing_count"]:
        print("已读清单校验    : %d 张已不存在（%s），其余 %d 张仍在库（%s）"
              % (report["plan_set_missing_count"],
                 " ".join(str(x) for x in report["plan_set_missing_ids"]),
                 len(report["plan_set_existing_ids"]),
                 " ".join(str(x) for x in report["plan_set_existing_ids"])))
    else:
        print("已读清单校验    : %d 张全部仍在库" % total_plan)
    print("分段汇总        : 计划内 %d 张 / 计划外 %d 张（已读清单 = 计划 §11.0 的 %d 张%s）"
          % (report["plan_in_count"], report["plan_out_count"], len(report["plan_set"]),
             "，--only-plan-set 生效" if report["only_plan_set"] else ""))
    if report["selection"] == "explicit_ids":
        # 显式入口是另一条授权路径（按 id 点名），此处不打「!= 已读清单」告警；
        # 计划内/计划外只作为阅读标签，删除集由 --ids 本身决定（见下方显式明细）。
        print("                （显式 --ids：删除集由点名的 id 决定，不按清单过滤）")
    elif report["only_plan_set"]:
        # 真过滤：只删计划内；计划外候选**原样留在库里**，必须逐条点名（不静默留存）。
        if report["plan_out_count"]:
            print("提示：候选里还有 %d 张计划外未删（不在已读清单内，已原样留在库里）：%s"
                  % (report["plan_out_count"],
                     " ".join(str(c["id"]) for c in report["plan_out"])))
        else:
            print("                （候选全部在已读清单内，--only-plan-set 未过滤掉任何候选）")
    elif report["plan_out_count"]:
        # 硬要求：不加 --only-plan-set 时，预演必须醒目提示「实际删除集合 != 已读清单」。
        print("!" * 78)
        print("!! 注意：本次实际删除集合 != 已读清单 —— 清单外还有 %d 张"
              % report["plan_out_count"])
        print("!! 未经用户再次确认，不得对含计划外候选的集合执行 --apply。")
        print("!! 若只想删已读清单内的 %d 张，加 --only-plan-set"
              "（真过滤：只删计划内，计划外留库并点名）。" % report["plan_in_count"])
        print("!" * 78)
    else:
        print("                （本次实际删除集合 == 已读清单，无计划外候选）")
    print("级联将删        : receipt_items %d 行" % report["delete_items"])
    for table, n in sorted(report["other_child_counts"].items()):
        if n:
            print("                   %s %d 行" % (table, n))
    print("引用清理        : JSON 列表引用命中 %d 行（保留行、仅摘除被删 id）"
          % len(report["reference_json_changes"]))
    for ch in report["reference_json_changes"]:
        print("                   %s.%s pk=%s: %s -> %s"
              % (ch["table"], ch["column"], ch["pk"], ch["before"], ch["after"]))
    if report["text_references"]:
        print("文本引用（只报告、不改写，理由见 docstring）: %d 条"
              % len(report["text_references"]))
        for t in report["text_references"]:
            print("                   %s.%s pk=%s = %s"
                  % (t["table"], t["column"], t["pk"], t["value"]))
    print("-" * 78)
    plan_ids = {int(x) for x in report["plan_set"]}
    # 表头与数据行共用同一份格式串，避免两边宽度口径不一致（中文列宽另算，此处按字符数）。
    table_fmt = "  %-8s %-8s %-11s %-6s  %-34s %s"
    print(table_fmt % ("单据 id", "计划", "状态", "有原图", "供应商", "命中判据"))
    for c in report["candidates"]:
        tag = "计划内" if int(c["id"]) in plan_ids else "计划外"
        print(table_fmt % (c["id"], tag, c["status"],
                           "是" if c["image_path"] else "否",
                           c["supplier_name"], ",".join(c["hits"])))
    print("-" * 78)
    if report["plan_in_count"]:
        print("计划内 %d 张: %s"
              % (report["plan_in_count"],
                 " ".join(str(c["id"]) for c in report["plan_in"])))
    if report["plan_out_count"]:
        print("计划外 %d 张: %s  <- 不在已读清单，未经确认不得删除"
              % (report["plan_out_count"],
                 " ".join(str(c["id"]) for c in report["plan_out"])))
    if report["selection"] == "explicit_ids":
        print("-" * 78)
        # `--ids` 三类口径分别计数（P23 mustFix 4）：让人一眼分清本次是「打错字」还是
        # 「已批准清退的幂等重跑」，而不是笼统一条「不存在」。
        print("--ids 分类计数  : 可删（过安全双条件）%d 行 / 存在但未过安全双条件 %d 行 / "
              "已被删除（幂等重跑）%d 行 / 从不存在 %d 行"
              % (len(report["delete_rows"]) - report["ids_unsafe_count"],
                 report["ids_unsafe_count"],
                 report["ids_already_deleted_count"],
                 report["ids_never_existed_count"]))
        if report["ids_already_deleted_count"]:
            print("已被删除（幂等）: %s <- 编号已分配过或属已读清单，视为已批准清退的重跑，"
                  "不报错（本次 total_changes=%d）"
                  % (" ".join(str(x) for x in report["ids_already_deleted"]),
                     report["total_changes"]))
        print("显式 --ids 明细（%d 行可删，逐行核过「存在 + 安全双条件」，允许非候选行）："
              % len(report["delete_rows"]))
        print(table_fmt % ("单据 id", "计划", "状态", "有原图", "供应商", "命中判据"))
        for c in report["delete_rows"]:
            tag = "计划内" if int(c["id"]) in plan_ids else "计划外"
            print(table_fmt % (c["id"], tag, c["status"],
                               "是" if c["image_path"] else "否",
                               c["supplier_name"], ",".join(c["hits"]) or "无"))
        # 危险开关必须自述「关掉了哪两道闸」（help / 错误文案 / 报告三处口径一致）。
        if report["allow_ids_unsafe"]:
            print("危险开关        : --allow-ids-unsafe 已生效 —— 它**同时关闭两道闸**：")
            print("                   ① 名字未命中夹具标记（缺省不得删无标记的行）")
            print("                   ② 有原图（缺省不得删有原图的行）")
            print("                   注意：它不是「只放宽清单范围」——清单范围由"
                  " --plan-ids/--only-plan-set 表达。")
        if report["ids_unsafe_rows"]:
            print("越权放行        : 上述两道闸被关闭后，本次实际放行 %d 行未过条件的行"
                  % len(report["ids_unsafe_rows"]))
            for r in report["ids_unsafe_rows"]:
                print("                   id=%s 供应商=%s 未过：%s"
                      % (r["id"], r["supplier_name"], r["unsafe_reason"]))
        else:
            print("越权放行        : 无（全部 %d 行都过安全双条件）" % len(report["delete_rows"]))
    if report["pending_decision"]:
        print("-" * 78)
        print("需单独拍板（%d 张，**不在**候选、不参与删除）：供应商 %s"
              % (len(report["pending_decision"]), "/".join(PENDING_DECISION_VENDORS)))
        print("  理由：原名含 `(直通审批)` 标记，已被 P9 命名归一剥掉 → 本工具判据结构上"
              "抓不到；")
        print("        实测这批全部无原图、确属夹具性质，但清退与否须单独拍板（不在 §11.0"
              " 的 27 张清单内）。")
        for p in report["pending_decision"]:
            print("  单据 id=%-8s 状态=%-11s 有原图=%-3s 供应商=%s"
                  % (p["id"], p["status"], "是" if p["image_path"] else "否",
                     p["supplier_name"]))
    print("-" * 78)
    fp_before = report["fingerprint_before"]
    print("非候选指纹(前)  : %d 行 md5=%s" % (fp_before["count"], fp_before["md5"]))
    before, after = report["before"], report["after"]
    print("前后对比        : receipts %d -> %d / receipt_items %d -> %d / max(id) %s -> %s"
          % (before["receipts"], after["receipts"],
             before["receipt_items"], after["receipt_items"],
             before["max_id"], after["max_id"]))
    if report["mode"] == "apply" and report["delete_count"]:
        print("备份            : %s (md5=%s%s)"
              % (report["backup_path"], report["backup_md5"],
                 "，含旁文件 %d 个" % len(report["backup_sidecars"])
                 if report["backup_sidecars"] else ""))
        print("分批            : %d 批 / 总 changes=%d"
              % (len(report["batches"]), report["total_changes"]))
        print("非候选指纹(后)  : md5=%s（一致=%s）"
              % (report["fingerprint_after"]["md5"], report["fingerprint_ok"]))
        print("恢复命令        : %s" % _recovery_command(report))
    elif report["delete_count"]:
        print("（预演：源库零写入；加 --apply 才落笔，落笔时会先整库备份）")
    else:
        print("（本次删除集合为空：未执行任何写入、未产备份%s）"
              % ("；计划外候选已按 --only-plan-set 留库" if report["out_of_plan_skipped"]
                 else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="夹具单据清退（缺省预演：源库零写入；--apply 才落笔并先备份）")
    ap.add_argument("--dry-run", action="store_true",
                    help="显式预演（等价于缺省行为；与 --apply 互斥）")
    ap.add_argument("--apply", action="store_true", help="真写；缺省只预演（不写源库）")
    ap.add_argument("--backup-to", default="/tmp",
                    help="--apply 的整库备份目录（缺省 /tmp，文件名带时间戳）")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                    help="每批单据数（每批一个事务，缺省 %d）" % DEFAULT_BATCH_SIZE)
    ap.add_argument("--allow-live", action="store_true",
                    help="允许 --apply 直接写 live 库（红线例外，请先确认已备份）")
    ap.add_argument("--only-plan-set", action="store_true",
                    help="真过滤：只删「已读清单」（计划 §11.0 的 27 张）内的候选，"
                         "计划外候选原样留在库里并点名「还有 N 张计划外未删」"
                         "（不再报错退出；与 --ids 互斥）")
    ap.add_argument("--plan-ids", default="",
                    help="显式覆盖「已读清单」的 id 集合（逗号/空白分隔）；"
                         "缺省用内置 PLAN_SET_IDS")
    ap.add_argument("--ids", default="",
                    help="显式 id 列表（逗号/空白分隔）：精确删除这些单据，允许包含非候选行。"
                         "每个 id 都必须存在（不存在即报错、零写入），且缺省必须同时满足"
                         "「image_path 为空 + 名字命中夹具标记」；与 --only-plan-set 互斥")
    ap.add_argument("--allow-ids-unsafe", action="store_true",
                    help="危险开关（仅与 --ids 搭配）：允许 --ids 删除未过安全双条件的行。"
                         "它会**同时关闭两道闸** —— ① 名字未命中夹具标记；② 有原图。"
                         "它不是「只放宽清单范围」（清单范围由 --plan-ids/--only-plan-set 表达），"
                         "而是对不可逆删除放行两类缺省被拒的行；报告会逐行留痕")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    if args.dry_run and args.apply:
        raise SystemExit("--dry-run 与 --apply 互斥：预演不写、落笔才写，二者只能选一")

    plan_ids = None
    if args.plan_ids.strip():
        try:
            plan_ids = [int(x) for x in re.split(r"[,\s]+", args.plan_ids.strip()) if x]
        except ValueError:
            raise SystemExit("--plan-ids 只接受整数 id（逗号/空白分隔），收到：%s"
                             % args.plan_ids)

    # explicit_ids 缺省为 None（= 走候选判据路径）；空串视为「未提供」。
    explicit_ids = None
    if args.ids.strip():
        try:
            explicit_ids = [int(x) for x in re.split(r"[,\s]+", args.ids.strip()) if x]
        except ValueError:
            raise SystemExit("--ids 只接受整数 id（逗号/空白分隔），收到：%s" % args.ids)

    if explicit_ids is not None and args.only_plan_set:
        raise SystemExit("--ids 与 --only-plan-set 互斥：--ids 已是精确删除集合，"
                         "无需再按清单过滤（二者同用会让「删除集由谁决定」有歧义）")
    if args.allow_ids_unsafe and explicit_ids is None:
        raise SystemExit("--allow-ids-unsafe 只与 --ids 搭配使用："
                         "候选路径的越权由 --plan-ids/--only-plan-set 表达。"
                         "该开关会同时关闭「名字未命中夹具标记」与「有原图」两道闸，"
                         "只对不可逆删除的显式 id 入口开放")

    report = run(db.DB_PATH, apply=args.apply, backup_to=args.backup_to,
                 batch_size=args.batch_size, allow_live=args.allow_live,
                 only_plan_set=args.only_plan_set, plan_ids=plan_ids,
                 ids=explicit_ids, allow_ids_unsafe=args.allow_ids_unsafe)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
    # 指纹不一致说明「非候选区域被动过」——必须显式失败（否则会静默带病通过）。
    if report["fingerprint_ok"] is False:
        raise SystemExit("非候选区域指纹前后不一致，已中止（请用备份核对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
