# -*- coding: utf-8 -*-
"""R12：给 `sweep_parsing_timeout` 接生产触发方（本仓②类「定义了没接线」）。

背景（实测）：`receipt_utils.sweep_parsing_timeout` 早就实现了 Gate-2（把超期卡在
`parsing` 的单据强制置 `error(parsing_timeout)`），但全仓 `grep` **只有它自己的定义** ——
零调用方，所以这条清扫从未跑过，超期单据状态永久假「在途」（live 曾积压 7 张、26–33 天）。
本文件钉住三件事：

1. **两个触发方都真的会跑**：`app/main.py::_sweep_stale_parsing_startup`（启动自检）与
   `GET /api/admin/parsing-stale`（只读巡检端点）；
2. **只识别超期行**（边界：刚 `parsing` 的不得误判），且阈值由 settings 键
   `parsing_timeout_seconds` 驱动（改阈值必须改变分类结果）；
3. **两侧复用同一识别口径**：端点不得重写扫描 SQL（本仓①类「同一根因多份实现」）。

判别力（反证形状，均为「替换唯一实现 / 停止调用」的突变体式，不往仓库文件打补丁）：
- `test_startup_sweep_flips_stale_parsing_row` 走**启动钩子本身** —— 把
  `_sweep_stale_parsing_startup` 里那次 `ru.sweep_parsing_timeout()` 调用注掉，
  该用例必须失败（它断言的是「自检会扫到它」）。
- `test_endpoint_delegates_to_single_scan_implementation` 替换
  `receipt_utils.stale_parsing_query` 后端点结果必须随之改变 —— 若端点自己写了一份
  扫描 SQL，替换不会有效果。
- `test_self_calibration_widened_criterion_flags_fresh_row` 把阈值口径改宽（任何
  `parsing` 都算超期）后，边界用例的断言必须翻面 —— 否则那条边界断言是恒真的装饰。

环境隔离：模块级把 DB_PATH / RAG_DIR 指向临时目录（**必须在首次 import app.* 之前** ——
`import app.main` 会在 import 期执行 db._make_engine() 的迁移块、直接对目标库做 ALTER）；
用例级再把 db.DB_PATH 切到本轮 tmp_path 的独立库。全程不启 uvicorn、不发网络请求。
"""

