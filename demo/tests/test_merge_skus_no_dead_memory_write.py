# -*- coding: utf-8 -*-
"""K4 / M8 处置：SKU 合并端点不得再写「全局品类库」死记忆，且别名表确实接替了它。

背景（why 需要这一批）：
`POST /api/inventory/skus/merge` 原先在合并成功后额外调一次
`ingest_memory("全局品类库", "SKU同义合并映射：…")` 反哺 VendorMemory。该写入是
**死数据**，本批按证据判死并删除（结论与三条证据见
`app/api_inventory.py::merge_skus_endpoint` 的 docstring）：

1. 召回门不可达 —— 检索侧 `rag._vendor_related` 按供应商名判定相关性，实测对任何
   真实供应商名都返回 False（本文件 `test_vendor_related_gate_rejects_global_vendor`
   把这条实测固化成断言）；
2. 无消费方 —— 全仓读 `vendor_memory` 的只有 `rag.retrieve_context` 与
   `db.list_vendor_memory`；SKU 匹配链路（`_match_sku` → `find_vendor_sku_alias` /
   `find_sku_by_name` / `list_skus`）**从不读 vendor_memory**，所以即便把它接进识别
   prompt 也不会改变任何一次 SKU 匹配；
3. 租户错位 —— 该调用未传 `tenant_id`（恒落 default 桶），与读侧「租户必填、读写同
   口径」相矛盾。

接替者（谁承担了这件事）：`vendor_sku_alias` 表（P2-T2）。`db.merge_skus` 内部调
`db._repoint_vendor_sku_alias`（db.py:2305）把原先指向副 SKU 的别名全部改指到主
SKU，消费方是 `db.find_vendor_sku_alias`（`_match_sku` 的第一优先通道）。

覆盖（每条都标明它是判别式还是口径守卫，以及反证靶点）：
1. **判别式（本任务的主反证靶点）**：合并端点调用前后 `vendor_memory` 行数不变且为
   0 —— 把那条 `ingest_memory(...)` 加回去，本用例必须失败；
2. 接替者生效：别名改指到主 SKU、副 SKU 停用、`find_vendor_sku_alias` 返回主 SKU；
3. **自校准（证明第 2 条不恒真）**：别名留在被停用的副 SKU 上时，读取侧判为死链返回
   None —— 即 `_repoint_vendor_sku_alias` 一旦失效，第 2 条必然失败；
4. **契约守卫**：`SkuMergeBody` 不再声明只控制死写入的 `sync_vendor_memory` 空开关
   （留着就是"定义了没接线"）；旧前端缓存仍可携带该多余字段（Pydantic 忽略），且带
   字段时同样不写记忆。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="merge_no_dead_memory_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_merge.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from fastapi.testclient import TestClient  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.api_inventory import SkuMergeBody  # noqa: E402
from app.main import app  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

client = TestClient(app)

_TENANT = "default"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与 P1/P2 系列用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"merge_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _mk_supplier(name, tenant_id=_TENANT):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    assert sid is not None
    return int(sid)


def _mk_sku(name, base_unit="", tenant_id=_TENANT):
    sid, err = db.create_sku(name, base_unit=base_unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err} ({name})"
    assert sid is not None
    return int(sid)


def _owner_headers():
    return {"X-Role": "owner", "X-Email": "boss@demo.hk"}


def _scalar(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        row = conn.execute(stmt, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _vendor_memory_count():
    return int(_scalar("select count(*) from vendor_memory") or 0)


def _post_merge(primary, secondaries, extra=None):
    payload = {"primary_sku_id": primary, "secondary_sku_ids": secondaries}
    if extra:
        payload.update(extra)
    return client.post("/api/inventory/skus/merge", json=payload,
                       headers=_owner_headers())


# -------------------------------------------------------------
# 1. 判别式：合并端点不得写任何 vendor_memory 行（本任务主反证靶点）
# -------------------------------------------------------------
def test_merge_endpoint_writes_no_vendor_memory_row():
    """把 `ingest_memory("全局品类库", ...)` 加回端点 → 本用例必须失败。"""
    _fresh_db()
    sup = _mk_supplier("辛供應商")
    primary = _mk_sku("本地牛腩")
    secondary = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, secondary, "本吧", tenant_id=_TENANT)

    # 前置断言（防"绿得没有意义"）：记忆总开关若被关掉，本用例会因为开关而非因为
    # 删除死写入而通过 —— 先证明开关是开的，判定才指向真实原因。
    assert rag.memory_enabled() is True, "记忆总开关被关掉，本用例的判定不成立"

    before = _vendor_memory_count()
    resp = _post_merge(primary, [secondary])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success", body

    # 判别式：合并前后 vendor_memory 行数不变，且不存在「全局品类库」这类假 vendor
    assert _vendor_memory_count() == before, (
        "合并端点又往 vendor_memory 写行了（死写入回归）："
        f"{before} -> {_vendor_memory_count()}")
    assert before == 0
    assert int(_scalar(
        "select count(*) from vendor_memory where vendor = ?", ("全局品类库",)) or 0) == 0


# -------------------------------------------------------------
# 2. 接替者生效：别名改指到主 SKU（消费方 find_vendor_sku_alias → _match_sku）
# -------------------------------------------------------------
def test_merge_repoints_alias_to_primary_and_deactivates_secondary():
    _fresh_db()
    sup = _mk_supplier("辛供應商")
    primary = _mk_sku("本地牛腩")
    secondary = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, secondary, "本吧", tenant_id=_TENANT)
    assert db.find_vendor_sku_alias(
        "本吧", tenant_id=_TENANT, supplier_id=sup).id == secondary

    resp = _post_merge(primary, [secondary])
    assert resp.json()["status"] == "success"

    # 别名已改指到主 SKU：这是「死写入删除后由谁接替」的证据
    hit = db.find_vendor_sku_alias("本吧", tenant_id=_TENANT, supplier_id=sup)
    assert hit is not None, "别名丢失（既没写记忆也没留在别名表）"
    assert hit.id == primary, f"别名未改指到主 SKU：{hit.id} != {primary}"
    # 副 SKU 已停用、流水归集到主 SKU
    assert (db.get_sku(secondary, tenant_id=_TENANT).active or 0) == 0
    assert resp.json()["data"]["alias_repointed"] == 1


# -------------------------------------------------------------
# 3. 自校准：证明第 2 条不恒真（别名留在停用副 SKU 上 → 读取侧判死链）
# -------------------------------------------------------------
def test_selfcalibration_alias_on_deactivated_sku_reads_as_dead_link():
    """把 `_repoint_vendor_sku_alias` 失效（别名留在副 SKU 上）时的形态复现一遍。

    这里手工构造"副 SKU 停用但别名未改指"的状态：若读取侧对这种死链仍返回 SKU，
    第 2 条断言就是恒真、没有任何判别力。实测返回 None → 第 2 条有判别力。
    """
    _fresh_db()
    sup = _mk_supplier("辛供應商")
    secondary = _mk_sku("本地牛腩_1787140411")
    db.upsert_vendor_sku_alias(sup, secondary, "本吧", tenant_id=_TENANT)
    # 只停用、不改指（等价于 hook C 未生效）
    db.update_sku(secondary, tenant_id=_TENANT, active=0)

    assert db.find_vendor_sku_alias(
        "本吧", tenant_id=_TENANT, supplier_id=sup) is None, (
        "指向已停用 SKU 的别名被当成有效命中 —— 第 2 条断言（别名必须指向主 SKU）"
        "将失去判别力")


# -------------------------------------------------------------
# 4. 契约守卫：死开关不得复活；旧前端多余字段不得 422
# -------------------------------------------------------------
def test_merge_body_drops_dead_sync_switch_but_tolerates_old_client_field():
    # 口径守卫：`sync_vendor_memory` 只控制那条死写入（别名改指在 db.merge_skus 内
    # 无条件执行），删除后若只删调用保留形参，就成了"定义了没接线"的空开关。
    assert "sync_vendor_memory" not in SkuMergeBody.model_fields, (
        "只控制死写入的空开关又回来了；要么接线到真实行为，要么一并删除")

    # 兼容性：旧前端缓存仍会发送该字段 —— Pydantic 默认忽略多余字段，不得 422，
    # 且带字段时不写记忆（同一判别的另一种入参形态）。
    _fresh_db()
    primary = _mk_sku("本地牛腩")
    secondary = _mk_sku("本地牛腩_1787140411")
    resp = _post_merge(primary, [secondary], extra={"sync_vendor_memory": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"
    assert _vendor_memory_count() == 0


# -------------------------------------------------------------
# 5. 证据 1 固化：召回门对「全局品类库」这个假 vendor 恒不放行
# -------------------------------------------------------------
def test_vendor_related_gate_rejects_global_vendor():
    _fresh_db()
    for real_vendor in ("德利行 Tak Lee Hong", "聯記號", "大生"):
        assert rag._vendor_related(real_vendor, "全局品类库") is False, (
            f"{real_vendor} 竟然能召回「全局品类库」的记忆？召回门判定变了，"
            "本任务的判死依据需要重新评估")
    # 唯一的例外：查询名自身含"全局"（真实单据的供应商名不可能长这样）
    assert rag._vendor_related("全局品类库", "全局品类库") is True
