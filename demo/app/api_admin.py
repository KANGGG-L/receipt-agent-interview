# -*- coding: utf-8 -*-
"""admin 端点：引擎/模型配置 + 灰测范围 + AI 复盘。

- /api/admin/engine-config（GET/PUT）：换识别/审核模型、切灰测模式
- /api/admin/grey-test：灰测状态可视化
- /api/review：AI 复盘（价格异动/供应商洞察，owner 可用）
"""

import logging
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

import re
from app import db
from app.auth import require_admin, require_role
from app.services.engine_config_diff import engine_config_field_diff
from app.services.security_guard import mask_secret_key

_SK_KEY_PATTERN = re.compile(r"sk-[a-zA-Z0-9_\-]{8,}")


def mask_engine_config_dict(data: dict) -> dict:
    """递归脱敏引擎配置字典中的所有 API Key 字段。"""
    if not isinstance(data, dict):
        return data
    masked = {}
    for k, v in data.items():
        if isinstance(v, dict):
            masked[k] = mask_engine_config_dict(v)
        elif isinstance(v, list):
            masked[k] = [mask_engine_config_dict(item) if isinstance(item, dict) else item for item in v]
        elif (k == "api_key" or k.endswith("_api_key") or "api_key" in k.lower()) and isinstance(v, str):
            masked[k] = mask_secret_key(v)
        else:
            masked[k] = v
    return masked


def sanitize_audit_log(logs: list) -> list:
    """脱敏系统审计日志列表中的敏感凭证。"""
    if not isinstance(logs, list):
        return []
    sanitized = []
    for item in logs:
        if not isinstance(item, dict):
            sanitized.append(item)
            continue
        entry = dict(item)
        field = str(entry.get("field") or "").lower()
        is_secret_field = (
            field == "api_key"
            or field.endswith("_api_key")
            or "api_key" in field
            or "secret" in field
            or "token" in field
            or "password" in field
        )
        for k, v in entry.items():
            if isinstance(v, str):
                if k in ("old", "new") and is_secret_field:
                    entry[k] = mask_secret_key(v)
                else:
                    entry[k] = _SK_KEY_PATTERN.sub(lambda m: mask_secret_key(m.group(0)), v)
        sanitized.append(entry)
    return sanitized


router = APIRouter()


# -------------------------------------------------------------
# 配置版本与字段级 diff（本轮新增：乐观锁 + 版本历史共用同一实现）
# -------------------------------------------------------------
def _parse_expected_version(raw) -> Optional[int]:
    """解析 If-Match 取值：接受 3 / "3" / W/"3"；`*` 或无法解析一律返回 None（不校验）。

    无法解析时选择「不校验」而非 409：该头可能来自中间代理/浏览器缓存，把它当冲突
    会把一次正常保存变成永远存不上的 409（体验倒退比漏一次并发检测更糟）。
    """
    if raw is None:
        return None
    token = str(raw).strip()
    if token.startswith("W/"):
        token = token[2:].strip()
    token = token.strip().strip('"').strip()
    if not token or token == "*":
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _request_expected_version(request: Request, body) -> Optional[int]:
    """请求声明的期望版本：If-Match 头优先，其次 body.version；都没有则 None。

    None 表示「调用方未参与乐观锁」——旧前端/脚本的 PUT 不带版本，必须继续可保存
    （既有测试与回退通道依赖这一点），故缺失即跳过校验而不是当作 0。
    """
    expected = _parse_expected_version(request.headers.get("If-Match"))
    if expected is None and body is not None:
        body_version = getattr(body, "version", None)
        if body_version is not None:
            try:
                expected = int(body_version)
            except (TypeError, ValueError):
                expected = None
    return expected


