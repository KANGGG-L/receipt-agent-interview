# -*- coding: utf-8 -*-
"""冷启动种子工具：从**历史数据**推导四类结构化事实候选，入人审队列（P2-T1/T3 前置）。

为什么需要这个脚本（而不是等系统自己"越用越准"）：
`vendor_memory` 的 live 现状是 **0 条 active**（9 行全 archived），而有效命中率的分母
口径是「供应商可确定 **且** 该供应商有可用记忆的单据数」—— 记忆为空时该分母恒为 0，
`effective_rate_pct` 只能输出 null（目标**当前不可测**，既不是 0% 也不是失败）。
所以第一件事不是调参，而是**把"可命中总体"建出来**：库里已经有一批被人确认过的历史
数据（`receipt_items.matched=1` 的匹配结果、审批入账后的库存流水、单据的版式分布），
它们足以推导出一批高可信的候选事实 —— 本脚本就是那条推导链。

四类事实的**来源逐条写死**（kind 全部落在 `memory_facts.ALLOWED_KINDS` 内）：

1. `alias` —— 写法 → 标准品名。
   来源：`receipt_items.matched=1` 且 `receipts.supplier_id` 非空，join `skus` 取标准名。
   同一 (供应商, 写法) 命中多个 SKU 时取**频次最高**者，同频次取**较新单据**
   （与更新四语义的冲突优先级「人工修正 > 近期证据 > 频次」同向）。
   `fact_key = "alias:<写法>"`，`value = 标准品名`。
2. `unit` —— 该供应商对某 SKU 的习惯单位。
   来源：同 `(supplier_id, sku_id)` 在 `receipt_items` 里出现过的 `unit`，取**频次最高**
   的一个作 `fact_value`，并把频次与分布写进报告与 content（`unit` 只作记录时的证据）。
   `fact_key = "unit:<标准品名>"`，`value = 单位`。
3. `layout` —— 版式先验。
   来源：每个 `supplier_id` 下 `receipts.doc_form` 的分布（排除软删行），取**占比最高**
   一项。分布原样写进报告的 `layout_distribution`（不只给结论）。
   `fact_key = "layout:doc_form"`，`value = 版式名`。
   注意口径：`doc_form` 是**识别输出**而非人工确认值 —— 这是该维度唯一可得的历史来源，
   故它的 `source_kind` 仍是 approve 但可信度按"统计推断"理解（见 `SEED_SOURCE_KIND`）。
4. `price_range` —— 历史价格区间。
   来源：`inventory_log` 中 `kind='in'` 的入库流水，按 `(supplier_id, sku_id)` 用
   `amount/qty` 汇总单价的最低价/最高价与样本数。
   **样本数低于阈值不产出**（缺省 `MIN_PRICE_SAMPLES=3`，可用 `--min-price-samples` 覆盖）：
   在 1-2 条流水上"算出"一个区间等于凭极少样本编造先验。
   供应商归属取 `receipt_id` 对应的 `receipts.supplier_id`，缺失时回落 `vendor` 名解析。
   `fact_key = "price_range:<标准品名>"`，`value = "最低价-最高价"`。

退化项过滤（T1：不承载映射信息的事实一律不产出，并**计数**而非静默丢）：
- `alias`：写法经项目**唯一**的 SKU 名归一实现 `receipt_utils._normalize_sku_name`
  归一后若与标准品名归一结果相同（例：写法「白糖」→ 标准名「白糖」），这条别名不承担
  「写法 → 标准名」的桥 —— 登记它得到的匹配结果与全局精确/核心词通道完全相同，属虚高，
  故不产出并计入「collector:alias 无映射信息」。**归一必须复用那一份实现**（别名表写入侧
  `services/inventory` 与匹配侧 `receipt_utils._match_sku` 的 canonical 通道都用它）；
  自建第二份必然分叉成"种子工具判退化、匹配侧判有用"，正是本仓高发的同一口径多份实现。
- `unit`：该供应商的常见单位若与 SKU 自身 `base_unit` 相同，不构成"供应商特有的单位习惯"
  （本来就是这个单位），同样不产出并计入「collector:unit 与基准单位相同」。
- **effective 分母预估只基于过滤后的候选**；报告另给「过滤前（含退化项）」对照值，
  以便看出退化项贡献了多少虚高（两个数字并列，不可混读）。

供应商质量门（V1：三层分类，逐条判据见 `_classify_suppliers` 的 docstring）：
- **hard-reject** —— 名字命中显式夹具/流程标记、或含括号内纯数字 id（`XXX (1542)`）、
  或**名下没有任何非测试形态的真实单据证据**（判别式＝原图路径是否落在测试临时目录、
  或 doc_form 与有无原图矛盾）。命中即**直接不产出**，并逐条计入
  「quality:hard-reject …」丢弃原因（不静默丢）。why 需要这一层：live 的 `suppliers`
  里混着 `测试供应商`/`直通审批测试店`/`7-ELEVEN HK (1542)` 这类夹具与占位主体，它们此前
  会照常产出候选（主 agent 曾在人审队列里逐条剔除），本层把它变成可计数、可复核的过滤。
- **suspect** —— 名字像买方（连锁便利店/餐厅名）、或其真实单据证据 <
  `SUSPECT_MIN_EVIDENCE`（缺省 3，CLI `--suspect-min-evidence` 可配）：1/1 的占比
  不构成先验，故**产出但打标** `suspect=true` + `suspect_reason`（`content` 前置可见标记），
  报告**单列一张表**供人审判断；why 只标可疑而不硬拒：供应商与买方在名字上并不总能区分，
  少样本也不等于错，这两个判断都必须由人拍板。
- **pass** —— 以上都不命中。
**计数口径**：报告的四类候选产出数与有效分母预估**只统计 pass 类**；suspect 计入单独一栏，
**不混入分母预估** —— 否则少样本/疑似买方的候选会把预估再次推高（与退化项虚高同族）。

写入纪律（三条，缺一条这个脚本就退化成"为了数字好看而编数据"）：
- **缺省预演**（只读、不写），`--apply` 才落笔；且**只写 `DB_PATH` 指向的库**。
- 候选**一律先过** `memory_facts.validate_fact`（结构闸门）**再过**
  `memory_consolidate.decide_action`（与既有 active 事实比对 → ADD/UPDATE/DELETE/NOOP）。
  只有 ADD/UPDATE 才入队；**每一条被丢弃的候选都带原因计数**，绝不静默丢。
- 产出**只进 `pending_memory` 人审队列**（`db.enqueue_pending_memory`，带
  supplier_id / fact_kind / fact_key / fact_value），**绝不直接写 `vendor_memory` 生效行** ——
  规则级事实必须人审，与双轨写入制一致。

幂等：`enqueue_pending_memory` 按 (vendor, tenant, content) 对 pending 行去重，且 content
里不含时间戳，故连续两次 `--apply` 第二次新增 0 条。

"预计能让 effective 分母增加多少"的计算口径（报告字段
`effective_denominator_projection`）：把每条待审事实在**临时副本**上模拟成
"已批准生效"的一行可用 `vendor_memory`（同 supplier_id 锚点、status=active、带内容、
decay_score=1.0），再用 `scripts/memory_metrics.py` 的**同一口径实现**复算
`eligible_denominator`，前后之差即预计增量。why 复用而不是另写 SQL：本仓已反复出现
"同一口径多份实现、只改一份"的缺陷，指标口径尤其不能再抄第二份。这是**上界**
（事实需经人工批准才生效；供应商本已可用则不再计入增量）。

**报告口径只算过滤后的候选**：退化项（上面那条过滤）不参与模拟，否则「预计增量」会把
永远不会被登记的虚高一起算进去。同时另算一份「过滤前（把退化项也算作候选）」的对照值
（字段 `delta_unfiltered` / `gaining_suppliers_unfiltered`），用于看出过滤掉了多少虚高 ——
两个数字并列输出且不可混读：前者是报告口径，后者只是对照。

消费方（本脚本不改生产链路，只投喂既有队列）：
- `db.enqueue_pending_memory` 写入的 supplier_id / fact_kind / fact_key / fact_value
  四个载荷列，消费方是 `db._pending_row_to_dict`（demo/app/db.py:3893）→ 
  `db.list_pending_memory`（demo/app/db.py:3916）→ 待确认记忆界面（逐字段 diff）；
- 人工批准后由 `rag.approve_pending_to_memory`（demo/app/services/rag.py:596）把载荷交给
  `db.save_memory_fact` 落 `vendor_memory`（载荷不齐才回落自由文本）。

用法（**必须先显式指定 DB_PATH 与 RAG_DIR**）：
  # why：本脚本 import app.db 时会在导入期执行 db._make_engine() 的迁移块，按 DB_PATH
  # 对目标库做 ALTER（不会自动跳过）。不带 DB_PATH 就是打 live 库。
  DB_PATH=/tmp/seed.db RAG_DIR=/tmp/seed_rag python demo/scripts/seed_memory_facts_from_history.py
  DB_PATH=/tmp/seed.db RAG_DIR=/tmp/seed_rag python demo/scripts/seed_memory_facts_from_history.py --json
  DB_PATH=/tmp/seed.db RAG_DIR=/tmp/seed_rag python demo/scripts/seed_memory_facts_from_history.py --apply
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.db as db  # noqa: E402  （import 即执行幂等迁移，保证结构列存在）

from app.services.memory_consolidate import (  # noqa: E402
    ACTION_ADD,
    ACTION_UPDATE,
    decide_action,
)
from app.services.memory_facts import (  # noqa: E402
    FACT_KEY_MAX_LEN,
    KIND_ALIAS,
    KIND_LAYOUT,
    KIND_PRICE_RANGE,
    KIND_UNIT,
    SOURCE_APPROVE,
    MemoryFact,
    validate_fact,
)
# 七个「扫描/手写得到的版式」的**唯一**白名单实现（在 contract.py:DocForm 上建）。
# 质量门用它判「doc_form 与有无原图矛盾」——自建第二份枚举必然与它分叉。
from app.services.contract import ALLOWED_DOC_FORMS  # noqa: E402
# 归一实现**复用**项目唯一的那一份（别名表写入侧与匹配侧 canonical 通道都用它），
# 不在本脚本另写一套 —— 否则必然出现"种子工具判退化、匹配侧判有用"的口径分叉。
from app.services.receipt_utils import _normalize_sku_name  # noqa: E402

# 留痕标识：审计要能回答"这条待审事实是谁推出来的"。
WRITTEN_BY = "seed_memory_facts_from_history"

# 来源口径：事实来自**已被人工确认过的历史数据**（matched=1 的匹配结果 / 审批入账后的
# 库存流水 / 单据版式分布），故记 approve 而不是 extract。why 显式指定而不吃
# enqueue_pending_memory 的缺省 feedback_distilled：把历史推导记成"用户反馈蒸馏"是
# 来源谎报，而 source_kind 正是「低可信不得覆盖高可信」防投毒判定的输入
# （memory_consolidate 的可信度守卫按它排序），错的来源会让下游判据建立在错的前提上。
SEED_SOURCE_KIND = SOURCE_APPROVE

# 价格区间的最小样本数：低于它不产出（默认值，CLI 可覆盖）。
MIN_PRICE_SAMPLES = 3

# 退化项的丢弃码与文案（alias / unit 各一条）。why 单列成常量：报告要能回答
# 「过滤掉了多少条虚高」，裸删会让这个数字在报告里彻底看不见（本仓高发的静默失效）。
_REASON_ALIAS_DEGENERATE = "collector:alias 无映射信息"
_TEXT_ALIAS_DEGENERATE = (
    "无映射信息（raw 与规范名相同）：写法与标准品名经同一归一实现 _normalize_sku_name "
    "后相同，该别名不承担「写法→标准名」的桥，登记后与全局精确/核心词匹配等价")
_REASON_UNIT_DEGENERATE = "collector:unit 与基准单位相同"
_TEXT_UNIT_DEGENERATE = (
    "无映射信息（常见单位与 SKU 基准单位相同）：该单位本就是 SKU 的 base_unit，"
    "不构成「该供应商特有的单位习惯」，登记后不改变任何判断")

# 回溯用的单据 id 列表上限：来源只是"凭什么推出来的"，不是全量归档。
_SOURCE_RECEIPT_CAP = 50

# -------------------------------------------------------------
# 供应商质量门（三层分类）—— 常量与判据
# -------------------------------------------------------------
VENDOR_PASS = "pass"
VENDOR_SUSPECT = "suspect"
VENDOR_HARD_REJECT = "hard-reject"

# ① hard-reject 的名字标记：这些词/后缀只会出现在**测试夹具**或「直通审批」这类**流程占位**
# 供应商名里（live 实例：`测试供应商` / `直通审批测试店` / `直通审批 parsed 供应商` /
# `九龍豆品廠(直通审批)` / `祥興凍肉(店员已核对)` / `金百加咖啡(已入账)`），出现即说明
# 该主体不是真实经营主体。why 名字标记优先于任何证据判断：名字本身就是主体身份的一部分。
HARD_REJECT_NAME_MARKERS = ("测试", "直通审批", "已入账", "已核对", "已解析")

# ① hard-reject：名字含**括号内纯数字 id**（半角/全角括号都算）。
# live 实例：`7-ELEVEN HK (1542)` —— 这是夹具/占位命名（括号里的数字是记录 id 不是名字）。
_PAREN_NUMERIC_ID = re.compile(r"[\(（]\s*\d+\s*[\)）]")

# ① hard-reject：「测试形态单据」的判别式（复用本仓既有约定，两条都能从数据本身复核）：
# a) 原图路径落在测试临时目录 —— 本项目测试污染的稳定特征（pytest-of-ethan /
#    /tmp/eval_reflow_* / private/var/folders 下的 pytest 临时目录）；
# b) doc_form 声称是**扫描/手写得到的版式之一**（ALLOWED_DOC_FORMS，7 种）却**没有任何
#    原图** —— 识别输出不可能凭空产生，这是「版式」与「有无原图」互相矛盾。
_TEST_IMAGE_PATH_MARKERS = ("pytest-of-", "/tmp/", "/private/var/folders/")

# ② suspect 的名字关键词（保守清单，宁少勿滥）：连锁便利店/餐厅/超市等**买方**才用的名字。
# why 不放进 hard-reject：供应商与买方在名字上并不总能区分（本仓就有 `鴻運餐飲` 这种看着像
# 买方、实际是供货方的名字），所以只标可疑交人审，不替人拍板。
SUSPECT_BUYER_NAME_MARKERS = (
    "餐室", "餐廳", "餐厅", "茶餐廳", "茶餐厅", "冰室", "酒樓", "酒楼", "食肆", "食堂",
    "便利店", "超市", "百貨", "百货", "士多",
    "7-eleven", "circle k", "ok便利店", "wellcome", "惠康", "parknshop", "百佳",
)

# ② suspect 的证据阈值（可配，CLI `--suspect-min-evidence`）：真实单据证据少于它即标可疑。
# why 缺省 3：1/1 或 2/2 的占比不构成先验（版式 1/1、单位 1/1、价格 1 个样本都是同一问题），
# 3 是本项目其它阈值（如 price_range 最小样本）同量级的保守取值。
SUSPECT_MIN_EVIDENCE = 3

# 质量门的理由短码（既用于**供应商级**计数，也拼进候选级丢弃原因码，保证两处口径一致）。
_HR_NAME_MARKER = "名字含测试/流程标记"
_HR_NUMERIC_ID = "名字含括号纯数字 id"
_HR_NO_EVIDENCE = "无非测试形态真实证据"
_SUSPECT_BUYER_NAME = "名字像买方"
_SUSPECT_FEW_EVIDENCE = "真实单据证据不足"

# suspect 候选写进 pending 时在 content 前置的可见标记 —— 人审界面读的就是 content，
# 不前置标记就等于"默默入队"（本任务要修的正是这个）。%s 位置填 suspect_reason。
_SUSPECT_CONTENT_PREFIX = "【可疑·待人审】%s。原始内容："

_QUALITY_GATE_CRITERIA = (
    "供应商质量门三层（硬拒 / 可疑 / 通过）：① hard-reject 命中名字夹具标记、括号内纯数字 id、"
    "或无任何非测试形态真实单据证据（原图落测试临时目录、或 doc_form 与无原图矛盾）之一 → "
    "**直接不产出**并逐条计入「quality:hard-reject …」丢弃原因；② suspect 命中「名字像买方」或"
    "「真实单据证据 < SUSPECT_MIN_EVIDENCE」之一 → 候选产出但打 suspect=true 标记，"
    "报告单列供人审；③ pass。"
    "**计数口径**：报告的四类候选产出数与有效分母预估只统计 pass 类；suspect 计入单独一栏、"
    "hard-reject 只进丢弃原因，两者都不计入分母预估（否则预估会因少样本/夹具主体再次虚高）。"
)

# 版式事实的 key：一个供应商只有一条版式先验，故 key 恒定。
_LAYOUT_KEY = "layout:doc_form"

_PROJECTION_CRITERIA = (
    "预计增量口径：把**过滤掉退化项、且只取 pass 类（供应商质量门判定为可疑 suspect 与"
    "硬拒 hard-reject 的候选都不算，后者已被剔除）**之后，通过校验与整合判定的候选事实，"
    "在一份**临时副本**上模拟成『已批准生效』的一行可用 vendor_memory（同一 supplier_id "
    "锚点、status=active、带内容、decay_score=1.0），再用 scripts/memory_metrics.py 的同一"
    "口径实现复算 memory_hit_effective.eligible_denominator（分母＝供应商可确定且该供应商"
    "有可用记忆的单据数），前后之差即预计增量。五点必须一起读：① 模拟只在副本上进行，"
    "DB_PATH 指向的库不被写入；② 这是**上界** —— 事实须经人工批准才会真正生效；③ 供应商"
    "本已可用时不再计入增量（故小于待审事实涉及的供应商数）；④ 报告口径**只计 pass 类候选**，"
    "退化项（raw 与规范名相同 / 单位与基准单位相同）与 suspect 类都不参与模拟；另有『过滤前』"
    "对照值（delta_unfiltered＝**只撤销退化项过滤、其余各层不变**时的分母贡献），"
    "**两个数字不可混读**；"
    "⑤ 少样本/疑似买方的 suspect 供应商的候选**不入**本预估 —— 否则预估会因这些无从确认的"
    "主体再次虚高。"
)


# -------------------------------------------------------------
# 小工具
# -------------------------------------------------------------
def _connect(path):
    """打开只读用途的连接。

    刻意不用 `mode=ro`：库里不存在时（全新临时库）`mode=ro` 直接抛错，而本脚本的读查询
    只发 SELECT，写操作一律走 `db` 的会话（便于复用其幂等与归一实现）。这与
    `backfill_vendor_sku_alias.py` 的读路径同款。
    """
    return sqlite3.connect(path)


def _snapshot_sqlite(src, dst):
    """把 src 的已提交内容写成 dst。不写源库。

    shutil.copy2 只拷主文件。源库是 WAL 时，已提交的表和行还在 -wal 里，
    副本会缺表（vendor_memory）或缺行，预演数字变成 0 且没有别的信号。
    backup API 由 sqlite 读出一致快照。
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


