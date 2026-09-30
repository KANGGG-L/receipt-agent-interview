# -*- coding: utf-8 -*-
"""D3：供应商×SKU 别名巡检器的**生产触发方**回归测试（P6·X2 报告 mid + 计划 §10.21）。

why：`demo/scripts/check_vendor_sku_alias.py` 早已实现四类别名异常的判定（死链 /
同键多条 active / canonical 与目标 SKU 归一后不一致 / 供应商缺失），也已被脚本自身的
CLI 覆盖 —— 但它此前**零生产触发方**，属本仓高发的「定义了没接线」：工具写好了却没人跑，
巡检结果永远不会被任何人看到。它与 R4（`memory_consistency`）完全同族：R4 已接启动自检
+ admin 端点，而**同一批交付的别名巡检器没接**。

本文件钉住它的消费方 `GET /api/admin/vendor-sku-alias-consistency`：
1. 端点结构：clean 时 verdict=clean + findings_total=0 + 四类计数均为 0；
2. 判别式：构造一个被污染的临时库 → 四类异常各自点名到具体 alias_id / reason / 组；
3. 复用同一实现（哨兵注入）：端点必须**原样透出脚本的实现**，不允许在此重写巡检 SQL；
   并静态断言端点函数体里没有 SELECT（结构上不可能出现第二份判定）。
4. 权限：匿名 401；staff / owner 403；admin 200。
5. 失败不抛 500：巡检异常 → 200 + status=error + report=None。
6. 只读：临时库 md5 不变；live 库与两个 live 向量库指纹不变。

反证口径（交付说明给实测记录与三个 md5，不写进用例）：把端点里的
`mod.check_vendor_sku_alias(...)` 换成返回硬编码报告（或改回在端点里自己拼 SQL）→
第 2、3 条必须失败。

隔离：本模块 DB_PATH / RAG_DIR 全部指向临时目录（首次 import app.* 之前设置），
零网络、零 live 库写入、零 live 向量库写入。
"""

import hashlib
import importlib
import inspect
import os
import sqlite3
import sys
import tempfile
import uuid

import pytest

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="vendor_sku_alias_api_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_alias_api.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_LIVE_RAG_DIRS = [os.path.join(_DEMO_DIR, ".rag_chroma"),
                  os.path.join(_REPO_ROOT, ".rag_chroma")]
_URL = "/api/admin/vendor-sku-alias-consistency"


