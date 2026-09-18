# -*- coding: utf-8 -*-
"""含点邮箱 token 解析回归测试（Y2）。

背景：token 格式是 f"{email}.{role}.{exp}.{signature}"，而 parse_token 原先用
payload.split(".") 解包 email, role, exp——本项目三个预置账号全是 xx@demo.hk
（邮箱本身含点），split 会切出 4 段导致 ValueError、被 except 静默吞掉，于是
issue_token → parse_token 往返恒失败，Bearer 令牌链路实际不可用（resolve_account
总是回落 X-Role 头，所以 demo 日常看不出来）。修复改为 payload.rsplit(".", 2)。

why 这份用例必须存在：往返形态正是当初能暴露该缺陷的那个测试形态，缺了它这个缺陷
曾长期不显形。硬约束是 token 生成格式不改（改了会让既有已签发令牌全部失效），
故这里显式断言令牌结构仍为四段、签名 64 位十六进制。

覆盖点：
- 含点邮箱正常往返（issue → parse 得到原 email/role）
- 过期 / 篡改签名 / 篡改 payload / 账号-角色不匹配 均返回 None
- 无令牌时行为不变：仍回落 X-Role 头、source=header
- 有令牌时 source=token 且 email/role 正确，优先于 X-Role
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


def _mint(email, role, exp):
    """按既定格式手工签发指定过期时间的令牌（用于构造过期/不匹配场景）。"""
    payload = f"{email}.{role}.{exp}"
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
    assert auth.parse_token(token) == (email, role)


def test_token_generation_format_unchanged():
    """生成格式一字未改：仍是 email.role.exp.signature，签名 64 位十六进制。

    注意 email 段自身含点，故按『从右切 3 段』还原（这也正是 parse_token 的做法）。
    """
    token = auth.issue_token("admin@demo.hk", "admin")
    parts = token.rsplit(".", 3)
    assert len(parts) == 4, "令牌必须仍是四段式（email/role/exp/signature）"
    assert parts[0] == "admin@demo.hk"
    assert parts[1] == "admin"
    assert parts[2].isdigit()
    assert len(parts[3]) == 64
    assert all(c in "0123456789abcdef" for c in parts[3])


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


def test_unknown_account_rejected():
    """签名合法但账号不在预置表 → 拒。"""
    token = _mint("nobody@demo.hk", "staff", int(time.time()) + 60)
    assert auth.parse_token(token) is None


# ---------- resolve_account 行为 ----------

def test_no_token_still_falls_back_to_header():
    """(b) 无令牌时行为不变：resolve_account 仍回落 X-Role 头、source=header。"""
    assert auth.resolve_account(_request({"X-Role": "owner"})) == {
        "email": "owner@demo.hk", "role": "owner", "source": "header"}
    assert auth.resolve_account(_request({"X-Role": "staff"})) == {
        "email": "staff@demo.hk", "role": "staff", "source": "header"}


def test_no_identity_at_all_is_anonymous():
    """无令牌且无角色头：source=None（不静默降级）。"""
    assert auth.resolve_account(_request({})) == {
        "email": None, "role": None, "source": None}
    # 未知角色头同样不算身份
    assert auth.resolve_account(_request({"X-Role": "bogus"})) == {
        "email": None, "role": None, "source": None}


def test_valid_token_wins_over_header():
    """(c) 有令牌时 source=token，email/role 取令牌、优先于 X-Role。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    account = auth.resolve_account(_request({
        "Authorization": f"Bearer {token}", "X-Role": "staff"}))
    assert account == {"email": "admin@demo.hk", "role": "admin", "source": "token"}

    token_owner = auth.issue_token("boss@demo.hk", "owner")
    account_owner = auth.resolve_account(_request({
        "Authorization": f"Bearer {token_owner}"}))
    assert account_owner == {"email": "boss@demo.hk", "role": "owner", "source": "token"}


def test_invalid_token_falls_back_to_header_not_privilege():
    """(d) 无效令牌不越权：不解析成功就回落 X-Role，绝不静默用令牌里的角色。"""
    token = auth.issue_token("admin@demo.hk", "admin")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    account = auth.resolve_account(_request({
        "Authorization": f"Bearer {tampered}", "X-Role": "staff"}))
    assert account == {"email": "staff@demo.hk", "role": "staff", "source": "header"}

    expired = _mint("admin@demo.hk", "admin", int(time.time()) - 10)
    assert auth.resolve_account(_request({
        "Authorization": f"Bearer {expired}"})) == {
        "email": None, "role": None, "source": None}


# ---------- 无回归 / 健壮性 ----------

def test_dotless_email_still_works(monkeypatch):
    """不含点邮箱（旧代码唯一能解析成功的形态）行为无回归。"""
    monkeypatch.setitem(auth.ACCOUNTS, "plainuser", ("pw", "staff"))
    token = auth.issue_token("plainuser", "staff")
    assert auth.parse_token(token) == ("plainuser", "staff")


@pytest.mark.parametrize("payload", ["plain.staff.999", "roleonly.admin.123"])
def test_rsplit_matches_split_when_segment_count_is_three(payload):
    """结构等价性：payload 恰好 2 个点（旧代码唯一能解析成功的形态）时，

    rsplit('.', 2) 与 split('.') 结果逐元素一致 → 对既有可解析令牌零行为变更。
    """
    assert payload.rsplit(".", 2) == payload.split(".")


def test_old_split_parse_would_fail_on_dotted_email():
    """缺陷留档：旧逻辑（payload.split('.')）对含点邮箱必然解包失败。

    这不是断言『旧代码应失败』，而是把缺陷机制固化成断言，防止将来有人改回 split。
    """
    token = auth.issue_token("admin@demo.hk", "admin")
    payload = token.rsplit(".", 1)[0]
    assert len(payload.split(".")) == 4  # admin@demo / hk / admin / <exp>
    with pytest.raises(ValueError):
        email, role, exp = payload.split(".")
    # 新逻辑从右侧切分，点被安全留在 email 段内
    assert payload.rsplit(".", 2) == ["admin@demo.hk", "admin", payload.rsplit(".", 1)[1]]


@pytest.mark.parametrize("bad", [
    "", "abc", "a.b", "a.b.c", "a.b.c.d.e",
    "admin@demo.hk.admin.notanumber.sig",
    "admin@demo.hk.admin." + str(int(time.time()) + 60),  # 缺签名
    "Bearer admin@demo.hk.admin.9999999999.deadbeef",
])
def test_malformed_tokens_return_none_without_raising(bad):
    """畸形输入一律返回 None，不抛异常（原有 except 兜底行为保留）。"""
    assert auth.parse_token(bad) is None
