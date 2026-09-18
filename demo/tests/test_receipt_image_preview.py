# -*- coding: utf-8 -*-
"""单据原图预览测试：HEIC/TIFF 经端点按需转 JPEG，Web 格式逐字节透传。

覆盖计划「HEIC 原图可见」缺口的验收点：
- public_image_url 对非 Web 格式分流到 /api/receipt/{id}/image?v=<stem>&sig=<HMAC>
- 新端点不带任何身份头即可取图（<img src> 带不了请求头），返回真 JPEG
- W7 签名防枚举：正确签名 200 / 篡改签名 403 / 无签名旧式请求 403 /
  签名绑定单据 id 与租户（换 id 或换租户取不到图）
- 预览副本缓存复用、Web 格式透传零回归
- 业务防护：跨租户 / 文件缺失 / 转码失败 / 软删 / 路径穿越 均正确拒绝
- api_admin 灰测样本 image_url 回归守卫（修复悬空 404 后必须路由可达）
- W5 预览缓存孤儿治理：换图后旧缓存被清理、仍被引用的缓存（含软删单据的引用）保留、
  启动期兜底扫描只删无主 `*_web.jpg` 且绝不触碰源原图
- W6 健壮性小项：缓存命中改按 st_mtime_ns 纳秒比较（消除同秒内覆盖命中旧缓存的理论
  窗口）、SIGKILL 残留的自身临时文件被幂等兜底清理且不误删他方 `*.tmp`
- X6 兜底清理的节流改为「扫描成功后才登记」：目录枚举首次失败后下次调用仍会重试
- X7 HEIF 注册状态加锁：多线程并发首次调用只真正执行一次 register_heif_opener()
- Y3 URL 查询参数值 percent 编码：含 空格/&/#/非 ASCII 的 stem/tenant 不截断 URL，
  且签名仍基于未编码原值（编码前后签名不变、端点解回原值后照常验签出图）
- Y4 预览缓存权限：新落盘的 `<stem>_web.jpg` 为 0644（对齐既有上传件，保障异用户
  静态托管可读），且写路径不残留 `*.tmp`

不写 live 库：沿用 test_recovery_actions 的 isolated_db + tmp_path 模式。
"""

import io
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_image_preview_test.db")
if os.path.exists("/tmp/receipt_demo_image_preview_test.db"):
    os.remove("/tmp/receipt_demo_image_preview_test.db")

import pytest
from PIL import Image

# HEIC 源文件需真解码器；缺失则整模块 skip（不静默造假的 .heic）
pillow_heif = pytest.importorskip("pillow_heif")

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_image_preview.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def upload_dir(monkeypatch, tmp_path):
    """把上传落盘目录指到临时目录，避免污染仓库 uploads/。"""
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


# -------------------------------------------------------------
# 夹具：真 HEIC / 真 JPEG 源文件 + 对应单据
# -------------------------------------------------------------
def _heic_bytes(w=48, h=32, color=(200, 30, 30)):
    """用 pillow_heif 生成真 HEIC 字节（非改扩展名的假文件）。"""
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="HEIF")
    return buf.getvalue()