# -------------------------------------------------------------
# 环境与取数辅助
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。

    `importlib.reload(db)` 会重跑模块级 `_make_engine()`，从而按新 DB_PATH 建表
    （真实 schema：skus / suppliers / vendor_sku_alias 都在），再往库里写种子数据。
    端点读 `db.DB_PATH`，因此跟随本次 reload 的路径。
    """
    os.environ["DB_PATH"] = os.path.join(_TMP, "alias_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _seed(skus=(), suppliers=(), rows=()):
    """直接写三张表（不经过业务写入口，避免顺带触发别的机制）。

    rows 元素＝(alias_id, tenant_id, supplier_id, sku_id, raw_name, canonical_name,
                status, valid_to)
    """
    conn = sqlite3.connect(db.DB_PATH)
    try:
        if skus:
            conn.executemany(
                "INSERT INTO skus (id, name, active, tenant_id) VALUES (?, ?, ?, ?)",
                list(skus))
        if suppliers:
            conn.executemany(
                "INSERT INTO suppliers (id, name, active, tenant_id) VALUES (?, ?, ?, ?)",
                list(suppliers))
        if rows:
            conn.executemany(
                "INSERT INTO vendor_sku_alias (id, tenant_id, supplier_id, sku_id,"
                " raw_name, canonical_name, status, valid_to)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(rows))
        conn.commit()
    finally:
        conn.close()


def _get(client, **params):
    return client.get(_URL, headers={"X-Role": "admin"}, params=params or None)


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _rag_fingerprint():
    """live 向量库指纹：目录不存在记 ABSENT，否则记 (文件数, sqlite md5)。"""
    out = []
    for d in _LIVE_RAG_DIRS:
        if not os.path.isdir(d):
            out.append((d, "ABSENT"))
            continue
        files = []
        for root, _dirs, names in os.walk(d):
            for n in sorted(names):
                files.append(os.path.join(root, n))
        sqlite = os.path.join(d, "chroma.sqlite3")
        digest = _md5(sqlite) if os.path.isfile(sqlite) else "-"
        out.append((d, (len(files), digest)))
    return out


# -------------------------------------------------------------
# 1. 干净库：verdict=clean + 四类计数全 0
# -------------------------------------------------------------
def test_endpoint_reports_clean_with_counts():
    """干净的别名表（唯一键、目标 SKU 存在且同名归一）→ 一致且四类计数为 0。"""
    _fresh_db()
    _seed(skus=[(1, "本地牛腩", 1, "default")],
          suppliers=[(10, "祥興凍肉", 1, "default")],
          rows=[(1, "default", 10, 1, "本吧", "本地牛腩", "active", "")])

    body = _get(_client()).json()
    assert body["status"] == "success", body
    report = body["report"]
    assert report is not None, body

    # 「是否一致」的判据就是 verdict（clean = 一致）
    assert report["verdict"] == "clean", report
    assert report["findings_total"] == 0, report
    c = report["checks"]
    assert c["dangling_sku"]["count"] == 0, c
    assert c["duplicate_active_key"]["count"] == 0, c
    assert c["canonical_mismatch"]["count"] == 0, c
    assert c["missing_supplier"]["count"] == 0, c
    # 计数口径也要在（前端/运维据此显示「N 行 active、0 处异常」）
    assert report["rows"]["total"] == 1 and report["rows"]["active"] == 1, report
    assert report["table_present"] is True, report


# -------------------------------------------------------------
# 2. 判别式：被污染的临时库 → 四类异常各自点名
# -------------------------------------------------------------
def test_endpoint_reports_pollution_with_specific_fields():
    """四类异常同时存在 → 每一类都要点名到具体 alias_id / reason / 组。

    why：只报「有异常」而不点名具体行的巡检没有可操作性；本用例把四类各自的
    字段钉死（漏掉任何一类的实现都会失败）。

    种子说明：第 ② 类（同键多条 active）只能用 `supplier_id IS NULL` 造 ——
    `ux_vendor_sku_alias_key` 唯一索引把 NULL 视作互不相同（这正是脚本注释里
    「本类最容易漏的形态」）。这两行因此同时命中第 ④ 类（null_supplier），
    属预期：两类的覆盖面不同，脚本对④按全部行统计。
    """
    _fresh_db()
    _seed(skus=[(1, "本地牛腩", 1, "default"), (2, "急凍牛肋條", 1, "default")],
          suppliers=[(10, "祥興凍肉", 1, "default")],
          rows=[
              (1, "default", 10, 999, "死鏈寫法", "死鏈寫法", "active", ""),      # ①
              (2, "default", None, 1, "未錨定寫法", "本地牛腩", "active", ""),    # ④
              (3, "default", None, 1, "重複寫法", "本地牛腩", "active", ""),      # ②④
              (4, "default", None, 1, "重複寫法", "本地牛腩", "active", ""),      # ②④
              (5, "default", 10, 1, "別名寫法", "急凍牛肋條", "active", ""),      # ③
          ])

    body = _get(_client()).json()
    assert body["status"] == "success", body
    report = body["report"]
    assert report["verdict"] == "polluted", report

    c = report["checks"]
    # ① 死链：目标 SKU 不存在
    assert c["dangling_sku"]["count"] == 1, c["dangling_sku"]
    assert c["dangling_sku"]["by_reason"] == {"missing_sku": 1}, c["dangling_sku"]
    assert c["dangling_sku"]["items"][0]["alias_id"] == 1, c["dangling_sku"]
    assert c["dangling_sku"]["items"][0]["reason"] == "missing_sku", c["dangling_sku"]
    # ② 同键多条 active（唯一口径被破坏）
    assert c["duplicate_active_key"]["count"] == 1, c["duplicate_active_key"]
    grp = c["duplicate_active_key"]["groups"][0]
    assert grp["active_rows"] == 2 and grp["alias_ids"] == [3, 4], grp
    assert grp["raw_name"] == "重複寫法" and grp["supplier_id"] is None, grp
    # ③ canonical 与目标 SKU 归一后不一致，且三角判据指到另一个 SKU
    assert c["canonical_mismatch"]["count"] == 1, c["canonical_mismatch"]
    assert c["canonical_mismatch"]["suspect_flipped_pointer"] == 1, c["canonical_mismatch"]
    item = c["canonical_mismatch"]["items"][0]
    assert item["alias_id"] == 5 and item["sku_name"] == "本地牛腩", item
    assert item["canonical_name"] == "急凍牛肋條", item
    assert item["other_sku_ids"] == [2] and item["suspect_flipped_pointer"] is True, item
    # ④ 供应商缺失（alias 2/3/4 的 supplier_id 为 NULL）
    assert c["missing_supplier"]["count"] == 3, c["missing_supplier"]
    assert c["missing_supplier"]["by_reason"] == {"null_supplier": 3}, c["missing_supplier"]
    # 合计 = 四类之和，且 > 0
    assert report["findings_total"] == 6, report


# -------------------------------------------------------------
# 3. 复用同一实现（哨兵注入 + 结构断言）
# -------------------------------------------------------------
def test_endpoint_surfaces_script_output_not_hardcoded(monkeypatch):
    """把脚本的巡检函数换成返回**哨兵报告** → 端点必须原样透出该报告。

    why：这是「定义了没接线 / 同一根因多份实现」的守卫。若有人把端点改成返回硬编码
    结论、或在端点里自己拼一份巡检 SQL（不再调脚本的 `check_vendor_sku_alias`），
    本用例立刻失败 —— 且静态断言那一条也会失败（见下一个用例）。
    """
    _fresh_db()
    from app import api_admin

    mod = api_admin._load_vendor_sku_alias_check_module()
    sentinel = {"db_path": "/哨兵/别名库.db", "verdict": "polluted",
                "verdict_text": "哨兵", "findings_total": 42, "checks": {}}
    seen = {}

    def _fake(*args, **kwargs):
        seen.update(kwargs)
        return sentinel

    monkeypatch.setattr(mod, "check_vendor_sku_alias", _fake)

    body = _get(_client()).json()
    assert body["status"] == "success", body
    assert body["report"] == sentinel, body
    # 端点不得在请求里打开回填预演（它的沙箱会临时改写 DB_PATH/RAG_DIR 环境变量，
    # 且归一实现是进程级缓存 —— 不适合常驻服务进程逐请求触发）
    assert seen.get("simulate_backfill") is False, seen
    # 目标库必须是「应用当前使用的库」，不是硬编码的仓库默认库
    assert seen.get("db_path") == db.DB_PATH, seen


def test_endpoint_has_no_rewritten_check_sql():
    """静态断言：端点函数体里不得出现任何 SELECT / FROM（即不得重写巡检 SQL）。

    why：本仓①类高发缺陷就是「同一根因多份实现」；端点若自己写 SQL，两处判定分叉后
    管理台结论与 CLI/CI 结论不一致且无人察觉。这条把「必须复用脚本实现」变成 CI 红。
    """
    from app import api_admin

    src = inspect.getsource(api_admin.vendor_sku_alias_consistency)
    upper = src.upper()
    assert "SELECT" not in upper, "端点里出现了 SELECT —— 必须复用脚本实现"
    assert " FROM " not in upper, "端点里出现了 FROM —— 必须复用脚本实现"
    assert "check_vendor_sku_alias(" in src, "端点必须调用脚本的巡检实现"


def test_endpoint_and_cli_loader_point_to_the_same_script():
    """端点加载的模块就是仓库里那份巡检脚本（唯一实现），不是另一份拷贝。"""
    from app import api_admin

    mod = api_admin._load_vendor_sku_alias_check_module()
    assert os.path.abspath(mod.__file__) == os.path.join(
        _DEMO_DIR, "scripts", "check_vendor_sku_alias.py"), mod.__file__
    assert hasattr(mod, "check_vendor_sku_alias"), dir(mod)
    # 缓存：重复调用返回同一模块对象（避免每次请求都重新 exec）
    assert api_admin._load_vendor_sku_alias_check_module() is mod


# -------------------------------------------------------------
# 4. 权限：仅 admin
# -------------------------------------------------------------
def test_only_admin_can_read():
    _fresh_db()
    client = _client()

    anon = client.get(_URL)
    assert anon.status_code == 401, anon.text

    for role in ("staff", "owner"):
        ret = client.get(_URL, headers={"X-Role": role})
        assert ret.status_code == 403, (role, ret.status_code, ret.text)

    ok = client.get(_URL, headers={"X-Role": "admin"})
    assert ok.status_code == 200, ok.text


# -------------------------------------------------------------
# 5. 失败不抛 500：巡检异常 → 200 + status=error + report=None
# -------------------------------------------------------------
def test_check_failure_returns_error_field_not_500(monkeypatch):
    _fresh_db()
    from app import api_admin

    mod = api_admin._load_vendor_sku_alias_check_module()

    def _boom(*_a, **_k):
        raise RuntimeError("模拟巡检不可用")

    monkeypatch.setattr(mod, "check_vendor_sku_alias", _boom)
    ret = _get(_client())
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "error", body
    assert "msg" in body and body["msg"], body
    # report=None 是「不得显示一致」的契约（调用方据此显示错误态而非「一致」）
    assert body["report"] is None, body


# -------------------------------------------------------------
# 6. 只读：临时库 md5 不变 + live 库/向量库指纹不变
# -------------------------------------------------------------
def test_endpoint_is_read_only():
    _fresh_db()
    _seed(skus=[(1, "本地牛腩", 1, "default")],
          suppliers=[(10, "祥興凍肉", 1, "default")],
          rows=[(1, "default", 10, 1, "本吧", "本地牛腩", "active", "")])
    client = _client()
    _get(client)                                    # 预热（含首次加载脚本与归一实现）

    before_live = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    before_tmp = _md5(db.DB_PATH)
    before_rag = _rag_fingerprint()

    for _ in range(3):
        assert _get(client).status_code == 200

    assert _md5(db.DB_PATH) == before_tmp, "巡检写了自己的库（应为 mode=ro 只读）"
    live_after = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    assert live_after == before_live, "端点动了 live 库"
    assert _rag_fingerprint() == before_rag, "端点动了 live 向量库"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
