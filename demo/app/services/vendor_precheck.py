# -*- coding: utf-8 -*-
"""首轮供应商预判（P2-T6 后端部分）：只裁抬头 ROI + 只抽 vendor 的低成本预判。

why：供应商记忆注入现状只有 `vendor_hint` 一条通道，而前端从不发送该字段
（`grep -rn vendor_hint demo/static demo/templates` 零命中），于是「首轮注入」
根本无从发生——实测记忆命中率仅 1.71%（12/701）。本模块补上第二条来源：
上传时做一次极低成本预判（只裁抬头、只要求模型回 `{"vendor": ...}`），
把结果作为 `vendor_hint` 交给 `start_recognition_job`。

抬头裁剪的几何部分在 `preprocess.crop_header_roi`（纯函数、返回 ndarray，解码
复用 `preprocess._imread` 的 HEIC/EXIF 统一口径）；本模块只负责「落临时文件 →
调识别腿模型 → 解析 vendor」，编码仍走 `extract_chain._image_data_url` 那份唯一实现。

与前端手选值的关系（与调用点口径一致）：**手选优先，预判仅在 `vendor_hint`
为空时生效**。调用点先判空再调用本模块，因此两者不会叠加、也不会互相覆盖。

开关：settings 键 `vendor_precheck_enabled`（缺省 false = 零行为变化；管理台可热切换）。
环境变量 `VENDOR_PRECHECK_ENABLED` 仍作为**兜底**入口保留（settings 读取失败或该键不存在时
才读它），保证纯离线/无 DB 场景仍能显式开启。关闭时 `predict_vendor` 直接返回空串：不裁剪、
不建模型、不发起任何网络调用。

失败语义（与 preprocess 的降级口径一致）：开关关闭 / 裁剪失败 / 引擎异常 /
超时 / 解析不出 vendor → 一律返回空串，**绝不抛异常、绝不阻断识别主链路**。
本模块不做「未尝试也算命中」的记账——拿不到就给空串，由度量侧按空串计入未命中。

批量上传（`/api/upload_batch`）本期不接预判：批量是 N 张图，逐张预判会让单次
请求付出 N 次模型调用，成本与收益不匹配，留待 P2-T5 度量确认预判收益后再定。
"""

import concurrent.futures
import logging
import os
import tempfile
import threading

logger = logging.getLogger("vendor_precheck")

# 预判独立超时（秒）：必须显著小于引擎自身 call_timeout（缺省 60s），
# 否则「低成本预判」反而成为上传响应的新瓶颈。超时即放弃预判。
DEFAULT_TIMEOUT_SECONDS = 15.0

# 开关（缺省关闭 = 零行为变化）；主入口是 settings 键，环境变量仅作兜底
SETTINGS_KEY = "vendor_precheck_enabled"
_ENV_SWITCH = "VENDOR_PRECHECK_ENABLED"
_TRUE_WORDS = ("1", "true", "yes", "on", "y", "t")

def _load_prompts():
    """从 ai_registry 读取预判提示词；读不到就显式失败（不留内联副本），由调用方告警并按未命中处理。"""
    from ai_registry.registry import ai_registry
    system_prompt = ai_registry.get_prompt("precheck")
    import importlib
    meta = ai_registry.get_prompt_metadata("precheck")
    mod = importlib.import_module(f"ai_registry.prompts.precheck.{meta['active_version']}")
    return system_prompt, getattr(mod, "HUMAN_TEXT")


# 最近一次预判的用量，按图片路径暂存，供随后的识别任务把预判成本计入单张成本。
_USAGE_BY_IMAGE: dict = {}
_USAGE_LOCK = threading.Lock()
_USAGE_MAX = 256


def _stash_usage(image_path, usage: dict) -> None:
    with _USAGE_LOCK:
        if len(_USAGE_BY_IMAGE) >= _USAGE_MAX:
            _USAGE_BY_IMAGE.pop(next(iter(_USAGE_BY_IMAGE)), None)
        _USAGE_BY_IMAGE[str(image_path)] = usage