def _reason_code(reason):
    """把可读理由归一成一个**短码**用于计数（理由全文另存一份，不丢信息）。

    为什么需要短码：理由里常内嵌值（`kind 非法：'x'`、`tenant='default'`），直接用全文
    当 key 会让同一个失败模式分裂成几十个 key，计数表反而看不出分布。取"（"或"："之前的
    前缀即该失败模式的稳定标识。
    """
    s = str(reason or "").strip()
    for sep in ("（", "："):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s[:32] or "unknown"


def _bump(bucket, code, text, n=1):
    """向 (code -> {count, text}) 计数桶累加，并保留一条理由原文供人读。"""
    slot = bucket.setdefault(code, {"count": 0, "text": text})
    slot["count"] += n
    return slot


def _merge_rejects(dst, src):
    """把某个采集器的拒绝计数并入总表，返回并入的条数。

    为什么采集器各自用一个桶、再在这里合并：报告要按 kind 给出"丢弃多少条"
    （`discarded[kind]` 必须含采集期拒绝），而原因计数表是全局一张 —— 分开计数再合并
    才能两边一致，不会出现"原因表里有 1 条、按 kind 却是 0 条"的对不上。
    """
    n = 0
    for code, slot in (src or {}).items():
        _bump(dst, code, slot["text"], slot["count"])
        n += slot["count"]
    return n


