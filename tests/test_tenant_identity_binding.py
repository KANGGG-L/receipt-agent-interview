# -*- coding: utf-8 -*-
"""D19 回归守卫：生产态租户必须由身份派生，客户端声明一律无效。

背景（独立 QA 对抗复核实测）：各 api_*.py 自己实现的 _tenant_id(request) 一律读客户端
X-Tenant-Id，与 Bearer 身份无关。生产态（AUTH_ENABLED=1）下于是出现：
  - owner token + X-Tenant-Id: tenantB → 读到 tenantB 的付款；
  - staff token + X-Tenant-Id: tenantB → 读/写 tenantB 的单据与消耗。
即上一批做的「租户隔离」只是知识型约束（知道 tenant 名即可越租），不构成安全边界。
本批次把租户来源收敛到 app.auth.resolve_tenant 这一份实现：生产态只取身份
（token 载荷 / request.state.account），开发/演示态维持 X-Tenant-Id 通道不变。

覆盖（全部离线：临时库 + 打桩识别管线，零外部调用、零活体服务）：
1. 生产态 owner token(租户 A) + X-Tenant-Id: tenantB → 只见 A 的付款与单据列表，
   B 独有单据详情 404（越租读被挡住）。
2. 生产态 staff token + X-Tenant-Id: tenantB → 越租读不到 B 的单据；越租上传不被承认
   （落库租户仍是身份租户）；越租反馈（header 与 body 同时谎报他租）同样落到身份租户，
   且不得在他租留下任何反馈行。
3. 生产态不带 X-Tenant-Id → 租户等于 token 载荷里的租户（用非 default 租户账号验证，
   排除「只是回落成 default」的假通过）。
4. 开发态（AUTH_ENABLED=0）X-Tenant-Id 仍生效（演示通道不能被改坏）。
5. 例外路径回归：GET /api/receipt/{id}/image 与 GET /api/payment/voucher/{name} 带正确
   签名的 URL 在**两种模式**下都能取到图，且签名仍绑定租户（浏览器原生 <img>/<a> 带不了
   请求头，其租户来自 query 参数并由 HMAC 绑定，不得改成要求身份）。

写法约束：全局 AUTH_ENABLED 由 tests/conftest.py 锁为 0，验证生产态一律 monkeypatch
app.auth 的判定函数（不改全局开关，不影响其余用例离线全绿）。

不写 live 库：conftest 的 autouse 夹具把 DB 与图片落盘目录重定向到 tmp_path；本文件
另注入两个非 default 租户账号（只在用例内 monkeypatch，不动预置三账号）。
"""

import os
import sys

import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import api_finance, api_receipts, auth, db
from app.main import app
from app.services import receipt_utils

TENANT_A = "tenant_a"
TENANT_B = "tenant_b"

OWNER_A_EMAIL = "boss@tenant-a.hk"
STAFF_A_EMAIL = "staff@tenant-a.hk"
PW_A = "tenant-a-pw"


def _request(headers=None):
    """构造最小 Starlette Request（用于直接断言 resolve_tenant 的解析结果）。"""
    from starlette.requests import Request
    raw = [(str(k).lower().encode(), str(v).encode())
           for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": raw, "query_string": b""})


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def accounts(monkeypatch):
    """注入「租户 A」的 owner / staff 两个账号。

    why 不直接用预置三账号：它们的租户都是 default，无法区分「租户来自 token 载荷」与
    「租户回落成 default」；注入非 default 租户账号后断言才有区分度。
    """
    monkeypatch.setitem(auth.ACCOUNTS, OWNER_A_EMAIL, (PW_A, "owner", TENANT_A))
    monkeypatch.setitem(auth.ACCOUNTS, STAFF_A_EMAIL, (PW_A, "staff", TENANT_A))
    return auth.ACCOUNTS


def _enable_production(monkeypatch):
    """切到生产态语义：把 app.auth.auth_enabled 判定函数改为 True。

    同时补一个非默认密钥，使「生产态该有的环境」自洽（弱密钥 + 生产态会被
    enforce_production_secret 拒绝启动）；签名逻辑不受影响（_TOKEN_SECRET 在导入时定死）。
    """
    monkeypatch.setenv("DEMO_TOKEN_SECRET", "tenant-binding-test-" + "x" * 32)
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)


