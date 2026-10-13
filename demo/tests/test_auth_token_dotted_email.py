# -*- coding: utf-8 -*-
"""含点邮箱 token 解析回归测试（Y2）。

背景：token 格式是 f"{email}.{role}.{exp}.{tenant}.{signature}"，而 parse_token 原先用
payload.split(".") 解包 email, role, exp——本项目三个预置账号全是 xx@demo.hk
（邮箱本身含点），split 会切出 4 段导致 ValueError、被 except 静默吞掉，于是
issue_token → parse_token 往返恒失败，Bearer 令牌链路实际不可用（resolve_account
总是回落 X-Role 头，所以 demo 日常看不出来）。修复改为 payload.rsplit(".", 2)。

why 这份用例必须存在：往返形态正是当初能暴露该缺陷的那个测试形态，缺了它这个缺陷
曾长期不显形。D19 之后载荷追加了 tenant 段（生产态租户归属只能来自身份），故这里的
结构断言同步为「邮箱含点时按从右切 4 段还原」——从右侧切分这条修复本身仍然保留，
只是在最右端再多一个不含点的 tenant 段。

覆盖点：
- 含点邮箱正常往返（issue → parse 得到原 email/role/tenant）
- 过期 / 篡改签名 / 篡改 payload / 账号-角色不匹配 / 账号-租户不匹配 均返回 None
- 无令牌时行为不变：仍回落 X-Role 头、source=header
- 有令牌时 source=token 且 email/role/tenant 正确，优先于 X-Role
- 无效令牌不越权：仍回落 X-Role，不静默提升
- 不含点邮箱（旧代码唯一能解析成功的形态）无回归
- 畸形输入不抛异常

不写 live 库：本文件只做纯函数级断言，不触碰 DB 与 uploads。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from starlette.requests import Request

from app import auth


def _request(headers=None):
    """构造最小可用的 Starlette Request，供 resolve_account 读取请求头。"""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": raw, "query_string": b""})


def _mint(email, role, exp, tenant="default", jti="testjti0"):
    """按既定格式手工签发指定过期时间/租户的令牌（用于构造过期/不匹配场景）。"""
    payload = f"{email}.{role}.{exp}.{tenant}.{jti}"
    return f"{payload}.{auth._sign(payload)}"


# ---------- 往返 ----------

@pytest.mark.parametrize("email,role", [
    ("admin@demo.hk", "admin"),
    ("boss@demo.hk", "owner"),
    ("staff@demo.hk", "staff"),
])
def test_issue_then_parse_roundtrip_for_dotted_email(email, role):
    """核心回归：含点邮箱往返必须成立（修复前此处返回 None）。"""
    token = auth.issue_token(email, role)
    assert auth.parse_token(token) == (email, role, "default")


def test_token_generation_format_unchanged():
    """生成格式：email.role.exp.tenant.jti.signature，签名 64 位十六进制。

    注意 email 段自身含点，故按『从右切 5 段』还原（最右段是签名，其左依次是不含点
    的 jti / tenant / exp / role，剩下的整体即邮箱）。
    """
    token = auth.issue_token("admin@demo.hk", "admin")
    parts = token.rsplit(".", 5)
    assert len(parts) == 6, "令牌必须是 email/role/exp/tenant/jti/signature 六段式"
    assert parts[0] == "admin@demo.hk"
    assert parts[1] == "admin"
    assert parts[2].isdigit()
    assert parts[3] == "default"
    assert len(parts[4]) == 16 and parts[4] != auth.issue_token("admin@demo.hk", "admin").rsplit(".", 2)[1]
    assert len(parts[5]) == 64
    assert all(c in "0123456789abcdef" for c in parts[5])


def test_signature_still_covers_full_payload():
    """签名覆盖整段 payload：改动 email 段（含其中的点）也会导致验签失败。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    payload, sig = token.rsplit(".", 1)
    assert auth._sign(payload) == sig
    # 把域名里的点挪走（payload 变化但段数不变）→ 验签必须失败
    assert auth.parse_token(token.replace("admin@demo.hk", "admin@demohk")) is None


# ---------- 四类拒绝 ----------

def test_expired_token_rejected():
    token = _mint("admin@demo.hk", "admin", int(time.time()) - 10)
    assert auth.parse_token(token) is None


def test_tampered_signature_rejected():
    token = auth.issue_token("admin@demo.hk", "admin")
    flipped = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert flipped != token
    assert auth.parse_token(flipped) is None