def record_engine_config_history(version, actor, action, snapshot, changed_keys):
    """写一条版本历史（写操作成功后调用，落库前由 db 层对 api_key 脱敏）。"""
    db.append_engine_config_history(version, actor, action, snapshot, changed_keys)


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：与 X-Role 同风格取请求头，缺省 default。"""
    return (request.headers.get("X-Tenant-Id")
            or request.headers.get("x-tenant-id") or "default").strip() or "default"


def resolve_tenant_filter(request: Request, query_value=None):
    """观测域租户过滤键（原 api_admin/api_phase2 两处内联块收敛）：
    Query 参数优先 → X-Tenant-Id 头 → 剔除空白；'all'/空 → None（全租户聚合）。"""
    t = query_value if query_value is not None else (
        request.headers.get("X-Tenant-Id") or request.headers.get("x-tenant-id"))
    if t:
        t = t.strip()
    return t if (t and t != "all") else None


class EngineConfigBody(BaseModel):
    """admin 可写契约（阶段 4 已下线「确认死字段」）。

    移除项（见 00-approved-plan §3.4/§7 阶段4）：
    - 8 个 api_format 字段（api_format / grey_api_format / openai_{rec,aud,parse}_api_format /
      grey_openai_{rec,aud,parse}_api_format）：唯一通道固定 chat_completion，管理台不再采集；
    - 6 个 engine 枚举字段（recognition_engine / audit_engine / parse_llm_engine /
      grey_* 三同）：归一逻辑内部恒置 openai，不再由入参驱动。
    这两个字段组仍存在于 models.EngineConfig（供 DB / 回滚快照兼容），但不经 admin 契约传入。
    """
    model_config = ConfigDict(extra="forbid")

    # 配置来源（T1）：body 必须声明，否则 extra=forbid 会让带该字段的请求 422。
    # PUT 保存一律由后端强制置 manual（见 set_engine_config），请求体传入值仅作占位。
    config_source: str = None
    # 配置版本（本轮新增乐观锁）：与 If-Match 头同义，头优先。必须声明，否则
    # extra=forbid 会让带 version 的请求 422。后端不接受它作为落库值，一律自增。
    version: int = None
    # 常规（engine 枚举不再暴露：唯一通道 openai，见类 docstring）
    recognition_model: str = None
    audit_model: str = None
    audit_enabled: bool = None
    openai_rec_base_url: str = None
    openai_rec_api_key: str = None
    openai_rec_model: str = None
    openai_aud_base_url: str = None
    openai_aud_api_key: str = None
    openai_aud_model: str = None
    # 新增开关（与 models.EngineConfig 对齐）：超时 / 审核模式
    call_timeout_seconds: int = None
    # 独立超时
    rec_timeout_seconds: Optional[int] = None
    aud_timeout_seconds: Optional[int] = None
    parse_timeout_seconds: Optional[int] = None
    grey_rec_timeout_seconds: Optional[int] = None
    grey_aud_timeout_seconds: Optional[int] = None
    grey_parse_timeout_seconds: Optional[int] = None
    # 思考强度
    openai_rec_reasoning_effort: Optional[str] = None
    openai_aud_reasoning_effort: Optional[str] = None
    openai_parse_reasoning_effort: Optional[str] = None
    grey_openai_rec_reasoning_effort: Optional[str] = None
    grey_openai_aud_reasoning_effort: Optional[str] = None
    grey_openai_parse_reasoning_effort: Optional[str] = None
    # T11：整图识别重试上限。必须声明，否则 GET 回读后再 PUT（前端回显整份配置）会因
    # models 侧 extra="forbid" 触发 422（与 T1 config_source 同一坑）。
    max_retry_rounds: int = None
    audit_mode: str = None
    # 历史注记：此处原有 recognition_transport / audit_transport / parse_transport
    # 及灰测组 3 个同名开关，随 CLI 引擎（2026-09-02 弃用）一并删除。
    # models.EngineConfig 会在校验前统一摘掉这些遗留键，旧前端 payload 不会 422。
    # 常规解析 LLM
    parse_llm_enabled: bool = None
    parse_llm_model: str = None
    openai_parse_base_url: str = None
    openai_parse_api_key: str = None
    openai_parse_model: str = None
    # 分组测试（灰测）
    grey_enabled: bool = None
    grey_percent: int = None
    grey_assign_mode: str = None
    grey_supplier_ids: list[int] = None
    grey_recognition_model: str = None
    grey_audit_enabled: bool = None
    grey_audit_model: str = None
    grey_openai_rec_base_url: str = None
    grey_openai_rec_api_key: str = None
    grey_openai_rec_model: str = None
    grey_openai_aud_base_url: str = None
    grey_openai_aud_api_key: str = None
    grey_openai_aud_model: str = None
    # 灰测组解析 LLM
    grey_parse_llm_enabled: bool = None
    grey_parse_llm_model: str = None
    grey_openai_parse_base_url: str = None
    grey_openai_parse_api_key: str = None
    grey_openai_parse_model: str = None
    # 各引擎腿供应商名称（保存时自动写入预设网关）
    vendor_name_rec: str = None
    vendor_name_aud: str = None
    vendor_name_parse: str = None
    vendor_name_grey_rec: str = None
    vendor_name_grey_aud: str = None
    vendor_name_grey_parse: str = None

    # 各引擎腿自定义 Token 单价（单位：元/1M Token，ge=0 拦截负数）
    price_input_rec: Optional[float] = Field(default=None, ge=0)
    price_cached_input_rec: Optional[float] = Field(default=None, ge=0)
    price_output_rec: Optional[float] = Field(default=None, ge=0)

    price_input_aud: Optional[float] = Field(default=None, ge=0)
    price_cached_input_aud: Optional[float] = Field(default=None, ge=0)
    price_output_aud: Optional[float] = Field(default=None, ge=0)

    price_input_parse: Optional[float] = Field(default=None, ge=0)
    price_cached_input_parse: Optional[float] = Field(default=None, ge=0)
    price_output_parse: Optional[float] = Field(default=None, ge=0)

    price_input_grey_rec: Optional[float] = Field(default=None, ge=0)
    price_cached_input_grey_rec: Optional[float] = Field(default=None, ge=0)
    price_output_grey_rec: Optional[float] = Field(default=None, ge=0)

    price_input_grey_aud: Optional[float] = Field(default=None, ge=0)
    price_cached_input_grey_aud: Optional[float] = Field(default=None, ge=0)
    price_output_grey_aud: Optional[float] = Field(default=None, ge=0)

    price_input_grey_parse: Optional[float] = Field(default=None, ge=0)
    price_cached_input_grey_parse: Optional[float] = Field(default=None, ge=0)
    price_output_grey_parse: Optional[float] = Field(default=None, ge=0)

    # 各引擎腿高级推理控制参数
    openai_rec_max_tokens: Optional[int] = None
    openai_rec_image_detail: Optional[str] = None
    openai_rec_temperature: Optional[float] = None

    openai_aud_max_tokens: Optional[int] = None
    openai_aud_temperature: Optional[float] = None

    openai_parse_max_tokens: Optional[int] = None
    openai_parse_temperature: Optional[float] = None

    grey_openai_rec_max_tokens: Optional[int] = None
    grey_openai_rec_image_detail: Optional[str] = None
    grey_openai_rec_temperature: Optional[float] = None

    grey_openai_aud_max_tokens: Optional[int] = None
    grey_openai_aud_temperature: Optional[float] = None

    grey_openai_parse_max_tokens: Optional[int] = None
    grey_openai_parse_temperature: Optional[float] = None

    rollback_snapshot: Optional[dict] = None


EngineConfigPayload = EngineConfigBody


def _quick_test_engine(engine_type: str, model_name: str, base_url: str, api_key: str, label: str, api_format: str = "chat_completion"):
    """轻量快速探测：校验 OpenAI/Response/Anthropic 兼容接口可用性与模型/Key合法性。"""
    import requests
    from app.services.security_guard import validate_safe_external_url

    if not base_url or not str(base_url).strip():
        return f"[{label}] Base URL 不能为空"
    if not api_key or not str(api_key).strip():
        return f"[{label}] API Key 不能为空"
    if not model_name or not str(model_name).strip():
        return f"[{label}] 模型名不能为空"

    is_safe, reason = validate_safe_external_url(base_url)
    if not is_safe:
        return f"[{label}] 非法 Base URL (安全阻断): {reason}"

    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    if base.endswith("/responses"):
        base = base[:-len("/responses")].rstrip("/")
    if base.endswith("/messages"):
        base = base[:-len("/messages")].rstrip("/")
    if base.endswith("/models"):
        base = base[:-len("/models")].rstrip("/")

    fmt = str(api_format or "chat_completion").strip().lower()

    if fmt == "anthropic":
        headers = {
            "x-api-key": api_key.strip(),
            "Authorization": f"Bearer {api_key.strip()}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        models_url = f"{base}/models"
        try:
            r = requests.get(models_url, headers=headers, timeout=12, allow_redirects=False)
            if r.status_code in (200, 401):
                return None
            msg_url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
            payload = {
                "model": model_name.strip(),
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }
            r_msg = requests.post(msg_url, headers=headers, json=payload, timeout=12, allow_redirects=False)
            if r_msg.status_code in (200, 400, 401):
                return None
            if r_msg.status_code == 404:
                alt_url = f"{base}/messages" if not base.endswith("/v1") else f"{base}/v1/messages"
                if alt_url != msg_url:
                    r_alt = requests.post(alt_url, headers=headers, json=payload, timeout=12, allow_redirects=False)
                    if r_alt.status_code in (200, 400, 401):
                        return None
            return f"[{label}] Anthropic 接口返回 HTTP {r_msg.status_code}: {r_msg.text[:200]}"
        except Exception as e:
            return f"[{label}] 连接 Anthropic 网关失败: {str(e)}"

    elif fmt == "response":
        models_url = f"{base}/models"
        headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
        try:
            r = requests.get(models_url, headers=headers, timeout=12, allow_redirects=False)
            if r.status_code == 200:
                return None
            resp_url = f"{base}/responses"
            payload = {"model": model_name.strip(), "input": "hi", "max_output_tokens": 5}
            r_resp = requests.post(resp_url, headers=headers, json=payload, timeout=12, allow_redirects=False)
            if r_resp.status_code == 200:
                return None
            if r_resp.status_code in (404, 405):
                chat_url = f"{base}/chat/completions"
                payload_chat = {
                    "model": model_name.strip(),
                    "messages": [{"role": "user", "content": "hi"}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 5,
                }
                r_chat = requests.post(chat_url, headers=headers, json=payload_chat, timeout=12, allow_redirects=False)
                if r_chat.status_code == 200:
                    return None
                return f"[{label}] OpenAI Structured 接口返回 HTTP {r_chat.status_code}: {r_chat.text[:200]}"
            return f"[{label}] OpenAI Responses 接口返回 HTTP {r_resp.status_code}: {r_resp.text[:200]}"
        except Exception as e:
            return f"[{label}] 连接 OpenAI Responses 网关失败: {str(e)}"

    else:
        # chat_completion (标准 OpenAI 协议)
        models_url = f"{base}/models"
        headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
        try:
            r = requests.get(models_url, headers=headers, timeout=12, allow_redirects=False)
            if r.status_code == 200:
                return None
            if r.status_code in (404, 405):
                chat_url = f"{base}/chat/completions"
                payload = {"model": model_name.strip(), "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
                r_chat = requests.post(chat_url, headers=headers, json=payload, timeout=12, allow_redirects=False)
                if r_chat.status_code != 200:
                    return f"[{label}] OpenAI 接口返回 HTTP {r_chat.status_code}: {r_chat.text[:200]}"
                return None
            return f"[{label}] OpenAI 接口返回 HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"[{label}] 连接 OpenAI 网关失败: {str(e)}"


def _normalize_and_sync_engine_updates(updates: dict, cfg=None) -> dict:
    """归一化引擎枚举（恒置 openai）与模型字段双向对齐，并统一解析带掩码的 API Key。

    api_format 双向同步已随管理台暴露下线（阶段4）：该字段不再经 admin 契约传入，
    其 DB 层兼容同步仍保留在 models.EngineConfig 的校验前归一里。
    """
    from app.models import GreyAssignMode, EngineKind

    if cfg is not None:
        key_fields = (
            "openai_rec_api_key",
            "openai_aud_api_key",
            "openai_parse_api_key",
            "grey_openai_rec_api_key",
            "grey_openai_aud_api_key",
            "grey_openai_parse_api_key",
        )
        for key_field in key_fields:
            if key_field in updates and updates[key_field]:
                val_str = str(updates[key_field])
                if "****" in val_str or val_str == "******":
                    updates[key_field] = getattr(cfg, key_field, "") if not isinstance(cfg, dict) else cfg.get(key_field, "")

    if "grey_assign_mode" in updates:
        updates["grey_assign_mode"] = GreyAssignMode(updates["grey_assign_mode"])
    # engine 枚举不再由 admin 契约暴露（阶段4）：内部恒置 openai，不依赖入参。
    # 现行唯一通道即 OpenAI 兼容（EngineKind 已删 CLI 成员），故「入参驱动」与
    # 「恒置 openai」运行时等价；下方 model/base_url 分支的强制 openai 保持不变。
    for key in ("recognition_engine", "audit_engine",
                "grey_recognition_engine", "grey_audit_engine",
                "parse_llm_engine", "grey_parse_llm_engine"):
        updates[key] = EngineKind.OPENAI
    if "grey_percent" in updates:
        updates["grey_percent"] = max(0, min(100, int(updates["grey_percent"])))

    # 纯 OpenAI 参数双向自动同步映射 (单一事实源)
    if updates.get("openai_rec_model"):
        updates["recognition_model"] = updates["openai_rec_model"]
        updates["recognition_engine"] = EngineKind.OPENAI
    elif updates.get("recognition_model"):
        updates["openai_rec_model"] = updates["recognition_model"]
        updates["recognition_engine"] = EngineKind.OPENAI

    if updates.get("openai_aud_model"):
        updates["audit_model"] = updates["openai_aud_model"]
        updates["audit_engine"] = EngineKind.OPENAI
    elif updates.get("audit_model"):
        updates["openai_aud_model"] = updates["audit_model"]
        updates["audit_engine"] = EngineKind.OPENAI

    if updates.get("openai_parse_model"):
        updates["parse_llm_model"] = updates["openai_parse_model"]
        updates["parse_llm_engine"] = EngineKind.OPENAI
    elif updates.get("parse_llm_model"):
        updates["openai_parse_model"] = updates["parse_llm_model"]
        updates["parse_llm_engine"] = EngineKind.OPENAI

    # 灰测组同理
    if updates.get("grey_openai_rec_model"):
        updates["grey_recognition_model"] = updates["grey_openai_rec_model"]
        updates["grey_recognition_engine"] = EngineKind.OPENAI
    elif updates.get("grey_recognition_model"):
        updates["grey_openai_rec_model"] = updates["grey_recognition_model"]
        updates["grey_recognition_engine"] = EngineKind.OPENAI

    if updates.get("grey_openai_aud_model"):
        updates["grey_audit_model"] = updates["grey_openai_aud_model"]
        updates["grey_audit_engine"] = EngineKind.OPENAI
    elif updates.get("grey_audit_model"):
        updates["grey_openai_aud_model"] = updates["grey_audit_model"]
        updates["grey_audit_engine"] = EngineKind.OPENAI

    if updates.get("grey_openai_parse_model"):
        updates["grey_parse_llm_model"] = updates["grey_openai_parse_model"]
        updates["grey_parse_llm_engine"] = EngineKind.OPENAI
    elif updates.get("grey_parse_llm_model"):
        updates["grey_openai_parse_model"] = updates["grey_parse_llm_model"]
        updates["grey_parse_llm_engine"] = EngineKind.OPENAI

    if "openai_rec_base_url" in updates:
        updates["recognition_engine"] = EngineKind.OPENAI
    if "openai_aud_base_url" in updates:
        updates["audit_engine"] = EngineKind.OPENAI
    if "openai_parse_base_url" in updates:
        updates["parse_llm_engine"] = EngineKind.OPENAI
    if "grey_openai_rec_base_url" in updates:
        updates["grey_recognition_engine"] = EngineKind.OPENAI
    if "grey_openai_aud_base_url" in updates:
        updates["grey_audit_engine"] = EngineKind.OPENAI
    if "grey_openai_parse_base_url" in updates:
        updates["grey_parse_llm_engine"] = EngineKind.OPENAI

    from app.models import clamp_max_tokens, clamp_temperature, clamp_image_detail
    for fld in (
        "openai_rec_max_tokens", "openai_aud_max_tokens", "openai_parse_max_tokens",
        "grey_openai_rec_max_tokens", "grey_openai_aud_max_tokens", "grey_openai_parse_max_tokens"
    ):
        if fld in updates and updates[fld] is not None:
            updates[fld] = clamp_max_tokens(updates[fld])
    for fld in (
        "openai_rec_temperature", "openai_aud_temperature", "openai_parse_temperature",
        "grey_openai_rec_temperature", "grey_openai_aud_temperature", "grey_openai_parse_temperature"
    ):
        if fld in updates and updates[fld] is not None:
            updates[fld] = clamp_temperature(updates[fld])
    for fld in ("openai_rec_image_detail", "grey_openai_rec_image_detail"):
        if fld in updates and updates[fld] is not None:
            updates[fld] = clamp_image_detail(updates[fld])

    return updates


@router.post("/api/admin/test-engine-config")
def test_engine_config(body: EngineConfigBody, request: Request):
    """保存前自测校验：对启用的引擎（识别/解析/审核及灰测）发送轻量探测。"""
    require_admin(request)
    from app.models import EngineConfig
    
    cfg = db.get_engine_config()
    updates = _normalize_and_sync_engine_updates(body.model_dump(exclude_none=True), cfg=cfg)
    test_cfg = EngineConfig(**{**cfg.model_dump(), **updates})

    # 1. 常规识别引擎测试
    rec_eng = test_cfg.recognition_engine.value if hasattr(test_cfg.recognition_engine, "value") else str(test_cfg.recognition_engine)
    err = _quick_test_engine(rec_eng, test_cfg.openai_rec_model or test_cfg.recognition_model,
                             test_cfg.openai_rec_base_url, test_cfg.openai_rec_api_key, "识别引擎")
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 2. 常规解析引擎测试（开启时）
    if test_cfg.parse_llm_enabled:
        parse_eng = test_cfg.parse_llm_engine.value if hasattr(test_cfg.parse_llm_engine, "value") else str(test_cfg.parse_llm_engine)
        err = _quick_test_engine(parse_eng, test_cfg.openai_parse_model or test_cfg.parse_llm_model,
                                 test_cfg.openai_parse_base_url, test_cfg.openai_parse_api_key, "解析 LLM")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 3. 常规审核引擎测试（开启时）
    if test_cfg.audit_enabled:
        aud_eng = test_cfg.audit_engine.value if hasattr(test_cfg.audit_engine, "value") else str(test_cfg.audit_engine)
        err = _quick_test_engine(aud_eng, test_cfg.openai_aud_model or test_cfg.audit_model,
                                 test_cfg.openai_aud_base_url, test_cfg.openai_aud_api_key, "审核引擎")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 4. 灰测测试（开启时）
    if test_cfg.grey_enabled and test_cfg.grey_percent > 0:
        g_rec_eng = test_cfg.grey_recognition_engine.value if hasattr(test_cfg.grey_recognition_engine, "value") else str(test_cfg.grey_recognition_engine)
        err = _quick_test_engine(g_rec_eng, test_cfg.grey_openai_rec_model or test_cfg.grey_recognition_model,
                                 test_cfg.grey_openai_rec_base_url, test_cfg.grey_openai_rec_api_key, "灰测识别")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

        if test_cfg.grey_parse_llm_enabled:
            gp_eng = test_cfg.grey_parse_llm_engine.value if hasattr(test_cfg.grey_parse_llm_engine, "value") else str(test_cfg.grey_parse_llm_engine)
            err = _quick_test_engine(gp_eng, test_cfg.grey_openai_parse_model or test_cfg.grey_parse_llm_model,
                                     test_cfg.grey_openai_parse_base_url, test_cfg.grey_openai_parse_api_key, "灰测解析")
            if err:
                return JSONResponse(status_code=400, content={"status": "error", "msg": err})

        if test_cfg.grey_audit_enabled:
            ga_eng = test_cfg.grey_audit_engine.value if hasattr(test_cfg.grey_audit_engine, "value") else str(test_cfg.grey_audit_engine)
            err = _quick_test_engine(ga_eng, test_cfg.grey_openai_aud_model or test_cfg.grey_audit_model,
                                     test_cfg.grey_openai_aud_base_url, test_cfg.grey_openai_aud_api_key, "灰测审核")
            if err:
                return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    return {"status": "success", "msg": "全部生效引擎测试通过"}


@router.put("/api/admin/engine-config/promote")
def promote_grey_config(request: Request):
    """一键推全：灰测组 → 常规组；推全前保存回滚快照。
    同时将灰测组重置为默认（推全后灰测停用，下次从干净态重新启用）。
    """
    account = require_admin(request)
    who = account.get("email", "unknown")
    from app.models import EngineConfig
    from datetime import datetime
    cfg = db.get_engine_config()
    grey = db.get_engine_config()

    # 灰测未启用时视为无内容可推
    if not grey.grey_enabled:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "灰测未启用，无内容可推全",
        })

    # 回滚快照：记录推全前常规组配置 + 操作信息
    prev = cfg.model_dump()
    snap = {
        "prev": {
            "recognition_engine": prev.get("recognition_engine"),
            "recognition_model": prev.get("recognition_model"),
            "audit_engine": prev.get("audit_engine"),
            "audit_model": prev.get("audit_model"),
            "audit_enabled": prev.get("audit_enabled"),
            "openai_rec_base_url": prev.get("openai_rec_base_url"),
            "openai_rec_api_key": prev.get("openai_rec_api_key"),
            "openai_rec_model": prev.get("openai_rec_model"),
            "openai_rec_api_format": prev.get("openai_rec_api_format", prev.get("api_format", "chat_completion")),
            "api_format": prev.get("api_format", prev.get("openai_rec_api_format", "chat_completion")),
            "openai_aud_base_url": prev.get("openai_aud_base_url"),
            "openai_aud_api_key": prev.get("openai_aud_api_key"),
            "openai_aud_model": prev.get("openai_aud_model"),
            "parse_llm_enabled": prev.get("parse_llm_enabled"),
            "parse_llm_engine": prev.get("parse_llm_engine"),
            "parse_llm_model": prev.get("parse_llm_model"),
            "openai_parse_base_url": prev.get("openai_parse_base_url"),
            "openai_parse_api_key": prev.get("openai_parse_api_key"),
            "openai_parse_model": prev.get("openai_parse_model"),
            "rec_timeout_seconds": prev.get("rec_timeout_seconds"),
            "aud_timeout_seconds": prev.get("aud_timeout_seconds"),
            "parse_timeout_seconds": prev.get("parse_timeout_seconds"),
            "openai_rec_reasoning_effort": prev.get("openai_rec_reasoning_effort", ""),
            "openai_aud_reasoning_effort": prev.get("openai_aud_reasoning_effort", ""),
            "openai_parse_reasoning_effort": prev.get("openai_parse_reasoning_effort", ""),
            "price_input_rec": prev.get("price_input_rec"),
            "price_cached_input_rec": prev.get("price_cached_input_rec"),
            "price_output_rec": prev.get("price_output_rec"),
            "price_input_aud": prev.get("price_input_aud"),
            "price_cached_input_aud": prev.get("price_cached_input_aud"),
            "price_output_aud": prev.get("price_output_aud"),
            "price_input_parse": prev.get("price_input_parse"),
            "price_cached_input_parse": prev.get("price_cached_input_parse"),
            "price_output_parse": prev.get("price_output_parse"),
            # 高级推理参数快照
            "openai_rec_max_tokens": prev.get("openai_rec_max_tokens", 4096),
            "openai_rec_image_detail": prev.get("openai_rec_image_detail", "high"),
            "openai_rec_temperature": prev.get("openai_rec_temperature", 0.0),
            "openai_aud_max_tokens": prev.get("openai_aud_max_tokens", 4096),
            "openai_aud_temperature": prev.get("openai_aud_temperature", 0.0),
            "openai_parse_max_tokens": prev.get("openai_parse_max_tokens", 4096),
            "openai_parse_temperature": prev.get("openai_parse_temperature", 0.0),
        },
        "meta": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "operator": account.get("email", "unknown"),
            "action": "promote",
        },
    }

    # 构建新常规配置：从灰测组拉取对应字段
    new_dict = {
        **cfg.model_dump(),
        "recognition_engine": grey.grey_recognition_engine,
        "recognition_model": grey.grey_recognition_model,
        "audit_engine": grey.grey_audit_engine,
        "audit_model": grey.grey_audit_model,
        "audit_enabled": grey.grey_audit_enabled,
        "openai_rec_base_url": grey.grey_openai_rec_base_url,
        "openai_rec_api_key": grey.grey_openai_rec_api_key,
        "openai_rec_model": grey.grey_openai_rec_model,
        "openai_rec_api_format": getattr(grey, "grey_openai_rec_api_format", getattr(grey, "grey_api_format", "chat_completion")),
        "api_format": getattr(grey, "grey_openai_rec_api_format", getattr(grey, "grey_api_format", "chat_completion")),
        "openai_aud_base_url": grey.grey_openai_aud_base_url,
        "openai_aud_api_key": grey.grey_openai_aud_api_key,
        "openai_aud_model": grey.grey_openai_aud_model,
        "parse_llm_enabled": grey.grey_parse_llm_enabled,
        "parse_llm_engine": grey.grey_parse_llm_engine,
        "parse_llm_model": grey.grey_parse_llm_model,
        "openai_parse_base_url": grey.grey_openai_parse_base_url,
        "openai_parse_api_key": grey.grey_openai_parse_api_key,
        "openai_parse_model": grey.grey_openai_parse_model,
        "rec_timeout_seconds": grey.grey_rec_timeout_seconds,
        "aud_timeout_seconds": grey.grey_aud_timeout_seconds,
        "parse_timeout_seconds": grey.grey_parse_timeout_seconds,
        "openai_rec_reasoning_effort": grey.grey_openai_rec_reasoning_effort,
        "openai_aud_reasoning_effort": grey.grey_openai_aud_reasoning_effort,
        "openai_parse_reasoning_effort": grey.grey_openai_parse_reasoning_effort,
        "price_input_rec": grey.price_input_grey_rec,
        "price_cached_input_rec": grey.price_cached_input_grey_rec,
        "price_output_rec": grey.price_output_grey_rec,
        "price_input_aud": grey.price_input_grey_aud,
        "price_cached_input_aud": grey.price_cached_input_grey_aud,
        "price_output_aud": grey.price_output_grey_aud,
        "price_input_parse": grey.price_input_grey_parse,
        "price_cached_input_parse": grey.price_cached_input_grey_parse,
        "price_output_parse": grey.price_output_grey_parse,
        # 晋升灰测高级推理参数至常规组
        "openai_rec_max_tokens": getattr(grey, "grey_openai_rec_max_tokens", 4096),
        "openai_rec_image_detail": getattr(grey, "grey_openai_rec_image_detail", "high"),
        "openai_rec_temperature": getattr(grey, "grey_openai_rec_temperature", 0.0),
        "openai_aud_max_tokens": getattr(grey, "grey_openai_aud_max_tokens", 4096),
        "openai_aud_temperature": getattr(grey, "grey_openai_aud_temperature", 0.0),
        "openai_parse_max_tokens": getattr(grey, "grey_openai_parse_max_tokens", 4096),
        "openai_parse_temperature": getattr(grey, "grey_openai_parse_temperature", 0.0),
        "rollback_snapshot": snap,
    }

    new_cfg = EngineConfig(**new_dict)
    # T1：推全是管理台人工改配，置 manual 以免重启被 .env 打回
    db.set_engine_config(new_cfg, source="manual")

    # 把灰测组清空/重置回默认（关闭灰测，恢复默认识别/审核模型，关闭灰测审核/解析开关）
    from app.models import GreyAssignMode, EngineKind, is_legacy_cli_engine
    grey_defaults = {
        "grey_enabled": False,
        "grey_percent": 0,
        "grey_assign_mode": GreyAssignMode.RECEIPT,
        "grey_recognition_engine": EngineKind.OPENAI,
        "grey_recognition_model": "Qwen/Qwen3-VL-32B-Instruct",
        "grey_audit_enabled": True,
        "grey_audit_engine": EngineKind.OPENAI,
        "grey_audit_model": "zai-org/GLM-4.5V",
        "grey_openai_rec_base_url": "",
        "grey_openai_rec_api_key": "",
        "grey_openai_rec_model": "",
        "grey_openai_rec_api_format": "chat_completion",
        "grey_api_format": "chat_completion",
        "grey_openai_aud_base_url": "",
        "grey_openai_aud_api_key": "",
        "grey_openai_aud_model": "",
        "grey_parse_llm_enabled": False,
        "grey_parse_llm_engine": EngineKind.OPENAI,
        "grey_parse_llm_model": "Qwen/Qwen3-VL-32B-Instruct",
        "grey_openai_parse_base_url": "",
        "grey_openai_parse_api_key": "",
        "grey_openai_parse_model": "",
        "grey_rec_timeout_seconds": None,
        "grey_aud_timeout_seconds": None,
        "grey_parse_timeout_seconds": None,
        "grey_openai_rec_reasoning_effort": "",
        "grey_openai_aud_reasoning_effort": "",
        "grey_openai_parse_reasoning_effort": "",
        "price_input_grey_rec": None,
        "price_cached_input_grey_rec": None,
        "price_output_grey_rec": None,
        "price_input_grey_aud": None,
        "price_cached_input_grey_aud": None,
        "price_output_grey_aud": None,
        "price_input_grey_parse": None,
        "price_cached_input_grey_parse": None,
        "price_output_grey_parse": None,
        # 灰测高级推理参数复位为默认
        "grey_openai_rec_max_tokens": 4096,
        "grey_openai_rec_image_detail": "high",
        "grey_openai_rec_temperature": 0.0,
        "grey_openai_aud_max_tokens": 4096,
        "grey_openai_aud_temperature": 0.0,
        "grey_openai_parse_max_tokens": 4096,
        "grey_openai_parse_temperature": 0.0,
    }
    final_cfg = EngineConfig(**{**new_cfg.model_dump(), **grey_defaults,
                                "version": int(getattr(cfg, "version", 1) or 1) + 1})
    db.set_engine_config(final_cfg, source="manual")

    old_model = cfg.recognition_model or cfg.openai_rec_model or "deepseek/deepseek-v4.1-flash"
    new_model = final_cfg.recognition_model or final_cfg.openai_rec_model or getattr(grey, "grey_recognition_model", "Qwen/Qwen3-VL-32B-Instruct")
    db.append_system_audit_log(who, "promote_grey_config",
                               "recognition_model",
                               str(old_model), str(new_model))
    # 推全也是一次配置写入：与 PUT 同口径落版本历史（action=promote），否则
    # 「谁在什么时候把灰测推成常规」在版本历史里查不到。
    record_engine_config_history(
        final_cfg.version, who, "promote", final_cfg.model_dump(),
        [d["key"] for d in engine_config_field_diff(cfg.model_dump(), final_cfg.model_dump(),
                                                    ignore=("version",))])

    return {"status": "success", "data": mask_engine_config_dict(final_cfg.model_dump()),
            "version": final_cfg.version}


@router.put("/api/admin/engine-config/rollback")
def rollback_engine_config(request: Request):
    """一键回滚：用最近一次推全快照覆盖回常规组并清空快照。

    T9：核心逻辑收敛到 services.guardian.perform_engine_rollback，
    与实验守护自动回滚同一事实源。
    """
    account = require_admin(request)
    who = account.get("email", "unknown")
    from app.services.guardian import perform_engine_rollback
    # 版本自增与 action=rollback 的版本历史由 perform_engine_rollback 落库
    # （管理台与实验守护自动回滚同一事实源，见该函数 docstring）。
    ok, new_cfg = perform_engine_rollback(who=who)
    if not ok:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "没有可回滚的快照（从未执行过推全，或快照已被清空）",
        })
    return {
        "status": "success",
        "data": mask_engine_config_dict(new_cfg.model_dump()),
        "msg": "已回滚至上次推全前的配置",
        "version": int(getattr(new_cfg, "version", 1) or 1),
    }



@router.get("/api/admin/engine-config/diff")
def diff_regular_vs_grey(request: Request):
    """常规 vs 灰测配置对比：字段级差异列表（敏感字段仅提示是否一致）。"""
    require_admin(request)
    cfg = db.get_engine_config()
    if not cfg.grey_enabled:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "灰测未启用，无内容可对比",
        })

    pairs = [
        ("recognition_engine", "grey_recognition_engine", "识别引擎"),
        ("recognition_model", "grey_recognition_model", "识别模型"),
        ("audit_enabled", "grey_audit_enabled", "审核开关"),
        ("audit_engine", "grey_audit_engine", "审核引擎"),
        ("audit_model", "grey_audit_model", "审核模型"),
        ("parse_llm_enabled", "grey_parse_llm_enabled", "解析 LLM 开关"),
        ("parse_llm_engine", "grey_parse_llm_engine", "解析引擎"),
        ("parse_llm_model", "grey_parse_llm_model", "解析模型"),
        ("openai_rec_model", "grey_openai_rec_model", "识别 · OpenAI 模型名"),
        ("openai_aud_model", "grey_openai_aud_model", "审核 · OpenAI 模型名"),
        ("openai_parse_model", "grey_openai_parse_model", "解析 · OpenAI 模型名"),
        ("rec_timeout_seconds", "grey_rec_timeout_seconds", "识别 · 调用超时 (秒)"),
        ("aud_timeout_seconds", "grey_aud_timeout_seconds", "审核 · 调用超时 (秒)"),
        ("parse_timeout_seconds", "grey_parse_timeout_seconds", "解析 · 调用超时 (秒)"),
        ("openai_rec_reasoning_effort", "grey_openai_rec_reasoning_effort", "识别 · 思考强度"),
        ("openai_aud_reasoning_effort", "grey_openai_aud_reasoning_effort", "审核 · 思考强度"),
        ("openai_parse_reasoning_effort", "grey_openai_parse_reasoning_effort", "解析 · 思考强度"),
        ("price_input_rec", "price_input_grey_rec", "识别 · Input 价格 (元/1M)"),
        ("price_cached_input_rec", "price_cached_input_grey_rec", "识别 · Cached Input 价格 (元/1M)"),
        ("price_output_rec", "price_output_grey_rec", "识别 · Output 价格 (元/1M)"),
        ("price_input_aud", "price_input_grey_aud", "审核 · Input 价格 (元/1M)"),
        ("price_cached_input_aud", "price_cached_input_grey_aud", "审核 · Cached Input 价格 (元/1M)"),
        ("price_output_aud", "price_output_grey_aud", "审核 · Output 价格 (元/1M)"),
        ("price_input_parse", "price_input_grey_parse", "解析 · Input 价格 (元/1M)"),
        ("price_cached_input_parse", "price_cached_input_grey_parse", "解析 · Cached Input 价格 (元/1M)"),
        ("price_output_parse", "price_output_grey_parse", "解析 · Output 价格 (元/1M)"),
        # 高级推理参数对比项
        ("openai_rec_max_tokens", "grey_openai_rec_max_tokens", "识别 · Max Tokens"),
        ("openai_aud_max_tokens", "grey_openai_aud_max_tokens", "审核 · Max Tokens"),
        ("openai_parse_max_tokens", "grey_openai_parse_max_tokens", "解析 · Max Tokens"),
        ("openai_rec_image_detail", "grey_openai_rec_image_detail", "识别 · 视觉切片粒度"),
        ("openai_rec_temperature", "grey_openai_rec_temperature", "识别 · 采样温度"),
        ("openai_aud_temperature", "grey_openai_aud_temperature", "审核 · 采样温度"),
        ("openai_parse_temperature", "grey_openai_parse_temperature", "解析 · 采样温度"),
    ]
    # OpenAI 参数区：敏感字段（api_key / base_url）不直接展示值，仅提示一致/不一致
    sensitive_pairs = [
        ("openai_rec_base_url", "grey_openai_rec_base_url", "识别 · OpenAI Base URL"),
        ("openai_aud_base_url", "grey_openai_aud_base_url", "审核 · OpenAI Base URL"),
        ("openai_parse_base_url", "grey_openai_parse_base_url", "解析 · OpenAI Base URL"),
    ]
    secret_pairs = [
        ("openai_rec_api_key", "grey_openai_rec_api_key", "识别 · OpenAI API Key"),
        ("openai_aud_api_key", "grey_openai_aud_api_key", "审核 · OpenAI API Key"),
        ("openai_parse_api_key", "grey_openai_parse_api_key", "解析 · OpenAI API Key"),
    ]

    def _norm(v):
        return v.value if hasattr(v, "value") else v

    diffs = []
    for reg_key, grey_key, label in pairs:
        reg = _norm(cfg.model_dump().get(reg_key))
        grey = _norm(cfg.model_dump().get(grey_key))
        if str(reg) != str(grey):
            diffs.append({
                "label": label,
                "regular": reg,
                "grey": grey,
                "sensitive": False,
                "identical": False,
            })
    for reg_key, grey_key, label in sensitive_pairs:
        reg = cfg.model_dump().get(reg_key)
        grey = cfg.model_dump().get(grey_key)
        identical = bool(reg) == bool(grey) and str(reg or "") == str(grey or "")
        if not identical:
            diffs.append({
                "label": label,
                "regular": "***" if reg else "",
                "grey": "***" if grey else "",
                "sensitive": True,
                "identical": identical,
            })
    for reg_key, grey_key, label in secret_pairs:
        reg = cfg.model_dump().get(reg_key)
        grey = cfg.model_dump().get(grey_key)
        identical = bool(reg) == bool(grey) and str(reg or "") == str(grey or "")
        diffs.append({
            "label": label,
            "regular": "********" if reg else "",
            "grey": "********" if grey else "",
            "sensitive": True,
            "identical": identical,
        })

    diffCount = sum(1 for d in diffs if not d.get("identical"))
    return {
        "status": "success",
        "data": {
            "diffs": diffs,
            "totalPairs": len(pairs) + len(sensitive_pairs) + len(secret_pairs),
            "diffCount": diffCount,
        },
    }


@router.get("/api/admin/engine-presets")
def get_engine_presets(request: Request):
    """返回常用预设网关配置，自动融入 .env 中的真实密钥。"""
    require_admin(request)
    dashscope_key = (
        os.environ.get("DASHSCOPE_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    siliconflow_key = (
        os.environ.get("SILICONFLOW_API_KEY", "")
        or (os.environ.get("OPENAI_API_KEY", "") if "siliconflow" in os.environ.get("OPENAI_BASE_URL", "").lower() else "")
        or os.environ.get("OPENAI_REC_API_KEY", "")
    )
    
    presets = {
        "agnes": {
            "label": "Agnes AI",
            "base_url": "https://api.agnes.ai/v1",
            "api_key": "",
            "rec_model": "agnes-2.0-flash",
            "aud_model": "agnes-2.0-flash",
            "rec_price_input": 0.5,
            "rec_price_cached": 0.1,
            "rec_price_output": 2.0,
            "aud_price_input": 0.5,
            "aud_price_cached": 0.1,
            "aud_price_output": 2.0,
        },
        "bailian": {
            "label": "阿里云百炼 · DashScope",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": dashscope_key,
            "rec_model": "qwen3.5-omni-flash",
            "aud_model": "qwen3-vl-plus",
            "rec_price_input": 2.2,
            "rec_price_cached": 0.55,
            "rec_price_output": 13.3,
            "aud_price_input": 1.5,
            "aud_price_cached": 0.375,
            "aud_price_output": 4.5,
        },
        "siliconflow": {
            "label": "SiliconFlow · 硅基流动",
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": siliconflow_key,
            "rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "aud_model": "zai-org/GLM-4.5V",
            "rec_price_input": 2.0,
            "rec_price_cached": 0.5,
            "rec_price_output": 6.0,
            "aud_price_input": 1.0,
            "aud_price_cached": 0.25,
            "aud_price_output": 6.0,
        },
    }
    # 合并用户通过管理台保存的自定义预设
    try:
        import json as _json
        custom_raw = db.get_app_setting("custom_engine_presets", "{}")
        custom = _json.loads(custom_raw or "{}")
        if isinstance(custom, dict):
            presets.update(custom)
    except Exception:
        pass
    # P0：预设回传前一律对 api_key 脱敏 —— 该端点此前明文返回 .env 真实密钥。
    # 逐条 copy 后再改，避免污染 presets（自定义预设来自 app_settings 的内存副本）。
    # 无密钥的预设保持空串（mask_secret_key("") == ""），不引入占位噪声。
    masked = {}
    for preset_key, preset in presets.items():
        if isinstance(preset, dict):
            entry = dict(preset)
            entry["api_key"] = mask_secret_key(entry.get("api_key"))
        else:
            entry = preset
        masked[preset_key] = entry
    return {"status": "success", "data": masked}


class _SavePresetBody(BaseModel):
    key: str
    label: str
    base_url: str
    api_key: str = ""
    rec_model: str = ""
    aud_model: str = ""
    parse_model: str = ""
    rec_price_input: Optional[float] = None
    rec_price_cached: Optional[float] = None
    rec_price_output: Optional[float] = None
    aud_price_input: Optional[float] = None
    aud_price_cached: Optional[float] = None
    aud_price_output: Optional[float] = None


@router.post("/api/admin/engine-presets")
def save_engine_preset(body: _SavePresetBody, request: Request):
    """保存一条自定义预设网关配置到 app_settings。key 为唯一标识符（会覆盖同 key 旧值）。"""
    require_admin(request)
    import json as _json
    import re
    # key 只允许字母/数字/下划线/连字符，最长 64 字符
    safe_key = re.sub(r"[^a-zA-Z0-9_\-]", "_", (body.key or "").strip())[:64]
    if not safe_key:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"status": "error", "msg": "preset key 不能为空"})
    label = (body.label or "").strip()[:100]
    if not label:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"status": "error", "msg": "供应商名称不能为空"})
    custom_raw = db.get_app_setting("custom_engine_presets", "{}")
    try:
        custom = _json.loads(custom_raw or "{}")
    except Exception:
        custom = {}
    custom[safe_key] = {
        "label": label,
        "base_url": body.base_url.strip(),
        "api_key": body.api_key.strip(),
        "rec_model": body.rec_model.strip(),
        "aud_model": body.aud_model.strip(),
        "parse_model": body.parse_model.strip(),
        "is_custom": True,
    }
    db.set_app_setting("custom_engine_presets", _json.dumps(custom, ensure_ascii=False))
    return {"status": "success", "key": safe_key, "data": custom}





@router.get("/api/admin/engine-config")
def get_engine_config(request: Request):
    require_admin(request)
    cfg = db.get_engine_config()
    from app.llm import get_timeout_advice, _resolve_timeout
    from app.models import MAX_CALL_TIMEOUT_SECONDS, is_legacy_cli_engine
    advice = get_timeout_advice(cfg)
    # 性能基线与当前达标判定
    engine_str = str(getattr(cfg, "recognition_engine", "") or "")
    if hasattr(cfg.recognition_engine, "value"):
        engine_str = str(cfg.recognition_engine.value)
    engine_str = engine_str.lower()
    has_valid_qwen = False
    try:
        from app.llm import _is_valid_dashscope_config
        has_valid_qwen = _is_valid_dashscope_config(
            getattr(cfg, "openai_rec_base_url", ""), getattr(cfg, "openai_rec_api_key", ""))
    except Exception:
        has_valid_qwen = False
    perf = {
        "baseline": "qwen3.5-omni-flash 单张 2.6-7.1s（2026-09-15 实测，3 张 confirmed 样本；旧基线 qwen3-vl-flash P50 9.0s 已废止）",
        "local_expected": "纯 API 驱动 (SiliconFlow / DashScope)，CLI 引擎已彻底废弃",
        "current_p95_compliant": (engine_str == "openai" and has_valid_qwen),
        "call_timeout_seconds": getattr(cfg, "call_timeout_seconds", 60),
        # T9 收尾：显式回显「实际生效超时」，避免管理台只看配置值而误判
        "effective_call_timeout_seconds": _resolve_timeout(cfg),
        "effective_timeouts": {
            "rec": _resolve_timeout(cfg, side="rec", use_grey=False),
            "aud": _resolve_timeout(cfg, side="aud", use_grey=False),
            "parse": _resolve_timeout(cfg, side="parse", use_grey=False),
            "grey_rec": _resolve_timeout(cfg, side="rec", use_grey=True),
            "grey_aud": _resolve_timeout(cfg, side="aud", use_grey=True),
            "grey_parse": _resolve_timeout(cfg, side="parse", use_grey=True),
        },
        "timeout_policy": (f"实际生效超时硬上限 {MAX_CALL_TIMEOUT_SECONDS}s"
                           "（llm._resolve_timeout 钳制 min(v, 硬上限)）；"
                           "omni 实测 2.6-7.1s 结余充足，禁止长期空转"),
    }
    data = mask_engine_config_dict(cfg.model_dump())

    resp = {"status": "success", "data": data, "perf": perf}

    if advice:
        resp["timeout_advice"] = advice
        resp["warning"] = advice
    if is_legacy_cli_engine(engine_str):
        resp["degradation_notice"] = (
            "提示：历史本地 CLI 引擎已彻底下线，系统已自动转换为 OpenAI 兼容接口。"
        )
    return resp


# -------------------------------------------------------------
# 分组瘦身 DTO（本轮新增）：admin 视图按模块 A/B/C/D 取数
# -------------------------------------------------------------
# 六腿映射：leg -> (字段前缀, 超时字段, api_key 字段, 单价后缀)
_ROUTING_LEGS = (
    ("rec", "openai_rec_", "rec_timeout_seconds", "openai_rec_api_key", "_rec"),
    ("aud", "openai_aud_", "aud_timeout_seconds", "openai_aud_api_key", "_aud"),
    ("parse", "openai_parse_", "parse_timeout_seconds", "openai_parse_api_key", "_parse"),
    ("grey_rec", "grey_openai_rec_", "grey_rec_timeout_seconds", "grey_openai_rec_api_key", "_grey_rec"),
    ("grey_aud", "grey_openai_aud_", "grey_aud_timeout_seconds", "grey_openai_aud_api_key", "_grey_aud"),
    ("grey_parse", "grey_openai_parse_", "grey_parse_timeout_seconds", "grey_openai_parse_api_key", "_grey_parse"),
)


def _routing_leg_dto(cfg, prefix: str, timeout_field: str, key_field: str) -> dict:
    """单腿瘦身视图：凭据只暴露 has_key 布尔位，绝不回传 api_key 明文。"""
    advanced = {
        "api_format": str(getattr(cfg, prefix + "api_format", "chat_completion") or "chat_completion"),
        "reasoning_effort": str(getattr(cfg, prefix + "reasoning_effort", "") or ""),
        "max_tokens": getattr(cfg, prefix + "max_tokens", None),
        "temperature": getattr(cfg, prefix + "temperature", None),
    }
    # image_detail 仅识别腿（常规 + 灰测）存在，其余腿不凭空补键
    image_detail = getattr(cfg, prefix + "image_detail", None)
    if image_detail is not None:
        advanced["image_detail"] = image_detail
    return {
        "base_url": str(getattr(cfg, prefix + "base_url", "") or ""),
        "model": str(getattr(cfg, prefix + "model", "") or ""),
        "has_key": bool(str(getattr(cfg, key_field, "") or "").strip()),
        "timeout_seconds": getattr(cfg, timeout_field, None),
        "advanced": advanced,
    }


@router.get("/api/admin/engine-config/routing")
def get_engine_config_routing(request: Request):
    """分组瘦身视图（模块 A/B/C/D）+ version + has_key 位。

    为什么单开一条而不是改造旧 GET：旧 GET 是 1:1 `model_dump()` 全量抛出（含 engine
    枚举、vendor_name_*、rollback_snapshot 等内部态），既有测试直接读
    `data["perf"]["effective_timeouts"]`／`data["rollback_snapshot"]["prev"]`，
    改形态会破坏既有契约。本端点只服务 admin 视图，**禁含** api_key 明文、
    rollback_snapshot、vendor_name_*、engine 枚举（见 00-approved-plan §3.3）。
    """
    require_admin(request)
    cfg = db.get_engine_config()

    legs = {}
    for leg, prefix, timeout_field, key_field, _price_suffix in _ROUTING_LEGS:
        legs[leg] = _routing_leg_dto(cfg, prefix, timeout_field, key_field)

    prices = {}
    for leg, _prefix, _timeout_field, _key_field, price_suffix in _ROUTING_LEGS:
        prices[leg] = {
            "input": getattr(cfg, "price_input" + price_suffix, None),
            "cached_input": getattr(cfg, "price_cached_input" + price_suffix, None),
            "output": getattr(cfg, "price_output" + price_suffix, None),
        }

    regular_legs = {k: legs[k] for k in ("rec", "aud", "parse")}
    grey_legs = {k: legs[k] for k in ("grey_rec", "grey_aud", "grey_parse")}

    data = {
        "version": int(getattr(cfg, "version", 1) or 1),
        "config_source": str(getattr(cfg, "config_source", "auto") or "auto"),
        "has_key": {leg: legs[leg]["has_key"] for leg in legs},
        # 模块 A：引擎与凭据（按腿分组 + 全局默认超时 + 启用开关；高级工程参数内嵌折叠）
        "module_a": {
            "call_timeout_seconds": getattr(cfg, "call_timeout_seconds", None),
            "audit_enabled": bool(getattr(cfg, "audit_enabled", False)),
            "audit_mode": str(getattr(cfg, "audit_mode", "") or ""),
            "parse_llm_enabled": bool(getattr(cfg, "parse_llm_enabled", False)),
            "legs": regular_legs,
        },
        # 模块 B：成本与计费（18 个单价按 6 腿分组，留空 = 回落模型刊例价）
        "module_b": {"unit": "元/1M Token", "prices": prices},
        # 模块 C：发布与流量管理（金丝雀区；A/B 实验区由既有实验端点提供）
        "module_c": {
            "grey_enabled": bool(getattr(cfg, "grey_enabled", False)),
            "grey_percent": int(getattr(cfg, "grey_percent", 0) or 0),
            "grey_assign_mode": str(getattr(cfg, "grey_assign_mode", "") or ""),
            "grey_supplier_ids": list(getattr(cfg, "grey_supplier_ids", None) or []),
            "grey_audit_enabled": bool(getattr(cfg, "grey_audit_enabled", False)),
            "grey_parse_llm_enabled": bool(getattr(cfg, "grey_parse_llm_enabled", False)),
            "legs": grey_legs,
        },
        # 模块 D：系统与治理（阈值/记忆预算/预处理开关由 /api/admin/settings 提供）
        "module_d": {
            "config_source": str(getattr(cfg, "config_source", "auto") or "auto"),
            "rollback_available": bool(getattr(cfg, "rollback_snapshot", None)),
            "system_settings_endpoint": "/api/admin/settings",
        },
    }
    return {"status": "success", "data": data}


@router.get("/api/admin/engine-config/history")
def get_engine_config_history(request: Request, page: int = 1, page_size: int = 20):
    """版本历史（分页）：每项 {version, actor, action, created_at, changed_keys} + total。

    page_size 上限 100（规格 §4.4）；snapshot 只在库里，不经任何端点回传。
    """
    require_admin(request)
    return {"status": "success",
            "data": db.list_engine_config_history(page=page, page_size=page_size)}


def _is_placeholder_api_key(key) -> bool:
    """空密钥或字面占位符（YOUR_xxx）→ True，保存前拦截。"""
    k = str(key or "").strip()
    if not k:
        return True
    return "YOUR_" in k.upper()


def _auto_save_vendor_presets(new_cfg, new_dict: dict):
    """保存引擎配置时，对供应商名非空的引擎腿，自动写入/更新自定义预设网关。

    每条预设的 key = vendor_name 去除特殊字符后的安全标识符（同名覆盖）。
    各引擎腿共享同一个 key（供应商相同时合并为一条预设，model 取各腿之和）。
    """
    import json as _json
    import re

    # 每腿：(vendor_name_field, base_url_field, api_key_field, rec_model_field, aud_model_field, parse_model_field, is_aud, is_parse)
    LEGS = [
        ("vendor_name_rec",        "openai_rec_base_url",       "openai_rec_api_key",       "openai_rec_model",       None,                 None,                    False, False),
        ("vendor_name_aud",        "openai_aud_base_url",       "openai_aud_api_key",        None,                    "openai_aud_model",   None,                    True,  False),
        ("vendor_name_parse",      "openai_parse_base_url",     "openai_parse_api_key",      None,                    None,                 "openai_parse_model",    False, True),
        ("vendor_name_grey_rec",   "grey_openai_rec_base_url",  "grey_openai_rec_api_key",   "grey_openai_rec_model", None,                 None,                    False, False),
        ("vendor_name_grey_aud",   "grey_openai_aud_base_url",  "grey_openai_aud_api_key",   None,                    "grey_openai_aud_model", None,                 True,  False),
        ("vendor_name_grey_parse", "grey_openai_parse_base_url","grey_openai_parse_api_key", None,                    None,                 "grey_openai_parse_model", False, True),
    ]

    def _safe_key(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())[:64].strip("_")

    cfg_dict = new_cfg.model_dump()
    custom_raw = db.get_app_setting("custom_engine_presets", "{}")
    try:
        custom = _json.loads(custom_raw or "{}")
    except Exception:
        custom = {}

    for vendor_field, url_field, key_field, rec_f, aud_f, parse_f, _is_aud, _is_parse in LEGS:
        vendor_name = (cfg_dict.get(vendor_field) or "").strip()
        if not vendor_name:
            continue
        safe_key = _safe_key(vendor_name)
        if not safe_key:
            continue
        base_url = (cfg_dict.get(url_field) or "").strip()
        api_key_raw = (cfg_dict.get(key_field) or "").strip()
        rec_model = (cfg_dict.get(rec_f) or "").strip() if rec_f else ""
        aud_model = (cfg_dict.get(aud_f) or "").strip() if aud_f else ""
        parse_model = (cfg_dict.get(parse_f) or "").strip() if parse_f else ""
        # 合并同 key 已有条目（保留现有 rec/aud/parse_model 不被单腿覆盖为空）
        existing = custom.get(safe_key, {})
        merged_rec = rec_model or existing.get("rec_model", "")
        merged_aud = aud_model or existing.get("aud_model", "")
        merged_parse = parse_model or existing.get("parse_model", "")
        # api_key 若含掩码（****）则保留旧值
        merged_key = api_key_raw if api_key_raw and "****" not in api_key_raw else existing.get("api_key", "")
        custom[safe_key] = {
            "label": vendor_name,
            "base_url": base_url or existing.get("base_url", ""),
            "api_key": merged_key,
            "rec_model": merged_rec,
            "aud_model": merged_aud,
            "parse_model": merged_parse,
            "is_custom": True,
        }

    db.set_app_setting("custom_engine_presets", _json.dumps(custom, ensure_ascii=False))


@router.put("/api/admin/engine-config")
def set_engine_config(body: EngineConfigBody, request: Request):
    require_admin(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")

    from app.models import EngineConfig
    from app.llm import _is_valid_dashscope_url, _is_valid_sk, _is_valid_dashscope_config, get_timeout_advice
    cfg = db.get_engine_config()
    # 乐观锁（本轮新增）：If-Match 头或 body.version 与库内版本不符即 409，附当前版本与
    # 字段级 diff（changed_keys + old/new）。两个 admin 会话并发保存时，后到的写入被拒绝，
    # 而不是静默覆盖前者的改动。
    stored_version = int(getattr(cfg, "version", 1) or 1)
    expected_version = _request_expected_version(request, body)
    if expected_version is not None and expected_version != stored_version:
        submitted = {k: v for k, v in body.model_dump(exclude_none=True).items()
                     if k != "version"}
        pending = engine_config_field_diff(cfg.model_dump(), submitted, ignore=("version",))
        return JSONResponse(status_code=409, content={
            "status": "error",
            "code": "VERSION_CONFLICT",
            "msg": f"配置已被他人修改（当前 v{stored_version}，你提交的是 v{expected_version}）。"
                   "请重新加载后再保存。",
            "current_version": stored_version,
            "version": stored_version,
            "changed_keys": [d["key"] for d in pending],
            "diff": pending,
        })
    updates = _normalize_and_sync_engine_updates(body.model_dump(exclude_none=True), cfg=cfg)
    # 版本不接受请求体声明：落库值一律由后端自增（与 config_source 同一口径）。
    updates.pop("version", None)
    # 对分腿独立超时，若请求显式传递了字段（如传递 null 恢复继承全局），予以放行
    for tf in (
        "rec_timeout_seconds",
        "aud_timeout_seconds",
        "parse_timeout_seconds",
        "grey_rec_timeout_seconds",
        "grey_aud_timeout_seconds",
        "grey_parse_timeout_seconds",
    ):
        if tf in body.model_fields_set:
            updates[tf] = getattr(body, tf)

    # 对各引擎腿自定义 Token 单价，若请求显式传递了字段（含传递 null 恢复使用默认刊例价），予以放行
    price_fields = (
        "price_input_rec", "price_cached_input_rec", "price_output_rec",
        "price_input_aud", "price_cached_input_aud", "price_output_aud",
        "price_input_parse", "price_cached_input_parse", "price_output_parse",
        "price_input_grey_rec", "price_cached_input_grey_rec", "price_output_grey_rec",
        "price_input_grey_aud", "price_cached_input_grey_aud", "price_output_grey_aud",
        "price_input_grey_parse", "price_cached_input_grey_parse", "price_output_grey_parse",
    )
    for pf in price_fields:
        if pf in body.model_fields_set:
            updates[pf] = getattr(body, pf)

    # 配置来源不接受请求体声明：PUT 保存后一律由后端置 manual（复位走 reset 端点）
    updates.pop("config_source", None)

    # 任务 3a：热切到 qwen3-vl-flash 需有效 dashscope.aliyuncs.com compatible-mode/v1 + sk-，
    # 若无有效 key 则显式降级提示而非静默超时（避免本地 165s 不达标却静默阻塞）
    # 在写入前对 openai 引擎做强校验（识别/审核/解析三侧）
    new_dict = {**cfg.model_dump(), **updates}

    # SSRF 阻断网关：校验所有外部 API Base URL
    from app.services.security_guard import validate_safe_external_url, mask_secret_key
    url_checks = [
        ("openai_rec_base_url", "识别引擎 Base URL"),
        ("openai_aud_base_url", "审核引擎 Base URL"),
        ("openai_parse_base_url", "解析 LLM Base URL"),
        ("grey_openai_rec_base_url", "灰测识别引擎 Base URL"),
        ("grey_openai_aud_base_url", "灰测审核引擎 Base URL"),
        ("grey_openai_parse_base_url", "灰测解析 LLM Base URL"),
    ]
    for field_name, field_label in url_checks:
        val = new_dict.get(field_name)
        if val and str(val).strip():
            is_safe, reason = validate_safe_external_url(str(val).strip())
            if not is_safe:
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "code": "ENGINE_CONFIG_INVALID",
                    "msg": f"{field_label} 安全校验未通过: {reason}"
                })

    # 识别引擎校验
    rec_engine = str(new_dict.get("recognition_engine", "") or "")
    if hasattr(new_dict.get("recognition_engine"), "value"):
        rec_engine = str(new_dict["recognition_engine"].value)
    rec_engine = rec_engine.lower()
    rec_base = new_dict.get("openai_rec_base_url") or ""
    rec_key = new_dict.get("openai_rec_api_key") or ""
    rec_model = new_dict.get("openai_rec_model") or new_dict.get("recognition_model") or ""
    if rec_engine == "openai":
        if not rec_base or not str(rec_base).strip():
            return JSONResponse(status_code=400, content={
                "status": "error", "code": "ENGINE_CONFIG_INVALID",
                "msg": "识别引擎选择 OpenAI 兼容接口时，需提供 Base URL"
            })
        if _is_placeholder_api_key(rec_key):
            return JSONResponse(status_code=400, content={
                "status": "error", "code": "ENGINE_CONFIG_INVALID",
                "msg": "识别引擎的 API 密钥为空或仍是占位符（如 YOUR_xxx），保存后每次识别都会鉴权失败。请填入该服务商的真实密钥后再保存。"
            })
    # 解析 LLM 校验（若启用）
    parse_enabled = bool(new_dict.get("parse_llm_enabled"))
    if parse_enabled:
        parse_engine = str(new_dict.get("parse_llm_engine", "") or "")
        if hasattr(new_dict.get("parse_llm_engine"), "value"):
            parse_engine = str(new_dict["parse_llm_engine"].value)
        parse_engine = parse_engine.lower()
        if parse_engine == "openai":
            pb = new_dict.get("openai_parse_base_url") or ""
            if not pb or not str(pb).strip():
                return JSONResponse(status_code=400, content={
                    "status": "error", "code": "ENGINE_CONFIG_INVALID",
                    "msg": "解析 LLM 引擎选择 OpenAI 兼容接口时，需提供 Base URL"
                })
            if _is_placeholder_api_key(new_dict.get("openai_parse_api_key") or ""):
                return JSONResponse(status_code=400, content={
                    "status": "error", "code": "ENGINE_CONFIG_INVALID",
                    "msg": "解析 LLM 的 API 密钥为空或仍是占位符，请填入真实密钥后再保存。"
                })
    # 灰测组识别校验
    if new_dict.get("grey_enabled") and int(new_dict.get("grey_percent") or 0) > 0:
        g_rec = str(new_dict.get("grey_recognition_engine", "") or "")
        if hasattr(new_dict.get("grey_recognition_engine"), "value"):
            g_rec = str(new_dict["grey_recognition_engine"].value)
        g_rec = g_rec.lower()
        if g_rec == "openai":
            gb = new_dict.get("grey_openai_rec_base_url") or ""
            if not gb or not str(gb).strip():
                return JSONResponse(status_code=400, content={
                    "status": "error", "code": "ENGINE_CONFIG_INVALID",
                    "msg": "灰测识别引擎选择 OpenAI 兼容接口时，需提供 Base URL"
                })
            if _is_placeholder_api_key(new_dict.get("grey_openai_rec_api_key") or ""):
                return JSONResponse(status_code=400, content={
                    "status": "error", "code": "ENGINE_CONFIG_INVALID",
                    "msg": "灰测识别引擎的 API 密钥为空或仍是占位符，请填入真实密钥后再保存。"
                })
    # call_timeout 语义（T9 收尾）：硬上限 60 是硬约束，不是管理台可绕过的偏好。
    # 保存时即归一到 ≤60，使「落库值 / 回显值 / 实际生效值」三者恒等，消除「填 90 实际 60」的误导。
    # 旧实现只在「请求未显式传该字段 且 是 qwen 路径 且 旧值 ≥200」时才降至 60，
    # 管理员显式填 90 会被原样落库 → 现收敛为无条件归一（选项 b 的 API 侧回显）。
    from app.models import clamp_call_timeout, MAX_CALL_TIMEOUT_SECONDS
    timeout_fields = [
        "call_timeout_seconds",
        "rec_timeout_seconds",
        "aud_timeout_seconds",
        "parse_timeout_seconds",
        "grey_rec_timeout_seconds",
        "grey_aud_timeout_seconds",
        "grey_parse_timeout_seconds",
    ]
    clamped_fields = []
    for tf in timeout_fields:
        if tf in new_dict and new_dict[tf] is not None:
            raw_v = new_dict[tf]
            clamped_v = clamp_call_timeout(raw_v)
            new_dict[tf] = clamped_v
            if clamped_v != raw_v:
                clamped_fields.append((tf, raw_v, clamped_v))

    old_engine = cfg.recognition_engine
    # T1：管理台保存即声明「人工配置」（config_source=manual），
    # 重启时 hydrate_engine_config_from_env 不再按 .env 覆盖，人工切换得以持久生效。
    # 版本自增：落库值 = 读到的库内版本 + 1（不接受请求体声明）。
    next_version = stored_version + 1
    new_cfg = EngineConfig(**{**new_dict, "config_source": "manual", "version": next_version})
    db.set_engine_config(new_cfg, source="manual")

    # ---- 自动将填写了供应商名的引擎腿写入自定义预设网关 ----
    _auto_save_vendor_presets(new_cfg, new_dict)

    changed_keys = list(updates.keys())
    if str(getattr(cfg, "config_source", "auto") or "auto") != "manual":
        changed_keys.append("config_source")
    for tf, raw_v, clamped_v in clamped_fields:
        changed_keys.append(f"{tf}(auto:{raw_v}->{clamped_v})")
    effective_model = new_cfg.recognition_model or new_cfg.openai_rec_model or "deepseek/deepseek-v4.1-flash"
    db.append_system_audit_log(who, "set_engine_config",
                               "engine_config",
                               " ".join(changed_keys),
                               str(effective_model))
    # 版本历史（不可变快照）：changed_keys 取「实际发生变化」的键（值级 diff），
    # 与上一条审计日志的「提交键」口径不同 —— 前者回答「改了什么」，后者回答「提交了什么」。
    field_diff = engine_config_field_diff(cfg.model_dump(), new_cfg.model_dump(),
                                          ignore=("version",))
    record_engine_config_history(next_version, who, "save", new_cfg.model_dump(),
                                 [d["key"] for d in field_diff])
    # 附带超时显式降级告警与性能预期（任务 3a）
    advice = get_timeout_advice(new_cfg)
    warning = ""
    # 性能基线提示
    perf_note = "预期：qwen3.5-omni-flash 单张 2.6-7.1s（2026-09-15 实测，3 张 confirmed 样本） / 纯 API 驱动"
    resp_data = mask_engine_config_dict(new_cfg.model_dump())
    resp = {"status": "success", "data": resp_data, "perf_note": perf_note,
            "version": next_version, "changed_keys": [d["key"] for d in field_diff]}

    if clamped_fields:
        # 显式回显（T9 收尾）：本次保存填的超限值已被归一，前端可用该字段提示管理员
        warn_msgs = [
            f"{tf}={raw_v} 超过硬上限 {MAX_CALL_TIMEOUT_SECONDS}s，"
            f"已归一为 {MAX_CALL_TIMEOUT_SECONDS}s；填更大值不会延长实际等待"
            for tf, raw_v, _ in clamped_fields
        ]
        warning = "；".join(warn_msgs)
    if advice:
        resp["timeout_advice"] = advice
    if warning:
        resp["timeout_advice"] = warning
        resp["warning"] = warning
    if rec_engine == "openai":
        resp["msg"] = "已保存引擎配置，预期单张 2.6-7.1s（3 张 confirmed 样本实测）"
    return resp


@router.post("/api/admin/engine-config/reset")
def reset_engine_config(request: Request):
    """复位为 .env 默认装配（T1）。

    管理台 PUT 保存会把 config_source 置 manual（重启不被 .env 覆盖）；
    本端点把来源改回 auto 并立即重新执行一次 .env 装配，
    使配置回到与「服务刚启动时」一致的状态。不可逆，操作前前端需二次确认。
    """
    account = require_admin(request)
    who = account.get("email", "unknown")
    cfg = db.get_engine_config()
    before_dump = cfg.model_dump()
    old_source = str(getattr(cfg, "config_source", "auto") or "auto")
    cfg.config_source = "auto"
    db.set_engine_config(cfg, source="auto")
    # 立即重新装配（内部按 auto 走 .env 覆盖逻辑）
    db.hydrate_engine_config_from_env()
    new_cfg = db.get_engine_config()
    # 版本自增放在 hydrate 之后：装配过程自身可能写回（补全/归一），
    # 在其后再前进一位，保证「一次复位 = 版本恰好 +1」且不出现中途的重复版本。
    new_cfg.version = int(before_dump.get("version", 1) or 1) + 1
    db.set_engine_config(new_cfg, source=str(getattr(new_cfg, "config_source", "auto") or "auto"))
    db.append_system_audit_log(who, "reset_engine_config",
                               "config_source", old_source,
                               str(getattr(new_cfg, "config_source", "auto") or "auto"))
    record_engine_config_history(
        new_cfg.version, who, "reset", new_cfg.model_dump(),
        [d["key"] for d in engine_config_field_diff(before_dump, new_cfg.model_dump(),
                                                    ignore=("version",))])
    return {
        "status": "success",
        "data": mask_engine_config_dict(new_cfg.model_dump()),
        "msg": "已复位为 .env 默认装配",
        "version": new_cfg.version,
    }


@router.get("/api/admin/system-audit")
def get_system_audit(request: Request):
    """系统级审计日志读取（engine 配置变更等无 receipt_id 的操作）。"""
    require_admin(request)
    return {"status": "success", "data": sanitize_audit_log(db.read_system_audit_log())}



# -------------------------------------------------------------
# 系统配置（T10 Gap E3）：阈值/开关写 app_settings，改完即时生效无需重启
# -------------------------------------------------------------
@router.get("/api/admin/settings")
def get_admin_settings(request: Request):
    """全量系统配置（当前生效值 + 缺省值对照，供管理台表单渲染与还原）。"""
    require_admin(request)
    from app.services import settings_service
    return {"status": "success",
            "data": settings_service.all_settings(),
            "defaults": dict(settings_service.SETTINGS_DEFAULTS)}


@router.put("/api/admin/settings")
async def update_admin_settings(request: Request):
    """批量保存系统配置：body 为 {key: value}；仅接受已登记键，未知键 400。"""
    require_admin(request)
    from app.services import settings_service
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"status": "error", "msg": "请求体必须是 JSON 对象"},
                            status_code=400)
    if not isinstance(body, dict) or not body:
        return JSONResponse(content={"status": "error", "msg": "请求体必须是非空 JSON 对象"},
                            status_code=400)
    unknown = [k for k in body if k not in settings_service.SETTINGS_DEFAULTS]
    if unknown:
        return JSONResponse(
            content={"status": "error",
                     "msg": "未知配置键: " + ", ".join(sorted(unknown))},
            status_code=400)
    changed = {}
    for key, value in body.items():
        old = settings_service.get(key)
        settings_service.set_value(key, value)
        changed[key] = {"old": old, "new": settings_service.get(key)}
    account = getattr(request.state, "account", {})
    who = account.get("email", "admin") if isinstance(account, dict) else "admin"
    try:
        db.append_system_audit_log(
            who, "settings_update", "app_settings",
            "", {k: v["new"] for k, v in changed.items()})
    except Exception as e:
        logging.getLogger("api_admin").warning(
            "审计写入失败(action=settings_update who=%s keys=%s): %s",
            who, list(changed.keys()), e)
    return {"status": "success", "data": settings_service.all_settings(),
            "changed": changed}


@router.post("/api/admin/guardian/check")
def guardian_check(request: Request):
    """T9（Gap C3）：手动触发实验守护巡检（与后台线程同一入口）。

    返回本次动作列表（rollback / alert）；无运行中实验或无触发时为空列表。
    """
    require_admin(request)
    from app.services import guardian
    actions = guardian.check_once()
    return {"status": "success", "data": {"actions": actions}}


@router.get("/api/admin/metrics")
def admin_metrics(request: Request):
    """最严格记忆落盘聚合：DB 可查 + 文件可追溯。

    从 ai_decision_log.extra 提取 {tokens_prompt, tokens_completion, tokens_total, cost_hkd, elapsed_ms, success}，
    聚合 avg_tokens, avg_elapsed, success_rate；同时尝试读 artifacts/memory/parse_log.jsonl 求文件层均值。
    对应 spec：GET /api/admin/metrics 聚合 avg_tokens, avg_elapsed, success_rate
    """
    require_admin(request)
    import json as _json
    import os
    from pathlib import Path
    # DB 聚合（extract 决策）
    try:
        s = db.get_session()
        try:
            rows = s.query(db._DecisionLogRow).filter(db._DecisionLogRow.decision_type == "extract").all()
        finally:
            s.close()
    except Exception:
        rows = []
    tokens = []
    elapsed = []
    success_cnt = 0
    cost_vals = []
    cost_estimated_cnt = 0
    for r in rows:
        try:
            extra = _json.loads(r.extra) if r.extra else {}
            if not isinstance(extra, dict):
                extra = {}
        except Exception:
            extra = {}
        # 兼容 ai_value 中 token（旧数据）
        tt = extra.get("tokens_total")
        if tt is None:
            try:
                av = _json.loads(r.ai_value) if r.ai_value and r.ai_value.strip().startswith("{") else {}
                if isinstance(av, dict):
                    tt = av.get("tokens_total", 0)
                else:
                    tt = 0
            except Exception:
                tt = 0
        try:
            tt = int(tt or 0)
        except Exception:
            tt = 0
        tokens.append(tt)
        # elapsed_ms 字典取 total
        em = extra.get("elapsed_ms", {})
        if isinstance(em, dict):
            tot = em.get("total", em.get("extract", 0))
            try:
                tot = float(tot or 0)
            except Exception:
                tot = 0.0
        else:
            try:
                tot = float(em or 0)
            except Exception:
                tot = 0.0
        elapsed.append(tot)
        if extra.get("success") is True:
            success_cnt += 1
        # 兼容 ai_value success
        elif extra.get("success") is None:
            try:
                av = _json.loads(r.ai_value) if r.ai_value and r.ai_value.strip().startswith("{") else {}
                if isinstance(av, dict) and av.get("success") is True:
                    success_cnt += 1
            except Exception:
                pass
        # cost
        try:
            c = float(extra.get("cost_hkd", 0) or 0)
        except Exception:
            c = 0.0
        # 成本可信度计数：估算行的 cost_hkd 不是实测值（拿不到 token / 成本链路异常 / 兜底单价），
        # 大盘必须能标注「这一栏含 N 行为估算」，否则会被当成实测平均成本读。
        if int(extra.get("cost_estimated") or 0) == 1:
            cost_estimated_cnt += 1
        else:
            # avg_cost_hkd 只用实测行（口径对齐 guardian._group_metrics）：估算行 cost_hkd
            # 是兜底/低估产物，混入均值会把「实测单张成本」拉偏；估算行仅计数上报。
            cost_vals.append(c)
    n = len(rows)
    avg_tokens = round(sum(tokens) / n, 2) if n else 0
    avg_elapsed = round(sum(elapsed) / n, 2) if n else 0
    success_rate = round(success_cnt / n, 4) if n else 0
    avg_cost = round(sum(cost_vals) / len(cost_vals), 6) if cost_vals else None
    # 文件层补充（artifacts/memory/parse_log.jsonl）
    file_stats = {"count": 0, "avg_tokens": 0, "avg_elapsed": 0, "success_rate": 0}
    try:
        # 项目根 artifacts/memory
        base = Path(__file__).resolve().parent.parent.parent
        candidates = [
            base / "artifacts" / "memory" / "parse_log.jsonl",
            Path(__file__).resolve().parent / "../../artifacts/memory/parse_log.jsonl",
        ]
        fpath = None
        for c in candidates:
            if c.exists():
                fpath = c
                break
        if fpath is None:
            # 绝对路径回退（supervisor 写入路径）
            import os as _os
            alt = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../../artifacts/memory/parse_log.jsonl"))
            if _os.path.exists(alt):
                fpath = Path(alt)
            else:
                alt2 = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../../../artifacts/memory/parse_log.jsonl"))
                if _os.path.exists(alt2):
                    fpath = Path(alt2)
        if fpath and fpath.exists():
            f_tokens = []
            f_elapsed = []
            f_success = 0
            f_count = 0
            with open(fpath, "r", encoding="utf-8") as fh:
                for line in fh:
                    line=line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    f_count += 1
                    try:
                        f_tokens.append(int(rec.get("tokens_total", 0) or 0))
                    except Exception:
                        f_tokens.append(0)
                    try:
                        em = rec.get("elapsed_ms", {})
                        if isinstance(em, dict):
                            f_elapsed.append(float(em.get("total", 0) or 0))
                        else:
                            f_elapsed.append(float(em or 0))
                    except Exception:
                        f_elapsed.append(0.0)
                    if rec.get("success") is True:
                        f_success += 1
            if f_count:
                file_stats = {
                    "count": f_count,
                    "avg_tokens": round(sum(f_tokens)/f_count, 2) if f_count else 0,
                    "avg_elapsed": round(sum(f_elapsed)/f_count, 2) if f_count else 0,
                    "success_rate": round(f_success/f_count, 4) if f_count else 0,
                }
    except Exception:
        pass
    return {
        "status": "success",
        "data": {
            "db_count": n,
            "avg_tokens": avg_tokens,
            "avg_elapsed": avg_elapsed,
            "success_rate": success_rate,
            "avg_cost_hkd": avg_cost,
            # 成本可信度：avg_cost_hkd 中含多少行是估算值。>0 时前端应标注「含估算」，
            # 不能把该平均值当作实测单张成本（0 表示全部为实测）。
            "cost_estimated_count": cost_estimated_cnt,
            "success_count": success_cnt,
            "file": file_stats,
        },
        "hint": "DB 可查 ai_decision_log.extra tokens_total/cost_hkd/elapsed_ms/success；文件可追溯 artifacts/memory/parse_log.jsonl",
    }


@router.get("/api/admin/grey-test")
def grey_test_status(request: Request):
    require_admin(request)
    cfg = db.get_engine_config()
    return {"status": "success", "data": {
        "current": mask_engine_config_dict(cfg.model_dump()),
        "assign_modes": {
            "receipt": "按单据随机分配（每单独立 random < 概率%）",
            "supplier": "按供应商分配（同供应商一致命中，确定性）",
        },
        "note": "",
    }}


def _normalize_audit_model_name(val: str, fallback: str = "deepseek/deepseek-v4.1-flash") -> str:
    """平滑映射：如果历史审计记录存的是 engine kind (EngineKind.OPENAI / openai / opencode 等)，
    转换为实际对应的模型名称。如果已是模型名称则保留。
    """
    if not val:
        return fallback
    s = str(val).strip()
    s_lower = s.lower()
    if "enginekind.openai" in s_lower or s_lower == "openai":
        return fallback
    if "enginekind.opencode" in s_lower or s_lower in ("opencode", "codebuddy", "enginekind.codebuddy"):
        return "Qwen/Qwen3-VL-32B-Instruct"
    if "mock" in s_lower:
        return "mock-vlm-rec"
    if s_lower == "qwen" or "enginekind.qwen" in s_lower:
        return "qwen3.5-omni-flash"
    if "->" in s:
        parts = [p.strip() for p in s.split("->", 1)]
        p0 = _normalize_audit_model_name(parts[0], fallback="deepseek/deepseek-v4.1-flash")
        p1 = _normalize_audit_model_name(parts[1], fallback="Qwen/Qwen3-VL-32B-Instruct")
        return f"{p0} -> {p1}"
    return s


@router.get("/api/admin/canary/history")
def canary_history(request: Request):
    """返回历史灰度发布、放量变更、推全与回滚履历。"""
    require_admin(request)
    raw_logs = db.read_system_audit_log()
    history = []
    promote_count = 0
    rollback_count = 0

    cur_cfg = db.get_engine_config()
    cur_prod_model = cur_cfg.recognition_model or cur_cfg.openai_rec_model or "deepseek/deepseek-v4.1-flash"
    cur_grey_model = cur_cfg.grey_recognition_model or cur_cfg.grey_openai_rec_model or "Qwen/Qwen3-VL-32B-Instruct"

    for l in reversed(raw_logs):
        act = l.get("action", "")
        fld = l.get("field", "")
        old_val = str(l.get("old", ""))
        new_val = str(l.get("new", ""))
        ts = l.get("ts", "")
        who = l.get("who", "admin@demo.hk")

        if act == "promote_grey_config":
            promote_count += 1
            old_m = _normalize_audit_model_name(old_val, fallback=cur_prod_model)
            new_m = _normalize_audit_model_name(new_val, fallback=cur_grey_model)
            if old_m == new_m and ("enginekind" in old_val.lower() or old_val.lower() in ("openai", "mock", "qwen", "opencode", "codebuddy")):
                new_m = cur_grey_model
            history.append({
                "ts": ts,
                "action": "promote",
                "action_label": "推全上线",
                "badge_class": "badge-success",
                "description": "灰测配置全量推全至常规生产组（生成回滚快照）",
                "models": f"{old_m} -> {new_m}",
                "operator": who,
                "status": "已推全",
            })
        elif act == "rollback_engine_config":
            rollback_count += 1
            old_m = _normalize_audit_model_name(old_val, fallback=cur_grey_model)
            new_m = _normalize_audit_model_name(new_val, fallback=cur_prod_model)
            if old_m == new_m and ("enginekind" in old_val.lower() or old_val.lower() in ("openai", "mock", "qwen", "opencode", "codebuddy")):
                old_m = cur_grey_model
            history.append({
                "ts": ts,
                "action": "rollback",
                "action_label": "紧急回滚",
                "badge_class": "badge-danger",
                "description": "从前次推全快照快速回滚常规生产配置",
                "models": f"{old_m} -> {new_m}",
                "operator": who,
                "status": "已回滚",
            })
        elif "grey" in old_val or "grey" in fld or act == "set_canary_config":
            is_grey_field = "grey" in old_val or "grey" in fld
            def_m = cur_grey_model if is_grey_field else cur_prod_model
            history.append({
                "ts": ts,
                "action": "config_update",
                "action_label": "灰度放量/参数变更",
                "badge_class": "badge-primary",
                "description": f"更新灰度分流策略或放量参数: {old_val[:50]}",
                "models": _normalize_audit_model_name(new_val, fallback=def_m),
                "operator": who,
                "status": "配置生效",
            })

    s = db.get_session()
    try:
        total_grey_receipts = s.query(db._ReceiptRow).filter(db._ReceiptRow.use_grey == 1).count()
        total_decision_grey = s.query(db._DecisionLogRow).filter(db._DecisionLogRow.use_grey == 1).count()
    finally:
        s.close()

    history.sort(key=lambda x: str(x.get("ts", "") or ""), reverse=True)
    return {
        "status": "success",
        "total_records": len(history),
        "promote_count": promote_count,
        "rollback_count": rollback_count,
        "total_grey_receipts": total_grey_receipts,
        "total_grey_decisions": total_decision_grey,
        "items": history,
    }



@router.get("/api/admin/trash")
def trash_list(request: Request):
    """回收站：软删除单据列表。"""
    require_admin(request)
    rows = db.list_trash_receipts()
    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "supplier_name": r.supplier_name or "",
            "receipt_date": r.receipt_date or "",
            "total_amount": r.total_amount or 0.0,
            "status": r.status,
            "created_at": r.created_at or "",
            "deleted_at": r.deleted_at or "",
        })
    return {"status": "success", "data": data}


@router.post("/api/admin/trash/{receipt_id}/restore")
def restore_trash(receipt_id: int, request: Request):
    """从回收站恢复单据。"""
    require_admin(request)
    if not db.restore_receipt(receipt_id):
        return JSONResponse(content={"status": "error", "msg": "单据不存在或不在回收站"},
                            status_code=404)
    return {"status": "success", "receipt_id": receipt_id,
            "msg": f"单据 #{receipt_id} 已恢复"}


@router.get("/api/review")
def ai_review(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary
    from app.chains import review_chain
    cost = cost_summary(tenant_id=_tenant_id(request))
    cfg = db.get_engine_config()
    result = review_chain.run_review(cost.get("items", {}), config=cfg)
    return {"status": "success", "data": result}


@router.get("/api/cost")
def cost_summary(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    return {"status": "success", "data": get_cost(tenant_id=_tenant_id(request))}


# -------------------------------------------------------------
# 阶段 1：AI 发现
# -------------------------------------------------------------
@router.get("/api/ai-insights")
def ai_insights(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    from app.chains import review_chain
    cost = get_cost(tenant_id=_tenant_id(request))
    data = review_chain.weekly_insights(cost)
    return {"status": "success", "data": data}


# -------------------------------------------------------------
# 阶段 4：对话式查询 & 生成式 UI 卡片
# -------------------------------------------------------------
class ChatQueryBody(BaseModel):
    question: str = ""


@router.post("/api/chat/query")
def chat_query(body: ChatQueryBody, request: Request):
    """对话式查询 Agent：自然语言查询库存、价格走势与未付账单。"""
    require_role("staff")(request)
    from app.chains.query_chain import run_query
    res = run_query(body.question, tenant_id=_tenant_id(request))
    return {"status": "success", "data": res}


@router.get("/api/insight-cards")
def get_insight_cards(request: Request):
    """生成式 UI 卡片：多步推理输出异动预警卡、谈判策略卡与健康度卡片。"""
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    from app.chains.review_chain import generate_insight_cards
    cost = get_cost(tenant_id=_tenant_id(request))
    cards = generate_insight_cards(cost)
    return {"status": "success", "cards": cards}


# -------------------------------------------------------------
# Prompt 工程化管理与评测资产
# -------------------------------------------------------------
@router.get("/api/admin/prompts")
def get_prompt_registry(request: Request):
    """获取全项目 6 大组件的 Prompt 版本演进与多维效果评测基准数据。"""
    require_admin(request)
    # T12 SSOT：app.prompts 已改为 ai_registry 的 re-export 委托层，版本清单动态派生自 metadata
    from app.prompts import list_prompt_versions, get_benchmark_report
    return {
        "status": "success",
        "versions": list_prompt_versions(),
        "benchmark": get_benchmark_report(),
    }

# -------------------------------------------------------------
# 灰测用户使用状态与脱敏单据抽样观测接口
# -------------------------------------------------------------
@router.get("/api/admin/grey-test/samples")
def get_grey_test_samples(request: Request):
    """Admin 在前端拉取灰测中用户的使用状态、被解析的图片与解析结果（全量过滤敏感信息）。"""
    require_admin(request)
    import re
    from app.api_receipts import _mask_sensitive, _resolve_upload_file
    # 惰性 import 避免 api_admin 与 receipt_utils/api_receipts 顶层循环依赖
    from app.services.receipt_utils import public_image_url

    def mask_vendor(name: str) -> str:
        if not name:
            return "匿名供应商_#V00"
        hash_val = sum(ord(c) for c in name) % 900 + 100
        category = "食材"
        if any(w in name for w in ["菜", "蔬", "农"]): category = "蔬菜"
        elif any(w in name for w in ["肉", "冻", "牛", "猪", "鸡"]): category = "冻肉"
        elif any(w in name for w in ["奶", "茶", "饮", "咖啡"]): category = "水吧"
        elif any(w in name for w in ["杂", "粮", "油", "海鲜"]): category = "粮油海鲜"
        return f"{category}批发商_#V{hash_val}"

    def mask_amount(amt) -> str:
        # 首位数字 + **.**：只保量级首位，不泄露金额（旧 f"{v:.1f}"[:2] 会把 100.0 扭曲成 10.**、5.0 变 5..**）
        if amt is None or float(amt) == 0:
            return "HK$ 0.00"
        sign = "-" if float(amt) < 0 else ""
        return f"HK$ {sign}{str(int(abs(float(amt))))[0]}**.**"

    def mask_number(v) -> str:
        # 明细行数值掩码，与 mask_amount 同规则：首字符 + **.**（不改数量级、不产生非法格式）
        if v is None or float(v) == 0:
            return "0.00"
        sign = "-" if float(v) < 0 else ""
        return f"{sign}{str(int(abs(float(v))))[0]}**.**"

    effective_tenant = resolve_tenant_filter(
        request, request.query_params.get("tenant_id"))

    rows = db.list_receipt_rows(tenant_id=effective_tenant)
    # 分流引擎标签读真实引擎配置（不再硬编码 opencode/mimo 文案）
    _cfg = db.get_engine_config()

    def _leg_label(engine, model, fallback_model):
        eng = str(getattr(engine, "value", engine) or "").lower()
        if eng == "openai":
            shown = model or fallback_model
            return f"openai/{shown} (SF)"
        return f"{eng}/{model or ''}".strip("/")

    _label_reg = _leg_label(_cfg.recognition_engine,
                            _cfg.openai_rec_model or _cfg.recognition_model,
                            "Qwen/Qwen3-VL-32B-Instruct") + " (常规组)"
    _label_grey = _leg_label(_cfg.grey_recognition_engine,
                             _cfg.grey_openai_rec_model or _cfg.grey_recognition_model,
                             "Qwen/Qwen3-VL-32B-Instruct") + " (灰测组)"
    samples = []
    # 批量拉取所有单据 items，杜绝 1484 次开闭 session 导致耗时高达 35 秒
    all_items_by_rid = db.get_receipt_items_multi(None, tenant_id=effective_tenant) if hasattr(db, "get_receipt_items_multi") else {}

    for r in rows:
        # 获取该单据脱敏后的信息
        items = (all_items_by_rid.get(r.id) or []) if hasattr(db, "get_receipt_items_multi") else (db.get_receipt_items(r.id, tenant_id=effective_tenant) if hasattr(db, "get_receipt_items") else [])
        masked_items = []
        for it in items:
            masked_items.append({
                "item_name": _mask_sensitive(it.get("name", "")),
                "quantity": it.get("qty", 1.0),
                "unit": it.get("unit", "斤"),
                "unit_price": mask_number(it.get("unit_price")),
                "amount": mask_number(it.get("amount")),
            })

        # 灰测分组只读真实 use_grey 列（历史上曾用 id 奇偶伪造分组，导致灰测统计失真）
        use_grey = bool(getattr(r, "use_grey", 0) or 0)

        # 分析 AI 原始解析数据 vs 用户最终录入数据（评估采纳率与用户反馈）。
        # 无真实 ai_prefill 时如实标注「无预填可比对」，不得用用户最终数据构造镜像冒充 AI 数据。
        import json as _json
        try:
            ai_prefill = _json.loads(getattr(r, "ai_prefill_json", "{}") or "{}")
        except Exception:
            ai_prefill = {}
        has_prefill = bool(ai_prefill.get("supplier_name"))

        user_vendor = mask_vendor(r.supplier_name)
        user_total = mask_amount(r.total_amount)

        # 逐字段比对（仅当存在真实 AI 预填数据）
        field_comparisons = []
        if has_prefill:
            ai_vendor_raw = mask_vendor(ai_prefill.get("supplier_name", ""))
            ai_total_raw = mask_amount(ai_prefill.get("total_amount", 0))
            ai_items_raw = []
            for it in ai_prefill.get("items", []):
                ai_items_raw.append({
                    "item_name": _mask_sensitive(it.get("name", "")),
                    "quantity": it.get("quantity", it.get("qty", 1.0)),
                    "unit": it.get("unit", "斤"),
                    "unit_price": mask_number(it.get("unit_price")),
                    "amount": mask_number(it.get("amount")),
                })

            # 1. 供应商比对
            vendor_match = (ai_vendor_raw == user_vendor)
            field_comparisons.append({
                "field": "供应商名称",
                "ai_value": ai_vendor_raw,
                "user_value": user_vendor,
                "is_match": vendor_match,
                "status_text": "完全采纳" if vendor_match else "人工纠偏"
            })

            # 2. 总金额比对
            total_match = (ai_total_raw == user_total)
            field_comparisons.append({
                "field": "单据总额",
                "ai_value": ai_total_raw,
                "user_value": user_total,
                "is_match": total_match,
                "status_text": "完全采纳" if total_match else "人工纠偏"
            })

            # 3. 明细行逐项比对
            for i in range(max(len(ai_items_raw), len(masked_items))):
                ai_it = ai_items_raw[i] if i < len(ai_items_raw) else {}
                u_it = masked_items[i] if i < len(masked_items) else {}
                item_match = (ai_it.get("item_name") == u_it.get("item_name") and
                              ai_it.get("amount") == u_it.get("amount"))
                field_comparisons.append({
                    "field": f"明细行 #{i+1} ({u_it.get('item_name') or ai_it.get('item_name') or '商品'})",
                    "ai_value": f"{ai_it.get('item_name', '-')} · {ai_it.get('amount', '-')}",
                    "user_value": f"{u_it.get('item_name', '-')} · {u_it.get('amount', '-')}",
                    "is_match": item_match,
                    "status_text": "完全采纳" if item_match else "人工纠偏"
                })

        total_fields = len(field_comparisons)
        matched_fields = sum(1 for fc in field_comparisons if fc["is_match"])
        # 无预填时 match_rate 如实返回 null（前端显示「—」），不再伪造成 100%
        match_rate = round(matched_fields / total_fields * 100, 1) if has_prefill and total_fields else None
        is_exact_match = bool(has_prefill and matched_fields == total_fields)

        # 综合效果与用户反馈判定 (thumbs_up / thumbs_down / no_prefill / pending)
        if not has_prefill:
            feedback_type = "no_prefill"
            feedback_label = "无 AI 预填 · 无法比对"
            feedback_badge_color = "#383d41"
            feedback_badge_bg = "#e2e3e5"
        elif r.status == "approved" or (r.status == "edited" and is_exact_match):
            feedback_type = "thumbs_up"
            feedback_label = "用户赞同 · 完全采纳"
            feedback_badge_color = "#155724"
            feedback_badge_bg = "#d4edda"
        elif r.status == "edited":
            feedback_type = "thumbs_down"
            feedback_label = "用户修正 · 存在纠偏"
            feedback_badge_color = "#856404"
            feedback_badge_bg = "#fff3cd"
        else:
            feedback_type = "pending"
            feedback_label = "待复核校验"
            feedback_badge_color = "#383d41"
            feedback_badge_bg = "#e2e3e5"

        # W8：历史 pytest 污染单据的 image_path 指向已被清理的临时测试文件
        # （如 /uploads/img_2.png、/uploads/eval_reflow_src_*.png），public_image_url
        # 仍会按其 basename 生成 URL，前端打开即 404 破图。展示层兜底：源文件不存在
        # 时返回空串（字段保留、响应结构不变，前端按 if(url) 降级）；不删任何历史数据行。
        image_url = public_image_url(r.id, r.image_path,
                                     getattr(r, "tenant_id", None)) if r.id else ""
        if image_url:
            _src = _resolve_upload_file(getattr(r, "image_path", ""))
            if _src is None or not _src.is_file():
                image_url = ""

        samples.append({
            "receipt_id": r.id,
            "masked_vendor": user_vendor,
            "date": r.receipt_date or "",
            "masked_total": user_total,
            "doc_form": r.doc_form or "ncr_handwritten",
            "user_status": r.status,  # uploaded, parsed, edited, approved, flagged
            "use_grey": use_grey,
            "engine": _label_grey if use_grey else _label_reg,
            "image_url": image_url,
            "masked_items": masked_items,
            "is_user_edited": r.status in ["edited", "approved"],
            "is_approved": r.status == "approved",
            # 无逐单真实门禁结果来源，置 null（不再硬编码 True 伪装）
            "math_gate_passed": None,
            "created_at": r.created_at or "",
            # 新增 AI 解析 vs 用户最终录入效果评估字段
            "effect_evaluation": {
                "is_exact_match": is_exact_match,
                "match_rate": match_rate,
                "total_fields": total_fields,
                "matched_fields": matched_fields,
                "modified_fields": total_fields - matched_fields,
                "feedback_type": feedback_type,
                "feedback_label": feedback_label,
                "feedback_badge_color": feedback_badge_color,
                "feedback_badge_bg": feedback_badge_bg,
                "field_comparisons": field_comparisons
            }
        })

    samples.sort(key=lambda s: int(s.get("receipt_id") or 0), reverse=True)

    # 统计全局采纳指标（口径：仅统计有真实 AI 预填可比对的样本，no_prefill 样本不计入）
    comparable = [s["effect_evaluation"] for s in samples
                  if s["effect_evaluation"]["feedback_type"] != "no_prefill"]
    positive_count = sum(1 for e in comparable if e["feedback_type"] == "thumbs_up")
    modified_count = sum(1 for e in comparable if e["feedback_type"] == "thumbs_down")
    rates = [e["match_rate"] for e in comparable if e["match_rate"] is not None]
    avg_match_rate = round(sum(rates) / len(rates), 1) if rates else None

    scope = request.query_params.get("scope", "all")
    if scope == "grey_only":
        filtered_samples = [s for s in samples if s["use_grey"]]
    elif scope == "prod_only":
        filtered_samples = [s for s in samples if not s["use_grey"]]
    elif scope == "modified":
        filtered_samples = [s for s in samples if s["effect_evaluation"]["feedback_type"] in ("thumbs_down", "modified") or s["effect_evaluation"]["modified_fields"] > 0]
    elif scope == "approved":
        filtered_samples = [s for s in samples if s["is_approved"]]
    else:
        filtered_samples = samples

    return {
        "status": "success",
        "scope": scope,
        "total_count": len(filtered_samples),
        "all_count": len(samples),
        "grey_count": sum(1 for s in samples if s["use_grey"]),
        "user_approved_count": sum(1 for s in samples if s["is_approved"]),
        "user_edited_count": sum(1 for s in samples if s["is_user_edited"]),
        "prefill_available_count": len(comparable),
        "positive_feedback_count": positive_count,
        "modified_feedback_count": modified_count,
        "avg_match_rate": avg_match_rate,
        "samples": filtered_samples
    }


@router.get("/api/admin/golden-samples")
def golden_samples(request: Request, scope: str = "all"):
    """黄金基准集看板：基准集成员标记 + 建议构成覆盖率（按成员统计）。
    scope=all|golden|gt_confirmed|gt_pending。
    """
    require_admin(request)
    tenant = _tenant_id(request)
    rows = db.list_receipt_rows(tenant_id=tenant)
    golden_rows = [r for r in rows if (getattr(r, "is_golden_sample", 0) or 0) == 1]

    # 1. 候选数据关联
    eval_candidates = db.list_eval_candidates(tenant_id=tenant, limit=5000)
    cand_by_rid = {}
    cand_by_id = {}
    for c in eval_candidates:
        cand_by_id[c.id] = c
        if c.receipt_id not in cand_by_rid:
            cand_by_rid[c.receipt_id] = c

    # 2. 评测集 GT 数据关联 (manifest.csv + expected/*.json)
    from app.api_evalset import get_evalset_dir, _load_manifest, _load_gt
    evalset_dir = get_evalset_dir()
    manifest_rows = _load_manifest(evalset_dir) or []
    confirmed_sids = {r["sample_id"] for r in manifest_rows if r.get("gt_status") == "confirmed"}
    manifest_by_sid = {r["sample_id"]: r for r in manifest_rows if r.get("sample_id")}

    sample_by_rid = {}
    sample_ids = set(manifest_by_sid.keys())
    exp_dir = os.path.join(evalset_dir, "expected")
    if os.path.isdir(exp_dir):
        for fname in os.listdir(exp_dir):
            if fname.endswith(".json"):
                sample_ids.add(fname[:-5])

    for sid in sample_ids:
        gt_data = _load_gt(evalset_dir, sid) or {}
        m_row = manifest_by_sid.get(sid, {})
        gt_status = gt_data.get("gt_status") or m_row.get("gt_status") or "draft"
        if gt_status == "confirmed":
            confirmed_sids.add(sid)

        matched_rid = None
        s_rid = gt_data.get("source_receipt_id")
        if s_rid is not None:
            try:
                matched_rid = int(s_rid)
            except (ValueError, TypeError):
                matched_rid = None
        if matched_rid is None and gt_data.get("source_candidate_id") is not None:
            cid = gt_data.get("source_candidate_id")
            try:
                cid_int = int(cid)
                cand = cand_by_id.get(cid_int) or db.get_eval_candidate(cid_int)
                if cand and cand.receipt_id is not None:
                    matched_rid = int(cand.receipt_id)
            except (ValueError, TypeError):
                pass

        if matched_rid is not None:
            if matched_rid not in sample_by_rid or gt_status == "confirmed":
                sample_by_rid[matched_rid] = {
                    "sample_id": sid,
                    "gt_status": gt_status,
                    "source_candidate_id": gt_data.get("source_candidate_id"),
                }

    # 3. 逐行判定 GT 状态并挂载标签与元数据
    all_annotated = []
    golden_total = len(golden_rows)
    gt_confirmed_total = 0
    gt_pending_total = 0

    for r in rows:
        sample_id = None
        eval_candidate_id = None

        if r.id in sample_by_rid and sample_by_rid[r.id].get("gt_status") == "confirmed":
            gt_status = "confirmed"
            gt_label = "已确权"
            sample_id = sample_by_rid[r.id].get("sample_id")
            eval_candidate_id = cand_by_rid[r.id].id if r.id in cand_by_rid else sample_by_rid[r.id].get("source_candidate_id")
        elif r.id in cand_by_rid:
            cand = cand_by_rid[r.id]
            eval_candidate_id = cand.id
            if cand.status == "pending":
                gt_status = "pending"
                gt_label = "待抽检"
            elif cand.status in ("promoted_to_val", "promoted_to_test"):
                gt_status = "promoted"
                gt_label = "已晋升评测集"
            elif cand.status == "rejected":
                gt_status = "rejected"
                gt_label = "已驳回"
            else:
                gt_status = "none"
                gt_label = "未确权"
            if r.id in sample_by_rid:
                sample_id = sample_by_rid[r.id].get("sample_id")
        elif r.id in sample_by_rid:
            sample_id = sample_by_rid[r.id].get("sample_id")
            eval_candidate_id = sample_by_rid[r.id].get("source_candidate_id")
            s_status = sample_by_rid[r.id].get("gt_status")
            if s_status in ("draft", "pending"):
                gt_status = "promoted"
                gt_label = "已晋升评测集"
            else:
                gt_status = "none"
                gt_label = "未确权"
        else:
            gt_status = "none"
            gt_label = "未确权"
            sample_id = None
            eval_candidate_id = None

        if gt_status == "confirmed":
            gt_confirmed_total += 1
        elif gt_status in ("pending", "promoted"):
            gt_pending_total += 1

        is_golden = (getattr(r, "is_golden_sample", 0) or 0) == 1
        all_annotated.append({
            "id": r.id,
            "supplier_name": r.supplier_name or "",
            "receipt_date": r.receipt_date or "",
            "doc_form": r.doc_form or "",
            "total_amount": r.total_amount or 0.0,
            "status": r.status or "",
            "currency": getattr(r, "currency", None) or "HKD",
            "use_grey": getattr(r, "use_grey", 0) or 0,
            "is_golden_sample": is_golden,
            "gt_status": gt_status,
            "gt_label": gt_label,
            "sample_id": sample_id,
            "eval_candidate_id": eval_candidate_id,
        })

    all_annotated.sort(key=lambda x: int(x.get("id") or 0), reverse=True)

    # 4. Scope 过滤
    if scope == "golden":
        scoped_rows = [item for item in all_annotated if item["is_golden_sample"]]
    elif scope == "gt_confirmed":
        scoped_rows = [item for item in all_annotated if item["gt_status"] == "confirmed"]
    elif scope == "gt_pending":
        scoped_rows = [item for item in all_annotated if item["gt_status"] in ("pending", "promoted")]
    else:
        scoped_rows = all_annotated

    # 黄金形态建议构成（仅为参考分布，不限制实际样本数量）
    target = {"printed_delivery_note": 15, "ncr_handwritten": 22, "thermal": 6, "weigh_slip": 4, "correction_note": 6, "monthly_statement": 4}
    from collections import Counter
    counter = Counter((r.doc_form or "unknown") for r in golden_rows)
    coverage = {k: {"target": v, "actual": counter.get(k, 0), "rate": round(counter.get(k, 0) / v * 100, 1) if v else 0} for k, v in target.items()}
    cur_counter = Counter((getattr(r, "currency", None) or "HKD") for r in golden_rows)

    items = scoped_rows[:500]
    valid_scopes = ("all", "golden", "gt_confirmed", "gt_pending")
    effective_scope = scope if scope in valid_scopes else "all"

    return {
        "status": "success",
        "total": len(scoped_rows),
        "golden_total": golden_total,
        "gt_confirmed_total": gt_confirmed_total,
        "gt_pending_total": gt_pending_total,
        "target_total": sum(target.values()),
        "scope": effective_scope,
        "coverage": coverage,
        "currency_breakdown": dict(cur_counter),
        "items": items,
    }


class GoldenMembershipBody(BaseModel):
    in_set: bool


@router.post("/api/admin/golden-samples/{receipt_id}/membership")
def set_golden_membership(receipt_id: int, body: GoldenMembershipBody, request: Request):
    """加入/移出黄金基准集（可逆策展标记；不删除单据本体，保留审计链）。"""
    require_admin(request)
    ok = db.set_golden_sample(receipt_id, 1 if body.in_set else 0, _tenant_id(request))
    if not ok:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "单据不存在"})
    return {"status": "success", "receipt_id": receipt_id, "in_set": body.in_set}


@router.post("/api/admin/golden-samples/import")
def import_golden_samples(request: Request, limit: int = 10):
    """一键导入黄金样本（调用 scripts/import_golden 逻辑，默认 10 张，上限 500）。"""
    require_admin(request)
    limit = max(1, min(500, int(limit or 10)))
    import subprocess
    import sys as _sys
    import os
    script = os.path.join(os.path.dirname(__file__), "../scripts/import_golden.py")
    try:
        res = subprocess.run([_sys.executable, script, "--limit", str(limit)],
                             capture_output=True, text=True, timeout=120,
                             cwd=os.path.join(os.path.dirname(__file__), ".."))
        out = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        ok = res.returncode == 0
        return {"status": "success" if ok else "error", "msg": out.strip()[:2000], "limit": limit}
    except Exception as e:
        return {"status": "error", "msg": str(e), "limit": limit}


@router.post("/api/admin/maintenance/deduplicate")
def maintenance_deduplicate(request: Request):
    """B-P0-1/F-P1-2 历史脏数据一键自愈：按 canonical 归一去重，合并 _\\d{10} 变体。

    幂等、无损历史流水（迁移 inventory_log 至主 SKU，停用副 SKU，日志 audit_logs 留痕）。
    权限：owner / admin 均可触发（店员 403）。
    前端 inventory 页 “一键清理重复食材” 按钮调用此接口；启动时亦自动执行 demo/app/main.py:42
    """
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    # P3-5 租户口径：按请求租户 scope 去重自愈（不跨租户合并）
    reports = db.deduplicate_skus_by_canonical(tenant_id=_tenant_id(request))
    # 追加手动触发审计（系统级）
    try:
        db.append_system_audit_log(who, "manual_deduplicate", "deduplicate_skus_by_canonical", "", str(reports))
    except Exception as e:
        logging.getLogger("api_admin").warning(
            "审计写入失败(action=manual_deduplicate who=%s): %s", who, e)
    merged_cnt = sum(len(r.get("merged_ids", [])) for r in reports) if reports else 0
    return {
        "status": "success",
        "reports": reports,
        "merged_groups": len(reports) if reports else 0,
        "merged_skus": merged_cnt,
        "msg": f"已自愈 {len(reports) if reports else 0} 组，共清理 {merged_cnt} 个重复 SKU" if reports else "无重复 SKU 需清理",
    }


class AdminExpCreateBody(BaseModel):
    name: str
    hypothesis: str = ""
    success_metric: str = "accuracy"
    # P2：目标流量比例必须是合法百分比；0（全部走对照组）与 100（全部走实验组）
    # 都是合法边界，越界值由 pydantic 直接拒绝（422），不再落到 db 层被静默改写。
    target_percent: int = Field(default=50, ge=0, le=100)
    # S2：min_sample=0 是合法边界（不做样本量门槛），负数无意义且会让守护门的
    # 「样本量不足不判」恒真（sample_size < 负数 永不成立），故在入口拦住。
    # 不设上界：样本门槛没有天然上限，且 db.coerce_int 不对越界做钳制。
    min_sample: int = Field(default=30, ge=0)
    treatment_model: Optional[str] = None
    control_model: Optional[str] = None
    treatment_base_url: Optional[str] = None
    control_base_url: Optional[str] = None
    treatment_api_key: Optional[str] = None
    control_api_key: Optional[str] = None


@router.get("/api/admin/experiments")
def list_experiments_admin(request: Request):
    require_admin(request)
    return {"status": "success", "data": db.list_experiments()}


@router.post("/api/admin/experiments")
def create_experiment_admin(body: AdminExpCreateBody, request: Request):
    require_admin(request)
    created = db.create_experiment(
        name=body.name,
        hypothesis=body.hypothesis,
        success_metric=body.success_metric,
        target_percent=body.target_percent,
        min_sample=body.min_sample,
        guardrail_metrics=[],
        target_supplier_ids=[],
    )
    if body.treatment_model or body.control_model or body.treatment_base_url or body.control_base_url:
        s = db.get_session()
        try:
            row = s.get(db._ExperimentRow, int(created["id"]))
            if row:
                snap = db._parse_json(row.grey_snapshot)
                if body.treatment_model:
                    snap["treatment_model"] = body.treatment_model
                if body.control_model:
                    snap["control_model"] = body.control_model
                if body.treatment_base_url:
                    snap["treatment_base_url"] = body.treatment_base_url
                if body.control_base_url:
                    snap["control_base_url"] = body.control_base_url
                if body.treatment_api_key:
                    snap["treatment_api_key"] = body.treatment_api_key
                if body.control_api_key:
                    snap["control_api_key"] = body.control_api_key
                row.grey_snapshot = db._safe_json(snap)
                s.commit()
        finally:
            s.close()
    exp = db.get_experiment(created["id"]) or created
    return {"status": "success", "id": created["id"], "experiment": exp}


@router.post("/api/admin/experiments/{exp_id}/start")
def start_experiment_admin(exp_id: int, request: Request):
    require_admin(request)
    existing = db.get_experiment(exp_id)
    if not existing:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
    snap = existing.get("grey_snapshot") or {}
    exp = db.start_experiment(exp_id, grey_snapshot=snap)
    return {"status": "success", "experiment": exp}


@router.post("/api/admin/experiments/{exp_id}/stop")
def stop_experiment_admin(exp_id: int, request: Request):
    require_admin(request)
    exp = db.stop_experiment(exp_id)
    if not exp:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
    return {"status": "success", "experiment": exp}


@router.get("/api/admin/experiments/{exp_id}")
def get_experiment_admin(exp_id: int, request: Request):
    require_admin(request)
    row = db.get_experiment(exp_id)
    if not row:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
    metrics = db.get_experiment_metrics(exp_id)
    detail = db.experiment_detail(exp_id)
    guardrail_events = db.list_guardrail_events(exp_id) if hasattr(db, "list_guardrail_events") else []
    snapshots = db.list_experiment_snapshots(exp_id, limit=5) if hasattr(db, "list_experiment_snapshots") else []
    return {
        "status": "success",
        "experiment": row,
        "metrics": metrics,
        "detail": detail,
        "guardrail_events": guardrail_events,
        "snapshots": snapshots,
    }


@router.get("/api/admin/experiments/{exp_id}/samples")
def get_experiment_samples(exp_id: int, request: Request):
    """返回分配至指定 A/B 实验的详细单据与决策样本明细。"""
    require_admin(request)
    from app.api_receipts import _mask_sensitive
    scope = request.query_params.get("scope", "all")

    s = db.get_session()
    try:
        assigns = s.query(db._ExperimentAssignRow).filter(
            db._ExperimentAssignRow.experiment_id == int(exp_id)).all()
        assign_map = {a.receipt_id: a.grp for a in assigns}
        assign_time_map = {a.receipt_id: a.assigned_at for a in assigns if getattr(a, "assigned_at", None)}

        dec_rows = s.query(db._DecisionLogRow).filter(
            db._DecisionLogRow.experiment_id == int(exp_id)).all()
        for dr in dec_rows:
            if dr.receipt_id and dr.receipt_id not in assign_map:
                assign_map[dr.receipt_id] = dr.grp or "control"
            if dr.receipt_id and dr.receipt_id not in assign_time_map and getattr(dr, "ts", None):
                assign_time_map[dr.receipt_id] = dr.ts

        rids = list(assign_map.keys())
        receipts = s.query(db._ReceiptRow).filter(db._ReceiptRow.id.in_(rids)).all() if rids else []
        receipt_map = {r.id: r for r in receipts}

        dec_by_rid = {}
        for dr in dec_rows:
            if dr.receipt_id not in dec_by_rid:
                dec_by_rid[dr.receipt_id] = []
            dec_by_rid[dr.receipt_id].append(dr)

        items = []
        for rid, grp in assign_map.items():
            r = receipt_map.get(rid)
            decs = dec_by_rid.get(rid, [])

            model_name = decs[0].model if decs else ("mimo-v3" if grp == "treatment" else "mimo-v2.5")
            engine_name = decs[0].engine if decs else "openai"
            adopted_count = sum(1 for d in decs if d.adopted == 1)
            hallu_count = sum(1 for d in decs if d.is_hallucination == 1)
            is_match = (adopted_count == len(decs)) if decs else True

            supplier = _mask_sensitive(r.supplier_name) if r and r.supplier_name else f"食材供应商_#V{rid}"
            total_amt = f"HK$ {r.total_amount:.2f}" if (r and r.total_amount) else "HK$ 1**.**"
            status = r.status if r else "parsed"

            item_data = {
                "receipt_id": rid,
                "grp": grp,
                "grp_label": "对照组 (Control)" if grp == "control" else "实验组 (Treatment)",
                "supplier_name": supplier,
                "total_amount": total_amt,
                "status": status,
                "engine": engine_name,
                "model": model_name,
                "decision_count": len(decs),
                "adopted_count": adopted_count,
                "hallu_count": hallu_count,
                "is_match": is_match,
                "accuracy": round(adopted_count / len(decs), 4) if decs else 1.0,
                "feedback_label": "完全采纳" if is_match else "人工修正",
                "assigned_at": assign_time_map.get(rid) or (decs[0].ts if decs and decs[0].ts else (r.created_at if r and getattr(r, "created_at", None) else "")),
            }

            if scope == "control" and grp != "control":
                continue
            if scope == "treatment" and grp != "treatment":
                continue
            items.append(item_data)

        items.sort(key=lambda x: x["receipt_id"], reverse=True)
        return {
            "status": "success",
            "experiment_id": exp_id,
            "total_samples": len(items),
            "control_count": sum(1 for it in items if it["grp"] == "control"),
            "treatment_count": sum(1 for it in items if it["grp"] == "treatment"),
            "samples": items,
        }
    finally:
        s.close()



@router.get("/api/admin/experiments/{exp_id}/pvalue")
def experiment_pvalue_card(exp_id: int, request: Request):
    """E-P1-3 p-value 卡片：显式返回显著性检验结果与样本阈值提示。"""
    require_admin(request)
    if db.get_experiment(exp_id) is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
    metrics = db.get_experiment_metrics(exp_id)
    tests = metrics.get("tests") or {}
    # 统一卡片结构，供前端直接渲染
    cards = []
    for metric in ("accuracy", "hallucination_rate", "edit_rate"):
        t = tests.get(metric)
        if not t:
            cards.append({"metric": metric, "p_value": None, "z": None, "effect_size_pp": None, "significant": None, "note": "样本不足或指标为空，无法计算"})
            continue
        p = t.get("p_value")
        sig = p is not None and float(p) < 0.05
        note = "显著 (p < 0.05)" if sig else "不显著 (p >= 0.05)"
        if metrics.get("low_confidence"):
            note += "｜样本不足，置信度低"
        cards.append({"metric": metric, "p_value": p, "z": t.get("z"), "effect_size_pp": t.get("effect_size_pp"), "significant": sig, "note": note})
    return {"status": "success", "experiment_id": exp_id, "low_confidence": metrics.get("low_confidence"), "cards": cards, "control": metrics.get("control"), "treatment": metrics.get("treatment")}


# -------------------------------------------------------------
# 识别全链路埋点聚合（11-组件Spec-全链路埋点与体验反馈体系 §6/§7）
# -------------------------------------------------------------
def _pct(vals, q):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f))


@router.get("/api/analytics/recognition-summary")
def recognition_summary(request: Request):
    require_admin(request)
    from datetime import datetime, timedelta
    import json as _j

    period_days = int(request.query_params.get("period_days", "7") or 7)
    end_ts = db.now_iso()
    start_ts = (datetime.fromisoformat(end_ts) - timedelta(days=period_days)).isoformat()

    # 观测口径跟随租户：Query 参数优先 → X-Tenant-Id 头；'all'/空 → 全租户聚合
    effective_tenant = resolve_tenant_filter(
        request, request.query_params.get("tenant_id"))

    s = db.get_session()
    try:
        q = s.query(db._UserEventRow).filter(db._UserEventRow.ts >= start_ts)
        if effective_tenant:
            q = q.filter(db._UserEventRow.tenant_id == effective_tenant)
        rows = q.all()
        events = [(e.event_type, e.properties or "{}", e.grp or "") for e in rows]
    finally:
        s.close()

    def _props(p):
        try:
            return _j.loads(p) if isinstance(p, str) else (p or {})
        except Exception:
            return {}

    def _is_warning_parsed(p):
        """R4：该 ocr_parsed 事件是否为"带警告通过"（门禁失败但保留了识别结构）。

        口径来源是 Job 埋点写入的 parse_status（见 receipt_utils 的 ocr_parsed）。
        历史事件没有该字段 → 按干净成功兼容，不追溯改写既有统计。
        """
        return str(p.get("parse_status") or "") == "parsed_with_warnings"

    started = [e for e in events if e[0] == "ocr_parse_started"]
    parsed = [(e[2], _props(e[1])) for e in events if e[0] == "ocr_parsed"]
    # parse_success 只计干净成功；带警告通过单列，避免 P14 的虚假成功信号流进看板
    parsed_ok = [(g, p) for g, p in parsed if not _is_warning_parsed(p)]
    parsed_warn = [(g, p) for g, p in parsed if _is_warning_parsed(p)]
    errors = [(e[2], _props(e[1])) for e in events if e[0] == "ocr_error"]
    guards = [(e[0], _props(e[1])) for e in events
              if e[0] in ("math_guard_checked", "contract_guard_checked")]
    reviews = [_props(e[1]) for e in events if e[0] == "receipt_review_submitted"]
    approves = [_props(e[1]) for e in events if e[0] == "receipt_approved"]
    abandons = [e for e in events if e[0] == "parse_abandoned_for_manual"]
    rag_hits = [e for e in events if e[0] == "rag_hit"]
    global_fb = [_props(e[1]) for e in events if e[0] == "feedback_received"]

    parse_total = len(started)
    # parse_success 与 parse_with_warnings 互斥：带警告通过的不得再计一次成功
    parse_success = len(parsed_ok)
    parse_with_warnings = len(parsed_warn)
    parse_fail = len(errors)
    elapsed = [p.get("elapsed_ms") for _, p in parsed + errors
               if isinstance(p.get("elapsed_ms"), (int, float))]
    gate_reject_reasons = {"math": 0, "contract": 0}
    for name, p in guards:
        if p.get("is_valid") is False:
            gate_reject_reasons["math" if name == "math_guard_checked"
                                else "contract"] += 1
    retry_distribution = {}
    for _, p in parsed:
        a = str(p.get("attempts") or 1)
        retry_distribution[a] = retry_distribution.get(a, 0) + 1

    rows_added = sum(int(p.get("rows_added") or 0) for p in reviews)
    rows_modified = sum(int(p.get("rows_modified") or 0) for p in reviews)
    rows_deleted = sum(int(p.get("rows_deleted") or 0) for p in reviews)
    sku_changed = sum(int(p.get("sku_changed") or 0) for p in reviews)
    fer_vals = [float(p.get("fer_rate")) for p in reviews
                if isinstance(p.get("fer_rate"), (int, float))]
    e2e_vals = [int(p.get("e2e_ms")) for p in approves
                if isinstance(p.get("e2e_ms"), (int, float))]

    fbs = db.list_receipt_feedbacks(tenant_id=effective_tenant)
    fb_up = sum(1 for f in fbs if f.get("like") == 1)
    fb_down = sum(1 for f in fbs if f.get("like") == -1)

    def _grp_block(grp):
        g_ok = [p for g, p in parsed_ok if g == grp]
        g_warn = [p for g, p in parsed_warn if g == grp]
        g_reviews = [_props(e[1]) for e in events
                     if e[0] == "receipt_review_submitted" and (e[2] or "") == grp]
        g_fer = [float(p.get("fer_rate")) for p in g_reviews
                 if isinstance(p.get("fer_rate"), (int, float))]
        return {
            "parse_success": len(g_ok),
            "parse_with_warnings": len(g_warn),
            "fer_rate": round(sum(g_fer) / len(g_fer), 4) if g_fer else None,
        }

    data = {
        "tenant_id": effective_tenant or "all",
        "parse_total": parse_total,
        "parse_success": parse_success,
        "parse_with_warnings": parse_with_warnings,
        "parse_fail": parse_fail,
        "parse_fail_rate": round(parse_fail / parse_total, 4) if parse_total else 0.0,
        "elapsed_p50_ms": _pct(elapsed, 0.5),
        "elapsed_p95_ms": _pct(elapsed, 0.95),
        "gate_reject_count": sum(gate_reject_reasons.values()),
        "gate_reject_reasons": gate_reject_reasons,
        "retry_distribution": retry_distribution,
        "rows_added": rows_added,
        "rows_modified": rows_modified,
        "rows_deleted": rows_deleted,
        "sku_changed": sku_changed,
        "sku_changed_rate": round(
            sum(1 for p in reviews if (p.get("sku_changed") or 0) > 0) / len(reviews), 4
        ) if reviews else 0.0,
        "fer_rate": round(sum(fer_vals) / len(fer_vals), 4) if fer_vals else None,
        "abandon_count": len(abandons),
        "abandon_rate": round(len(abandons) / parse_total, 4) if parse_total else 0.0,
        "e2e_avg_ms": round(sum(e2e_vals) / len(e2e_vals)) if e2e_vals else None,
        "rag_hit_count": len(rag_hits),
        "feedback": {
            "up": fb_up, "down": fb_down,
            "global_up": sum(1 for p in global_fb if p.get("like") == 1),
            "global_down": sum(1 for p in global_fb if p.get("like") == -1),
        },
        "grey_split": {"control": _grp_block("control"),
                       "treatment": _grp_block("treatment")},
    }
    return {"status": "success", "period_days": period_days, "data": data}


@router.get("/api/analytics/parsing-logs")
def analytics_parsing_logs(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    tenant_id: str = "all",
    status: str = "all",
    gate_status: str = "all",
    feedback: str = "all",
    is_modified: str = "all",
    modified_only: Optional[str] = None,
    search: str = "",
    date_range: str = "all",
    start_date: str = "",
    end_date: str = "",
):
    """05 解析日志与链路审计：全量解析日志明细查询与 KPI 汇总。"""
    require_admin(request)
    from app.services.parsing_logs import query_parsing_logs

    if modified_only is not None and modified_only != "":
        val_str = str(modified_only).strip().lower()
        if val_str in ("true", "1", "yes"):
            is_modified = "yes"
        elif val_str in ("false", "0", "no"):
            is_modified = "no"

    effective_tenant = resolve_tenant_filter(request, tenant_id)

    s = db.get_session()
    try:
        result = query_parsing_logs(
            s=s,
            tenant_id=effective_tenant,
            status=status,
            gate_status=gate_status,
            feedback=feedback,
            is_modified=is_modified,
            search=search,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
        return {"status": "success", "data": result}
    finally:
        s.close()


@router.get("/api/analytics/parsing-logs/detail")
def analytics_parsing_log_detail(
    request: Request,
    record_id: Optional[int] = None,
    job_id: Optional[str] = None,
):
    """01 线上体验感知与链路审计：单据解析生命周期全息详情与关联打点时序下钻。"""
    require_admin(request)
    from app.services.parsing_logs import get_parsing_log_detail

    s = db.get_session()
    try:
        detail = get_parsing_log_detail(s=s, record_id=record_id, job_id=job_id)
        if not detail:
            return {"status": "error", "detail": "单据或链路未找到"}
        return {"status": "success", "data": detail}
    finally:
        s.close()


