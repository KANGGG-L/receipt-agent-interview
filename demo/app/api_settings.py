# -*- coding: utf-8 -*-
"""商户设置：单位、单位换算、汇率（按租户存储，仅 owner 及以上可读写）。"""

from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import units_store
from app.auth import require_role, resolve_tenant
from app.services import unit_factors as uf

router = APIRouter()


def _tenant_id(request: Request) -> str:
    return resolve_tenant(request)


def _err(status, code, msg, **extra):
    body = {"status": "error", "code": code, "msg": msg}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


def _audit(request, action, detail):
    try:
        from app import db
        account = getattr(request.state, "account", {})
        who = account.get("email", "owner") if isinstance(account, dict) else "owner"
        db.append_system_audit_log(who, action, "settings", "", str(detail)[:300])
    except Exception:
        pass


class UnitBody(BaseModel):
    name: str = ""
    kind: Optional[str] = None


class ConversionBody(BaseModel):
    from_unit: str = ""
    to_unit: str = ""
    factor: Optional[float] = None
    note: str = ""


class ConversionPatch(BaseModel):
    from_unit: Optional[str] = None
    to_unit: Optional[str] = None
    factor: Optional[float] = None
    note: Optional[str] = None


class FxRateItem(BaseModel):
    currency: str
    rate: float
    confirmed: bool = True
    note: str = ""


class FxRatesBody(BaseModel):
    rates: List[FxRateItem] = []


@router.get("/api/settings/units")
def get_units(request: Request):
    require_role("owner")(request)
    tid = _tenant_id(request)
    return {"status": "success", "data": {
        "units": units_store.list_units(tid),
        "conversions": units_store.list_conversions(tid)}}


@router.post("/api/settings/units")
def add_unit(body: UnitBody, request: Request):
    require_role("owner")(request)
    uid, err = units_store.add_unit(_tenant_id(request), body.name, body.kind)
    if err:
        status = 409 if "已经有了" in err else 400
        return _err(status, "UNIT_DUPLICATE" if status == 409 else "UNIT_INVALID", err)
    _audit(request, "settings_unit_add", body.name)
    return {"status": "success", "id": uid, "msg": "单位已添加"}


@router.delete("/api/settings/units/{unit_id}")
def delete_unit(unit_id: int, request: Request):
    require_role("owner")(request)
    ok, msg, code = units_store.delete_unit(_tenant_id(request), unit_id)
    if not ok:
        return _err(code, "UNIT_IN_USE" if code == 409 else "UNIT_DELETE_REFUSED", msg)
    _audit(request, "settings_unit_delete", unit_id)
    return {"status": "success", "msg": "单位已删除"}


@router.get("/api/settings/unit_conversions/check")
def check_conversion(request: Request, from_unit: str = "", to_unit: str = ""):
    require_role("owner")(request)
    res = uf.conversion_path(from_unit, to_unit, _tenant_id(request))
    if res is None:
        return {"status": "success", "convertible": False, "factor": None, "path": []}
    return {"status": "success", "convertible": True, "factor": res[0], "path": res[1]}


@router.post("/api/settings/unit_conversions")
def add_conversion(body: ConversionBody, request: Request):
    require_role("owner")(request)
    if body.factor is None:
        return _err(400, "FACTOR_INVALID", "请填写换算数值，例如 1 箱 = 12 瓶，就填 12")
    cid, err, code = units_store.upsert_conversion(
        _tenant_id(request), body.from_unit, body.to_unit, body.factor, body.note)
    if err:
        return _err(code, "CONVERSION_INVALID", err)
    _audit(request, "settings_conversion_set",
           "%s->%s=%s" % (body.from_unit, body.to_unit, body.factor))
    return {"status": "success", "id": cid, "msg": "换算已保存"}


@router.put("/api/settings/unit_conversions/{conv_id}")
def update_conversion(conv_id: int, body: ConversionPatch, request: Request):
    require_role("owner")(request)
    cid, err, code = units_store.update_conversion(
        _tenant_id(request), conv_id, body.from_unit, body.to_unit, body.factor, body.note)
    if err:
        return _err(code, "CONVERSION_INVALID", err)
    _audit(request, "settings_conversion_update", conv_id)
    return {"status": "success", "id": cid, "msg": "换算已保存"}


@router.delete("/api/settings/unit_conversions/{conv_id}")
def delete_conversion(conv_id: int, request: Request):
    require_role("owner")(request)
    ok, msg, code = units_store.delete_conversion(_tenant_id(request), conv_id)
    if not ok:
        return _err(code, "CONVERSION_DELETE_REFUSED", msg)
    _audit(request, "settings_conversion_delete", conv_id)
    return {"status": "success", "msg": "换算已删除"}


@router.get("/api/settings/fx_rates")
def get_fx_rates(request: Request):
    require_role("owner")(request)
    return {"status": "success", "data": {
        "base_currency": units_store.BASE_CURRENCY,
        "rates": units_store.get_fx_rates(_tenant_id(request))}}


@router.put("/api/settings/fx_rates")
def put_fx_rates(body: FxRatesBody, request: Request):
    require_role("owner")(request)
    ok, err = units_store.set_fx_rates(
        _tenant_id(request), [r.model_dump() for r in body.rates])
    if not ok:
        return _err(400, "FX_INVALID", err)
    _audit(request, "settings_fx_update", len(body.rates))
    return {"status": "success", "msg": "汇率已保存",
            "data": {"base_currency": units_store.BASE_CURRENCY,
                     "rates": units_store.get_fx_rates(_tenant_id(request))}}