def test_tampered_payload_rejected():
    """把 payload 里的角色改成另一个合法角色 → 签名与 payload 不匹配，必须拒。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    assert auth.parse_token(token.replace(".admin.", ".owner.")) is None


def test_role_mismatch_rejected():
    """签名合法但 exp 段角色与账号不符 → 仍拒（账号-角色一致性校验保留）。"""
    token = _mint("admin@demo.hk", "owner", int(time.time()) + 60)
    assert auth.parse_token(token) is None


def test_tenant_mismatch_rejected():
    """签名合法但租户与账号在 ACCOUNTS 中的租户不符 → 拒（D19 新增校验）。

    why：租户随令牌载荷下发并成为生产态租户归属的唯一来源，若只验账号-角色、
    不验账号-租户，就会留下「用合法账号签出他租令牌」的构造口子（密钥泄露时
    尤其危险）。这里穷举两条路径：他租 / 缺租户段。
    """
    assert auth.parse_token(
        _mint("admin@demo.hk", "admin", int(time.time()) + 60,
              tenant="tenantB")) is None
    # 缺 tenant 段的四段式令牌（本批次前的旧格式）同样不再被接受
    old_format = f"admin@demo.hk.admin.{int(time.time()) + 60}"
    assert auth.parse_token(f"{old_format}.{auth._sign(old_format)}") is None


def test_unknown_account_rejected():
    """签名合法但账号不在预置表 → 拒。"""
    token = _mint("nobody@demo.hk", "staff", int(time.time()) + 60)
    assert auth.parse_token(token) is None


# ---------- resolve_account 行为 ----------

def test_no_token_still_falls_back_to_header():
    """(b) 无令牌时行为不变：resolve_account 仍回落 X-Role 头、source=header。

    tenant 在开发态取自 X-Tenant-Id（缺省 default），与 resolve_tenant 的开发态口径一致。
    """
    assert auth.resolve_account(_request({"X-Role": "owner"})) == {
        "email": "owner@demo.hk", "role": "owner", "tenant": "default",
        "source": "header"}
    assert auth.resolve_account(_request({"X-Role": "staff"})) == {
        "email": "staff@demo.hk", "role": "staff", "tenant": "default",
        "source": "header"}
    assert auth.resolve_account(_request({"X-Role": "owner", "X-Tenant-Id": "tenant_a"})) == {
        "email": "owner@demo.hk", "role": "owner", "tenant": "tenant_a",
        "source": "header"}


def test_no_identity_at_all_is_anonymous():
    """无令牌且无角色头：source=None（不静默降级）、tenant=None（无身份即无租户）。"""
    assert auth.resolve_account(_request({})) == {
        "email": None, "role": None, "tenant": None, "source": None}
    # 未知角色头同样不算身份
    assert auth.resolve_account(_request({"X-Role": "bogus"})) == {
        "email": None, "role": None, "tenant": None, "source": None}


def test_valid_token_wins_over_header():
    """(c) 有令牌时 source=token，email/role/tenant 取令牌、优先于 X-Role。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    account = auth.resolve_account(_request({
        "Authorization": f"Bearer {token}", "X-Role": "staff"}))
    assert account == {"email": "admin@demo.hk", "role": "admin",
                       "tenant": "default", "source": "token"}

    token_owner = auth.issue_token("boss@demo.hk", "owner")
    account_owner = auth.resolve_account(_request({
        "Authorization": f"Bearer {token_owner}"}))
    assert account_owner == {"email": "boss@demo.hk", "role": "owner",
                             "tenant": "default", "source": "token"}


def test_invalid_token_falls_back_to_header_not_privilege():
    """(d) 无效令牌不越权：不解析成功就回落 X-Role，绝不静默用令牌里的角色。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    account = auth.resolve_account(_request({
        "Authorization": f"Bearer {tampered}", "X-Role": "staff"}))
    assert account == {"email": "staff@demo.hk", "role": "staff",
                       "tenant": "default", "source": "header"}

    expired = _mint("admin@demo.hk", "admin", int(time.time()) - 10)
    assert auth.resolve_account(_request({
        "Authorization": f"Bearer {expired}"})) == {
        "email": None, "role": None, "tenant": None, "source": None}


# ---------- 无回归 / 健壮性 ----------

def test_dotless_email_still_works(monkeypatch):
    """不含点邮箱（旧代码唯一能解析成功的形态）行为无回归。"""
    monkeypatch.setitem(auth.ACCOUNTS, "plainuser", ("pw", "staff", "default"))
    token = auth.issue_token("plainuser", "staff")
    assert auth.parse_token(token) == ("plainuser", "staff", "default")


@pytest.mark.parametrize("payload", [
    "plain.staff.999.default", "roleonly.admin.123.default"])
def test_rsplit_matches_split_when_email_has_no_dot(payload):
    """结构等价性：邮箱不含点时 payload 就是 role/exp/tenant 三段 + 邮箱段（4 段），

    此时 rsplit('.', 3) 与 split('.') 结果逐元素一致 → 对不含点账号零行为变更
    （含点邮箱才必须走 rsplit，见下一条用例）。
    """
    assert payload.rsplit(".", 3) == payload.split(".")


def test_old_split_parse_would_fail_on_dotted_email():
    """缺陷留档：旧逻辑（payload.split('.')）对含点邮箱必然解包失败。

    这不是断言『旧代码应失败』，而是把缺陷机制固化成断言，防止将来有人改回 split。
    """
    token = auth.issue_token("admin@demo.hk", "admin")
    payload = token.rsplit(".", 1)[0]
    # admin@demo / hk / admin / <exp> / <tenant> / <jti>
    assert len(payload.split(".")) == 6
    with pytest.raises(ValueError):
        email, role, exp, tenant, jti = payload.split(".")
    # 新逻辑从右侧切分，点被安全留在 email 段内
    assert payload.rsplit(".", 4) == [
        "admin@demo.hk", "admin", *payload.rsplit(".", 3)[1:]]


@pytest.mark.parametrize("bad", [
    "", "abc", "a.b", "a.b.c", "a.b.c.d.e",
    "admin@demo.hk.admin.notanumber.sig",
    "admin@demo.hk.admin." + str(int(time.time()) + 60),  # 缺签名
    "admin@demo.hk.admin." + str(int(time.time()) + 60) + ".default",  # 缺签名
    "Bearer admin@demo.hk.admin.9999999999.default.deadbeef",
])
def test_malformed_tokens_return_none_without_raising(bad):
    """畸形输入一律返回 None，不抛异常（原有 except 兜底行为保留）。"""
    assert auth.parse_token(bad) is None
