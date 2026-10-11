# -*- coding: utf-8 -*-
"""租户设置存储：自定义单位、单位换算、汇率表。

表在首次使用时用 CREATE TABLE IF NOT EXISTS 建好（数据库被测试或迁移重建后会重新检查），
所有读写都带 tenant_id。
"""

import logging
import math
import threading
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app import db
from app.store_util import Gate
from app.services import unit_factors as uf

logger = logging.getLogger("units_store")

_DDL = [
    """CREATE TABLE IF NOT EXISTS tenant_units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id VARCHAR(64) NOT NULL,
        name VARCHAR(64) NOT NULL,
        kind VARCHAR(16) NOT NULL DEFAULT 'other',
        created_at VARCHAR(32) DEFAULT '',
        UNIQUE (tenant_id, name))""",
    """CREATE TABLE IF NOT EXISTS tenant_unit_conversions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id VARCHAR(64) NOT NULL,
        from_unit VARCHAR(64) NOT NULL,
        to_unit VARCHAR(64) NOT NULL,
        factor FLOAT NOT NULL,
        note VARCHAR(200) DEFAULT '',
        created_at VARCHAR(32) DEFAULT '',
        updated_at VARCHAR(32) DEFAULT '',
        UNIQUE (tenant_id, from_unit, to_unit))""",
    """CREATE TABLE IF NOT EXISTS tenant_fx_rates (
        tenant_id VARCHAR(64) NOT NULL,
        currency VARCHAR(8) NOT NULL,
        rate FLOAT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        note VARCHAR(200) DEFAULT '',
        updated_at VARCHAR(32) DEFAULT '',
        PRIMARY KEY (tenant_id, currency))""",
    """CREATE TABLE IF NOT EXISTS receipt_fx_snapshots (
        receipt_id INTEGER NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        currency VARCHAR(8) NOT NULL,
        rate FLOAT NOT NULL,
        created_at VARCHAR(32) DEFAULT '',
        PRIMARY KEY (receipt_id, tenant_id))""",
]

_gate = Gate(_DDL, ('tenant_units', 'tenant_unit_conversions', 'tenant_fx_rates', 'receipt_fx_snapshots'))


def _engine():
    return _gate


def _tid(tenant_id) -> str:
    return str(tenant_id or "default").strip() or "default"


def _changed():
    uf.invalidate_cache()


# ---------------------------------------------------------------- 单位
BUILTIN_UNIT_ID_BASE = -1000


def _kind_ok(kind) -> str:
    k = (kind or "").strip().lower()
    return k if k in ("weight", "volume", "count", "other") else "other"


def list_units(tenant_id) -> List[dict]:
    tid = _tid(tenant_id)
    out = [{"id": -(i + 1), "name": n, "kind": k, "builtin": True}
           for i, (n, k) in enumerate(uf.SEED_UNITS)]
    seed_names = {n for n, _k in uf.SEED_UNITS}
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, kind FROM tenant_units WHERE tenant_id=:t ORDER BY id"),
            {"t": tid}).fetchall()
    for r in rows:
        if r[1] in seed_names:
            continue
        out.append({"id": r[0], "name": r[1], "kind": r[2], "builtin": False})
    return out


def unit_exists(tenant_id, name) -> bool:
    n = uf.norm_unit_name(name)
    if not n:
        return False
    if n in {x for x, _k in uf.SEED_UNITS}:
        return True
    with _engine().connect() as conn:
        r = conn.execute(text("SELECT 1 FROM tenant_units WHERE tenant_id=:t AND name=:n"),
                         {"t": _tid(tenant_id), "n": n}).fetchone()
    return r is not None


def add_unit(tenant_id, name, kind=None) -> Tuple[Optional[int], Optional[str]]:
    """返回 (id, 错误提示)。重复返回 (None, 提示)。"""
    n = uf.norm_unit_name(name)
    if not n:
        return None, "单位名称不能为空，请填写例如「箱」「斤」"
    if len(n) > 20:
        return None, "单位名称太长了，请控制在 20 个字以内"
    if unit_exists(tenant_id, n):
        return None, "「%s」已经有了，不用重复添加" % n
    k = _kind_ok(kind) if kind else (uf.seed_kind(n) or "other")
    with _engine().begin() as conn:
        res = conn.execute(text(
            "INSERT INTO tenant_units (tenant_id, name, kind, created_at) VALUES (:t,:n,:k,:c)"),
            {"t": _tid(tenant_id), "n": n, "k": k, "c": db.now_iso()})
        uid = res.lastrowid
    _changed()
    return uid, None