import datetime as dt
import json
import os
import sys
import tempfile

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="parsing_timeout_sweep_")
os.environ["DB_PATH"] = os.path.join(_TMP, "wiring.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.main as main  # noqa: E402
import app.services.receipt_utils as ru  # noqa: E402
from app import db  # noqa: E402

ADMIN = {"X-Role": "admin"}

# 「很久以前」的时刻：远超任何合理阈值（缺省 300 秒）
_LONG_AGO = "2020-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    """每个用例一个干净的库（模块级 DB_PATH 是共享语义，必须按用例切到 tmp_path）。"""
    old = db.DB_PATH
    path = str(tmp_path / "r12.db")
    monkeypatch.setenv("DB_PATH", path)
    db.DB_PATH = path
    db._make_engine()
    try:
        yield
    finally:
        db.DB_PATH = old
        db._make_engine()


@pytest.fixture
def client():
    """带 lifespan 的客户端（startup 在非默认库下不会写共享语料，见 main.py 的自愈闸门）。"""
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


# -------------------------------------------------------------
# 夹具与小工具
# -------------------------------------------------------------
def _mk_parsing(created_at=None, tenant_id="default"):
    """建一条 parsing 单据；created_at 缺省为「现在」（即刚刚开始解析）。"""
    created_at = created_at or dt.datetime.now().isoformat(timespec="seconds")
    return db.create_receipt_record(status="parsing", supplier_name="",
                                    created_at=created_at, updated_at=created_at,
                                    tenant_id=tenant_id)


def _status(rid):
    return db.get_receipt_row(rid).status


def _stale_ids(**kw):
    return [r["id"] for r in ru.list_stale_parsing_receipts(**kw)]


class _EmptyQuery:
    """`stale_parsing_query` 的替身：模拟「识别口径被换掉」以测端点是否真委托它。"""

    def all(self):
        return []


# -------------------------------------------------------------
# 1(a). 启动自检：把超期 parsing 行置 error(parsing_timeout)
# -------------------------------------------------------------
def test_startup_sweep_flips_stale_parsing_row():
    """判别式：本用例走**启动钩子本身**。

    反证（突变体式，不改仓库文件）：注掉 `_sweep_stale_parsing_startup` 里那次
    `ru.sweep_parsing_timeout()` 调用 → 行仍停在 parsing → 本用例失败。
    """
    rid = _mk_parsing(created_at=_LONG_AGO)
    assert _status(rid) == "parsing", "前置：造出来的行必须是 parsing"

    main._sweep_stale_parsing_startup()

    row = db.get_receipt_row(rid)
    assert row.status == "error", "启动自检必须把超期 parsing 行置为 error"
    logs = json.loads(row.audit_logs_json or "[]")
    assert any(l.get("reason") == "parsing_timeout" and l.get("new") == "error"
               for l in logs), \
        "置 error 的同时必须留 parsing_timeout 原因的审计痕迹（供前端标红区分）"


# -------------------------------------------------------------
# 1(b). 边界：刚刚 parsing 的行不得被判超期（阈值上下分类相反）
# -------------------------------------------------------------
def test_startup_sweep_leaves_fresh_parsing_row():
    """边界用例：created_at = 现在 的行晚于 cutoff，必须原样保留。

    这条同时是 `test_self_calibration_widened_criterion_flags_fresh_row` 的对照面。
    """
    rid = _mk_parsing()  # created_at = now
    main._sweep_stale_parsing_startup()
    assert _status(rid) == "parsing", \
        "刚上传/刚开始解析的单据不得被启动自检误置 error"


def test_sweep_does_not_touch_non_parsing_or_deleted_rows():
    """幂等边界：非 parsing 状态、以及已软删的 parsing 行都不在清扫范围内。"""
    done = db.create_receipt_record(status="parsed", created_at=_LONG_AGO,
                                    updated_at=_LONG_AGO)
    soft_deleted = db.create_receipt_record(status="parsing", created_at=_LONG_AGO,
                                            updated_at=_LONG_AGO, tenant_id="default")
    db.soft_delete_receipt(soft_deleted)

    swept = ru.sweep_parsing_timeout()

    assert done not in swept and soft_deleted not in swept, swept
    assert _status(done) == "parsed"
    assert _status(soft_deleted) == "parsing", "已软删行不得被清扫（deleted_at 过滤）"


# -------------------------------------------------------------
# 2. 只读端点：列出的是「sweep 会动哪些行」，且 GET 无副作用
# -------------------------------------------------------------
def test_admin_endpoint_lists_stale_rows_readonly(client):
    stale = _mk_parsing(created_at=_LONG_AGO)
    fresh = _mk_parsing()

    resp = client.get("/api/admin/parsing-stale", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success", body
    ids = [r["id"] for r in body["rows"]]
    assert ids == [stale], "端点必须只列超期行（实得 %s）" % ids
    assert body["count"] == 1
    assert body["timeout_seconds"] == 300, "返回体必须暴露当前生效阈值，便于与 sweep 对账"
    assert fresh not in ids

    # GET 必须无副作用：端点跑完两侧都还是 parsing（写动作只能由显式 POST 承担）
    assert _status(stale) == "parsing" and _status(fresh) == "parsing", \
        "只读端点不得改动任何单据状态"


def test_admin_endpoint_tenant_filter_narrows_only(client):
    """tenant 给值时只收窄（不改识别口径）；缺省看全租户，与 sweep 作用域一致。"""
    a = _mk_parsing(created_at=_LONG_AGO, tenant_id="default")
    b = _mk_parsing(created_at=_LONG_AGO, tenant_id="t2")

    all_ids = [r["id"] for r in
               client.get("/api/admin/parsing-stale", headers=ADMIN).json()["rows"]]
    assert sorted(all_ids) == sorted([a, b])

    t2_ids = [r["id"] for r in
              client.get("/api/admin/parsing-stale?tenant=t2", headers=ADMIN).json()["rows"]]
    assert t2_ids == [b]


# -------------------------------------------------------------
# 3. 单一实现：端点必须委托 receipt_utils 的识别口径
# -------------------------------------------------------------
def test_endpoint_delegates_to_single_scan_implementation(client, monkeypatch):
    """硬判据（替换唯一实现 → 端点结果必须随之改变）。

    why：若端点在 `api_admin.py` 里重写了一份扫描 SQL，替换 `stale_parsing_query`
    不会产生任何效果 —— 那正是本仓①类「同一根因多份实现，只改一份」。
    """
    stale = _mk_parsing(created_at=_LONG_AGO)

    ids = [r["id"] for r in
           client.get("/api/admin/parsing-stale", headers=ADMIN).json()["rows"]]
    assert ids == [stale], "前置：默认口径下端点能列到该超期行"

    monkeypatch.setattr(ru, "stale_parsing_query", lambda *a, **k: _EmptyQuery())
    body = client.get("/api/admin/parsing-stale", headers=ADMIN).json()
    assert body["status"] == "success"
    assert body["count"] == 0 and body["rows"] == [], \
        "端点必须委托 receipt_utils.stale_parsing_query；结果没跟着变说明它自己写了一份 SQL"


def test_sweep_and_listing_share_one_scan_oracle(monkeypatch):
    """写侧与读侧同源：替换唯一口径后，`sweep_parsing_timeout` 也必须跟着改变。"""
    rid = _mk_parsing(created_at=_LONG_AGO)
    assert ru.sweep_parsing_timeout() == [rid], "前置：默认口径下写侧能扫到该行"

    rid2 = _mk_parsing(created_at=_LONG_AGO)
    monkeypatch.setattr(ru, "stale_parsing_query", lambda *a, **k: _EmptyQuery())
    assert ru.sweep_parsing_timeout() == [], \
        "写侧必须委托同一口径；结果没变说明它自己写了第二份扫描 SQL"
    assert _status(rid2) == "parsing"


# -------------------------------------------------------------
# 4. 阈值可配（settings 键），且真的驱动分类
# -------------------------------------------------------------
def test_timeout_threshold_reads_settings_key():
    """阈值与执行时机是两件事：本条只测**阈值**（缺省 300、可配、非正值钳回缺省）。

    执行时机的变更（清扫由「从不执行」变为「每次服务启动执行」，属有意的行为变更）
    由 `test_execution_timing_is_pinned_and_wording_is_accurate` 单独盯住。
    """
    assert ru.parsing_timeout_seconds() == 300, \
        "阈值缺省必须与接线前硬编码值一致（这一项未变；变的是执行时机：清扫此前零调用方、" \
        "现在由启动自检在每次服务启动时执行）"

    db.set_app_setting(ru.PARSING_TIMEOUT_SETTINGS_KEY, "50")
    assert ru.parsing_timeout_seconds() == 50

    for bad in ("0", "-1", ""):
        db.set_app_setting(ru.PARSING_TIMEOUT_SETTINGS_KEY, bad)
        assert ru.parsing_timeout_seconds() == 300, \
            "非正阈值必须钳回缺省（否则 cutoff 落到未来、把在跑的解析误判为超期）"


def test_threshold_boundary_drives_classification():
    """边界：100 秒前创建的行，缺省阈值（300s）下不算超期，阈值调到 50s 后必须算超期。

    这条证明 settings 键**真的被识别口径消费**（而不是只登记不接线）。
    """
    created = (dt.datetime.now() - dt.timedelta(seconds=100)).isoformat(timespec="seconds")
    rid = _mk_parsing(created_at=created)

    assert rid not in _stale_ids(), "100s < 300s：不得判为超期"
    db.set_app_setting(ru.PARSING_TIMEOUT_SETTINGS_KEY, "50")
    assert rid in _stale_ids(), "100s > 50s：阈值调小后必须判为超期（口径未被消费则不会变）"


# -------------------------------------------------------------
# 5. 自校准：口径改宽后，边界用例的断言必须翻面
# -------------------------------------------------------------
def test_self_calibration_widened_criterion_flags_fresh_row(monkeypatch):
    """把识别口径改宽（任何 parsing 都算超期）→ 1(b) 的断言必须失败。

    why：若 `_stale_ids` 退化成恒返空清单（口径坏了），1(b)「fresh 不在清单里」
    会**恒真通过**。这里用「阈值 = -1 ⇒ cutoff 落到未来」把口径改宽，要求同一个
    `_stale_ids` 立刻把 fresh 行报出来 —— 做不到就说明 1(b) 没有判别力。
    """
    fresh = _mk_parsing()
    assert fresh not in _stale_ids(), "前置：口径正常时 fresh 行不得被识别"

    monkeypatch.setattr(ru, "parsing_timeout_seconds", lambda: -1)
    assert fresh in _stale_ids(), \
        "口径改宽后 fresh 行必须被识别 —— 否则 1(b) 是恒真断言（识别逻辑坏了也照样过）"


# -------------------------------------------------------------
# 6. 启动自检容错：DB 不可用不得阻断启动
# -------------------------------------------------------------
def test_startup_sweep_does_not_break_when_db_unavailable(monkeypatch, caplog):
    """3(c)：清扫抛异常时启动钩子只 warning，绝不冒泡（否则服务起不来）。"""
    def _boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ru, "sweep_parsing_timeout", _boom)

    import logging
    with caplog.at_level(logging.WARNING, logger="startup"):
        main._sweep_stale_parsing_startup()  # 不得抛异常（抛了本用例直接 error）

    assert any("parsing_sweep" in (rec.message % rec.args if rec.args else rec.message)
               for rec in caplog.records), \
        "清扫失败必须留下可检索的 warning，而不是静默吞掉（③类：异常被静默吞掉）"


# -------------------------------------------------------------
# 7. 接线证据：生产调用点确实存在（非定义处）
# -------------------------------------------------------------
def test_sweep_has_production_callers_outside_its_definition():
    """`grep -rn sweep_parsing_timeout demo/app` 必须出现非定义处的生产调用点。

    why：本任务的根因就是「零调用方」。用源码级判据把它钉住 —— 只要有人删掉两个
    触发方之一，这里就会失败（比只跑行为用例更早暴露）。
    """
    app_dir = os.path.join(_DEMO_DIR, "app")
    call_sites = []
    for root, _dirs, files in os.walk(app_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if "sweep_parsing_timeout" in line and "def sweep_parsing_timeout" not in line:
                        call_sites.append("%s:%d" % (os.path.relpath(path, _DEMO_DIR), lineno))
    assert call_sites, "sweep_parsing_timeout 又变成零调用方了（本任务要修的正是这个）"
    assert any("main.py" in s for s in call_sites), \
        "启动自检触发方缺失：%s" % call_sites


# -------------------------------------------------------------
# 8. 口径守卫：阈值「未变」与执行时机「变更」必须分开陈述
# -------------------------------------------------------------
# 误导词用拼接构造，避免守卫文件自身成为命中源（防自杀）。
_MISLEADING_WORD = "行为零" + "变化"


def _comment_block_before(lines, anchor_substr):
    """取锚点行**紧邻上方**的连续注释块。

    why 只取紧邻注释块而不是整文件：本仓别处（vendor_precheck / memory_extract 等开关）
    的「与接线前一致」表述是准确的，整文件扫描会误伤。
    """
    idx = None
    for i, line in enumerate(lines):
        if anchor_substr in line:
            idx = i
            break
    assert idx is not None, "锚点未找到：%s（口径守卫失效，需同步更新锚点）" % anchor_substr
    block = []
    j = idx - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        block.append(lines[j])
        j -= 1
    return "".join(reversed(block))


def test_execution_timing_is_pinned_and_wording_is_accurate():
    """口径守卫：既盯行为（执行时机真的变了），也盯文案（不得再写成「无变化」）。

    why：P23 与 P29 两波独立复现过同一处不准确表述 —— 只写「无变化」会让人以为接线后
    系统行为完全没变，而实际上清扫的执行时机变了（且已实际生效）。

    ① 行为面：`_sweep_stale_parsing_startup` 必须注册在 FastAPI 的 startup 事件上 ——
       这正是「每次服务启动执行」这个新执行时机的载体（接线前该函数零调用方）。
    ② 文案面：parsing 超时阈值的注释必须**分开陈述**两件事 —— 阈值缺省 300 未变、
       执行时机从「从不执行」变为「每次服务启动执行」。

    判别力（反证）：注释改回含误导词的写法 → ② 失败；改成「执行时机未变」或删掉对
    执行时机的陈述 → ② 失败；把 startup 注册摘掉 → ① 失败。
    """
    # ① 行为面：startup 事件确实挂着清扫器（不启服务，直接读注册表）
    handlers = list(getattr(main.app.router, "on_startup", []) or [])
    assert main._sweep_stale_parsing_startup in handlers, \
        "启动自检必须注册在 startup 事件上，否则「每次服务启动执行」这一执行时机不成立"

    # ② 文案面：两处产品注释（阈值缺省与执行时机必须分开陈述）
    cases = [
        (os.path.join(_DEMO_DIR, "app", "services", "receipt_utils.py"),
         "PARSING_TIMEOUT_SECONDS_DEFAULT = 300"),
        (os.path.join(_DEMO_DIR, "app", "services", "settings_service.py"),
         '"parsing_timeout_seconds": 300'),
    ]
    for path, anchor in cases:
        with open(path, encoding="utf-8") as f:
            block = _comment_block_before(f.readlines(), anchor)
        name = os.path.basename(path)
        assert block, "%s: 锚点上方没有注释块（口径守卫失效）" % name
        assert _MISLEADING_WORD not in block, (
            "%s 的 parsing 超时阈值注释仍写着误导词：阈值缺省确实未变，但执行时机已从"
            "「从不执行」变为「每次服务启动执行」，属有意的行为变更" % name)
        assert "执行时机" in block and "启动" in block, (
            "%s 必须显式陈述执行时机的变更（从「从不执行」到「每次服务启动执行」），"
            "实得注释块：%s" % (name, block))
        assert "300" in block and "未变" in block, (
            "%s 必须显式陈述「阈值缺省 300 未变」（与执行时机分开陈述），实得注释块：%s"
            % (name, block))

    # ③ 本文件自身也不得复述该误导词（本文件只讲这一条清扫链，无其它同义表述）
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        self_src = f.read()
    assert _MISLEADING_WORD not in self_src, \
        "本测试文件里不得复述该误导词（口径守卫自身的文案也要与事实一致）"