def _fmt_ids(ids):
    """单据 id 列表 → 可读文本（超出上限截断，避免 content 里塞进几百个 id）。"""
    items = [str(x) for x in ids]
    head = items[:_SOURCE_RECEIPT_CAP]
    tail = "…（共 %d 条）" % len(items) if len(items) > _SOURCE_RECEIPT_CAP else ""
    return ",".join(head) + tail


def _sku_key(prefix, sku_id, canonical):
    """按标准品名拼 fact_key；超长时回落 sku_id 形态。

    why 不直接丢弃超长 key：那会让"名字很长"的 SKU 的事实静默消失（本仓高发的静默失效）。
    回落形态 (#<sku_id>) 仍然确定唯一，且长度可控。
    """
    key = "%s:%s" % (prefix, canonical)
    if len(key) <= FACT_KEY_MAX_LEN:
        return key
    return "%s:#%s" % (prefix, sku_id)


# -------------------------------------------------------------
# 采集器：从历史数据推导候选（纯读，不写库）
# -------------------------------------------------------------
def _load_supplier_names(conn):
    """(tenant_id, supplier_id) -> 供应商名。锚点解析不出档案时不产出（绝不编造主体）。"""
    out = {}
    for sid, name, tid in conn.execute(
            "SELECT id, name, ifnull(tenant_id,'default') FROM suppliers"):
        out[(str(tid), int(sid))] = str(name or "")
    return out