def _unit_references(tenant_id, name) -> List[str]:
    """谁在用这个单位：SKU 基准单位、换算。返回人话列表。"""
    n = uf.norm_unit_name(name)
    refs = []
    try:
        for s in db.list_skus(include_inactive=True, tenant_id=_tid(tenant_id)):
            if uf.norm_unit_name(getattr(s, "base_unit", "")) == n:
                refs.append("库存商品「%s」的库存单位" % s.name)
    except Exception as e:
        logger.warning("检查单位引用失败：%s", e)
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT from_unit, to_unit FROM tenant_unit_conversions WHERE tenant_id=:t "
            "AND (from_unit=:n OR to_unit=:n)"), {"t": _tid(tenant_id), "n": n}).fetchall()
    for a, b in rows:
        refs.append("换算「1 %s = ? %s」" % (a, b))
    return refs


def delete_unit(tenant_id, unit_id) -> Tuple[bool, Optional[str], int]:
    """返回 (成功, 提示, http 状态)。"""
    if int(unit_id) < 0:
        return False, "这是系统自带的单位，不能删除。如果数值不对，可以在「单位换算」里改。", 400
    tid = _tid(tenant_id)
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT name FROM tenant_units WHERE id=:i AND tenant_id=:t"),
                           {"i": int(unit_id), "t": tid}).fetchone()
    if row is None:
        return False, "没有找到这个单位", 404
    refs = _unit_references(tid, row[0])
    if refs:
        return False, "「%s」还在使用中，不能删除：%s。请先改掉再删。" % (row[0], "；".join(refs[:5])), 409
    with _engine().begin() as conn:
        conn.execute(text("DELETE FROM tenant_units WHERE id=:i AND tenant_id=:t"),
                     {"i": int(unit_id), "t": tid})
    _changed()
    return True, None, 200


# ---------------------------------------------------------------- 换算
def tenant_edges(tenant_id) -> List[Tuple[str, str, float]]:
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT from_unit, to_unit, factor FROM tenant_unit_conversions WHERE tenant_id=:t"),
            {"t": _tid(tenant_id)}).fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]


def validate_factor(factor) -> Tuple[Optional[float], Optional[str]]:
    try:
        f = float(factor)
    except (TypeError, ValueError):
        return None, "换算数值要填数字，例如 0.6048"
    if not math.isfinite(f) or f <= 0:
        return None, "换算数值必须大于 0"
    return f, None


def list_conversions(tenant_id) -> List[dict]:
    tid = _tid(tenant_id)
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, from_unit, to_unit, factor, note FROM tenant_unit_conversions "
            "WHERE tenant_id=:t ORDER BY id"), {"t": tid}).fetchall()
    custom = [{"id": r[0], "from_unit": r[1], "to_unit": r[2], "factor": float(r[3]),
               "note": r[4] or "", "builtin": False} for r in rows]
    overridden = set()
    for c in custom:
        overridden.add((c["from_unit"], c["to_unit"]))
        overridden.add((c["to_unit"], c["from_unit"]))
    out = []
    for i, (a, b, f, note) in enumerate(uf.SEED_CONVERSIONS):
        if (a, b) in overridden:
            continue
        out.append({"id": -(i + 1), "from_unit": a, "to_unit": b, "factor": f,
                    "note": note, "builtin": True})
    return out + custom


def _ensure_unit(tenant_id, name):
    if not unit_exists(tenant_id, name):
        add_unit(tenant_id, name)


def _cycle_free_check(tenant_id, a, b, factor, skip_pair=None) -> Optional[str]:
    """新换算与现有换算图矛盾（形成环且系数不一致）时给出提示。"""
    edges = [e for e in tenant_edges(tenant_id)
             if (e[0], e[1]) != (a, b) and (e[1], e[0]) != (a, b)
             and (skip_pair is None or (e[0], e[1]) != skip_pair)]
    g = uf.build_graph(edges)
    g.get(a, {}).pop(b, None)
    g.get(b, {}).pop(a, None)
    res = uf.find_path(g, a, b)
    if res is None:
        return None
    existing = res[0]
    if abs(existing - factor) / max(abs(existing), 1e-12) > 1e-6:
        chain = "、".join(res[1])
        return ("和已有换算对不上：按现有设置（经过 %s），1 个「%s」等于 %s 个「%s」，"
                "而你填的是 %s。请先改掉其中一条再保存。"
                % (chain, a, _fmt(existing), b, _fmt(factor)))
    return None