def pop_precheck_usage(image_path) -> dict:
    """取走并清除该图片的预判用量（{"token_usage","cost_cny","cost_estimated"}）；没有则返回空 dict。"""
    with _USAGE_LOCK:
        return _USAGE_BY_IMAGE.pop(str(image_path), {}) or {}


def is_enabled() -> bool:
    """预判开关：settings 键 vendor_precheck_enabled 优先，环境变量兜底（缺省 false）。

    why 读原始行而不是 settings_service.get_bool：get_bool 在「键未设置」时返回的是
    SETTINGS_DEFAULTS 里的 False，与「管理台显式设为 False」不可区分 —— 那样环境变量
    兜底就永远生效不了（会被 False 短路）。这里读 db.get_app_setting 的原始值：
    None/空 = 未设置 → 回落环境变量；有值 → 以它为准（大小写不敏感的布尔词）。
    缺省 false 保证与接线前行为逐字节一致（零行为变化）。
    """
    try:
        from app import db as _db
        raw = _db.get_app_setting(SETTINGS_KEY)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip().lower() in _TRUE_WORDS
    except Exception:
        pass  # settings 不可用（无 DB）→ 回落环境变量，不阻断上传
    return str(os.getenv(_ENV_SWITCH, "")).strip().lower() in ("1", "true", "yes", "on")


def _safe_remove(path) -> None:
    """删除临时文件；失败只告警，不抛异常（清理不得影响主流程）。"""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning("[vendor_precheck] 临时 ROI 文件清理失败(不影响识别): %s", e)


def _roi_to_temp_jpeg(roi):
    """抬头 ROI（BGR ndarray）→ 系统临时目录下的 JPEG 路径；失败返回 None。

    why：模型输入走 `extract_chain._image_data_url`，它按「路径」契约工作（限边 /
    JPEG / base64 仍是全仓唯一那份实现）；而几何裁剪本身是 preprocess 的职责
    （`preprocess.crop_header_roi`，纯函数不落盘）。临时文件必须落系统临时目录、
    **绝不落 uploads**（本仓有过「跑测试/上传留下无引用孤儿文件」的事故）。
    返回路径的生命周期由调用方负责（见 `predict_vendor` 的 finally 清理）。
    """
    out_path = None
    try:
        fd, out_path = tempfile.mkstemp(prefix="vendor_precheck_", suffix=".jpg")
        os.close(fd)
        ok = False
        try:
            import cv2
            ok = bool(cv2.imwrite(out_path, roi, [cv2.IMWRITE_JPEG_QUALITY, 90]))
        except Exception:
            ok = False
        if not ok:
            # cv2 不可用时回退 PIL（_imread 返回 BGR，PIL 需要 RGB）
            try:
                from PIL import Image
                Image.fromarray(roi[:, :, ::-1]).save(out_path, format="JPEG", quality=90)
                ok = True
            except Exception as e:
                logger.warning("[vendor_precheck] 抬头 ROI 编码失败: %s", e)
        if not ok or not os.path.exists(out_path):
            _safe_remove(out_path)
            return None
        return out_path
    except Exception as e:
        logger.warning("[vendor_precheck] 抬头 ROI 落盘失败，跳过预判: %s", e)
        _safe_remove(out_path)
        return None