def _token(email, role, tenant):
    return auth.issue_token(email, role, tenant)


def _bearer(token):
    return {"Authorization": "Bearer " + token}


def _mk_receipt(vendor, tenant_id):
    return db.create_receipt(supplier_name=vendor, status="parsed", tenant_id=tenant_id)


def _mk_payment(amount, tenant_id, paid_at="2026-01-01"):
    return db.create_payment(supplier_id=1, amount=amount, method="cash",
                             paid_at=paid_at, notes="", tenant_id=tenant_id)


def _stub_pipeline(monkeypatch):
    """打桩识别管线：上传端点只验证「落库租户」这一读点，不触发外部模型调用。"""
    from app.chains import supervisor
    monkeypatch.setattr(supervisor, "run_pipeline",
                        lambda image_path, **kw: {"data": None,
                                                  "last_error": "mock: pipeline stubbed"})


# -------------------------------------------------------------
# 1. 生产态：owner 令牌 + 他租声明 → 只见自己的付款与单据
# -------------------------------------------------------------
def test_production_owner_token_ignores_declared_tenant(client, accounts, monkeypatch):
    _enable_production(monkeypatch)
    r_a = _mk_receipt("供應商A", TENANT_A)
    r_b = _mk_receipt("供應商B", TENANT_B)
    p_a = _mk_payment(10.0, TENANT_A)
    p_b = _mk_payment(20.0, TENANT_B, paid_at="2026-01-02")

    headers = dict(_bearer(_token(OWNER_A_EMAIL, "owner", TENANT_A)),
                   **{"X-Tenant-Id": TENANT_B})

    # 单据列表：A 可见、B 独有数据不可见
    resp = client.get("/api/receipts", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["data"]}
    assert r_a in ids, "本租户单据必须可见"
    assert r_b not in ids, "生产态不得凭 X-Tenant-Id 读到 tenantB 的单据列表"

    # B 独有单据详情：404（等价于不存在）
    assert client.get("/api/receipt/%d" % r_b, headers=headers).status_code == 404
    assert client.get("/api/receipt/%d" % r_a, headers=headers).status_code == 200

    # 付款列表（owner 权限）：A 可见、B 不可见（这是 QA 实测的越权读点）
    pays = client.get("/api/payments", headers=headers)
    assert pays.status_code == 200, pays.text
    pay_ids = {p["id"] for p in pays.json()["data"]}
    assert p_a in pay_ids, "本租户付款必须可见"
    assert p_b not in pay_ids, "生产态不得凭 X-Tenant-Id 读到 tenantB 的付款"


# -------------------------------------------------------------
# 2. 生产态：staff 令牌越租读 / 上传 / 反馈均不被承认
# -------------------------------------------------------------
def test_production_staff_token_cannot_cross_tenant_read(client, accounts, monkeypatch):
    _enable_production(monkeypatch)
    r_a = _mk_receipt("供應商A", TENANT_A)
    r_b = _mk_receipt("供應商B", TENANT_B)

    headers = dict(_bearer(_token(STAFF_A_EMAIL, "staff", TENANT_A)),
                   **{"X-Tenant-Id": TENANT_B})
    resp = client.get("/api/receipts", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["data"]}
    assert r_a in ids and r_b not in ids, "staff 令牌不得借 X-Tenant-Id 越租读单据"
    assert client.get("/api/receipt/%d" % r_b, headers=headers).status_code == 404