def _fmt(x) -> str:
    return ("%.6f" % x).rstrip("0").rstrip(".")


def upsert_conversion(tenant_id, from_unit, to_unit, factor, note="") -> Tuple[Optional[int], Optional[str], int]:
    a, b = uf.norm_unit_name(from_unit), uf.norm_unit_name(to_unit)
    if not a or not b:
        return None, "请把要换算的两个单位都填上", 400
    if a == b:
        return None, "两个单位是同一个，不用设置换算", 400
    f, err = validate_factor(factor)
    if err:
        return None, err, 400
    tid = _tid(tenant_id)
    # 覆盖同一对已有换算（含反向写法）：先删掉旧的再写
    with _engine().connect() as conn:
        old = conn.execute(text(
            "SELECT id, from_unit, to_unit FROM tenant_unit_conversions WHERE tenant_id=:t AND "
            "((from_unit=:a AND to_unit=:b) OR (from_unit=:b AND to_unit=:a))"),
            {"t": tid, "a": a, "b": b}).fetchall()
    msg = _cycle_free_check(tid, a, b, f)
    if msg:
        return None, msg, 400
    _ensure_unit(tid, a)
    _ensure_unit(tid, b)
    now = db.now_iso()
    with _engine().begin() as conn:
        for o in old:
            conn.execute(text("DELETE FROM tenant_unit_conversions WHERE id=:i"), {"i": o[0]})
        res = conn.execute(text(
            "INSERT INTO tenant_unit_conversions (tenant_id, from_unit, to_unit, factor, note, created_at, updated_at) "
            "VALUES (:t,:a,:b,:f,:n,:c,:c)"),
            {"t": tid, "a": a, "b": b, "f": f, "n": (note or "")[:200], "c": now})
        cid = res.lastrowid
    _changed()
    return cid, None, 200


def update_conversion(tenant_id, conv_id, from_unit=None, to_unit=None, factor=None, note=None):
    if int(conv_id) < 0:
        return None, "这是系统自带的换算，不能直接改。请在下面新增同一对单位的换算，会自动覆盖它。", 400
    tid = _tid(tenant_id)
    with _engine().connect() as conn:
        row = conn.execute(text(
            "SELECT from_unit, to_unit, factor, note FROM tenant_unit_conversions WHERE id=:i AND tenant_id=:t"),
            {"i": int(conv_id), "t": tid}).fetchone()
    if row is None:
        return None, "没有找到这条换算", 404
    a = from_unit if from_unit not in (None, "") else row[0]
    b = to_unit if to_unit not in (None, "") else row[1]
    f = factor if factor is not None else row[2]
    n = note if note is not None else (row[3] or "")
    a, b = uf.norm_unit_name(a), uf.norm_unit_name(b)
    if not a or not b:
        return None, "请把要换算的两个单位都填上", 400
    if a == b:
        return None, "两个单位是同一个，不用设置换算", 400
    f, err = validate_factor(f)
    if err:
        return None, err, 400
    msg = _cycle_free_check(tid, a, b, f)
    if msg:
        return None, msg, 400
    _ensure_unit(tid, a)
    _ensure_unit(tid, b)
    with _engine().begin() as conn:
        conn.execute(text(
            "DELETE FROM tenant_unit_conversions WHERE tenant_id=:t AND id<>:i AND "
            "((from_unit=:a AND to_unit=:b) OR (from_unit=:b AND to_unit=:a))"),
            {"t": tid, "i": int(conv_id), "a": a, "b": b})
        conn.execute(text(
            "UPDATE tenant_unit_conversions SET from_unit=:a, to_unit=:b, factor=:f, note=:n, "
            "updated_at=:u WHERE id=:i AND tenant_id=:t"),
            {"a": a, "b": b, "f": f, "n": (n or "")[:200], "u": db.now_iso(),
             "i": int(conv_id), "t": tid})
    _changed()
    return int(conv_id), None, 200


def delete_conversion(tenant_id, conv_id) -> Tuple[bool, Optional[str], int]:
    if int(conv_id) < 0:
        return False, "这是系统自带的换算，不能删除。想改数值，请新增同一对单位的换算来覆盖。", 400
    tid = _tid(tenant_id)
    with _engine().begin() as conn:
        res = conn.execute(text("DELETE FROM tenant_unit_conversions WHERE id=:i AND tenant_id=:t"),
                           {"i": int(conv_id), "t": tid})
    if not res.rowcount:
        return False, "没有找到这条换算", 404
    _changed()
    return True, None, 200