def _content_text(result) -> str:
    """取模型返回的消息正文（str）。

    与 `extract_chain._extract_text` 同口径；后者是 `extract_receipt` 内部的嵌套
    闭包、无法导入，故此处保留等价的最小实现（仅做类型适配，不含业务规则——
    真正的 vendor 解析统一走 `_extract_vendor_from_raw`，不在此重复）。
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if content is not None:
        return str(content)
    return str(result)


def predict_vendor(image_path, cfg=None, usage_out=None) -> str:
    """预判单据供应商名；任何失败返回空串（绝不抛异常，供上传链路直接调用）。

    流程：开关关闭即返回空串（零开销）→ 裁抬头 ROI → 识别腿模型（只抽 vendor）
    → `extract_chain._extract_vendor_from_raw` 解析（json.loads 优先、正则兜底；
    该函数此前全仓无任何调用方，此处成为它的唯一消费方，保证预判与识别链路
    的解析口径完全一致）。

    cfg：EngineConfig；缺省 None 时**本函数自行解析管理台配置**（见下）。
    usage_out：可选 dict，成功调用后写入本次预判的 token 用量与成本；无论是否传入，
    用量都会按图片路径暂存，供 supervisor 计入同一张单的成本（pop_precheck_usage）。
    """
    if not is_enabled():
        # 缺省路径：开关关闭时零行为变化（不裁剪、不建模型、不发网络请求）
        return ""
    if not image_path:
        return ""

    if cfg is None:
        # 为什么必须自己解析：build_recognition_model(cfg=None) 会走硬编码默认模型
        # （llm.py:645 的 "Qwen/Qwen3-VL-32B-Instruct"），**绕过管理台配置的识别引擎**
        # ——预判与识别主链路就会用两个不同引擎（P1 审核 mustFix M1）。这里兜底解析一次；
        # 解析失败只留痕并回落 None（保持"预判是可选增益"的降级语义）。
        try:
            from app import db as _db
            cfg = _db.get_engine_config()
        except Exception as _cfg_err:
            logger.warning(
                "[vendor_precheck] 读取引擎配置失败，回落默认模型（预判可能不是管理台配置的引擎）: %s",
                _cfg_err)

    roi_path = None
    pool = None
    try:
        from app.services import preprocess
        roi = preprocess.crop_header_roi(image_path)
        if roi is None:
            return ""
        roi_path = _roi_to_temp_jpeg(roi)
        if not roi_path:
            return ""

        from langchain_core.messages import HumanMessage, SystemMessage

        from app.chains import extract_chain
        from app.llm import build_recognition_model

        system_prompt, human_text = _load_prompts()
        model = build_recognition_model(cfg=cfg)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=[
                {"type": "image_url",
                 "image_url": {"url": extract_chain._image_data_url(roi_path)}},
                {"type": "text", "text": human_text},
            ]),
        ]
        # 独立超时：见 DEFAULT_TIMEOUT_SECONDS 的 why。shutdown(wait=False)
        # 与 extract_chain 的并发用法同口径——超时后不等底层 HTTP 调用收尾，
        # 否则「超时保护」会把上传响应又拖回去。
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(model.invoke, messages)
        result = future.result(timeout=DEFAULT_TIMEOUT_SECONDS)
        try:
            tu = extract_chain._extract_token_usage(result)
            cost, est, _why = extract_chain._cost_from_tokens_ex(
                tu, extract_chain._model_cost_tier_name(model))
            usage = {"token_usage": tu, "cost_cny": float(cost or 0), "cost_estimated": bool(est)}
            _stash_usage(image_path, usage)
            if isinstance(usage_out, dict):
                usage_out.update(usage)
        except Exception as usage_err:
            logger.warning("[vendor_precheck] 预判成本统计失败(不影响识别): %s", usage_err)
        vendor = extract_chain._extract_vendor_from_raw(_content_text(result))
        return str(vendor or "").strip()
    except concurrent.futures.TimeoutError:
        logger.warning("[vendor_precheck] 预判超时(>%ss)，按未命中处理",
                       DEFAULT_TIMEOUT_SECONDS)
        return ""
    except Exception as e:
        # 预判是可选增益：任何异常都降级为空串并留痕，不阻断识别主链路
        logger.warning("[vendor_precheck] 预判失败，按未命中处理（不影响识别）: %s", e)
        return ""
    finally:
        if pool is not None:
            pool.shutdown(wait=False)
        _safe_remove(roi_path)