def _jpeg_bytes(w=48, h=32, color=(30, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes(w=48, h=32, color=(30, 200, 90)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _mk_receipt(upload_dir, name, data, tenant_id="default", status="parsed"):
    """在 uploads 目录落一个真文件并建对应单据，返回 (rid, 文件路径)。"""
    path = upload_dir / name
    path.write_bytes(data)
    rid = db.create_receipt(supplier_name="測試供應商", status=status,
                            tenant_id=tenant_id)
    db.update_receipt(rid, image_path="uploads/" + name)
    return rid, path


def _image_url(rid, name, tenant="default"):
    """按后端同一规则构造原图 URL（等价 public_image_url 的签名 + 分流结果）。"""
    from app.services.receipt_utils import public_image_url
    return public_image_url(rid, "uploads/" + name, tenant)


def _legacy_unsigned_url(rid, name):
    """W7 之前后端签发的旧式 URL（有 ?v= 但无 sig）→ 必须 403。"""
    return f"/api/receipt/{rid}/image?v={os.path.splitext(name)[0]}"


def _tampered_signature_url(rid, name, tenant="default"):
    """签名被篡改一位的 URL → 必须 403（验签失败，不得退化为放行）。"""
    url = _image_url(rid, name, tenant)
    pos = url.index("&sig=") + len("&sig=")
    old = url[pos]
    return url[:pos] + ("0" if old != "0" else "1") + url[pos + 1:]


def _manual_signed_url(rid, stem, tenant="default"):
    """手工按 stem 拼签名 URL：用于 image_path 非图片扩展名的越界用例。

    why: public_image_url 对非图片扩展名会回落 /uploads 静态地址，拿不到端点 URL；
    越界用例需要「签名有效但路径非法」这一组合，才能验证 404 而非 403。
    """
    from app.services.receipt_utils import preview_image_signature
    url = (f"/api/receipt/{rid}/image?v={stem}"
           f"&sig={preview_image_signature(rid, stem, tenant)}")
    if tenant != "default":
        url += f"&tenant_id={tenant}"
    return url


# -------------------------------------------------------------
# 1. URL 分流
# -------------------------------------------------------------
def test_heic_image_url_points_to_transcode_endpoint(client, upload_dir):
    """HEIC 单据的 image_url 指向按需转码端点（带 ?v=<stem> 与 &sig= 签名）。"""
    rid, _ = _mk_receipt(upload_dir, "sample.heic", _heic_bytes())
    body = client.get(f"/api/receipt/{rid}", headers={"X-Role": "staff"}).json()
    assert body["image_url"].startswith(f"/api/receipt/{rid}/image?v=sample&sig=")


def test_heic_image_url_signature_verifies(client, upload_dir):
    """后端签发的 URL 可原样通过验签（签名规则两侧一致，无自相矛盾）。"""
    from app.services.receipt_utils import (public_image_url,
                                            verify_preview_image_signature)
    rid, _ = _mk_receipt(upload_dir, "consistency.heic", _heic_bytes())
    url = public_image_url(rid, "uploads/consistency.heic", "default")
    sig = url.split("&sig=")[1].split("&")[0]
    assert verify_preview_image_signature(rid, "consistency", "default", sig) is True


def test_public_image_url_encodes_query_values():
    """Y3 回归：stem/tenant 含 空格/&/#/非 ASCII 时对参数值做 percent 编码。

    why: 不编码时 `&` 会把 query 从中间截断、`#` 会截断整个 URL；签名必须仍基于
    未编码原值（端点 query 解析自动解回原值），故编码只作用于 URL 字面量。
    """
    from app.services.receipt_utils import (public_image_url,
                                            preview_image_signature)
    url = public_image_url(7, "uploads/abc 12#34&x.heic", "team & co")
    assert "?v=abc%2012%2334%26x&sig=" in url
    assert url.endswith("&tenant_id=team%20%26%20co")
    assert " " not in url and "#" not in url
    # 签名基于未编码的 stem/tenant：编码前后签名值必须不变
    assert "&sig=" + preview_image_signature(7, "abc 12#34&x", "team & co") in url


def test_special_char_stem_round_trips_through_endpoint(client, upload_dir):
    """特殊字符文件名端到端：后端签发的编码 URL 原样 GET 仍 200 出图。"""
    from app.services.receipt_utils import public_image_url
    rid, _ = _mk_receipt(upload_dir, "sp ace &号.heic", _heic_bytes())
    url = public_image_url(rid, "uploads/sp ace &号.heic", "default")
    # query 里不得出现裸 &（除分隔符）与空格，否则 URL 会被截断/串参
    assert " " not in url
    assert url.split("?", 1)[1].split("&sig=")[0].count("&") == 0
    r = client.get(url)
    assert r.status_code == 200
    assert r.content[:3] == b"\xff\xd8\xff"


# -------------------------------------------------------------
# 2-3. 无身份头取图 + 真 JPEG
# -------------------------------------------------------------
def test_transcode_endpoint_anonymous_returns_jpeg(client, upload_dir):
    """端点不带任何身份头返回 200 + image/jpeg（<img src> 场景）。

    签名在 URL 里（query），因此「匿名 + 无请求头」与签名门槛可以共存。
    """
    rid, _ = _mk_receipt(upload_dir, "anon.heic", _heic_bytes())
    r = client.get(_image_url(rid, "anon.heic"))  # 刻意不传 X-Role / X-Tenant-Id
    assert r.status_code == 200, r.text
    assert "image/jpeg" in r.headers["content-type"]


def test_transcode_bytes_are_real_jpeg(client, upload_dir):
    """返回字节以 JPEG SOI(ffd8ff) 开头，且 Pillow 识别为 JPEG。"""
    rid, _ = _mk_receipt(upload_dir, "real.heic", _heic_bytes())
    r = client.get(_image_url(rid, "real.heic"))
    assert r.status_code == 200
    assert r.content[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.format == "JPEG"


# -------------------------------------------------------------
# W7 签名防枚举：正确签名 / 篡改签名 / 无签名旧式请求
# -------------------------------------------------------------
def test_tampered_signature_returns_403(client, upload_dir):
    """篡改任一签名位 → 403 IMAGE_SIGNATURE_INVALID，绝不因「格式对」而放行。"""
    rid, _ = _mk_receipt(upload_dir, "tamper.heic", _heic_bytes())
    r = client.get(_tampered_signature_url(rid, "tamper.heic"))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "IMAGE_SIGNATURE_INVALID"


def test_missing_signature_returns_403(client, upload_dir):
    """旧式未签名请求（W7 之前的 URL 形态）→ 403，堵住遍历 receipt_id 的扫描。"""
    rid, _ = _mk_receipt(upload_dir, "legacy.heic", _heic_bytes())
    r = client.get(_legacy_unsigned_url(rid, "legacy.heic"))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "IMAGE_SIGNATURE_INVALID"
    # 连 ?v= 都不带的裸路径同样拒绝
    assert client.get(f"/api/receipt/{rid}/image").status_code == 403


def test_signature_is_bound_to_receipt_id(client, upload_dir):
    """把 A 单据的合法签名套到 B 单据上 → 403（签名绑定 receipt_id）。"""
    rid_a, _ = _mk_receipt(upload_dir, "bind_a.heic", _heic_bytes())
    rid_b, _ = _mk_receipt(upload_dir, "bind_b.heic", _heic_bytes())
    sig_a = _image_url(rid_a, "bind_a.heic").split("&sig=")[1].split("&")[0]
    r = client.get(f"/api/receipt/{rid_b}/image?v=bind_b&sig={sig_a}")
    assert r.status_code == 403, r.text


def test_signature_is_bound_to_tenant(client, upload_dir):
    """签名绑定租户：把 default 租户的签名配 tenant_b 使用 → 403，不得跨租户复用。"""
    rid, _ = _mk_receipt(upload_dir, "bind_t.heic", _heic_bytes(), tenant_id="tenant_b")
    sig = _image_url(rid, "bind_t.heic", "tenant_b").split("&sig=")[1].split("&")[0]
    ok = client.get(f"/api/receipt/{rid}/image?v=bind_t&sig={sig}&tenant_id=tenant_b")
    assert ok.status_code == 200, ok.text
    bad = client.get(f"/api/receipt/{rid}/image?v=bind_t&sig={sig}")
    assert bad.status_code == 403, bad.text


# -------------------------------------------------------------
# 4. 缓存复用
# -------------------------------------------------------------
def test_transcode_cache_reused(client, upload_dir):
    """二次请求命中磁盘副本，不重写（mtime_ns 不变）。"""
    rid, src = _mk_receipt(upload_dir, "cache.heic", _heic_bytes())
    url = _image_url(rid, "cache.heic")
    r1 = client.get(url)
    assert r1.status_code == 200
    preview = upload_dir / "cache_web.jpg"
    assert preview.is_file(), "首次请求应生成缓存副本"
    first_mtime = preview.stat().st_mtime_ns

    r2 = client.get(url)
    assert r2.status_code == 200
    assert r2.content == r1.content
    assert preview.stat().st_mtime_ns == first_mtime, "缓存命中不得重写副本"


def test_preview_cache_file_mode_is_0644(client, upload_dir):
    """Y4 回归：新落盘的 `<stem>_web.jpg` 权限对齐 0644（与既有上传件一致）。

    why: mkstemp 固定产出 0600；同进程 FileResponse 读取不受影响，但若将来由别的
    用户身份（nginx 等）静态托管 uploads/，0600 会读不到图。修复是 os.replace 之后
    对目标文件 os.chmod(0644) —— 临时文件在替换前始终保持 0600，不被他人提前读到。
    本用例同时覆盖端点路径与非端点的 web_preview_path 直调路径（所有写者共用
    _atomic_write，两条路径都必须 0644）。
    """
    rid, _ = _mk_receipt(upload_dir, "mode.heic", _heic_bytes())
    r = client.get(_image_url(rid, "mode.heic"))
    assert r.status_code == 200
    preview = upload_dir / "mode_web.jpg"
    assert preview.is_file()
    assert stat.S_IMODE(preview.stat().st_mode) == 0o644, (
        "预览缓存权限应为 0644，实际 %s" % oct(stat.S_IMODE(preview.stat().st_mode)))

    # 非端点路径（如启动期兜底 / 直接调用者）重新生成时同样 0644
    from app.services.image_web import web_preview_path
    preview.unlink()
    assert web_preview_path(upload_dir / "mode.heic") == preview
    assert stat.S_IMODE(preview.stat().st_mode) == 0o644

    # 写路径不得留下临时残骸
    assert list(upload_dir.glob("*.tmp")) == []


# -------------------------------------------------------------
# 5. Web 格式零回归
# -------------------------------------------------------------
def test_web_format_keeps_uploads_url_and_passthrough(client, upload_dir):
    """jpg 仍走 /uploads/ 静态地址（逐字节不变）；端点透传字节与源文件逐字节一致。"""
    rid, src = _mk_receipt(upload_dir, "plain.jpg", _jpeg_bytes())
    body = client.get(f"/api/receipt/{rid}", headers={"X-Role": "staff"}).json()
    assert body["image_url"] == "/uploads/plain.jpg"

    # 端点侧（Web 格式的规范 URL 仍是 /uploads，故这里手工构造带签名的端点 URL）
    r = client.get(_manual_signed_url(rid, "plain"))
    assert r.status_code == 200
    assert r.content == src.read_bytes(), "Web 格式必须原样透传，不得二次编码"
    assert not (upload_dir / "plain_web.jpg").exists(), "Web 格式不应生成缓存副本"


# -------------------------------------------------------------
# 6. 租户隔离
# -------------------------------------------------------------
def test_transcode_tenant_isolation(client, upload_dir):
    """跨租户取图 404；带正确租户签名与 ?tenant_id= 返回 200。

    签名绑定租户后，「跨租户」有两种失败形态，都必须挡住：
    default 租户身份去取 tenant_b 的单据 → 404；签名与租户不匹配 → 403。
    """
    rid, _ = _mk_receipt(upload_dir, "tenant.heic", _heic_bytes(),
                         tenant_id="tenant_b")
    # default 租户的合法签名（tenant 参数与签名一致）→ 行按租户过滤 → 404
    assert client.get(_image_url(rid, "tenant.heic", "default")).status_code == 404
    # 正确租户 + 匹配签名 → 200
    assert client.get(_image_url(rid, "tenant.heic", "tenant_b")).status_code == 200


# -------------------------------------------------------------
# 7-10. 业务防护
# -------------------------------------------------------------
def test_missing_file_returns_image_not_found(client, upload_dir):
    """DB 有行、磁盘无文件 → 404 IMAGE_NOT_FOUND（签名有效，问题在文件）。"""
    rid = db.create_receipt(supplier_name="无图", status="parsed")
    db.update_receipt(rid, image_path="uploads/not_on_disk.heic")
    r = client.get(_image_url(rid, "not_on_disk.heic"))
    assert r.status_code == 404
    assert r.json()["code"] == "IMAGE_NOT_FOUND"


def test_transcode_failure_returns_500_without_residue(client, upload_dir):
    """损坏的 .heic → 500 IMAGE_TRANSCODE_FAILED，且不残留 .tmp / 半截副本。"""
    rid, _ = _mk_receipt(upload_dir, "broken.heic", b"not-a-real-heic" * 8)
    r = client.get(_image_url(rid, "broken.heic"))
    assert r.status_code == 500
    assert r.json()["code"] == "IMAGE_TRANSCODE_FAILED"
    assert list(upload_dir.glob("*.tmp")) == [], "转码失败不得残留临时文件"
    assert not (upload_dir / "broken_web.jpg").exists(), "失败不得留下半截副本"


def test_soft_deleted_receipt_returns_404(client, upload_dir):
    """已软删单据不可取原图（签名有效也不放行）。"""
    rid, _ = _mk_receipt(upload_dir, "deleted.heic", _heic_bytes())
    db.update_receipt(rid, deleted_at="2026-01-01T00:00:00")
    r = client.get(_image_url(rid, "deleted.heic"))
    assert r.status_code == 404
    assert r.json()["code"] == "IMAGE_NOT_FOUND"


def test_path_traversal_rejected(client, upload_dir):
    """image_path 带 ../ 或绝对路径不得越出 uploads 目录。

    这里刻意用「签名合法」的 URL：签名门槛不能掩盖路径校验，越界仍须 404。
    """
    rid = db.create_receipt(supplier_name="越界", status="parsed")
    db.update_receipt(rid, image_path="../../etc/passwd")
    r = client.get(_manual_signed_url(rid, "passwd"))
    assert r.status_code == 404
    assert r.json()["code"] == "IMAGE_NOT_FOUND"

    db.update_receipt(rid, image_path="/etc/hosts")
    r2 = client.get(_manual_signed_url(rid, "hosts"))
    assert r2.status_code == 404


# -------------------------------------------------------------
# 11. api_admin 灰测样本回归守卫
# -------------------------------------------------------------
def test_admin_grey_samples_image_url_reachable(client, upload_dir):
    """灰测样本弹窗原图：image_url 非空、指向新端点、且该路由匿名可达 200。

    回归守卫：api_admin 旧代码生成的 /api/receipt/{id}/image 是悬空路由（实测 404），
    观测台弹窗必然破图；改走 public_image_url 后必须真能取到 JPEG。
    """
    rid, _ = _mk_receipt(upload_dir, "admin_grey.heic", _heic_bytes())
    headers = {"X-Role": "admin", "X-Email": "admin@demo.hk"}
    res = client.get("/api/admin/grey-test/samples?scope=all", headers=headers)
    assert res.status_code == 200, res.text
    samples = res.json()["samples"]
    mine = [s for s in samples if s["receipt_id"] == rid]
    assert mine, "本单据应出现在灰测样本列表"
    image_url = mine[0]["image_url"]
    assert image_url, "灰测样本 image_url 不得为空"
    assert image_url.startswith(f"/api/receipt/{rid}/image"), image_url
    assert "&sig=" in image_url, f"新端点 URL 必须带签名: {image_url}"
    # 路由可达：前端 <img> 不带身份头也能取到真 JPEG
    img = client.get(image_url)
    assert img.status_code == 200, img.text
    assert img.content[:3] == b"\xff\xd8\xff"


# -------------------------------------------------------------
# 12. content-type 与 body 实际格式一致（PNG 不得谎报 JPEG）
# -------------------------------------------------------------
def test_png_passthrough_reports_png_content_type(client, upload_dir):
    """PNG 源经端点原样透传时，content-type 必须是 image/png 而非 image/jpeg。

    回归守卫：端点曾硬编码 media_type="image/jpeg"，而 web_preview_path() 对
    Web 格式是原样返回源路径（未转码），导致 PNG 单据响应头与 body 魔数矛盾。
    """
    rid, src = _mk_receipt(upload_dir, "plain.png", _png_bytes())
    r = client.get(_manual_signed_url(rid, "plain"))
    assert r.status_code == 200
    # body 确为 PNG 魔数 89504e47，且逐字节透传
    assert r.content[:4] == b"\x89PNG"
    assert r.content == src.read_bytes()
    content_type = r.headers["content-type"]
    assert "image/jpeg" not in content_type, f"PNG 谎报为 JPEG: {content_type}"
    assert content_type.startswith("image/png"), content_type


def test_heic_transcoded_still_reports_jpeg(client, upload_dir):
    """HEIC 经转码后 content-type 仍为 image/jpeg，与 JPEG 魔数 ffd8ff 一致。"""
    rid, _ = _mk_receipt(upload_dir, "still.heic", _heic_bytes())
    r = client.get(_image_url(rid, "still.heic"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content[:3] == b"\xff\xd8\xff"


# -------------------------------------------------------------
# 13. 空 image_path 契约：返回 ''（不是旧实现的 '/uploads/'）
# -------------------------------------------------------------
def test_empty_image_path_returns_empty_string(client):
    """空 / None image_path 的 image_url 为 ''，由前端空值守卫降级。

    回归守卫（行为变更显式化）：旧实现
    `"/uploads/" + (image_path.split("/")[-1] if image_path else "")` 对空路径
    返回 '/uploads/' —— 一个指向目录、浏览器打不开的无效 URL。新契约返回 ''。
    live 库有 238 条空 image_path 单据，不得再回退成 '/uploads/'。
    """
    from app.services.receipt_utils import public_image_url

    assert public_image_url(123, "") == ""
    assert public_image_url(123, None) == ""

    # 端到端：image_path 为空的单据，详情接口 image_url 必须为空串而非 '/uploads/'
    rid = db.create_receipt(supplier_name="无原图", status="parsed")
    body = client.get(f"/api/receipt/{rid}", headers={"X-Role": "staff"}).json()
    assert body["image_url"] == ""
    assert body["image_url"] != "/uploads/"


# -------------------------------------------------------------
# 14. HEIF 解码器缺失不得拖垮 /api/convert-image（jpg/png 仍须可用）
# -------------------------------------------------------------
def _break_heif_registration(monkeypatch):
    """模拟环境缺 pillow-heif / 版本不兼容：注册函数抛异常，并清空模块缓存状态。"""
    from app.services import image_web
    monkeypatch.setattr(image_web, "_HEIF_REGISTERED", False)
    monkeypatch.setattr(image_web, "_HEIF_WARNED", False)
    monkeypatch.setattr(image_web, "_HEIF_REGISTER_ERROR", None)

    import pillow_heif

    def _boom(*args, **kwargs):
        raise ImportError("simulated: pillow-heif unavailable")

    monkeypatch.setattr(pillow_heif, "register_heif_opener", _boom)


def test_heif_decoder_missing_does_not_break_web_formats(monkeypatch, tmp_path):
    """回归守卫：注册失败只影响 HEIC/HEIF 输入，jpg/png 仍须转码成功。

    旧实现无条件先调 _register_heif_opener() 并在失败时 raise，把可选解码器升级成
    整个端点的硬依赖 —— 缺 pillow-heif 时连本不需要它的 jpg/png 都会 500。
    """
    from app.services import image_web

    heic = _heic_bytes()  # 真 HEIC 字节须在打桩前生成（生成过程自身要注册解码器）
    _break_heif_registration(monkeypatch)

    # (a) 字节入口：jpg / png 照常成功，输出为真 JPEG
    for raw in (_jpeg_bytes(), _png_bytes()):
        out = image_web.transcode_bytes_to_jpeg(raw)
        assert out[:3] == b"\xff\xd8\xff"

    # (a') 路径入口：非 HEIF 扩展名同样不受影响（含 Pillow 原生支持的 TIFF 语义）
    jpg_path = tmp_path / "plain.jpg"
    jpg_path.write_bytes(_jpeg_bytes())
    assert image_web.transcode_to_jpeg_bytes(jpg_path)[:3] == b"\xff\xd8\xff"

    # (b) 只有 HEIC/HEIF 输入才失败，且错误信息可读
    with pytest.raises(RuntimeError) as ei:
        image_web.transcode_bytes_to_jpeg(heic)
    assert "解码器不可用" in str(ei.value), str(ei.value)

    heic_path = tmp_path / "plain.heic"
    heic_path.write_bytes(heic)
    with pytest.raises(RuntimeError) as ei_path:
        image_web.transcode_to_jpeg_bytes(heic_path)
    assert "解码器不可用" in str(ei_path.value), str(ei_path.value)


def test_convert_image_endpoint_ok_without_heif(client, monkeypatch):
    """端点级：解码器缺失时 POST jpg 仍 200 JPEG；POST HEIC 才 500 且原因可读。"""
    heic = _heic_bytes()
    _break_heif_registration(monkeypatch)

    ok = client.post("/api/convert-image",
                     files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")})
    assert ok.status_code == 200, ok.text
    assert ok.content[:3] == b"\xff\xd8\xff"

    bad = client.post("/api/convert-image",
                      files={"file": ("a.heic", heic, "image/heic")})
    assert bad.status_code == 500
    assert "解码器不可用" in bad.json()["msg"], bad.text


# -------------------------------------------------------------
# 15. 预览转码缓存孤儿治理（W5）
#   换图后旧缓存必须清理；仍被引用的缓存（含软删单据的引用）必须保留；
#   启动期兜底扫描只删无主 `*_web.jpg`，任何源原图一律不动。
# -------------------------------------------------------------
@pytest.fixture()
def job_stub(monkeypatch):
    """替换后台识别派发：换图用例只验证缓存清理，不真正跑识别管线。"""
    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None):
        calls.append({"image_path": image_path, "receipt_id": receipt_id})
        return ("job-test-w5", receipt_id)

    from app import api_receipts
    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    return calls


def _replace_image(client, rid, filename="retake.png", data=None):
    """走真实端点重拍换图（force=true 跳过模糊硬拦截，避免依赖图像熵）。"""
    return client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": (filename, data if data is not None else _jpeg_bytes(),
                           "image/png")},
        data={"force": "true"},
        headers={"X-Role": "staff"})


def test_replace_image_cleans_old_preview_cache(client, job_stub, upload_dir):
    """换图后旧 `<stem>_web.jpg` 缓存被清理；旧源原图保留可审计、新图正常落盘。"""
    rid, old_src = _mk_receipt(upload_dir, "old.heic", _heic_bytes())
    # 先取一次原图，确保缓存确实存在（否则「被清理」无从谈起）
    assert client.get(_image_url(rid, "old.heic")).status_code == 200
    cache = upload_dir / "old_web.jpg"
    assert cache.is_file(), "首次取图应生成缓存副本"

    r = _replace_image(client, rid)
    assert r.status_code == 200, r.text
    assert not cache.exists(), "换图后旧源文件的预览缓存应被清理"

    assert old_src.is_file(), "旧源原图必须保留（可审计），不得删除"
    row = db.get_receipt_row(rid)
    assert os.path.basename(row.image_path) != "old.heic"
    assert (upload_dir / os.path.basename(row.image_path)).is_file(), "新图应正常落盘"


def test_replace_image_keeps_cache_still_referenced(client, job_stub, upload_dir):
    """旧源文件仍被其他单据引用时，缓存不得被误删。"""
    rid_a, _ = _mk_receipt(upload_dir, "shared.heic", _heic_bytes())
    rid_b = db.create_receipt(supplier_name="另一单据", status="parsed")
    db.update_receipt(rid_b, image_path="uploads/shared.heic")

    assert client.get(_image_url(rid_a, "shared.heic")).status_code == 200
    cache = upload_dir / "shared_web.jpg"
    assert cache.is_file()

    assert _replace_image(client, rid_a).status_code == 200
    assert cache.is_file(), "仍有其他单据引用该源文件，缓存必须保留"


def test_replace_image_keeps_cache_referenced_by_soft_deleted(client, job_stub,
                                                              upload_dir):
    """软删单据（回收站可恢复、恢复后仍要展示原图）的引用同样算引用。"""
    rid_a, _ = _mk_receipt(upload_dir, "trashed.heic", _heic_bytes())
    rid_b = db.create_receipt(supplier_name="回收站单据", status="parsed")
    db.update_receipt(rid_b, image_path="uploads/trashed.heic",
                      deleted_at="2026-01-01T00:00:00")

    assert client.get(_image_url(rid_a, "trashed.heic")).status_code == 200
    cache = upload_dir / "trashed_web.jpg"
    assert cache.is_file()

    assert _replace_image(client, rid_a).status_code == 200
    assert cache.is_file(), "软删单据的 image_path 仍构成引用，不得据 filtered 判定误删"


def test_sweep_orphan_previews_removes_only_orphans(upload_dir):
    """兜底扫描：无源文件 / 无单据引用的缓存删除；仍被引用的保留；源原图不动。"""
    from app.services import image_web

    (upload_dir / "alive.heic").write_bytes(_heic_bytes())
    (upload_dir / "alive_web.jpg").write_bytes(b"cache-alive")
    (upload_dir / "stale.heic").write_bytes(_heic_bytes())
    (upload_dir / "stale_web.jpg").write_bytes(b"cache-stale")
    (upload_dir / "nosrc_web.jpg").write_bytes(b"cache-nosrc")  # 无对应源文件
    (upload_dir / "plain.jpg").write_bytes(_jpeg_bytes())       # Web 格式，无缓存

    removed = image_web.sweep_orphan_previews(upload_dir, {"alive"})
    names = sorted(p.name for p in removed)
    assert names == ["nosrc_web.jpg", "stale_web.jpg"], names

    assert (upload_dir / "alive_web.jpg").is_file(), "仍被引用的缓存不得删除"
    for src in ("alive.heic", "stale.heic", "plain.jpg"):
        assert (upload_dir / src).is_file(), f"源原图不得被清理: {src}"

    # 幂等：再扫一次无任何可删项
    assert image_web.sweep_orphan_previews(upload_dir, {"alive"}) == []


def test_sweep_without_reference_info_is_conservative(upload_dir):
    """无引用信息时只按「源文件是否存在」清理，绝不因信息缺失删掉有效缓存。"""
    from app.services import image_web

    (upload_dir / "kept.heic").write_bytes(_heic_bytes())
    (upload_dir / "kept_web.jpg").write_bytes(b"cache-kept")
    (upload_dir / "lost_web.jpg").write_bytes(b"cache-lost")

    removed = image_web.sweep_orphan_previews(upload_dir)
    assert [p.name for p in removed] == ["lost_web.jpg"]
    assert (upload_dir / "kept_web.jpg").is_file()


def test_sweep_ignores_reference_set_unrelated_to_dir(upload_dir):
    """引用集合与本目录无交集（库/目录不匹配、测试临时库）时退化为保守清理。"""
    from app.services import image_web

    (upload_dir / "here.heic").write_bytes(_heic_bytes())
    (upload_dir / "here_web.jpg").write_bytes(b"cache-here")

    # 传入的引用集合指向别处的文件，无一能在本目录找到 → 不启用「无引用即删」规则
    removed = image_web.sweep_orphan_previews(upload_dir, {"elsewhere"})
    assert removed == []
    assert (upload_dir / "here_web.jpg").is_file()


def test_remove_preview_cache_never_touches_source(upload_dir):
    """定向清理只删 `<stem>_web.jpg`，源原图与 Web 格式无缓存路径都安全。"""
    from app.services import image_web

    src = upload_dir / "one.heic"
    src.write_bytes(_heic_bytes())
    cache = upload_dir / "one_web.jpg"
    cache.write_bytes(b"cache-one")

    assert image_web.remove_preview_cache(src) is True
    assert not cache.exists()
    assert src.is_file(), "源原图不得被删除"

    # Web 格式没有缓存副本，函数必须 no-op 且绝不误删同名 jpg
    jpg = upload_dir / "two.jpg"
    jpg.write_bytes(_jpeg_bytes())
    assert image_web.remove_preview_cache(jpg) is False
    assert jpg.is_file()
    # 缓存不存在 → False，重复调用幂等
    assert image_web.remove_preview_cache(src) is False


# -------------------------------------------------------------
# 16. 缓存命中的纳秒精度比较（W6-a）
# -------------------------------------------------------------
def test_cache_hit_uses_nanosecond_mtime(upload_dir):
    """_cache_hit 按 st_mtime_ns 比较：同一秒内源被原地覆盖后旧缓存必须立即失效。

    回归守卫：旧实现用浮点秒 st_mtime，`dst >= src` 在同秒内恒为真 —— 若出现原地
    覆盖同一路径（新图 mtime 只前进不到 1 秒），会命中旧缓存展示错图。判定必须依赖
    纳秒精度，不需要等下一秒才失效。
    """
    from app.services import image_web

    src = upload_dir / "ns.heic"
    dst = upload_dir / "ns_web.jpg"
    src.write_bytes(b"src-bytes")
    dst.write_bytes(b"cache-bytes")

    base_ns = 1_700_000_000_000_000_000  # 同一整秒内的基准时刻

    # (a) 副本比源早 1 纳秒（秒级 mtime 完全相同）→ 不得命中
    os.utime(src, ns=(base_ns, base_ns + 1))
    os.utime(dst, ns=(base_ns, base_ns))
    assert src.stat().st_mtime == dst.stat().st_mtime, "前提：两者秒级 mtime 相同"
    assert image_web._cache_hit(src, dst) is False

    # (b) 副本与源同刻 → 命中（边界保持「不早于」语义）
    os.utime(dst, ns=(base_ns, base_ns + 1))
    assert image_web._cache_hit(src, dst) is True

    # (c) 源再晚 1 纳秒 → 缓存立即失效，无需跨秒
    os.utime(src, ns=(base_ns, base_ns + 2))
    assert src.stat().st_mtime == dst.stat().st_mtime, "前提：仍在同一秒内"
    assert image_web._cache_hit(src, dst) is False


def test_cache_hit_rejects_empty_cache(upload_dir):
    """副本存在但 size 为 0 → 不命中（半截文件不得被当作有效缓存）。"""
    from app.services import image_web

    src = upload_dir / "empty.heic"
    src.write_bytes(b"src")
    dst = upload_dir / "empty_web.jpg"
    dst.write_bytes(b"")
    assert image_web._cache_hit(src, dst) is False


# -------------------------------------------------------------
# 17. SIGKILL 残留临时文件的幂等兜底清理（W6-b）
# -------------------------------------------------------------
def test_stale_tmp_sweep_removes_only_own_old_tmp(monkeypatch, upload_dir):
    """只清自身命名模式且超龄的 `*.tmp`：他方 tmp 与可能正在写入的新残骸都不动。"""
    from app.services import image_web

    own_stale = upload_dir / "old_web.ab12cd34.tmp"   # 自身命名模式 + 超龄 → 删
    own_fresh = upload_dir / "new_web.ef56gh78.tmp"   # 自身命名模式但未超龄 → 留
    foreign = upload_dir / "other_module.tmp"         # 非自身命名模式 → 留
    own_stale.write_bytes(b"residue")
    own_fresh.write_bytes(b"inflight")
    foreign.write_bytes(b"other-module")

    old = time.time() - image_web._STALE_TMP_AGE_SECONDS - 60
    os.utime(own_stale, (old, old))

    assert image_web._sweep_stale_tmp(upload_dir) == 1
    assert not own_stale.exists(), "超龄的自身临时文件应被清理"
    assert own_fresh.is_file(), "未超龄的临时文件可能正在被写入，不得删除"
    assert foreign.is_file(), "非自身命名模式的 *.tmp 不得删除"

    # 幂等：清掉「每进程每目录只扫一次」的节流后重扫，无可删项
    monkeypatch.setattr(image_web, "_TMP_SWEPT_DIRS", set())
    assert image_web._sweep_stale_tmp(upload_dir) == 0


def test_stale_tmp_sweep_is_throttled_per_dir(upload_dir):
    """每进程每目录只扫一次：第二次调用直接短路返回 0，不在请求热路径重复 iterdir。"""
    from app.services import image_web

    stale = upload_dir / "again_web.zz99yy88.tmp"
    stale.write_bytes(b"residue")
    old = time.time() - image_web._STALE_TMP_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    assert image_web._sweep_stale_tmp(upload_dir) == 1
    assert not stale.exists()
    stale.write_bytes(b"residue-again")
    os.utime(stale, (old, old))
    assert image_web._sweep_stale_tmp(upload_dir) == 0, "同目录第二次调用应被节流"
    assert stale.is_file(), "被节流的第二次调用不得再删文件"


def test_web_preview_path_sweeps_stale_tmp_on_entry(upload_dir):
    """web_preview_path 入口触发兜底清理：正常转码不受残留tmp影响，且残骸被清掉。"""
    from app.services import image_web

    src = upload_dir / "entry.heic"
    src.write_bytes(_heic_bytes())
    stale = upload_dir / "entry_web.aa11bb22.tmp"
    stale.write_bytes(b"residue")
    old = time.time() - image_web._STALE_TMP_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    out = image_web.web_preview_path(src)
    assert out.name == "entry_web.jpg"
    assert out.is_file(), "入口应正常完成转码"
    assert not stale.exists(), "入口应清掉上次硬中断留下的临时文件残骸"
    assert list(upload_dir.glob("*.tmp")) == [], "转码完成不得残留临时文件"


def test_stale_tmp_sweep_retries_after_scan_failure(monkeypatch, upload_dir):
    """目录枚举首次失败不得登记为「已扫」：下次调用仍要重试并完成清理（X6）。

    why: 原实现先写 _TMP_SWEPT_DIRS 再 iterdir，首次因权限/IO 失败即被永久节流，
    兜底清理在本进程内形同失效；节流必须只在扫描真正成功后才登记。
    """
    from pathlib import Path

    from app.services import image_web

    stale = upload_dir / "retry_web.qq11rr22.tmp"
    stale.write_bytes(b"residue")
    old = time.time() - image_web._STALE_TMP_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    key = str(Path(str(upload_dir)))
    monkeypatch.setattr(image_web, "_TMP_SWEPT_DIRS", set())

    real_iterdir = Path.iterdir
    calls = {"n": 0}

    def flaky_iterdir(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("模拟权限/IO 异常")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

    assert image_web._sweep_stale_tmp(upload_dir) == 0, "首次扫描失败应返回 0"
    assert key not in image_web._TMP_SWEPT_DIRS, "扫描失败不得登记为已扫"
    assert stale.is_file(), "首次失败时残骸应仍在"

    assert image_web._sweep_stale_tmp(upload_dir) == 1, "第二次调用应重试并清理残骸"
    assert not stale.exists()
    assert key in image_web._TMP_SWEPT_DIRS, "扫描成功后才允许登记为已扫"

    # 成功登记后节流本意保留：第三次直接短路，不再 iterdir
    assert image_web._sweep_stale_tmp(upload_dir) == 0
    assert calls["n"] == 2, "登记成功后不得再枚举目录"


# -------------------------------------------------------------
# 18. 换图后旧签名 URL 立即失效（签名主体 = 当前原图）
# -------------------------------------------------------------
def test_replace_image_invalidates_old_signed_url(client, job_stub, upload_dir):
    """换图后旧 URL → 403，不得串到换图后的新图。

    why: 签名绑定 单据 id + 文件名主体；若只验签名不校验主体，一张「旧图的合法
    URL」就会取到换图后的新图（签名与真实资源脱钩）。前端每次换图后拿到的都是
    新 URL，故本约束对正常使用无影响。
    """
    rid, _ = _mk_receipt(upload_dir, "bust.heic", _heic_bytes())
    old_url = _image_url(rid, "bust.heic")
    assert client.get(old_url).status_code == 200

    r = _replace_image(client, rid)
    assert r.status_code == 200, r.text
    assert client.get(old_url).status_code == 403, "换图后旧 URL 必须失效"

    # 新原图是 Web 格式（retake.png），规范 URL 回落 /uploads/ 且与旧 URL 不同
    new_url = r.json()["image_url"]
    assert new_url.startswith("/uploads/") and new_url != old_url


# -------------------------------------------------------------
# 19. HEIF 注册状态加锁（X7）：并发首次调用只执行一次注册
# -------------------------------------------------------------
def test_heif_registration_is_locked_under_concurrency(monkeypatch):
    """多线程并发首次调用时，register_heif_opener 实际只被执行一次（X7）。

    why: _register_heif_opener 是「读 _HEIF_REGISTERED → 执行注册 → 写回」的复合
    操作。未加锁时并发首次调用会让每个线程都读到未注册而各注册一次（注册函数幂等、
    无实际危害，但属竞态）。此处用「计数 + 故意放慢」的桩把并发窗口放大到必然重叠：
    未加锁时计数必然 > 1，加锁后严格为 1。
    """
    import threading

    import pillow_heif

    from app.services import image_web

    # 复位模块内注册状态，制造「本进程首次调用」场景（monkeypatch 结束后自动还原）
    monkeypatch.setattr(image_web, "_HEIF_REGISTERED", False)
    monkeypatch.setattr(image_web, "_HEIF_WARNED", False)
    monkeypatch.setattr(image_web, "_HEIF_REGISTER_ERROR", None)

    calls = {"n": 0}
    calls_guard = threading.Lock()

    def _slow_register(*args, **kwargs):
        with calls_guard:
            calls["n"] += 1
        time.sleep(0.05)  # 放大窗口：让 12 个线程必然同时处于「已进入注册」状态

    monkeypatch.setattr(pillow_heif, "register_heif_opener", _slow_register)

    results = []
    results_guard = threading.Lock()

    def _worker():
        ok = image_web._register_heif_opener()
        with results_guard:
            results.append(ok)

    threads = [threading.Thread(target=_worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1, f"并发首次调用应只注册一次，实际注册 {calls['n']} 次"
    assert results == [True] * 12, f"所有并发调用都应返回 True，实际 {results}"


# -------------------------------------------------------------
# 20. 并发转码（审核观察项）：同一 HEIC 多线程并发只转码一次、无 tmp 残留
# -------------------------------------------------------------
def test_concurrent_transcode_single_flight(monkeypatch, upload_dir):
    """同一源文件被多线程并发请求预览时，只真正转码一次，且无临时文件残留。

    why: 若无 _lock_for 的「按目标路径加锁」，N 个并发请求会各自解码同一张 HEIC、
    各自跑一遍 mkstemp + os.replace（功能仍正确，但重复解码浪费 CPU，中间态也更多）。
    此前 12 线程压测的结论只存在于人工验证里、未沉淀为用例，本用例补上这道回归。
    用「计数 + 故意放慢」的桩把并发窗口放大到必然重叠：未加锁时计数必然 > 1，
    加锁后严格为 1。同时断言锁表引用计数归零（不泄漏条目）。
    """
    import threading

    from app.services import image_web

    # 复位锁表，制造「无人持锁」的干净场景（monkeypatch 结束后自动还原）
    monkeypatch.setattr(image_web, "_LOCKS", {})
    monkeypatch.setattr(image_web, "_LOCK_REFS", {})

    src = upload_dir / "concurrent.heic"
    src.write_bytes(_heic_bytes(64, 48))

    calls = {"n": 0}
    calls_guard = threading.Lock()
    real_transcode = image_web.transcode_to_jpeg_bytes

    def _slow_transcode(src_path, quality=image_web.JPEG_QUALITY):
        with calls_guard:
            calls["n"] += 1
        time.sleep(0.08)  # 放大窗口：让 12 个线程必然同时处于「已进入转码」状态
        return real_transcode(src_path, quality=quality)

    monkeypatch.setattr(image_web, "transcode_to_jpeg_bytes", _slow_transcode)

    paths = []
    errors = []
    out_guard = threading.Lock()

    def _worker():
        try:
            p = image_web.web_preview_path(src)
            with out_guard:
                paths.append(p)
        except Exception as e:  # noqa: BLE001 并发异常需收集后统一断言
            with out_guard:
                errors.append(repr(e))

    threads = [threading.Thread(target=_worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发转码不应抛异常，实际: {errors}"
    assert calls["n"] == 1, f"并发应只转码一次，实际转码 {calls['n']} 次"
    assert len({str(p) for p in paths}) == 1, "所有线程应拿到同一缓存路径"
    cache = paths[0]
    assert cache.is_file(), "缓存文件应存在"
    assert cache.read_bytes()[:3] == b"\xff\xd8\xff", "缓存应是真 JPEG（SOI 魔数）"
    assert list(upload_dir.glob("*.tmp")) == [], "不应有临时文件残留"
    assert image_web._LOCKS == {}, f"锁表应回收干净，实际残留 {list(image_web._LOCKS)}"
    assert image_web._LOCK_REFS == {}, "锁引用计数应归零"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))