def test_production_staff_upload_lands_in_identity_tenant(client, accounts, monkeypatch):
    """越租上传不被承认：落库租户 = 身份租户，而非请求头声明的租户。"""
    _enable_production(monkeypatch)
    _stub_pipeline(monkeypatch)

    # W-SEC-3：/api/upload 按**内容**校验上传件，必须用真 JPEG（伪字节会被 400 拒）
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (48, 32), (30, 120, 200)).save(buf, format="JPEG", quality=90)

    headers = dict(_bearer(_token(STAFF_A_EMAIL, "staff", TENANT_A)),
                   **{"X-Tenant-Id": TENANT_B})
    resp = client.post(
        "/api/upload", headers=headers,
        files={"receipt": ("tenant_binding.jpg", buf.getvalue(), "image/jpeg")},
        data={"async_": "true", "force": "true"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued", resp.json()

    new_rid = int(resp.json()["receipt_id"])
    row = db.get_receipt_row(new_rid)
    assert row is not None, "上传入口必须落库单据行"
    assert row.tenant_id == TENANT_A, \
        "越租上传必须落到身份租户（tenant_a），不得采信 X-Tenant-Id: tenant_b"
    assert db.get_receipt_row(new_rid, tenant_id=TENANT_B) is None, \
        "该单据不得在 tenant_b 口径下可见"

    # 反向确认：身份租户列表可见、他租列表不可见
    ids_a = {r["id"] for r in client.get(
        "/api/receipts", headers=_bearer(_token(STAFF_A_EMAIL, "staff", TENANT_A))
    ).json()["data"]}
    assert new_rid in ids_a


def test_production_staff_feedback_cannot_be_written_to_other_tenant(client, accounts,
                                                                    monkeypatch):
    """反馈落库租户：header 与 body 同时谎报他租都无效；他租单据写反馈被拒（404）。"""
    _enable_production(monkeypatch)
    r_a = _mk_receipt("供應商A", TENANT_A)
    r_b = _mk_receipt("供應商B", TENANT_B)

    headers = dict(_bearer(_token(STAFF_A_EMAIL, "staff", TENANT_A)),
                   **{"X-Tenant-Id": TENANT_B})

    ok = client.post(
        "/api/receipt/%d/feedback" % r_a, headers=headers,
        json={"like": 1, "comment": "", "item_index": None, "tenant_id": TENANT_B})
    assert ok.status_code == 200, ok.text
    assert ok.json()["feedback"]["tenant_id"] == TENANT_A, \
        "反馈必须落到身份租户，客户端 header/body 声明不得改写归属"

    denied = client.post(
        "/api/receipt/%d/feedback" % r_b, headers=headers,
        json={"like": 1, "comment": "", "item_index": None, "tenant_id": TENANT_B})
    assert denied.status_code == 404, "他租单据在身份租户口径下不存在，写反馈必须被拒"
    assert all(f["receipt_id"] != r_b for f in db.list_receipt_feedbacks(tenant_id=TENANT_B)), \
        "越租写不得在 tenant_b 留下任何反馈行"


# -------------------------------------------------------------
# 3. 生产态：不带 X-Tenant-Id 时租户 == token 载荷里的租户
# -------------------------------------------------------------
def test_production_tenant_comes_from_token_without_header(client, accounts, monkeypatch):
    _enable_production(monkeypatch)
    r_a = _mk_receipt("供應商A", TENANT_A)
    r_b = _mk_receipt("供應商B", TENANT_B)

    token = _token(OWNER_A_EMAIL, "owner", TENANT_A)
    resp = client.get("/api/receipts", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["data"]}
    assert r_a in ids and r_b not in ids

    # 直接断言解析结果：非 default 租户，排除「回落成 default」的假通过
    assert auth.resolve_tenant(_request(_bearer(token))) == TENANT_A
    # 身份缺失（无令牌）→ default（受保护端点在到达租户解析前已 401，这里只钉住余量路径）
    assert auth.resolve_tenant(_request({"X-Tenant-Id": TENANT_B})) == "default"


# -------------------------------------------------------------
# 4. 开发态：X-Tenant-Id 仍然生效（不把演示改坏）
# -------------------------------------------------------------
def test_dev_mode_declared_tenant_still_effective(client, accounts):
    assert auth.auth_enabled() is False, "本套件应处于开发态（AUTH_ENABLED=0）"
    r_a = _mk_receipt("供應商A", TENANT_A)
    r_b = _mk_receipt("供應商B", TENANT_B)

    ids_a = {r["id"] for r in client.get(
        "/api/receipts", headers={"X-Role": "staff", "X-Tenant-Id": TENANT_A}
    ).json()["data"]}
    assert r_a in ids_a and r_b not in ids_a
    ids_b = {r["id"] for r in client.get(
        "/api/receipts", headers={"X-Role": "staff", "X-Tenant-Id": TENANT_B}
    ).json()["data"]}
    assert r_b in ids_b and r_a not in ids_b

    # 租户解析口径：开发态读 X-Tenant-Id（缺省 default），不因本轮引入身份派生而漂移
    assert auth.resolve_tenant(_request({"X-Tenant-Id": TENANT_B})) == TENANT_B
    assert auth.resolve_tenant(_request({})) == "default"
    # 开发态 + 有效令牌：租户仍由头声明（既有免密演示通道语义）
    token = _token(STAFF_A_EMAIL, "staff", TENANT_A)
    assert auth.resolve_tenant(_request(dict(_bearer(token),
                                             **{"X-Tenant-Id": TENANT_B}))) == TENANT_B


# -------------------------------------------------------------
# 5. 例外路径：签名取图端点不要求身份，两种模式下都能出图
# -------------------------------------------------------------
@pytest.mark.parametrize("mode", ["dev", "production"], indirect=True)
def test_signed_media_urls_work_without_identity_in_both_modes(client, accounts,
                                                               monkeypatch, mode):
    """浏览器原生 <img>/<a> 带不了请求头 → 这两条端点靠 query 里的 HMAC 签名取图。

    生产态也**不得**改成要求身份，否则演示取图全挂；同时签名必须仍然绑定租户
    （换租户参数即签名失配 403），例外路径不得退化成「任取他租图片」。
    """
    if mode == "production":
        _enable_production(monkeypatch)

    # --- 原图：/api/receipt/{id}/image ---
    img_name = "signed_%s.jpg" % mode
    img_bytes = b"JPEG-BYTES-" + mode.encode()
    (api_receipts.UPLOAD_DIR / img_name).write_bytes(img_bytes)
    rid = db.create_receipt(supplier_name="簽名測試", status="parsed",
                            tenant_id="default")
    db.update_receipt(rid, image_path="uploads/" + img_name)
    img_url = receipt_utils.public_image_url(rid, "uploads/" + img_name, "default")
    assert "sig=" in img_url

    resp = client.get(img_url)          # 刻意不带任何身份头
    assert resp.status_code == 200, resp.text
    assert resp.content == img_bytes
    # 签名绑定租户：把租户换成他租 → 403
    assert client.get(
        img_url.replace("&sig=", "&tenant_id=%s&sig=" % TENANT_B)).status_code == 403

    # --- 付款凭证：/api/payment/voucher/{name} ---
    voucher_name = "signed_%s_voucher.jpg" % mode
    voucher_bytes = b"VOUCHER-" + mode.encode()
    (api_finance.VOUCHER_DIR / voucher_name).write_bytes(voucher_bytes)
    # 凭证端点的归属校验要求本租户确有付款行引用该凭证
    db.create_payment(supplier_id=1, amount=2.0, method="cash",
                      paid_at="2026-01-03", notes="",
                      voucher_image_path=voucher_name, tenant_id="default")
    voucher_url = api_finance.public_voucher_url(voucher_name, "default")
    assert "sig=" in voucher_url
    vresp = client.get(voucher_url)     # 同样不带任何身份头
    assert vresp.status_code == 200, vresp.text
    assert vresp.content == voucher_bytes
    # 签名绑定租户：换成他租 → 403
    assert client.get(
        voucher_url.replace("&sig=", "&tenant_id=%s&sig=" % TENANT_B)
    ).status_code == 403


@pytest.fixture()
def mode(request):
    """参数化夹具：dev = 本套件默认（AUTH_ENABLED=0）；production 由用例内 _enable_production 切换。"""
    return request.param