# ---------------------------------------------------------------- 汇率
FX_SEED: Dict[str, float] = {
    "CNY": 1.08, "USD": 7.80, "EUR": 8.40, "GBP": 9.90, "JPY": 0.052,
    "SGD": 5.80, "MOP": 0.97, "AUD": 5.10, "CAD": 5.70, "TWD": 0.24, "THB": 0.22,
}
FX_SEED_NOTE = "参考值，请老板按实际汇率确认"
BASE_CURRENCY = "HKD"


def norm_currency(code) -> str:
    return (str(code or "").strip().upper()) or BASE_CURRENCY


def get_fx_rates(tenant_id) -> List[dict]:
    """每个币种 1 单位折合多少港币。租户自己改过的覆盖种子。"""
    tid = _tid(tenant_id)
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT currency, rate, confirmed, note, updated_at FROM tenant_fx_rates WHERE tenant_id=:t"),
            {"t": tid}).fetchall()
    mine = {r[0]: r for r in rows}
    out = [{"currency": BASE_CURRENCY, "rate": 1.0, "confirmed": True, "note": "本位币",
            "seed": False}]
    for cur in sorted(set(FX_SEED) | set(mine)):
        if cur == BASE_CURRENCY:
            continue
        if cur in mine:
            r = mine[cur]
            out.append({"currency": cur, "rate": float(r[1]), "confirmed": bool(r[2]),
                        "note": r[3] or "", "seed": False})
        else:
            out.append({"currency": cur, "rate": FX_SEED[cur], "confirmed": False,
                        "note": FX_SEED_NOTE, "seed": True})
    return out


def get_fx_rate(tenant_id, currency) -> Optional[float]:
    cur = norm_currency(currency)
    if cur == BASE_CURRENCY:
        return 1.0
    for r in get_fx_rates(tenant_id):
        if r["currency"] == cur:
            return r["rate"]
    return None


def set_fx_rates(tenant_id, rates: List[dict]) -> Tuple[bool, Optional[str]]:
    tid = _tid(tenant_id)
    clean = []
    for r in rates or []:
        cur = norm_currency(r.get("currency"))
        if cur == BASE_CURRENCY:
            continue
        if not (len(cur) == 3 and cur.isalpha()):
            return False, "币种代码「%s」不对，请填三个字母，例如 USD" % cur
        f, err = validate_factor(r.get("rate"))
        if err:
            return False, "「%s」的汇率不对：%s" % (cur, err)
        clean.append((cur, f, 1 if r.get("confirmed", True) else 0, (r.get("note") or "")[:200]))
    now = db.now_iso()
    with _engine().begin() as conn:
        for cur, f, conf, note in clean:
            conn.execute(text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t AND currency=:c"),
                         {"t": tid, "c": cur})
            conn.execute(text(
                "INSERT INTO tenant_fx_rates (tenant_id, currency, rate, confirmed, note, updated_at) "
                "VALUES (:t,:c,:r,:f,:n,:u)"),
                {"t": tid, "c": cur, "r": f, "f": conf, "n": note, "u": now})
    return True, None


def save_receipt_fx(tenant_id, receipt_id, currency, rate):
    with _engine().begin() as conn:
        conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE receipt_id=:r AND tenant_id=:t"),
                     {"r": int(receipt_id), "t": _tid(tenant_id)})
        conn.execute(text(
            "INSERT INTO receipt_fx_snapshots (receipt_id, tenant_id, currency, rate, created_at) "
            "VALUES (:r,:t,:c,:x,:d)"),
            {"r": int(receipt_id), "t": _tid(tenant_id), "c": norm_currency(currency),
             "x": float(rate), "d": db.now_iso()})


def receipt_fx_map(tenant_id) -> Dict[int, float]:
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT receipt_id, rate FROM receipt_fx_snapshots WHERE tenant_id=:t"),
            {"t": _tid(tenant_id)}).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def hkd_rate_for_receipt(tenant_id, receipt_id, currency, snap_map=None, rate_map=None) -> Optional[float]:
    """入账时留下的汇率优先；没有快照就用现在设置里的汇率；都没有返回 None。"""
    cur = norm_currency(currency)
    if cur == BASE_CURRENCY:
        return 1.0
    if snap_map is None:
        snap_map = receipt_fx_map(tenant_id)
    if receipt_id in snap_map:
        return snap_map[receipt_id]
    if rate_map is None:
        rate_map = {r["currency"]: r["rate"] for r in get_fx_rates(tenant_id)}
    return rate_map.get(cur)