def _collect_alias(conn, names, rejects, degenerate):
    """① 别名：已确认匹配 (supplier, 写法) -> 标准品名。

    `degenerate` 是调用方传入的集合，记录"被退化过滤挡下的 (租户, 供应商)" —— 供分母
    预估算「过滤前」对照值时把这类供应商也算进去（否则看不见虚高）。
    """
    rows = conn.execute(
        "SELECT ifnull(r.tenant_id,'default'), r.supplier_id, i.sku_id, i.name, s.name, r.id "
        "FROM receipt_items i "
        "JOIN receipts r ON r.id = i.receipt_id "
        "JOIN skus s ON s.id = i.sku_id "
        "WHERE ifnull(i.matched,0) = 1 AND i.sku_id IS NOT NULL "
        "  AND r.supplier_id IS NOT NULL "
        "  AND ifnull(s.tenant_id,'default') = ifnull(r.tenant_id,'default') "
        "ORDER BY r.id").fetchall()

    votes = {}   # (tid, sid, raw) -> {sku_id: [count, latest_rid, canonical, ids]}
    for tid, sid, sku_id, raw, canonical, rid in rows:
        raw = str(raw or "").strip()
        canonical = str(canonical or "").strip()
        if not raw:
            _bump(rejects, "collector:alias 写法为空",
                  "receipt_items.name 为空的已匹配明细，无法作为别名键")
            continue
        if not canonical:
            _bump(rejects, "collector:alias 标准名为空",
                  "sku.name 为空（SKU 记录未命名），别名没有落点")
            continue
        bucket = votes.setdefault((str(tid), int(sid), raw), {})
        slot = bucket.setdefault(int(sku_id), [0, 0, canonical, []])
        slot[0] += 1
        slot[1] = max(slot[1], int(rid))
        slot[2] = canonical
        if len(slot[3]) < _SOURCE_RECEIPT_CAP:
            slot[3].append(int(rid))

    out = []
    for (tid, sid, raw), bucket in votes.items():
        sku_id, slot = sorted(bucket.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[0]
        canonical = slot[2]
        # 退化过滤：写法经**同一归一实现**后与标准名相同 → 这条别名不承载映射信息。
        # why 用同一实现（而非自建去空白/大小写）：匹配侧 `_match_sku` 的 canonical 通道
        # 与写入侧登记用的就是 `_normalize_sku_name`；两套归一必然分叉，出现
        # "种子工具判退化、匹配侧判有用"的自相矛盾。
        norm_raw = _normalize_sku_name(raw)
        norm_canon = _normalize_sku_name(canonical)
        if norm_raw and norm_raw == norm_canon:
            _bump(rejects, _REASON_ALIAS_DEGENERATE, _TEXT_ALIAS_DEGENERATE)
            degenerate.add((str(tid), int(sid)))
            continue
        sup = names.get((tid, sid)) or ""
        if not sup:
            _bump(rejects, "collector:供应商档案不存在",
                  "receipts.supplier_id 指向的供应商不存在（悬空锚点），不产出")
            continue
        out.append({
            "kind": KIND_ALIAS, "tenant_id": tid, "supplier_id": sid,
            "supplier_name": sup, "sku_id": sku_id,
            "fact_key": "alias:%s" % raw, "value": canonical,
            "source_receipt_ids": list(slot[3]),
            "evidence": {"写法": raw, "标准品名": canonical, "命中次数": slot[0],
                         "同写法候选 SKU 数": len(bucket)},
            "content": ("供应商「%s」的别名先验：写法「%s」→ 标准品名「%s」"
                        "（依据：%d 条已确认明细；单据 %s）"
                        % (sup, raw, canonical, slot[0], _fmt_ids(slot[3]))),
        })
    return out


def _collect_unit(conn, names, rejects, degenerate):
    """② 单位：同 (supplier, sku) 在已确认明细里出现过的 unit，取频次最高者。

    退化过滤：常见单位若与该 SKU 自身的 `base_unit` 相同 → 不产出（那本来就是 SKU 的
    基准单位，登记它不构成"供应商特有用单位习惯"），并计入 `degenerate` 供对照。
    """
    rows = conn.execute(
        "SELECT ifnull(r.tenant_id,'default'), r.supplier_id, i.sku_id, i.unit, s.name, "
        "       ifnull(s.base_unit,''), r.id "
        "FROM receipt_items i "
        "JOIN receipts r ON r.id = i.receipt_id "
        "JOIN skus s ON s.id = i.sku_id "
        "WHERE ifnull(i.matched,0) = 1 AND i.sku_id IS NOT NULL "
        "  AND r.supplier_id IS NOT NULL "
        "  AND trim(ifnull(i.unit,'')) <> '' "
        "  AND ifnull(s.tenant_id,'default') = ifnull(r.tenant_id,'default') "
        "ORDER BY r.id").fetchall()

    groups = {}   # (tid, sid, sku_id) -> {unit: [count, ids], canonical, base_unit}
    for tid, sid, sku_id, unit, canonical, base_unit, rid in rows:
        unit = str(unit or "").strip()
        canonical = str(canonical or "").strip()
        if not canonical:
            _bump(rejects, "collector:unit 标准名为空", "sku.name 为空，单位习惯没有落点")
            continue
        g = groups.setdefault((str(tid), int(sid), int(sku_id)),
                              {"units": {}, "canonical": canonical,
                               "base_unit": str(base_unit or "").strip()})
        slot = g["units"].setdefault(unit, [0, []])
        slot[0] += 1
        if len(slot[1]) < _SOURCE_RECEIPT_CAP:
            slot[1].append(int(rid))

    out = []
    for (tid, sid, sku_id), g in groups.items():
        sup = names.get((tid, sid)) or ""
        if not sup:
            _bump(rejects, "collector:供应商档案不存在",
                  "receipts.supplier_id 指向的供应商不存在（悬空锚点），不产出")
            continue
        dist = {u: v[0] for u, v in g["units"].items()}
        total = sum(dist.values())
        winner = sorted(g["units"].items(), key=lambda kv: (-kv[1][0], kv[0]))[0]
        unit, slot = winner
        canonical = g["canonical"]
        base_unit = g.get("base_unit") or ""
        # 退化过滤：常见单位 == SKU 基准单位（本来就一样）
        if base_unit and unit == base_unit:
            _bump(rejects, _REASON_UNIT_DEGENERATE, _TEXT_UNIT_DEGENERATE)
            degenerate.add((str(tid), int(sid)))
            continue
        out.append({
            "kind": KIND_UNIT, "tenant_id": tid, "supplier_id": sid,
            "supplier_name": sup, "sku_id": sku_id,
            "fact_key": _sku_key("unit", sku_id, canonical), "value": unit,
            "source_receipt_ids": list(slot[1]),
            "evidence": {"标准品名": canonical, "单位": unit, "频次": slot[0],
                         "样本数": total, "单位分布": dist, "基准单位": base_unit},
            "content": ("供应商「%s」对「%s」的习惯单位：%s（频次 %d/%d，分布 %s；单据 %s）"
                        % (sup, canonical, unit, slot[0], total,
                           json.dumps(dist, ensure_ascii=False), _fmt_ids(slot[1]))),
        })
    return out


def _collect_layout(conn, names, rejects):
    """③ 版式：每个 supplier 下 receipts.doc_form 的分布，取占比最高者。"""
    rows = conn.execute(
        "SELECT ifnull(tenant_id,'default'), supplier_id, ifnull(doc_form,''), id "
        "FROM receipts "
        "WHERE supplier_id IS NOT NULL AND trim(ifnull(doc_form,'')) <> '' "
        "  AND ifnull(deleted_at,'') = '' "
        "ORDER BY id").fetchall()

    groups = {}   # (tid, sid) -> {form: [count, ids]}
    for tid, sid, form, rid in rows:
        form = str(form or "").strip()
        if not form:
            continue
        bucket = groups.setdefault((str(tid), int(sid)), {})
        slot = bucket.setdefault(form, [0, []])
        slot[0] += 1
        if len(slot[1]) < _SOURCE_RECEIPT_CAP:
            slot[1].append(int(rid))

    out, distribution = [], {}
    for (tid, sid), bucket in groups.items():
        sup = names.get((tid, sid)) or ""
        if not sup:
            _bump(rejects, "collector:供应商档案不存在",
                  "receipts.supplier_id 指向的供应商不存在（悬空锚点），不产出")
            continue
        dist = {f: v[0] for f, v in bucket.items()}
        total = sum(dist.values())
        distribution["%s|%s" % (tid, sid)] = dist
        form = sorted(bucket.items(), key=lambda kv: (-kv[1][0], kv[0]))[0][0]
        slot = bucket[form]
        out.append({
            "kind": KIND_LAYOUT, "tenant_id": tid, "supplier_id": sid,
            "supplier_name": sup, "sku_id": None,
            "fact_key": _LAYOUT_KEY, "value": form,
            "source_receipt_ids": list(slot[1]),
            "evidence": {"版式": form, "占比": "%d/%d" % (slot[0], total),
                         "doc_form 分布": dist},
            "content": ("供应商「%s」的单据版式先验：%s（占比 %d/%d，分布 %s；样本单据 %s）"
                        % (sup, form, slot[0], total,
                           json.dumps(dist, ensure_ascii=False), _fmt_ids(slot[1]))),
        })
    return out, distribution


def _collect_price_range(conn, names, rejects, min_samples):
    """④ 价格区间：inventory_log(kind='in') 按 (supplier, sku) 汇总单价的最低价/最高价。

    单价取 `amount/qty`（流水里存的是总额与数量，没有单价列）。供应商归属优先取
    `receipt_id` 对应的 receipts.supplier_id（结构关联），缺失时回落 vendor 名解析
    （复用 `db.resolve_supplier_id` 这一唯一实现，不另写一套名字归一）。
    """
    req_rows = conn.execute(
        "SELECT id, ifnull(supplier_id, -1) FROM receipts").fetchall()
    receipt_supplier = {int(rid): int(sid) for rid, sid in req_rows}

    rows = conn.execute(
        "SELECT ifnull(tenant_id,'default'), sku_id, ifnull(vendor,''), receipt_id, "
        "       amount, qty, id "
        "FROM inventory_log "
        "WHERE kind = 'in' AND sku_id IS NOT NULL AND qty > 0 AND amount > 0 "
        "ORDER BY id").fetchall()

    resolve_cache = {}
    sku_canonical = {int(r[0]): str(r[1] or "") for r in
                     conn.execute("SELECT id, name FROM skus")}

    def _resolve(tid, vendor):
        key = (tid, vendor)
        if key not in resolve_cache:
            try:
                resolve_cache[key] = db.resolve_supplier_id(vendor, tenant_id=tid)
            except Exception:
                resolve_cache[key] = None
        return resolve_cache[key]

    groups = {}   # (tid, sid, sku_id) -> {"prices": [...], "ids": [...]}
    for tid, sku_id, vendor, receipt_id, amount, qty, log_id in rows:
        tid = str(tid)
        sid = receipt_supplier.get(int(receipt_id)) if receipt_id is not None else None
        if sid is None or sid < 0:
            sid = _resolve(tid, str(vendor or ""))
        if not sid:
            _bump(rejects, "collector:价格流水供应商未锚定",
                  "inventory_log 行既无可用 receipt_id 锚点、按 vendor 名也解析不出档案，"
                  "无供应商维度不产出")
            continue
        canonical = sku_canonical.get(int(sku_id), "")
        if not canonical:
            _bump(rejects, "collector:price_range 标准名为空",
                  "库存流水的 sku_id 在 skus 表里不存在，价格区间没有落点")
            continue
        g = groups.setdefault((tid, int(sid), int(sku_id)),
                              {"prices": [], "ids": [], "canonical": canonical})
        g["prices"].append(float(amount) / float(qty))
        if receipt_id is not None and len(g["ids"]) < _SOURCE_RECEIPT_CAP:
            g["ids"].append(int(receipt_id))

    out = []
    for (tid, sid, sku_id), g in groups.items():
        samples = len(g["prices"])
        if samples < min_samples:
            _bump(rejects, "collector:price_range 样本不足",
                  "入库流水样本数 < %d（阈值），不在极少样本上凭空造价格区间；"
                  "实际样本数见 content 之外的报告中逐组统计" % min_samples)
            continue
        sup = names.get((tid, sid)) or ""
        if not sup:
            _bump(rejects, "collector:供应商档案不存在",
                  "价格流水的供应商档案不存在，不产出")
            continue
        lo, hi = min(g["prices"]), max(g["prices"])
        value = "%.2f-%.2f" % (lo, hi)
        canonical = g["canonical"]
        out.append({
            "kind": KIND_PRICE_RANGE, "tenant_id": tid, "supplier_id": sid,
            "supplier_name": sup, "sku_id": sku_id,
            "fact_key": _sku_key("price_range", sku_id, canonical), "value": value,
            "source_receipt_ids": list(g["ids"]),
            "evidence": {"标准品名": canonical, "区间": value, "样本数": samples,
                         "最低单价": round(lo, 4), "最高单价": round(hi, 4)},
            "content": ("供应商「%s」对「%s」的历史价格区间：%s"
                        "（入库 %d 次，来自库存流水；参考单据 %s）"
                        % (sup, canonical, value, samples, _fmt_ids(g["ids"]))),
        })
    return out


# -------------------------------------------------------------
# 供应商质量门（三层分类，纯读）
# -------------------------------------------------------------
def _is_test_shaped_receipt(image_path, doc_form):
    """判断一条单据是否**测试形态**（不可能作为真实主体的证据）。

    两条判据（都能从数据本身复核，不依赖"像不像夹具"的主观判断）：
    - 原图路径落在测试临时目录（`pytest-of-` / `/tmp/` / `/private/var/folders/`）——
      本项目测试污染的稳定特征（历史事故：pytest 直接往 live 库插测试单据）；
    - `doc_form` 声称是**扫描/手写得到的版式之一**（复用 `contract.ALLOWED_DOC_FORMS` 的
      唯一白名单）却**没有任何原图** —— 识别输出不可能凭空产生，这是自相矛盾。

    注意 `manual_entry`（人工录入）本就无原图：它既不算 test 也不算 real，
    即"人工录入不构成该供应商真实单据的证据"（这是刻意的，见 `_judge_vendor`）。

    **局限如实说明（live 实测到的形态）**：`doc_form` 与无原图矛盾这一条**区分不了**
    「测试污染的伪造单据」与「真实历史单据但原图已丢失」（live 实例：`德利行 Tak Lee Hong`
    73 张里 28 张属此类，它的真身是原图丢失而非夹具）。故本判据只在**供应商级**使用
    （要求"名下**全部**证据都是测试形态"才硬拒），并对 mixed 的供应商保留 pass ——
    把单张单据判死会误伤真实历史数据。
    """
    p = str(image_path or "").strip()
    form = str(doc_form or "").strip()
    if any(m in p for m in _TEST_IMAGE_PATH_MARKERS):
        return True
    if p:
        return False
    return form in ALLOWED_DOC_FORMS


def _judge_vendor(name, ev, min_evidence):
    """单个供应商 → (分类, 理由短码, 理由全文)。三层准则与 why 见 `_classify_suppliers`。"""
    name = str(name or "")
    lowered = name.lower()
    marker = next((m for m in HARD_REJECT_NAME_MARKERS if m in name), "")
    if marker:
        return (VENDOR_HARD_REJECT, _HR_NAME_MARKER,
                "名字命中夹具/流程标记「%s」：这类词/后缀只出现在测试夹具或直通审批这类"
                "流程用的占位供应商名里，不是真实经营主体" % marker)
    m = _PAREN_NUMERIC_ID.search(name)
    if m:
        return (VENDOR_HARD_REJECT, _HR_NUMERIC_ID,
                "名字含括号内纯数字 id「%s」：括号里的数字是记录 id 不是名字，属夹具/占位命名"
                % m.group(0))
    total, real = int(ev.get("total", 0)), int(ev.get("real", 0))
    if total == 0:
        return (VENDOR_HARD_REJECT, _HR_NO_EVIDENCE,
                "名下没有任何单据，无从确认是真实经营主体，也不可能有可推导的事实")
    if real == 0:
        return (VENDOR_HARD_REJECT, _HR_NO_EVIDENCE,
                "名下 %d 条单据全部是测试形态（原图落测试临时目录 / 声称扫描版式却无原图）"
                "或人工录入（无原图），没有任何非测试形态的真实单据证据" % total)
    reasons = []
    hit = next((m for m in SUSPECT_BUYER_NAME_MARKERS if m in lowered), "")
    if hit:
        reasons.append("名字像买方（命中「%s」）—— 供应商与买方在名字上并不总能区分，需人审确认"
                       % hit)
    if real < min_evidence:
        reasons.append("真实单据证据仅 %d 条（< 阈值 %d）—— 少样本上的占比不构成先验"
                       % (real, min_evidence))
    if reasons:
        return (VENDOR_SUSPECT, _SUSPECT_BUYER_NAME if hit else _SUSPECT_FEW_EVIDENCE,
                "；".join(reasons))
    return (VENDOR_PASS, "", "真实单据证据 %d 条，名字无夹具/流程/买方标记" % real)


def _classify_suppliers(conn, names, min_evidence=None):
    """给**每一个**供应商打质量门标签（纯读）。返回 (分类表, 门报告, 条目索引)。

    三层准则（逐条写死；报告只复述这三层，不另行给结论）：

    ① **hard-reject**（直接不产出，逐条计入丢弃原因）—— 命中任一即判：
       a) 名字命中显式夹具/流程标记（`HARD_REJECT_NAME_MARKERS`，如含 `测试`/`直通审批`/
          `已入账`/`已核对`/`已解析`）；
       b) 名字含**括号内纯数字 id**（`_PAREN_NUMERIC_ID`，如 `7-ELEVEN HK (1542)`）；
       c) 名下**没有任何非测试形态的真实单据证据**（判据见 `_is_test_shaped_receipt`）。
       局限如实说明：c) 的判据是"原图 + doc_form 自洽"，它把两类数据都算作"无真实证据"——
       ① 只有人工录入单据的供应商（刻意如此：人工录入凭据太弱，不足以支撑一条供应商级先验）；
       ② 真实历史单据但原图已丢失的供应商。两类都**只在"全部证据都是测试形态"时才硬拒**，
       所以原图部分丢失、仍留有真实原图的供应商不会被误伤（live 实例：`德利行` 73 张里 28 张
       属②，仍有 11 张真实原图 → pass）。
    ② **suspect**（产出但打标，报告单列供人审）—— 命中任一即判：
       a) 名字像买方（`SUSPECT_BUYER_NAME_MARKERS`，连锁便利店/餐厅名）；
       b) 真实单据证据 < `min_evidence`（缺省 `SUSPECT_MIN_EVIDENCE=3`）。
    ③ **pass** —— 以上都不命中。

    why 分类要覆盖全部供应商（而不是只覆盖"有候选的"）：报告要能回答「live/pending 里到底
    有多少家是夹具主体」；只统计有候选的会在夹具恰好没产出候选时把它藏起来。
    why 用"真实单据证据数"作阈值主体（而非任务示例里的 layout 证据）：证据不足是**供应商级**
    性质 —— 名下真实单据少于 3 条时，版式 1/1、单位 1/1、价格 1 样本都会同时不成立。
    """
    min_evidence = max(1, int(min_evidence or SUSPECT_MIN_EVIDENCE))

    evidence = {}
    for tid, sid, image_path, doc_form in conn.execute(
            "SELECT ifnull(tenant_id,'default'), supplier_id, ifnull(image_path,''), "
            "       ifnull(doc_form,'') FROM receipts "
            "WHERE supplier_id IS NOT NULL AND ifnull(deleted_at,'') = ''"):
        slot = evidence.setdefault((str(tid), int(sid)),
                                   {"total": 0, "real": 0, "test": 0})
        slot["total"] += 1
        if _is_test_shaped_receipt(image_path, doc_form):
            slot["test"] += 1
        elif str(image_path or "").strip():
            slot["real"] += 1

    classified, entry_by_key = {}, {}
    buckets = {VENDOR_PASS: [], VENDOR_SUSPECT: [], VENDOR_HARD_REJECT: []}
    reason_counts = {VENDOR_PASS: {}, VENDOR_SUSPECT: {}, VENDOR_HARD_REJECT: {}}
    for (tid, sid), name in sorted(names.items()):
        ev = evidence.get((tid, sid), {"total": 0, "real": 0, "test": 0})
        cls, code, reason = _judge_vendor(name, ev, min_evidence)
        key = (str(tid), int(sid))
        entry = {"tenant_id": str(tid), "supplier_id": int(sid), "name": str(name or ""),
                 "reason_code": code, "reason": reason,
                 "total_receipts": ev["total"], "real_receipts": ev["real"],
                 "test_receipts": ev["test"], "dropped_candidates": 0}
        classified[key] = {"class": cls, "reason_code": code, "reason": reason}
        entry_by_key[key] = entry
        buckets[cls].append(entry)
        if cls != VENDOR_PASS:
            reason_counts[cls][code] = reason_counts[cls].get(code, 0) + 1

    gate = {
        "counts": {cls: len(buckets[cls]) for cls in
                   (VENDOR_PASS, VENDOR_SUSPECT, VENDOR_HARD_REJECT)},
        "suppliers": buckets,
        "reason_counts": reason_counts,
        "suspect_min_evidence": min_evidence,
        "dropped_candidates_total": 0,
        "criteria": _QUALITY_GATE_CRITERIA,
    }
    return classified, gate, entry_by_key


def _apply_vendor_quality_gate(candidates, classified, entry_by_key, rejects,
                               collector_discards):
    """按质量门过滤候选：hard-reject 不产出并**逐条计数**；suspect 打标放行；pass 原样。

    why 放在采集之后、`validate_fact` 之前：这样 hard-reject 的丢弃与其它丢弃共用同一张
    原因表（不会出现"某个采集器拒了但总表没有"的对不上），且计数落在该候选自己的 kind 上。
    why suspect 要放行（而不是同样丢弃）：它的候选是给人审用的 —— 打标后人审界面看得见理由，
    静默丢弃等于把"需要人判断的样本"直接抹掉。
    """
    kept, dropped = [], 0
    for c in candidates:
        key = (str(c["tenant_id"]), int(c["supplier_id"]))
        info = classified.get(key) or {}
        cls = info.get("class", VENDOR_PASS)
        if cls == VENDOR_HARD_REJECT:
            code = "quality:hard-reject %s" % info.get("reason_code", "未分类")
            collector_discards[c["kind"]] += 1
            _bump(rejects, code, info.get("reason", ""))
            entry = entry_by_key.get(key)
            if entry is not None:
                entry["dropped_candidates"] += 1
            dropped += 1
            continue
        # 只带 `suspect` 一个布尔（不另存 classification 字符串：那是只写不读的死字段，
        # 消费方是 plan() 的分栏/标记与 _gaining_scopes/_suspect_scopes 的分流）。
        c["suspect"] = (cls == VENDOR_SUSPECT)
        c["suspect_reason"] = info.get("reason", "") if cls == VENDOR_SUSPECT else ""
        kept.append(c)
    return kept, dropped


# -------------------------------------------------------------
# 主流程
# -------------------------------------------------------------
def collect(path, min_price_samples=None, suspect_min_evidence=None):
    """从历史数据推导四类候选（只读）。返回 (candidates, meta)。

    meta 含：各类采集条数、采集期按 kind 的丢弃数、采集期丢弃原因计数、版式分布、
    `quality_gate`（供应商三层分类：计数/各家名与理由/按理由计数），
    以及 `degenerate_scopes`（被退化过滤挡下的 (租户, 供应商) 集合，供分母预估的
    「过滤前」对照值使用）。

    候选在返回前已过供应商质量门：hard-reject 的候选被剔除（计入丢弃原因），suspect 的
    候选带 `suspect=True` + `suspect_reason` 放行。
    """
    min_samples = max(1, int(min_price_samples or MIN_PRICE_SAMPLES))
    conn = _connect(path)
    try:
        names = _load_supplier_names(conn)
        rejects = {}
        degenerate = set()
        collected_counts = {}
        collector_discards = {k: 0 for k in
                              (KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE)}
        candidates = []

        sub = {}
        rows = _collect_alias(conn, names, sub, degenerate)
        collector_discards[KIND_ALIAS] += _merge_rejects(rejects, sub)
        collected_counts[KIND_ALIAS] = len(rows)
        candidates.extend(rows)

        sub = {}
        rows = _collect_unit(conn, names, sub, degenerate)
        collector_discards[KIND_UNIT] += _merge_rejects(rejects, sub)
        collected_counts[KIND_UNIT] = len(rows)
        candidates.extend(rows)

        sub = {}
        rows, distribution = _collect_layout(conn, names, sub)
        collector_discards[KIND_LAYOUT] += _merge_rejects(rejects, sub)
        collected_counts[KIND_LAYOUT] = len(rows)
        candidates.extend(rows)

        sub = {}
        rows = _collect_price_range(conn, names, sub, min_samples)
        collector_discards[KIND_PRICE_RANGE] += _merge_rejects(rejects, sub)
        collected_counts[KIND_PRICE_RANGE] = len(rows)
        candidates.extend(rows)

        # 供应商质量门：分类全部供应商 → hard-reject 的候选在此剔除（逐条计数），
        # suspect 的候选打标后放行。放在四个采集器之后，使丢弃原因与按 kind 的丢弃数同源。
        classified, gate, entry_by_key = _classify_suppliers(
            conn, names, suspect_min_evidence)
        candidates, dropped = _apply_vendor_quality_gate(
            candidates, classified, entry_by_key, rejects, collector_discards)
        gate["dropped_candidates_total"] = dropped
    finally:
        conn.close()
    return candidates, {
        "collected": collected_counts,
        "collector_discards": collector_discards,
        "collector_rejects": rejects,
        "degenerate_scopes": degenerate,
        "layout_distribution": distribution,
        "quality_gate": gate,
    }


def _empty_discards():
    return {"reasons": {}, "total": 0}


def _count_discard(discards, code, text, n=1):
    slot = discards["reasons"].setdefault(code, {"count": 0, "text": text})
    slot["count"] += n
    discards["total"] += n


def plan(path=None, min_price_samples=None, existing_loader=None,
         suspect_min_evidence=None):
    """预演：推导候选 → 过 validate_fact → 过 decide_action → 汇总报告（**不写库**）。

    `existing_loader(tenant_id, supplier_id)` 可注入（测试用）；缺省走
    `db.list_vendor_memory` 的默认读口径（**只取 active 且未过期**的行）—— 被归档/被
    supersede 的旧行不该拦住一条新事实（它们已不参与注入）。

    `produced`（四类产出数）**只统计 pass 类**；suspect 类计入 `suspect_produced` 单独一栏，
    hard-reject 只进丢弃原因 —— 前两者都不进分母预估（口径见 `_PROJECTION_CRITERIA`）。
    """
    path = path or db.DB_PATH
    min_samples = max(1, int(min_price_samples or MIN_PRICE_SAMPLES))
    candidates, meta = collect(path, min_samples, suspect_min_evidence)

    if existing_loader is None:
        def existing_loader(tenant_id, supplier_id):
            return db.list_vendor_memory(tenant_id=tenant_id, supplier_id=supplier_id)

    existing_cache = {}
    discards = _empty_discards()
    kinds = (KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE)
    produced = {k: 0 for k in kinds}
    suspect_produced = {k: 0 for k in kinds}
    # 采集期拒掉的（样本不足 / 未锚定 / 写法为空…）先记入按 kind 的丢弃数，
    # 再把原因并入下面那张全局原因表 —— 两边必须能对得上。
    discarded = {k: meta["collector_discards"].get(k, 0) for k in kinds}
    suppliers_by_kind = {k: set() for k in kinds}
    suspect_suppliers_by_kind = {k: set() for k in kinds}
    enqueue_items = []
    out_candidates = []

    for c in candidates:
        kind = c["kind"]
        fact = MemoryFact(
            kind=kind, fact_key=c["fact_key"], value=c["value"],
            supplier_id=str(c["supplier_id"]), tenant_id=str(c["tenant_id"]),
            source_kind=SEED_SOURCE_KIND,
            source_ref=json.dumps([str(x) for x in c["source_receipt_ids"]]),
            written_by=WRITTEN_BY,
        )
        ok, reason = validate_fact(fact)
        if not ok:
            discarded[kind] += 1
            _count_discard(discards, "validate:" + _reason_code(reason), reason)
            out_candidates.append({"kind": kind, "fact_key": c["fact_key"],
                                   "supplier_id": c["supplier_id"],
                                   "suspect": bool(c.get("suspect")),
                                   "decision": "DISCARD", "reason": reason})
            continue
        cache_key = (str(c["tenant_id"]), int(c["supplier_id"]))
        if cache_key not in existing_cache:
            try:
                existing_cache[cache_key] = existing_loader(*cache_key)
            except Exception as e:  # 读既有事实失败：宁可保守也不误判
                existing_cache[cache_key] = []
                _count_discard(discards, "existing_load_failed",
                               "读取既有事实失败（%s: %s），本批按无既有事实处理"
                               % (type(e).__name__, e))
        result = decide_action(existing_cache[cache_key], fact)
        if result.action in (ACTION_ADD, ACTION_UPDATE):
            is_suspect = bool(c.get("suspect"))
            suspect_reason = c.get("suspect_reason") or ""
            content = c["content"]
            if is_suspect:
                # 人审界面读的就是 content；不前置标记就等于"默默入队"（本任务要修的形态）。
                suspect_produced[kind] += 1
                suspect_suppliers_by_kind[kind].add(cache_key)
                content = (_SUSPECT_CONTENT_PREFIX % suspect_reason) + content
            else:
                produced[kind] += 1
                suppliers_by_kind[kind].add(cache_key)
            enqueue_items.append({
                "vendor": c["supplier_name"], "content": content,
                "tenant_id": c["tenant_id"], "source_kind": SEED_SOURCE_KIND,
                "source_receipt_ids": c["source_receipt_ids"],
                "supplier_id": c["supplier_id"], "fact_kind": kind,
                "fact_key": c["fact_key"], "fact_value": c["value"],
                "suspect": is_suspect, "suspect_reason": suspect_reason,
            })
            out_candidates.append({
                "kind": kind, "fact_key": c["fact_key"], "value": c["value"],
                "supplier_id": c["supplier_id"], "supplier_name": c["supplier_name"],
                "decision": result.action, "reason": result.reason,
                "suspect": is_suspect, "suspect_reason": suspect_reason,
                "evidence": c["evidence"],
            })
        else:
            discarded[kind] += 1
            _count_discard(discards, "%s:%s" % (result.action, _reason_code(result.reason)),
                           result.reason)
            out_candidates.append({"kind": kind, "fact_key": c["fact_key"],
                                   "value": c["value"],
                                   "supplier_id": c["supplier_id"],
                                   "suspect": bool(c.get("suspect")),
                                   "decision": result.action, "reason": result.reason})

    # 采集期的丢弃（样本不足 / 未锚定 / 写法为空…）并入同一张原因表，不另立一份
    for code, slot in meta["collector_rejects"].items():
        _count_discard(discards, code, slot["text"], n=slot["count"])
    gaining = set()
    for s in suppliers_by_kind.values():
        gaining |= s
    degenerate_scopes = meta.get("degenerate_scopes") or set()

    return {
        "db_path": path,
        "min_price_samples": min_samples,
        "collected": meta["collected"],
        "produced": produced,
        # suspect 单独一栏：**不计入 produced、不计入分母预估**（见 _PROJECTION_CRITERIA）
        "suspect_produced": suspect_produced,
        "discarded": discarded,
        "discarded_total": discards["total"],
        "discard_reasons": {k: v["count"] for k, v in discards["reasons"].items()},
        "discard_reason_texts": {k: v["text"] for k, v in discards["reasons"].items()},
        "suppliers_by_kind": {k: len(v) for k, v in suppliers_by_kind.items()},
        "suspect_suppliers_by_kind": {k: len(v) for k, v in
                                      suspect_suppliers_by_kind.items()},
        "suppliers_gaining_facts": len(gaining),
        # 供应商质量门三层分类（计数 / 各家名与理由 / 按理由计数 / 判据自述）
        "quality_gate": meta["quality_gate"],
        # 被退化过滤挡下的 (租户, 供应商)：**只**服务 `_prefilter_gaining_scopes`
        # （分母预估的过滤前对照值）。消费方见 `_prefilter_gaining_scopes` 与
        # `_print_report`；不参与 `apply`（那里只认 enqueue_items）。
        "degenerate_scopes": sorted([t, s] for t, s in degenerate_scopes),
        "layout_distribution": meta["layout_distribution"],
        "candidates": out_candidates,
        "enqueue_items": enqueue_items,
    }


def apply(report):
    """把预演结果写进 pending 人审队列（幂等：同候选重跑新增 0 条）。返回计数。

    **只写 `pending_memory`**：规则级事实必须人审（双轨写入制），绝不落到 vendor_memory
    生效行。返回 {"new", "dedup", "failed"}。
    """
    new = dedup = failed = 0
    for it in report.get("enqueue_items") or []:
        try:
            _pid, is_new = db.enqueue_pending_memory(
                it["vendor"], it["content"], tenant_id=it["tenant_id"],
                source_kind=it["source_kind"],
                source_receipt_ids=it["source_receipt_ids"],
                supplier_id=it["supplier_id"], fact_kind=it["fact_kind"],
                fact_key=it["fact_key"], fact_value=it["fact_value"])
        except Exception as e:
            failed += 1
            print("  [warn] 入队失败 %s/%s: %s: %s"
                  % (it["fact_kind"], it["fact_key"], type(e).__name__, e))
            continue
        if is_new:
            new += 1
        else:
            dedup += 1
    return {"new": new, "dedup": dedup, "failed": failed}


def _simulate_eligible_after(path, gaining, memory_metrics_mod, before_eligible):
    """在临时副本上模拟「gaining 里的供应商各有一行可用记忆」，返回 after 的

    `eligible_denominator`。空集合直接返回 before（无需复制库）。异常上抛由调用方降级。
    """
    if not gaining:
        return before_eligible
    tmp = tempfile.mkdtemp(prefix="seed_facts_sim_")
    copy = os.path.join(tmp, "sim.db")
    try:
        _snapshot_sqlite(path, copy)
        c = sqlite3.connect(copy)
        try:
            c.executemany(
                "INSERT INTO vendor_memory (memory_id, vendor, notes, sample, tenant_id,"
                " supplier_id, status, decay_score, created_at, updated_at)"
                " SELECT ?, s.name, ?, '', ifnull(s.tenant_id,'default'), s.id, 'active',"
                " 1.0, datetime('now'), datetime('now') FROM suppliers s"
                " WHERE s.id = ? AND ifnull(s.tenant_id,'default') = ?",
                [("seed-sim-%s-%s" % (tid, int(sid)), "（种子化预演）", int(sid), str(tid))
                 for tid, sid in sorted(gaining)])
            c.commit()
        finally:
            c.close()
        return memory_metrics_mod.collect(copy)["memory_hit_effective"]["eligible_denominator"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def estimate_effective_denominator_delta(path, gaining, gaining_unfiltered=None,
                                         memory_metrics_mod=None):
    """预计「有效命中率分母」的增量（口径见 `_PROJECTION_CRITERIA`）。

    `gaining` 必须是**过滤后**的分母贡献者集合（退化项已剔除）—— 它是**报告口径**。
    `gaining_unfiltered` 可选；给出时另算一份「把退化项也算作候选」的对照值
    （字段 `after_eligible_unfiltered` / `delta_unfiltered` /
    `gaining_suppliers_unfiltered`）。两个数字**不可混读**：前者是报告口径，后者只是
    用来看退化项贡献了多少虚高。why 两个都要有：只报过滤后的数字，就看不出"过滤前虚高
    多少"；只报过滤前的数字，就是拿虚高当成绩。

    在**临时副本**上模拟"候选事实已批准生效"，再用 `memory_metrics` 的同一口径复算 ——
    不另写第二份 SQL（本仓纪律：同一口径只有一份实现）。任何异常都降级为
    `available=False` 并带上原因，绝不因为"算不出预计值"让整次预演失败。
    """
    try:
        if memory_metrics_mod is None:
            import memory_metrics as memory_metrics_mod
        before = memory_metrics_mod.collect(path)["memory_hit_effective"]
    except Exception as e:
        return {"available": False, "gaining_suppliers": len(gaining),
                "reason": "无法调用 memory_metrics 复算：%s: %s" % (type(e).__name__, e),
                "criteria": _PROJECTION_CRITERIA}
    before_eligible = before["eligible_denominator"]
    try:
        after = _simulate_eligible_after(path, gaining, memory_metrics_mod, before_eligible)
    except Exception as e:
        return {"available": False, "gaining_suppliers": len(gaining),
                "reason": "副本模拟失败：%s: %s" % (type(e).__name__, e),
                "criteria": _PROJECTION_CRITERIA}
    out = {
        "available": True,
        "before_eligible": before_eligible,
        "after_eligible": after,
        "delta": after - before_eligible,
        "gaining_suppliers": len(gaining),
        "criteria": _PROJECTION_CRITERIA,
    }
    if gaining_unfiltered is not None:
        try:
            after_un = _simulate_eligible_after(
                path, gaining_unfiltered, memory_metrics_mod, before_eligible)
            out["after_eligible_unfiltered"] = after_un
            out["delta_unfiltered"] = after_un - before_eligible
            out["gaining_suppliers_unfiltered"] = len(gaining_unfiltered)
        except Exception as e:
            out["unfiltered_error"] = "副本模拟失败：%s: %s" % (type(e).__name__, e)
    return out


# -------------------------------------------------------------
# CLI
# -------------------------------------------------------------
def _gaining_scopes(report):
    """从入队项还原"会新增事实"的 (tenant_id, supplier_id) 集合（**pass 类，报告口径**）。

    suspect 类候选虽然也入队（带标记供人审），但**不计入分母预估** —— 它们来自少样本或
    疑似买方的供应商，把它们算进去就是拿"无从确认的主体"虚高预估（见 `_suspect_scopes`）。
    """
    out = set()
    for it in report.get("enqueue_items") or []:
        if it.get("suspect"):
            continue
        out.add((str(it["tenant_id"]), int(it["supplier_id"])))
    return out


def _suspect_scopes(report):
    """入队项里 suspect 类的 (tenant_id, supplier_id) 集合（只作报告，不进分母预估）。"""
    out = set()
    for it in report.get("enqueue_items") or []:
        if it.get("suspect"):
            out.add((str(it["tenant_id"]), int(it["supplier_id"])))
    return out


def _prefilter_gaining_scopes(report):
    """「过滤前」对照的分母贡献者 = pass 类 ∪ 只有退化事实的**通过类**供应商 —— 仅作对照。

    判据：`enqueue_items` 里 pass 类的供应商 ∪ `degenerate_scopes`（剔除 hard-reject 与
    suspect 主体）。等价说法：**只把「退化项过滤」这一层撤销、其余各层保持不变**时的分母
    贡献者。why 是上界：退化项若未被过滤，会与普通候选一样参与模拟（结构上都是合法
    MemoryFact）；真实模拟由 `_simulate_eligible_after` 执行，档案不存在的供应商自然落空。
    why 要剔除 hard-reject 与 suspect：报告口径已声明「两类都不计入分母预估」，对照值若把它们
    掺进来，"退化项造成的虚高"就同时掺了三种来源（退化项 / 夹具主体 / 少样本主体），读者无法
    归因 —— 而且 hard-reject 的候选本来就被质量门丢弃，算进去等于在对比一个不会发生的场景。
    """
    out = _gaining_scopes(report)
    suppliers = (report.get("quality_gate") or {}).get("suppliers", {})
    excluded = set()
    for cls in (VENDOR_HARD_REJECT, VENDOR_SUSPECT):
        for e in suppliers.get(cls, []):
            excluded.add((str(e["tenant_id"]), int(e["supplier_id"])))
    for pair in report.get("degenerate_scopes") or []:
        key = (str(pair[0]), int(pair[1]))
        if key in excluded:
            continue
        out.add(key)
    return out


def _print_quality_gate(gate):
    """打印供应商质量门三层分类：每层家数 + 各家名与理由 + 按理由的计数。

    why 逐家列名与理由（而不是只给结论）：任务要求"不得只给结论不给依据"，且下一手是人审 ——
    人需要看到是**哪些**供应商被判可疑/硬拒，以及**凭什么**。
    """
    counts = gate.get("counts") or {}
    print("【供应商分类】hard-reject %d 家 / suspect %d 家 / pass %d 家"
          % (counts.get(VENDOR_HARD_REJECT, 0), counts.get(VENDOR_SUSPECT, 0),
             counts.get(VENDOR_PASS, 0)))
    for cls in (VENDOR_HARD_REJECT, VENDOR_SUSPECT, VENDOR_PASS):
        for e in gate.get("suppliers", {}).get(cls, []):
            detail = "%s（单据 %d / 真实证据 %d / 测试形态 %d）" % (
                e["reason"], e["total_receipts"], e["real_receipts"], e["test_receipts"])
            if cls == VENDOR_HARD_REJECT:
                detail += "；剔除候选 %d 条" % e["dropped_candidates"]
            print("  %-11s %-24s | %s" % (cls, e["name"], detail))
    rc = (gate.get("reason_counts") or {}).get(VENDOR_HARD_REJECT) or {}
    if rc:
        print("  hard-reject 按理由计数：%s"
              % "，".join("%s %d 家" % (k, v) for k, v in sorted(rc.items())))
    rc = (gate.get("reason_counts") or {}).get(VENDOR_SUSPECT) or {}
    if rc:
        print("  suspect 按理由计数   ：%s"
              % "，".join("%s %d 家" % (k, v) for k, v in sorted(rc.items())))
    print("  判据：%s" % gate.get("criteria"))
    print("")


def _print_report(report):
    print("DB_PATH            : %s" % report["db_path"])
    print("价格区间最小样本数 : %d" % report["min_price_samples"])
    print("可疑证据阈值       : < %d 条真实单据证据即标 suspect"
          % (report.get("quality_gate", {}).get("suspect_min_evidence",
                                                SUSPECT_MIN_EVIDENCE)))
    print("")
    gate = report.get("quality_gate") or {}
    if gate:
        _print_quality_gate(gate)
    print("【按 kind】产出(pass) / 采集 / 丢弃")
    for kind in (KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE):
        print("  %-12s 产出 %-5d 采集 %-5d 丢弃 %-5d 涉及供应商 %d"
              % (kind, report["produced"][kind], report["collected"][kind],
                 report["discarded"][kind], report["suppliers_by_kind"][kind]))
    print("")
    suspect = report.get("suspect_produced") or {}
    print("【suspect 单独一栏】候选 %d 条（**不计入上面的产出数与分母预估**）"
          % sum(suspect.values()))
    for kind in (KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE):
        print("  %-12s suspect 候选 %-5d 涉及供应商 %d"
              % (kind, suspect.get(kind, 0),
                 (report.get("suspect_suppliers_by_kind") or {}).get(kind, 0)))
    suspects = gate.get("suppliers", {}).get(VENDOR_SUSPECT, [])
    if suspects:
        print("  待审清单（名字 | 理由）：")
        for e in suspects:
            print("    %-24s | %s" % (e["name"], e["reason"]))
    print("")
    print("【丢弃原因计数】合计 %d 条（不得静默丢）" % report["discarded_total"])
    for code in sorted(report["discard_reasons"]):
        print("  %-34s %5d  | %s" % (code, report["discard_reasons"][code],
                                     report["discard_reason_texts"][code]))
    print("")
    if report.get("layout_distribution"):
        print("【doc_form 分布】")
        for key in sorted(report["layout_distribution"]):
            print("  %-14s %s" % (key, json.dumps(report["layout_distribution"][key],
                                                  ensure_ascii=False)))
        print("")
    proj = report.get("effective_denominator_projection") or {}
    print("【预计能让 effective 分母增加多少】")
    if proj.get("available"):
        print("  pass-only（报告口径）  : %s -> %s（预计 %+d），涉及供应商 %d"
              % (proj["before_eligible"], proj["after_eligible"], proj["delta"],
                 proj["gaining_suppliers"]))
        n_susp = len(report.get("suspect_scopes") or [])
        print("  suspect 已排除          : %d 家（其候选入 pending 但不进本预估）" % n_susp)
        if proj.get("delta_unfiltered") is not None:
            print("  过滤前（只撤销退化项过滤，对照）: %s -> %s（预计 %+d），涉及供应商 %d"
                  % (proj["before_eligible"], proj.get("after_eligible_unfiltered"),
                     proj["delta_unfiltered"], proj.get("gaining_suppliers_unfiltered")))
            print("  退化项造成的虚高        : %+d（对照 - 报告口径；硬拒与 suspect 两层都不掺）"
                  % (proj["delta_unfiltered"] - proj["delta"]))
        elif proj.get("unfiltered_error"):
            print("  过滤前对照不可用：%s" % proj["unfiltered_error"])
    else:
        print("  不可用：%s" % proj.get("reason"))
    print("  计算口径：%s" % proj.get("criteria"))
    print("")
    n_deg = len(report.get("degenerate_scopes") or [])
    print("【退化项过滤】被挡下的 (租户, 供应商) 数 %d（明细见上方丢弃原因计数）" % n_deg)
    print("")
    if report.get("apply_result"):
        r = report["apply_result"]
        print("【入队 pending（人审队列）】新增 %d / 去重 %d / 失败 %d"
              % (r["new"], r["dedup"], r["failed"]))
    else:
        print("【预演】待入队 %d 条（加 --apply 才落笔；只进 pending_memory，"
              "不写 vendor_memory 生效行）" % len(report.get("enqueue_items") or []))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="从历史数据推导四类结构化事实候选入人审队列（缺省预演，不写库）")
    ap.add_argument("--apply", action="store_true",
                    help="把候选写进 DB_PATH 库的 pending_memory（不写 vendor_memory）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--min-price-samples", type=int, default=MIN_PRICE_SAMPLES,
                    help="price_range 的最小样本数（缺省 %d，低于它不产出）"
                         % MIN_PRICE_SAMPLES)
    ap.add_argument("--suspect-min-evidence", type=int, default=SUSPECT_MIN_EVIDENCE,
                    help="供应商质量门的可疑阈值：真实单据证据 < 它即标 suspect"
                         "（缺省 %d，可配）" % SUSPECT_MIN_EVIDENCE)
    args = ap.parse_args(argv)

    report = plan(min_price_samples=args.min_price_samples,
                  suspect_min_evidence=args.suspect_min_evidence)
    report["mode"] = "apply" if args.apply else "dry-run"
    # 报告口径 = pass 类（过滤退化项 + 排除 suspect）；同时给「过滤前」对照值
    # （退化项若不过滤会贡献多少虚高）。
    report["suspect_scopes"] = sorted(
        [t, s] for t, s in _suspect_scopes(report))
    report["effective_denominator_projection"] = estimate_effective_denominator_delta(
        report["db_path"], _gaining_scopes(report),
        gaining_unfiltered=_prefilter_gaining_scopes(report))
    if args.apply:
        report["apply_result"] = apply(report)

    if args.json:
        printable = {k: v for k, v in report.items() if k != "enqueue_items"}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return 0
    _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
