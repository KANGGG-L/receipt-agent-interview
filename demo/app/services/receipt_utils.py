# -*- coding: utf-8 -*-
"""收据数据桥接：AI 识别管线结果 ↔ 前端契约数据模型。

- run_ai_pipeline(image_path, vendor_hint): 调用 supervisor，返回前端 upload 响应 data
- build_detail(receipt_row): 收据行 → /api/receipt/{id} 的 ReceiptDetail
- build_row(receipt_row): 收据行 → /api/receipts 列表行
"""

import hmac
import json
import logging
import os
import re
import threading
import time
from urllib.parse import quote

from app import db
from app.models import EngineConfig
from app.services import image_web
from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool as _S

# B-P0-1 专用：流水号后缀正则 (FR-7 品名归一) —— 有机菜心_1787140420→有机菜心, 本地新鲜菜心_1787140411→本地新鲜菜心
_SERIAL_SUFFIX_RE = re.compile(r"_\d{10}$")

# 阶段 2A 工作项③：名称后缀剥离规则单例（懒加载 + 缓存，避免每次明细行读盘）。
# 规则表 name_suffix_rules.yaml 与消费方同目录；缺文件/坏规则 fail-fast SystemExit，
# 不得静默按"无后缀剥离"降级。_match_sku 双侧归一、L1 钩子 canonical_name、
# 候选生成器分组全部消费 _normalize_sku_name —— 一处接线三处生效。
_SUFFIX_RULES = None


def _suffix_rules():
    global _SUFFIX_RULES
    if _SUFFIX_RULES is None:
        from app.services.name_suffix_rules import load_rules
        _SUFFIX_RULES = load_rules()
    return _SUFFIX_RULES

# 模块顶层单例：避免 save_parsed_data/canonical_sku_name 每次实例化
try:
    _sanitizer_singleton = _S()
except Exception:
    _sanitizer_singleton = None

# 在内存 job 表（单进程部署约束，对齐完整版）
JOBS = {}
JOBS_LOCK = threading.Lock()


def _track_internal(event_type, receipt_id=None, properties=None):
    """埋点内部封装：Job 线程内无 request context，直写 user_event，失败静默。"""
    try:
        db.log_user_event(
            account_id="system",
            session_id="",
            event_type=str(event_type),
            receipt_id=int(receipt_id) if receipt_id else None,
            properties=properties or {},
        )
    except Exception:
        pass


def _apply_snapshot_credential(cfg, snap, side, applied):
    """把 A/B 快照的 <side>_api_key 应用到 cfg，并处理「换端点不换密钥」的凭据外泄。

    规则（凭据保护）：
      - 快照提供了 <side>_api_key：采用该密钥，applied["api_key_source"] = "snapshot"。
      - 快照覆盖了 <side>_base_url 但未提供对应密钥：显式清空 cfg.openai_rec_api_key，
        applied["api_key_source"] = "cleared"。

    why（为什么必须清空）：base_url 被快照改写后，库中真实密钥若不清空，会被当作
    Authorization 发往实验指定的第三方端点，构成凭据外泄；清空后该次调用会因缺密钥
    失败，属可观测的失败而非静默外泄。仅当 base_url 也未被覆盖时才保留库内密钥。

    applied：调用方的 _applied 字典，就地记录 api_key_source，供 experiment_override
    透传与日志留痕（不静默）。
    """
    key_field = f"{side}_api_key"
    base_field = f"{side}_base_url"
    if snap.get(key_field):
        cfg.openai_rec_api_key = snap[key_field]
        applied["api_key_source"] = "snapshot"
    elif snap.get(base_field):
        cfg.openai_rec_api_key = ""
        applied["api_key_source"] = "cleared"


def _db_path_switched(job_id, stage):
    """后台 Job 跨库写入前哨：库路径在任务执行期间被切换 → 放弃写入并置可辨失败态。

    why：识别 Job 跑在后台线程，写库时读的是「当时的」db.DB_PATH。若任务执行期间
    DB_PATH 被切到另一个库（测试夹具收尾还原、外部调用切换），晚到的 Job 会把结果
    写进新库 —— 这正是历史「测试污染 live 库」的机制。db 层的
    `_guard_job_db_write` 已兜住一切写库调用（含 supervisor 的决策日志/埋点/回流），
    这里负责在关键节点提前放弃，并给 Job 一个前端可辨别的失败态，避免前端误以为成功。

    返回 True 表示已放弃（调用方应直接 return）。绝不抛异常：Job 外层被异常打断会把
    「库被切换」伪装成识别失败，也会让线程状态失控。
    """
    mismatch = db.job_db_path_mismatch()
    if mismatch is None:
        return False
    bound, current = mismatch
    msg = ("库路径在识别任务执行期间被切换（任务库=%s → 当前库=%s），"
           "已放弃写入以避免污染新库" % (bound, current))
    logging.getLogger("receipt_utils").warning(
        "识别 Job %s 在 %s 阶段放弃写入：%s", job_id, stage, msg)
    with JOBS_LOCK:
        JOBS[job_id].update({
            "job_status": "error",
            "error_code": "db_path_changed",
            "error_msg": msg,
        })
    return True


def _job_elapsed_ms(job_id):
    with JOBS_LOCK:
        started = JOBS.get(job_id, {}).get("started_ts")
    if not started:
        return None
    return int((time.time() - started) * 1000)


def _log_max_attempt(result):
    log = result.get("log") or []
    attempts = [int(e.get("attempt", 0)) for e in log if isinstance(e, dict)]
    return max(attempts) if attempts else 1


def _log_gate_rejects(result):
    log = result.get("log") or []
    return sum(1 for e in log if isinstance(e, dict)
               and e.get("action") in ("gate_reject", "gate_reject_fast"))


# -------------------------------------------------------------
# AI 管线：异步 Job
# -------------------------------------------------------------
def start_recognition_job(image_path, vendor_hint="", receipt_id=None,
                          tenant_id=None, image_guard=None,
                          vendor_hint_origin="", raw_image_path=None, quality_warnings=None):
    """启动后台识别 Job。返回 job_id。

    线程执行识别管线；完成后写回收据行并更新 JOBS。
    P0-1 写入链路租户透传：tenant_id 为上传入口的租户键，缺省 "default"
    向后兼容；必须在派发后台线程前捕获为局部变量，Job 线程内不得读 request。
    P2-1 图内隐藏文字检测结果（image_guard）：上传入口在 preprocess.apply_pipeline
    之前对**原图**算出，随 job 透传——落 receipts.image_guard_json（供 build_detail
    详情与前端横幅）、进 JOBS（供轮询），并转交 supervisor 写入 extract 决策 extra。
    缺省 None 时不写、行为零变化；异常只告警不阻断。
    vendor_hint_origin（可观测性，additive）：显式 vendor_hint 的来源
    （"user"=用户手填 / "precheck"=上传端点预判填充），随管线 state → Job result /
    extract 决策 extra 透传。缺省空串时管线行为与接线前一致（不写 extra 键、
    result 字段为空串）。
    raw_image_path（真理源保护）：单据入库持久化的原始上传图片路径。若传入，
    receipts.image_path 恒指向 raw_image_path，识别管线使用 image_path（可为预处理图）。
    quality_warnings（W-SEC-4）：前置画质/安全扫描告警（如 image_guard_degraded）。
    """
    job_id = db.new_id()
    # R1：捕获任务创建时的库路径，随后台线程一起带走。每次写库前校验生效的
    # db.DB_PATH 是否仍等于它；不一致说明库已被切换，宁可放弃写入也不能污染新库。
    job_db_path = db.DB_PATH
    _tenant = str(tenant_id or "default").strip() or "default"
    if receipt_id is None:
        receipt_id = db.create_receipt(status="uploaded", tenant_id=_tenant)
    _guard_json = None
    if isinstance(image_guard, dict):
        try:
            _guard_json = json.dumps(image_guard, ensure_ascii=False)
        except Exception as e:
            logging.getLogger("receipt_utils").warning(
                "[WARN] image_guard 序列化失败，跳过落库: %s", e)
    db_image_path = raw_image_path if raw_image_path else image_path
    _qw_list = list(quality_warnings or [])
    if isinstance(image_guard, dict) and image_guard.get("metrics", {}).get("degraded"):
        if "image_guard_degraded" not in _qw_list:
            _qw_list.append("image_guard_degraded")
    _qw_json = json.dumps(_qw_list, ensure_ascii=False) if _qw_list else None
    update_kw = {"status": "parsing", "image_path": db_image_path,
                 "image_guard_json": _guard_json}
    if _qw_json is not None:
        update_kw["quality_warnings_json"] = _qw_json
    db.update_receipt(receipt_id, **update_kw)

    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "job_status": "queued",
                        "receipt_id": receipt_id, "started_ts": time.time(),
                        "image_guard": image_guard}
    _track_internal("ocr_parse_started", receipt_id, {"job_id": job_id})

    def _run():
        # R1：把任务库路径绑定到本线程（线程级），db 层写入闸门据此拦下跨库写入；
        # 绑定本身失败不得打断 Job（否则线程异常逃逸），降级为「无闸门」并告警。
        try:
            db.bind_job_db_path(job_db_path, job_id)
        except Exception as bind_err:
            logging.getLogger("receipt_utils").warning(
                "识别 Job %s 绑定任务库路径失败，跨库写入闸门未生效: %s",
                job_id, bind_err)
        with JOBS_LOCK:
            JOBS[job_id]["job_status"] = "running"

        def _on_event(ev):
            # 首选引擎降级回退时实时写入 Job，供前端轮询第一时间弹提示
            try:
                if isinstance(ev, dict) and ev.get("type") == "engine_fallback":
                    with JOBS_LOCK:
                        JOBS[job_id]["progress_fallback"] = {
                            "reason": str(ev.get("reason") or "")[:300],
                            "from": str(ev.get("from") or "")[:60],
                            "to": str(ev.get("to") or "")[:60],
                        }
            except Exception:
                pass

        try:
            # 线程启动到真正开工之间库可能已被切换：提前放弃，省掉无谓的引擎调用
            if _db_path_switched(job_id, "start"):
                return
            cfg = db.get_engine_config()
            experiment_id = None
            # P5：实验覆盖管理台配置的事实必须可追溯。覆盖语义保持不变（实验本就该覆盖），
            # 但要把「哪条实验、哪个组、覆盖成了哪个模型」透传给 Job 与前端；
            # 否则管理员只看到「改了引擎没生效」，无从得知是实验在起作用。
            experiment_override = None
            try:
                running_exp = db.get_running_experiment()
                if running_exp:
                    experiment_id = running_exp["id"]
                    # P2：target_percent=0（"全部走对照组"）必须被尊重为 0，
                    # 不能用 `or 50` 把它静默当成 50；归一逻辑见 db.coerce_int。
                    target_pct = db.coerce_int(running_exp.get("target_percent"), 50)
                    bucket = (int(receipt_id) % 100) if receipt_id else 0
                    grp = "treatment" if bucket < target_pct else "control"
                    db.add_assignment(experiment_id, receipt_id, grp)
                    snap = running_exp.get("grey_snapshot") or {}
                    if isinstance(snap, str):
                        try:
                            snap = json.loads(snap)
                        except Exception:
                            snap = {}
                    # 只在快照真的改写了 model/base_url 时才算「覆盖发生」；
                    # 空快照的 running 实验仅做分组，不应误报成覆盖。
                    _applied = {}
                    if grp == "treatment":
                        if snap.get("treatment_model"):
                            cfg.openai_rec_model = snap["treatment_model"]
                            _applied["model"] = cfg.openai_rec_model
                        if snap.get("treatment_base_url"):
                            cfg.openai_rec_base_url = snap["treatment_base_url"]
                            _applied["base_url"] = cfg.openai_rec_base_url
                        _apply_snapshot_credential(cfg, snap, "treatment", _applied)
                    else:
                        if snap.get("control_model"):
                            cfg.openai_rec_model = snap["control_model"]
                            _applied["model"] = cfg.openai_rec_model
                        if snap.get("control_base_url"):
                            cfg.openai_rec_base_url = snap["control_base_url"]
                            _applied["base_url"] = cfg.openai_rec_base_url
                        _apply_snapshot_credential(cfg, snap, "control", _applied)
                    if _applied:
                        experiment_override = {
                            "experiment_id": experiment_id,
                            "grp": grp,
                            "model": cfg.openai_rec_model,
                            "base_url": cfg.openai_rec_base_url,
                            "api_key_source": _applied.get("api_key_source"),
                        }
                        logging.getLogger("receipt_utils").warning(
                            "A/B 实验 #%s(%s) 已覆盖管理台引擎配置：model=%s base_url=%s api_key_source=%s",
                            experiment_id, grp, cfg.openai_rec_model,
                            cfg.openai_rec_base_url,
                            _applied.get("api_key_source") or "unchanged")
            except Exception as exp_err:
                logging.getLogger("receipt_utils").warning(f"解析运行中 A/B 实验路由失败: {exp_err}")
            # Job 级透传：错误/进行中轮询也能看到本次是否被实验覆盖
            with JOBS_LOCK:
                JOBS[job_id]["experiment_override"] = experiment_override

            from app.chains import supervisor
            if _db_path_switched(job_id, "before_pipeline"):
                return
            # 可观测性：显式 vendor_hint 的来源（"user"/"precheck"）随管线透传，落
            # extract 决策 extra 与 Job result。仅在非空时传参 —— 缺省语义等价
            # （管线侧形参缺省即空串），又不必破坏固定签名的替身/直接调用消费方
            # （既有 run_pipeline 打桩惯例，见 test_rag_hint_source_persistence 等）。
            _pipeline_kwargs = {}
            if str(vendor_hint_origin or "").strip():
                _pipeline_kwargs["vendor_hint_origin"] = vendor_hint_origin
            result = supervisor.run_pipeline(
                image_path, vendor_hint=vendor_hint, config=cfg,
                supplier_name=vendor_hint or "",
                receipt_id=receipt_id,  # U-2: 透传 receipt_id，AI 决策履历落库关联单据
                experiment_id=experiment_id,
                on_event=_on_event,
                image_guard=image_guard,  # P2-1 图内隐藏文字 flags 随管线落 extract 决策 extra
                # Gap H1：把 Job 启动时捕获的租户带进识别管线，VendorMemory 读侧与
                # approve 写侧（api_receipts 回写）因此同租户；_tenant 是派发线程前的
                # 局部变量（见本函数开头），Job 线程内不读 request。
                tenant_id=_tenant,
                **_pipeline_kwargs,
            )
            data = result.get("data")
            if data is None:
                if _db_path_switched(job_id, "error_writeback"):
                    return
                db.update_receipt(receipt_id, status="error")
                with JOBS_LOCK:
                    JOBS[job_id].update({
                        "job_status": "error",
                        "error_msg": result.get("contract_error") or result.get("last_error") or "识别失败",
                        "fallback_triggered": result.get("fallback_triggered", False),
                        "fallback_failed": result.get("fallback_failed", False),
                        "fallback_from": result.get("fallback_from", ""),
                        "fallback_engine": result.get("fallback_engine", ""),
                    })
                _track_internal("ocr_error", receipt_id, {
                    "job_id": job_id, "status": "error",
                    "elapsed_ms": _job_elapsed_ms(job_id),
                    "reason": str(result.get("contract_error") or result.get("last_error") or "识别失败")[:300],
                    "attempts": _log_max_attempt(result),
                    "gate_rejects": _log_gate_rejects(result),
                })
                return

            if _db_path_switched(job_id, "before_save"):
                return
            detail = save_parsed_data(receipt_id, data, result)
            # 极端窄窗（save 内部被切换）：写库已被 db 层拦下，不能把空壳结果报成 done
            if _db_path_switched(job_id, "after_save"):
                return
            # 提为局部变量：原实现的 image_url/version/quality_warnings 三处各查一次，
            # 既冗余又可能在同一结果里读到不同快照
            done_row = db.get_receipt_row(receipt_id)
            # why：save_parsed_data 与 get_receipt_row 之间单据可能被删除/软删，
            # 此时 done_row 为 None。所有取自 done_row 的字段都必须纳入同一守卫，
            # 否则 version / quality_warnings_json 会抛 AttributeError，
            # 且原先只有 image_url 行有 else 分支，会让人误以为 None 已被处理。
            if done_row:
                image_url = public_image_url(receipt_id, done_row.image_path,
                                             getattr(done_row, "tenant_id", None))
                version = done_row.version
                quality_warnings = json.loads(done_row.quality_warnings_json or "[]")
            else:
                image_url = ""
                version = ""
                quality_warnings = []
            with JOBS_LOCK:
                JOBS[job_id].update({
                    "job_status": "done",
                    "result": {
                        "status": "success",
                        "receipt_id": receipt_id,
                        "data": detail,
                        "image_url": image_url,
                        "version": version,
                        "quality_warnings": quality_warnings,
                        # P2-1：图内隐藏文字检测结果随 job 结果透传（前端轮询可见）
                        "image_guard": image_guard,
                        "fallback_triggered": result.get("fallback_triggered", False),
                        "fallback_reason": result.get("fallback_reason", ""),
                        "fallback_from": result.get("fallback_from", ""),
                        "fallback_engine": result.get("fallback_engine", ""),
                        "experiment_override": experiment_override,
                        # FP-1：run 级 hint 来源标记随 Job result 透传（"first_round"
                        # 或空串）。恒有键：Job result 是内存态非落盘，新增键对前端纯增量；
                        # 开关关/显式 hint 时为空串（get_job 的兜底分支刻意不补，无 state 可取）。
                        "rag_hint_source": result.get("rag_hint_source") or "",
                        # 可观测性：显式 vendor_hint 的来源（"user"=用户手填 /
                        # "precheck"=上传端点预判填充）随 Job result 透传。同款恒有键
                        # 纯增量手法；无显式 hint / 未接线调用方时为空串。
                        "vendor_hint_origin": result.get("vendor_hint_origin") or "",
                    },
                })
            # R4：看板口径修复。该事件此前只带 Job 生命周期的 status="done"，
            # 而看板（api_admin.recognition_summary）直接按事件条数计 parse_success，
            # 于是门禁失败、仅"带警告通过"的单据在看板维度仍是干净成功（P14 的
            # 虚假成功信号残留）。这里显式带上管线结果状态与门禁警告，供看板单列。
            _parse_status = str(result.get("status") or "").strip()
            if _parse_status not in ("parsed", "parsed_with_warnings"):
                # 兜底：调用方未标注 status 时，按门禁警告有无推断，避免把带警告
                # 单据漏计成干净成功；两者皆无则维持原有"成功"口径。
                _parse_status = ("parsed_with_warnings"
                                 if result.get("gate_warnings") else "parsed")
            _track_internal("ocr_parsed", receipt_id, {
                "job_id": job_id, "status": "done",
                "parse_status": _parse_status,
                "gate_warnings": [str(w)[:200]
                                  for w in (result.get("gate_warnings") or [])][:5],
                "elapsed_ms": _job_elapsed_ms(job_id),
                "attempts": _log_max_attempt(result),
                "gate_rejects": _log_gate_rejects(result),
                "use_grey": int(bool(result.get("use_grey"))),
            })
        except Exception as e:
            # 库被切换时优先按「放弃写入」收口，不要把 db_path_changed 伪装成识别错误
            if _db_path_switched(job_id, "exception"):
                return
            db.update_receipt(receipt_id, status="error")
            with JOBS_LOCK:
                JOBS[job_id].update({"job_status": "error", "error_msg": str(e)})
            _track_internal("ocr_error", receipt_id, {
                "job_id": job_id, "status": "error",
                "elapsed_ms": _job_elapsed_ms(job_id),
                "reason": str(e)[:300],
            })
        finally:
            db.unbind_job_db_path()

    threading.Thread(target=_run, daemon=True).start()
    return job_id, receipt_id


# Gate-2 parsing 超时阈值（秒）：settings 键 parsing_timeout_seconds 可覆盖。这里必须把
# 两件事分开陈述，不要合并成一句「无变化」（P23 与 P29 两波独立复现过这处不准确表述）：
#   ① 阈值缺省值：300，与接线前的硬编码值一致 —— 这一项确实未变；
#   ② 执行时机：接线前 sweep_parsing_timeout **零调用方**（从不执行），接线后由
#      app/main.py:181 的启动自检（_sweep_stale_parsing_startup）在**每次服务启动**时
#      执行 —— 这是有意的行为变更。
# ② 的可观测后果：卡在 parsing 超过该阈值的单据会在服务启动时被置为
# error(parsing_timeout) 并落 audit_logs_json 审计留痕（实测 live 原有 7 张卡 26-33 天的
# 单据已因此被清扫，首次返回 7 个 id、再次返回 [] 即幂等）。
PARSING_TIMEOUT_SECONDS_DEFAULT = 300
PARSING_TIMEOUT_SETTINGS_KEY = "parsing_timeout_seconds"

# 超期 parsing 行被强制置为 error 时写进 audit_logs_json 的 reason；前端与巡检
# 据此把「识别超时」与「识别失败」区分开。
PARSING_TIMEOUT_REASON = "parsing_timeout"


def parsing_timeout_seconds():
    """超期 parsing 阈值（秒）：settings 键 parsing_timeout_seconds，缺省 300。

    why 抽成函数而不是把缺省直接写成形参默认值：写侧 `sweep_parsing_timeout` 与只读
    巡检端点 `GET /api/admin/parsing-stale` 必须读**同一个**阈值 —— 各写一份读取就是
    本仓①类「同一口径多份实现，只改一份」，管理台改完阈值后两侧会静默分叉（一处按新
    值判、另一处仍按 300 判）。settings_service.get_int 本身已对「库不可用 / 值非法」
    回退缺省；这里再钳一次非正值 —— 0/负数会让 cutoff 落到未来，把**正在解析中**的
    单据误判成超期并置 error。
    """
    from app.services import settings_service
    try:
        value = settings_service.get_int(PARSING_TIMEOUT_SETTINGS_KEY,
                                        PARSING_TIMEOUT_SECONDS_DEFAULT)
    except Exception as e:
        logging.getLogger("receipt_utils").warning(
            "[parsing_sweep] 读取 %s 失败，回退缺省 %d: %s",
            PARSING_TIMEOUT_SETTINGS_KEY, PARSING_TIMEOUT_SECONDS_DEFAULT, e)
        return PARSING_TIMEOUT_SECONDS_DEFAULT
    if not value or int(value) <= 0:
        return PARSING_TIMEOUT_SECONDS_DEFAULT
    return int(value)


def stale_parsing_query(session, timeout_seconds=None, tenant_id=None, now=None):
    """构造「超期 parsing 单据」查询（唯一识别口径，只构造不执行）。

    why 必须只有一份：写侧 `sweep_parsing_timeout`（置 error）与只读巡检端点
    `GET /api/admin/parsing-stale`（列清单）必须看到**同一批**行 —— 端点在别处重写
    一遍扫描 SQL 就是本仓①类「同一根因多份实现，只改一份」：两处判定一旦分叉，会出现
    「管理台显示无超期行、而 sweep 实际在把它们置 error」且无人察觉。

    识别口径（三条件同时成立）：status == 'parsing'、created_at 早于 cutoff、
    未软删（deleted_at IS NULL）；cutoff = now - 阈值秒数，阈值经
    `parsing_timeout_seconds()` 读 settings。**缺省不含租户过滤**（sweep 是全租户语义，
    巡检必须与它同范围才能解释「sweep 会动哪些行」）；tenant_id 给值时只做额外收窄。

    `now` 仅供测试注入固定「当前时刻」（边界用例需要确定性的阈值上下分类），生产不传。
    """
    import datetime as _dt
    if timeout_seconds is None:
        timeout_seconds = parsing_timeout_seconds()
    if now is None:
        now = _dt.datetime.now()
    cutoff = (now - _dt.timedelta(seconds=int(timeout_seconds))).isoformat(timespec="seconds")
    q = session.query(db._ReceiptRow).filter(
        db._ReceiptRow.status == "parsing",
        db._ReceiptRow.created_at < cutoff,
        db._ReceiptRow.deleted_at.is_(None),
    )
    if tenant_id:
        q = q.filter(db._ReceiptRow.tenant_id == tenant_id)
    return q.order_by(db._ReceiptRow.id)


def list_stale_parsing_receipts(timeout_seconds=None, tenant_id=None):
    """只读列出当前超期 parsing 单据（dict 列表），复用 `stale_parsing_query` 口径。

    why 存在：`GET /api/admin/parsing-stale` 需要一份「sweep 会动哪些行」的只读视图，
    且必须与 sweep 同口径。这里只 SELECT、不 commit、不改 status —— 端点因此天然无副作用
    （带副作用的「强制清扫」另有显式入口，不由 GET 触发）。

    返回前端巡检清单所需的最小字段集。
    """
    s = db.get_session()
    try:
        rows = stale_parsing_query(s, timeout_seconds=timeout_seconds,
                                   tenant_id=tenant_id).all()
        return [{
            "id": r.id,
            "status": r.status,
            "created_at": r.created_at or "",
            "updated_at": r.updated_at or "",
            "supplier_name": r.supplier_name or "",
            "doc_form": r.doc_form or "",
            "tenant_id": r.tenant_id or "default",
        } for r in rows]
    finally:
        s.close()


def sweep_parsing_timeout(timeout_seconds=None):
    """Gate-2：扫描超时的 parsing 单据，强制置为 error(parsing_timeout)。

    生产触发方：`app/main.py::_sweep_stale_parsing_startup`（启动自检）—— 本函数此前
    **零调用方**（全仓 grep 只有这一处定义），所以这条清扫从未跑过，超期单据状态永久假
    「在途」（live 曾积压 7 张、26–33 天）。判定复用 `stale_parsing_query`（唯一口径），
    与只读端点 `GET /api/admin/parsing-stale` 同源。

    幂等：不触碰非 parsing / 未超期 / 已软删的行。返回被置为 error 的 receipt_id 列表。
    """
    s = db.get_session()
    try:
        rows = stale_parsing_query(s, timeout_seconds=timeout_seconds).all()
        ids = []
        for r in rows:
            r.status = "error"
            # reason 写入 audit_logs_json 供前端标红“识别超时”
            logs = json.loads(r.audit_logs_json or "[]")
            logs.append({"who": "system", "action": "timeout", "field": "status",
                         "old": "parsing", "new": "error",
                         "reason": PARSING_TIMEOUT_REASON, "ts": db.now_iso()})
            r.audit_logs_json = json.dumps(logs, ensure_ascii=False)
            r.updated_at = db.now_iso()
            ids.append(r.id)
        if ids:
            s.commit()
        for rid in ids:
            _track_internal("ocr_error", rid, {
                "status": "timeout", "reason": PARSING_TIMEOUT_REASON,
            })
        return ids
    finally:
        s.close()


def get_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id, {}))
    if not job:
        return None
    if job.get("job_status") == "done":
        row = db.get_receipt_row(job["receipt_id"])
        if row:
            job["image_url"] = public_image_url(job["receipt_id"], row.image_path,
                                                getattr(row, "tenant_id", None))
            if not job.get("result"):
                job["result"] = {"status": "success", "receipt_id": job["receipt_id"],
                                 "data": build_detail(row), "version": row.version}
    return job


# -------------------------------------------------------------
# AI 结果 → 前端数据模型
# -------------------------------------------------------------
def strip_serial_suffix(name: str) -> str:
    """B-P0-1 核心词归一：显式正则剥离 _\\d{10} 后缀，返回纯净品名。"""
    t = str(name or "").strip()
    return _SERIAL_SUFFIX_RE.sub("", t).strip()


def canonical_sku_name(name: str) -> str:
    """FR-7/B-P0-1 统一归一入口：优先走 SmartSplitter v1_2_0 单例，fallback 正则剥离。"""
    if _sanitizer_singleton is not None:
        try:
            return _sanitizer_singleton.sanitize_name(name)
        except Exception:
            pass
    return strip_serial_suffix(name)


def _merge_math_warnings(problems, warnings):
    """保序去重合并两类门禁警告：管线的 math_problems 在前，校准写入的 math_warnings 在后。

    why：花码/单位折算等整单警告由 huama_evaluator 写入 data.math_warnings，而门禁
    （算术/契约/总额）警告写在 result["math_problems"]。落库只取后者会让整单花码警告
    永远进不了 math_warnings_json，本批次新增的门禁横幅对这类单据永不生效。
    去重保留首次出现顺序，避免同一警告（门禁与校准同时命中）在列里重复展示。
    """
    merged = []
    for src in (problems, warnings):
        if not src:
            continue
        if not isinstance(src, (list, tuple)):
            src = [src]
        for w in src:
            if w is None or w in merged:
                continue
            merged.append(w)
    return merged


def _resolve_supplier_anchor(vendor_name, tenant_id):
    """在当前租户内按「精确名 -> canonical_supplier_key 归一」解析供应商档案 id。

    返回三态语义必须区分清楚：
      - 命中的 int id；
      - None（读取成功但**确实没有**匹配档案）；
      - 抛异常（读取本身失败）—— 调用方据此放弃刷新，绝不当成"无档案"而清锚点。

    口径与 db._backfill_receipt_supplier_ids（D17）同款：先按档案名精确匹配，再按
    canonical_supplier_key 归一匹配，且不按 active 过滤（D17 回填同样扫描全量档案）。
    只读：仅 db.list_suppliers，不写任何表。
    """
    from app.services.supplier_normalizer import canonical_supplier_key
    suppliers = db.list_suppliers(include_inactive=True, tenant_id=tenant_id)
    for sup in suppliers:
        if (sup.name or "") == vendor_name:
            return int(sup.id)
    key = canonical_supplier_key(vendor_name)
    if not key:
        return None
    for sup in suppliers:
        if canonical_supplier_key(sup.name or "") == key:
            return int(sup.id)
    return None


def _refresh_receipt_supplier_anchor(receipt_id, vendor_name, tenant_id):
    """回写 supplier_name 后同步刷新 receipts.supplier_id（修复 D17 锚点陈旧缺陷）。

    why：save_parsed_data 用识别出的 vendor 覆盖 receipts.supplier_name，却从不更新
    supplier_id，于是重新识别把供应商名改写后锚点仍指向旧档案 —— supplier_name 与
    supplier_id 互相矛盾（独立复核实测：receipt 45 名=鴻運餐飲 却挂 supplier_id=4）。
    本函数让锚点始终是"当前名字"的解析结果，两者不再分叉。

    口径：`_resolve_supplier_anchor`（精确名 -> canonical 归一，当前租户内）。解析不出
    档案则写 NULL，**绝不凭空建档**（与 db.resolve_supplier_id / D17 同一条纪律）。
    名字为空（未改写的空 vendor）时不刷新，保留原锚点 —— 没有可对齐的名字就无从判断。

    幂等：解析结果与当前 supplier_id 相同则纯读、不落库（连 updated_at 都不触碰）。
    update_receipt 会跳过 None 值（无法把陈旧锚点清成 NULL），故这里用独立会话直接写；
    同时复用 db.job_db_path_mismatch 复刻 Job 跨库写入闸门，避免库在任务执行期间被
    切换时把锚点写进新库。
    """
    name = str(vendor_name or "").strip()
    if not name:
        return
    try:
        resolved = _resolve_supplier_anchor(name, tenant_id)
    except Exception as e:
        logging.getLogger("receipt_utils").warning(
            "[supplier_anchor] 读取供应商档案失败（receipt=%s vendor=%r tenant=%r），"
            "保留原 supplier_id: %s", receipt_id, name, tenant_id, e)
        return
    if db.job_db_path_mismatch() is not None:
        return
    try:
        s = db.get_session()
        try:
            row = s.get(db._ReceiptRow, int(receipt_id))
            if row is None:
                return
            if getattr(row, "supplier_id", None) != resolved:
                row.supplier_id = resolved
                row.updated_at = db.now_iso()
                s.commit()
        finally:
            s.close()
    except Exception as e:
        logging.getLogger("receipt_utils").warning(
            "[supplier_anchor] 刷新 receipts.supplier_id 失败（receipt=%s vendor=%r）: %s",
            receipt_id, name, e)


def save_parsed_data(receipt_id, data, result):
    """把 AI 结构化结果写入收据行 + 明细 + SKU 匹配。返回 detail。"""
    from app.services.contract import sanitize_nan, normalize_evidence

    # P2-2：租户上下文从单据行派生（Job 线程内无 request 可读），
    # SKU 匹配限定本租户，避免跨租户 SKU 误匹配；行缺失时不过滤（向后兼容）
    _ctx_row = db.get_receipt_row(receipt_id)
    _tenant_ctx = None
    if _ctx_row is not None:
        _tenant_ctx = (getattr(_ctx_row, "tenant_id", "") or "").strip() or None
    # P2-T2：供应商上下文同样从单据行派生（别名表按供应商建键）。行上已锚定时直接
    # 用锚点；未锚定（新上传的单据在解析阶段通常还没写入 supplier_id）则按本次识别
    # 出的 vendor 名解析一次 —— 复用 db.resolve_supplier_id 这一唯一解析实现，
    # 解析不出留 None（宁可回落全局匹配，也不新建成一条假供应商档案）。
    _supplier_ctx = getattr(_ctx_row, "supplier_id", None) if _ctx_row is not None else None
    if not _supplier_ctx:
        _vendor_name = getattr(data, "vendor", "") or ""
        if _vendor_name:
            _supplier_ctx = db.resolve_supplier_id(_vendor_name, tenant_id=_tenant_ctx)
    # L0 锚点回退链（只追加、既有两档顺序与实现不动；每档成功即止）：
    # ③ vendor_hint 档 —— result["vendor_hint"] 同时承载用户手填 hint 与首轮预判供应商
    #   （supervisor 首轮注入分支在无显式 hint 时覆写为预判结果），是对锚点的两个上游
    #   信号在 parse 期的唯一可得形态；同名不重复解析（hint == vendor 时 ② 已试过）。
    # ④ 记忆先验档 —— db.resolve_supplier_id_from_memory（唯一实现）：同名字历史记忆行
    #   携带的锚点，唯一候选才用（不猜）。全落空 → 保持 None（现状语义，回落全局匹配）。
    # 注意：这里只影响解析期 _match_sku 的供应商收窄，**不改** receipts.supplier_id
    # 落库值 —— D17 锚点持久化仍只认识别 vendor 名（_refresh_receipt_supplier_anchor）。
    if not _supplier_ctx:
        _hint = str(result.get("vendor_hint") or "").strip()
        if _hint and _hint != (getattr(data, "vendor", "") or ""):
            _supplier_ctx = db.resolve_supplier_id(_hint, tenant_id=_tenant_ctx)
            if _supplier_ctx:
                logging.getLogger("receipt_utils").info(
                    "[supplier_anchor] parse 期回退锚定 receipt=%s 档=vendor_hint "
                    "supplier_id=%s", receipt_id, _supplier_ctx)
    if not _supplier_ctx:
        _supplier_ctx = db.resolve_supplier_id_from_memory(
            getattr(data, "vendor", ""), tenant_id=_tenant_ctx)
        if _supplier_ctx:
            logging.getLogger("receipt_utils").info(
                "[supplier_anchor] parse 期回退锚定 receipt=%s 档=memory_prior "
                "supplier_id=%s", receipt_id, _supplier_ctx)

    # 使用模块顶层单例，避免每次实例化
    _sanitizer = _sanitizer_singleton

    items_raw = []
    for it in data.items:
        raw = it.name or ""
        if _sanitizer is not None:
            try:
                clean_name = _sanitizer.sanitize_name(raw)
            except Exception:
                clean_name = strip_serial_suffix(raw)
        else:
            clean_name = strip_serial_suffix(raw)
        # Gap E1 / T7：字段级证据（可选）——归一后随行落库，缺失/非法为 None 不阻断
        _ev = getattr(it, "evidence", None)
        if _ev is not None and hasattr(_ev, "model_dump"):
            _ev = _ev.model_dump()
        items_raw.append({
            "name": clean_name, "raw_name": it.name,
            "quantity": sanitize_nan(it.qty), "unit": it.unit or "", "raw_unit": it.unit or "",
            "unit_price": sanitize_nan(it.unit_price), "amount": sanitize_nan(it.amount),
            "sku_id": None, "cost_center_id": None,
            # P11：item 级置信度落库（低置信行可按行定位）。契约新增该字段后
            # 从 LLM 输出一路带到这里；LLM 未给出则**保留 None**（不再兜底 0.5）——
            # 「未给出」与「给了 0.5」是两回事，兜底会把无数据伪装成中等置信度，
            # 而 0.5 又高于前端低置信阈值 0.40，等于让「无从判断」的行永远不被提示复核。
            "confidence": sanitize_nan(getattr(it, "confidence", None), default=None),
            "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
            # Gap 7：花码/单位不可折算提示必须落库并传前端（原实现硬编码空串，
            # 导致 huama_evaluator 写入的警示在详情接口消失）。
            "price_diff_percent": 0.0,
            "unit_conversion_warning": str(getattr(it, "unit_conversion_warning", None) or ""),
            "fuzzy_candidates": [], "entity_candidates": [],
            "is_void": int(bool(getattr(it, "is_void", False))),
            "actual_qty": getattr(it, "actual_qty", None),
            "evidence": normalize_evidence(_ev),
        })
        _match_sku(items_raw[-1], tenant_id=_tenant_ctx, supplier_id=_supplier_ctx)

    anomaly_items = [it for it in items_raw if it.get("price_anomaly")]
    if anomaly_items:
        _track_internal("price_anomaly_flagged", receipt_id, {
            "anomaly_count": len(anomaly_items),
            "max_surge_pct": max(abs(float(it.get("price_diff_percent") or 0))
                                 for it in anomaly_items),
        })

    use_grey = int(bool(result.get("use_grey"))) if result.get("use_grey") is not None else 0
    # Gate-3：交叉审核分歧 → review_priority_score（Top10%标重点复核）
    audit_res = result.get("audit_result") or {}
    discrepancies = audit_res.get("discrepancies") if isinstance(audit_res, dict) else None
    if isinstance(discrepancies, list) and discrepancies:
        review_priority_score = min(1.0, 0.5 + 0.15 * len(discrepancies))
    elif audit_res.get("reason") == "AI 识别与原图一致":
        review_priority_score = 0.0
    else:
        review_priority_score = 0.1 if discrepancies == [] else 0.0

    # 红章补充：LLM 未标记但图片含红章 → 补充为 stamp（单一来源：contract.payment_mark_from_image）
    try:
        from app.services.contract import payment_mark_from_image
        row_tmp = db.get_receipt_row(receipt_id)
        img_path = row_tmp.image_path if row_tmp else ""
        payment_mark_val = payment_mark_from_image(img_path, llm_marked=data.payment_marked)
    except Exception:
        payment_mark_val = "已付款" if data.payment_marked else ""

    # RAG 上下文持久化（供 data_only 调试开关按需展示，默认不暴露）
    rag_ctx = result.get("vendor_context") or result.get("rag_context") or ""
    # 归一为结构化文本：保留供应商先验与检索片段
    if isinstance(rag_ctx, dict):
        rag_ctx_str = json.dumps(rag_ctx, ensure_ascii=False)
    else:
        rag_ctx_str = str(rag_ctx or "")
    # P15：按管线返回的最终状态落库，不再无条件写 parsed——门禁带警告的单据
    # （state["status"]="parsed_with_warnings"）必须如实落库，否则"解析成功"的假象仍在。
    _final_status = str(result.get("status") or "").strip() or "parsed"
    if _final_status not in ("parsed", "parsed_with_warnings"):
        _final_status = "parsed"
    db.update_receipt(
        receipt_id,
        status=_final_status,
        supplier_name=data.vendor,
        receipt_date=data.date,
        sheet_name=(data.date or "")[:7],
        total_amount=sanitize_nan(data.total),
        doc_form=data.doc_form.value if hasattr(data.doc_form, "value") else str(data.doc_form),
        payment_mark=payment_mark_val,
        confidence=sanitize_nan(data.confidence),
        raw_llm=result.get("raw", ""),
        audit_json=json.dumps(audit_res, ensure_ascii=False),
        ai_prefill_json=json.dumps(data.model_dump(), ensure_ascii=False),
        math_warnings_json=json.dumps(
            _merge_math_warnings(result.get("math_problems", []),
                                 getattr(data, "math_warnings", [])),
            ensure_ascii=False),
        use_grey=use_grey,
        review_priority_score=review_priority_score,
        rag_context_json=rag_ctx_str,
        currency=getattr(data, "currency", None) or "HKD",
        # Gap 9 / Gap 6：识别直出费用与注记落列（店员可在 save_edited 修正覆写）
        service_fee=sanitize_nan(getattr(data, "service_fee", 0.0)),
        tax_amount=sanitize_nan(getattr(data, "tax_amount", 0.0)),
        adjustment_notes_json=json.dumps(
            [str(n) for n in (getattr(data, "adjustment_notes", None) or [])],
            ensure_ascii=False),
        payment_evidence=str(getattr(data, "payment_evidence", "") or ""),
    )
    # D17 锚点陈旧修复：supplier_name 已按识别结果回写，supplier_id 必须同步刷新，
    # 否则重新识别改写供应商名后锚点仍指向旧档案（名/id 互相矛盾）。
    _refresh_receipt_supplier_anchor(receipt_id, data.vendor, _tenant_ctx)
    db.set_receipt_items(receipt_id, items_raw)
    return build_detail(db.get_receipt_row(receipt_id))


def _normalize_sku_name(name):
    """SKU 匹配用规范化：去空格/去常见规格后缀，保留核心词 + B-P0-1 流水号剥离。

    例："6包裝 維他朱古奶" → "維他朱古奶"
        "500毫升維他蘋果茉莉綠茶飲品" → "維他蘋果茉莉綠茶飲品"
        "有机菜心_1787140420" → "有机菜心" (FR-7)
        "本地新鲜菜心_1787140411" → "本地新鲜菜心"
    """
    text = str(name or "").strip()
    # B-P0-1：优先显式剥离 _\d{10} 后缀（核心词归一），再走通用清洗
    text = _SERIAL_SUFFIX_RE.sub("", text).strip()
    # 兼容：再尝试走 SmartSplitter 单例兜底（若本地正则未命中泛化流水号）
    if _sanitizer_singleton is not None and "_" in text and re.search(r"_[A-Za-z0-9_\-]{4,}$", text):
        try:
            text = _sanitizer_singleton.sanitize_name(text)
        except Exception:
            pass
    # 去掉开头的数量/包装前缀（如 6包裝、500毫升、480毫升、500mL、10x100個）
    text = re.sub(r"^\d+(\.\d+)?(x\d+)*\s*(包裝|包|毫升|mL|ml|升|L|瓶|罐|條|盒|個|合|箱|隻)?\s*", "", text)
    # 去掉结尾规格（括号内容 / 数字x数字 / 体积容量）
    text = re.sub(r"[（(][^）)]*[)）]\s*$", "", text)
    # 阶段 2A 工作项③：显式数量/规格后缀剥离（YAML 规则表，匹配与候选生成同一份）。
    # 只剥锚定结尾且含显式单位/容器词的后缀（斤/司馬斤 族、裸数字、NxM 货号禁入）。
    from app.services.name_suffix_rules import strip_suffix
    text = strip_suffix(text, _suffix_rules())
    text = re.sub(r"[\s·•：:]*$", "", text)
    return text


def _match_sku(item, tenant_id=None, supplier_id=None):
    """按商品名匹配 SKU（供应商别名 → 精确 → 核心词匹配）。

    匹配到则回填 sku_id / last_price。匹配规则收紧：
    - **P2-T2 新增供应商维度优先**：先查「该供应商历史用这个写法指哪个 SKU」
      （`db.find_vendor_sku_alias`，命中即回，并累加 hit_count）
    - 未命中别名再走原全局口径：优先精确匹配；模糊匹配要求核心词长度 ≥2，
      且 SKU 名包含商品核心词（双向包含）
    - 避免"茶"这类单字误配（核心词最短 2 字符）
    P2-2：tenant_id 从所属单据行派生传入，SKU 检索限定本租户；
    缺省 None 时不过滤（既有调用向后兼容）。
    P2-T2：supplier_id 同样从单据行派生（行上没有锚点时由调用方按 vendor 名解析）。
    缺省 None 时不查别名表 —— 别名表按 (租户, 供应商) 建键，没有供应商维度就无从
    判定"同一写法指哪个 SKU"，故回落全局匹配（不猜、也不全量）。
    """
    name = item["name"] or ""
    sku = None
    if supplier_id:
        # 别名通道内部已吞异常并留 warning，返回 None 时按"未命中"继续走全局匹配
        sku = db.find_vendor_sku_alias(
            name, tenant_id=tenant_id, supplier_id=supplier_id,
            canonical_name=_normalize_sku_name(name))
    if sku is None:
        sku = db.find_sku_by_name(name, tenant_id=tenant_id)
    if sku is None:
        core = _normalize_sku_name(name)
        if len(core) >= 2:
            for s in db.list_skus(tenant_id=tenant_id):
                s_core = _normalize_sku_name(s.name)
                # 双向匹配都要求核心词 ≥2（避免"茶"这类单字 SKU 误配到任何含该字的长商品）
                if s_core and len(s_core) >= 2 and (s_core in core or core in s_core):
                    sku = s
                    break
    if sku:
        item["sku_id"] = sku.id
        item["matched"] = 1
        if sku.last_unit_price and item["unit_price"]:
            diff_pct = round((item["unit_price"] - sku.last_unit_price) / sku.last_unit_price * 100, 1)
            item["price_diff_percent"] = diff_pct
            if abs(diff_pct) > 10:
                item["price_anomaly"] = 1
                item["price_anomaly_direction"] = "up" if diff_pct > 0 else "down"


# -------------------------------------------------------------
# 序列化
# -------------------------------------------------------------
def _load_json_list(raw):
    """JSON list 列安全解析（非 list/空/坏 JSON → []）。"""
    try:
        val = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def _load_json_obj(raw):
    """JSON object 列安全解析（非 dict/空/坏 JSON → {}）。"""
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def build_detail(row):
    """收据行 → ReceiptDetail（/api/receipt/{id} 与 upload data 结构）。"""
    if row is None:
        return None
    items = db.get_receipt_items(row.id)
    dept_name = ""
    if row.department_id:
        for d in db.list_departments():
            if d.id == row.department_id:
                dept_name = d.name
                break

    payment_mark_val = row.payment_mark or ""
    if not payment_mark_val:
        try:
            from app.services.contract import payment_mark_from_image
            payment_mark_val = payment_mark_from_image(row.image_path or "", llm_marked=False)
        except Exception:
            pass

    return {
        "receipt_id": row.id,
        "supplier_name": row.supplier_name or "",
        "date": row.receipt_date or "",
        "sheet_name": row.sheet_name or "",
        "total_amount": row.total_amount or 0.0,
        "status": row.status,
        "settlement_type": row.settlement_type or "credit",
        "payment_mark": payment_mark_val,
        "department_id": row.department_id,
        "department_name": dept_name,
        "doc_form": row.doc_form or "",
        "layout_type": row.layout_type or "",
        "version": row.version or 1,
        "math_warnings": json.loads(row.math_warnings_json or "[]"),
        "quality_warnings": json.loads(row.quality_warnings_json or "[]"),
        "review_priority_score": row.review_priority_score or 0.0,
        "items": items,
        "audit_logs": json.loads(row.audit_logs_json or "[]"),
        "ai_prefill": json.loads(row.ai_prefill_json or "{}"),
        "audit_result": _patch_audit_reason(json.loads(row.audit_json or "{}")),
        "use_grey": row.use_grey or 0,
        "confidence": row.confidence or 0.0,
        "currency": getattr(row, "currency", None) or "HKD",
        "rag_context": getattr(row, "rag_context_json", None) or "",
        # Gap 9 / Gap 6：店员可修正字段回读（复核界面与识别 prefill 同名直灌）
        "service_fee": float(getattr(row, "service_fee", 0.0) or 0.0),
        "tax_amount": float(getattr(row, "tax_amount", 0.0) or 0.0),
        "adjustment_notes": _load_json_list(getattr(row, "adjustment_notes_json", None)),
        "payment_evidence": str(getattr(row, "payment_evidence", "") or ""),
        # P2-1：图内隐藏文字检测结果（{flagged, reasons, metrics}）；缺省 {} = 未检测/无结果。
        # 新增字段，既有字段不变；只告警不拦截，前端据此决定是否显示人话横幅。
        "image_guard": _load_json_obj(getattr(row, "image_guard_json", None)),
        # U-2: AI 决策履历（extract 各轮 + audit），单表索引查询，detail 频次可接受
        "ai_decisions": db.list_ai_decisions(row.id),
    }


def _patch_audit_reason(audit_result):
    """阶段 1：如果 audit_result 有 discrepancies 但 reason 为空/缺失，
    根据 discrepancies 中的 issue 聚合生成人类可读 reason。
    无 discrepancies → 补简短说明。
    """
    if not isinstance(audit_result, dict):
        return audit_result
    if audit_result.get("skipped"):
        return audit_result
    reason = audit_result.get("reason")
    discrepancies = audit_result.get("discrepancies") or []
    if not discrepancies:
        if not reason:
            return {**audit_result, "reason": "AI 识别与原图一致"}
        return audit_result
    # 已有 reason 则返回原样
    if reason:
        return audit_result
    # 无 reason 时按 severity 聚合 discrepancies 中的 issue
    parts = []
    for d in discrepancies:
        if not isinstance(d, dict):
            continue
        issue = d.get("issue") or ""
        if not issue:
            continue
        parts.append(issue)
    audit_result = {**audit_result}
    audit_result["reason"] = "；".join(parts) if parts else "交叉审核发现分歧"
    return audit_result


# -------------------------------------------------------------
# 原图 URL：统一签名端点 + HMAC 签名防枚举
# -------------------------------------------------------------
def _sign_tenant(tenant_id):
    """签名与 URL 共用的租户归一：空 / None → default。

    why: build_row 传 row.tenant_id（可能为 None），端点传解析后的 query/header 值
    （非空，默认 default）；两侧必须归一到同一字面量，否则签名必然对不上。
    """
    return str(tenant_id or "").strip() or "default"


def preview_image_signature(receipt_id, stem, tenant_id="default"):
    """原图预览 URL 的 HMAC 签名：绑定 单据 id + 文件名主体 + 租户。

    why: 该端点匿名可取图（`<img src>` 无法携带请求头），而 receipt_id 自增可枚举；
    签名让「URL 由后端签发」成为取图前提，堵住遍历 id 的横向枚举。复用 app.auth
    的 _sign / _TOKEN_SECRET，不新造密钥：轮换 DEMO_TOKEN_SECRET 即让旧签名全部失效。

    强度如实陈述（D21，不得美化）：密钥仍是仓库内置默认值（
    auth.token_secret_is_weak() 为真，/api/health 的 signature_secret_weak=true）时，
    签名**只防误撞、不防构造** —— 任何读过源码的人都能用公开常量自行签发任意
    receipt_id / 文件名主体 / 租户的合法 sig。此形态下签名不构成访问控制，真正的边界
    是端点的租户归属校验与「文件必须落在 UPLOAD_DIR 内」的路径收敛。
    """
    from app.auth import _sign
    payload = "%d|%s|%s" % (int(receipt_id), stem, _sign_tenant(tenant_id))
    return _sign(payload)


def verify_preview_image_signature(receipt_id, stem, tenant_id, sig):
    """常量时间比对预览签名；缺签名 / 篡改 / 参数非法一律 False。"""
    if not sig:
        return False
    try:
        expected = preview_image_signature(receipt_id, stem, tenant_id)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(sig))


def voucher_image_signature(name, tenant_id="default"):
    """付款凭证取图 URL 的 HMAC 签名：绑定 凭证文件名 + 租户。

    why: 与单据原图端点同构 —— 凭证端点也由浏览器原生发起（`<a href>` 带不了请求头），
    而凭证文件名可枚举/可猜；签名让「URL 由后端签发」成为取图前提。复用 app.auth
    的 _sign / _TOKEN_SECRET，不新造密钥：轮换 DEMO_TOKEN_SECRET 即让旧签名全部失效。

    强度如实陈述（D21）：与 preview_image_signature 同一密钥，故在弱密钥形态下同样
    **只防误撞、不防构造**（签名不构成访问控制，见该函数 docstring 与 /api/health 的
    signature_secret_weak）；凭证端点的真实边界是 `_voucher_tenant_owns` 的租户归属
    校验与 VOUCHER_DIR 的路径收敛。
    """
    from app.auth import _sign
    payload = "voucher|%s|%s" % (str(name or ""), _sign_tenant(tenant_id))
    return _sign(payload)


def verify_voucher_image_signature(name, tenant_id, sig):
    """常量时间比对凭证签名；缺签名 / 篡改 / 参数非法一律 False。"""
    if not sig:
        return False
    try:
        expected = voucher_image_signature(name, tenant_id)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(sig))


def _quote_query_value(value):
    """查询参数值编码：只编码值本身，不编码整个 URL。

    why: stem/tenant 当前恒为十六进制与 default，实测编码前后逐字节相同（live 库
    432 条非空 URL 全量比对一致）；但 legacy 数据或手工导入可能带 空格/&/# 等字符，
    不编码会把 URL 从中间截断或让端点解不出原值。safe="" 让 "/" 也参与编码（它是
    值而非路径分隔符）。签名仍基于未编码的原值计算——端点的 query 解析会自动解回
    原值，故签名值不因编码而变，编码仅作用于 URL 字面量。
    """
    return quote(str(value or ""), safe="")


def public_image_url(receipt_id, image_path, tenant_id="default"):
    """收据原图对外 URL：一律指向签名取图端点 GET /api/receipt/{id}/image。

    why: 原图曾经有两条出口 —— Web 格式直链 `/uploads/<basename>` 静态目录，非 Web
    格式（`.heic/.tiff` 经 StaticFiles 返回 text/plain、浏览器不渲染）走按需转码端点。
    `/uploads` 静态挂载本身零鉴权、零租户校验，属未授权文件面，已整体撤销；原图不再有
    「直链文件」形态，Web 与非 Web 统一走同一签名端点。运行时无额外成本：Web 格式在
    端点侧由 web_preview_path 原样返回源文件、不做转码（见 api_receipts._preview_media_type）。
    `?v=<stem>` 兼作版本参数：replace-image 换图后 receipt_id 不变，不带版本参数会命中
    旧图的浏览器/HTTP 缓存；它同时参与签名载荷，端点据此把签名与「当前真实原图文件名
    主体」绑定，换图后旧 URL 立即失效。
    `&sig=<HMAC(id|stem|租户)>` 是该端点的取图凭证（端点为浏览器原生发起、带不了请求头，
    故不能靠 RBAC）；签名随 URL 由后端签发，前端零改动，见 verify_preview_image_signature。
    非默认租户追加 `&tenant_id=`，端点据此完成租户归属校验；default 省略该参数。

    URL 只对查询参数值做 percent 编码（见 _quote_query_value）：签名基于未编码的
    原值，端点由 query 解析自动解回原值，故签名与取图行为不受编码影响。

    无图即空串：image_path 为空、或 receipt_id 无法解析为整数（None/空串/非数字）时
    返回 `""` —— 签不出有效 URL 就不产出任何形态的地址，语义为「无图」，由消费方按
    空值降级处理（前端须有 `if (url)` / `|| fallback` 守卫，见 main.js 归档弹窗）。
    """
    if not image_path:
        # 空路径 = 无图：返回空串，避免调用方拿到指向目录的无效 URL
        return ""
    name = str(image_path).split("/")[-1]
    if not name:
        # 路径只由目录成分构成（如 "uploads/"）→ 同样无图
        return ""
    try:
        rid = int(receipt_id)
    except (TypeError, ValueError):
        # receipt_id 缺失/非法 → 无可用签名主体，按无图处理
        return ""
    stem = image_web.preview_stem(name)
    tenant = _sign_tenant(tenant_id)
    url = "/api/receipt/%d/image?v=%s&sig=%s" % (
        rid, _quote_query_value(stem), preview_image_signature(rid, stem, tenant))
    if tenant != "default":
        url += "&tenant_id=" + _quote_query_value(tenant)
    return url


def build_row(row):
    """收据行 → /api/receipts 列表行（含付款派生字段）。"""
    payment_status = "unknown"
    if row.payment_id and row.paid_at:
        payment_status = "paid"
    elif row.settlement_type == "credit":
        payment_status = "unpaid"
        if row.expected_pay_date:
            from datetime import date
            today = date.today().isoformat()
            if row.expected_pay_date < today:
                payment_status = "overdue"
    elif row.settlement_type == "cash":
        payment_status = "paid_at_delivery"

    # W2 列表性能修复：列表行不再逐条读图补红章，直接返回已落库的 payment_mark。
    # why: 旧实现对本行 payment_mark 为空时调 contract.payment_mark_from_image →
    # detect_red_stamp 解码图片，配合 list_receipts 的同类补充形成每请求上千次读图
    # （详见 api_receipts.list_receipts）。红章补充改由详情视图承担
    # （build_detail 与本文件的 detail 路径）。
    payment_mark_val = row.payment_mark or ""

    import json as _json
    quality_warnings = _json.loads(row.quality_warnings_json or "[]") if getattr(row, "quality_warnings_json", None) else []
    # P14/P15：列表行透出门禁警告，供前端对 parsed_with_warnings 醒目标出
    math_warnings = _json.loads(row.math_warnings_json or "[]") if getattr(row, "math_warnings_json", None) else []
    review_priority = getattr(row, "review_priority_score", 0.0) or 0.0

    return {
        "id": row.id,
        "supplier_name": row.supplier_name or "",
        "supplier_code": row.supplier_code or "",
        "image_url": public_image_url(row.id, row.image_path,
                                      getattr(row, "tenant_id", None)),
        "status": row.status,
        "total_amount": row.total_amount or 0.0,
        "department_name": "",
        "payment_status": payment_status,
        "receipt_date": row.receipt_date or "",
        "upload_date": row.created_at or "",
        "updated_date": row.updated_at or "",
        "settlement_type": row.settlement_type or "",
        "doc_form": row.doc_form or "",
        "paid_method": row.paid_method or "",
        "expected_pay_date": row.expected_pay_date or "",
        "due_soon": False,
        "paid_at": row.paid_at or "",
        "payment_id": row.payment_id,
        "use_grey": row.use_grey or 0,
        "payment_mark": payment_mark_val,
        "quality_warnings": quality_warnings,
        "math_warnings": math_warnings,
        "review_priority_score": review_priority,
        "currency": getattr(row, "currency", None) or "HKD",
    }


# -------------------------------------------------------------
# 埋点：人工复核 diff（AI 原版 vs 用户最终提交）
# -------------------------------------------------------------
_DIFF_FIELDS = ("name", "quantity", "unit", "unit_price", "amount")


def compute_review_diff(ai_items, final_items):
    """行级三类 diff + SKU 更改计数（纯函数，无 DB 依赖）。

    对齐算法：按位置 zip 到 min 长度逐行比关键字段；final 多出计 added，
    ai 多出计 deleted。ai_items 为空（手工单无 AI 原版）→ 全部计 added。
    """
    ai_items = ai_items or []
    final_items = final_items or []
    rows_added = rows_modified = rows_deleted = sku_changed = 0
    field_mod_counts = {f: 0 for f in _DIFF_FIELDS}

    for ai_it, fin_it in zip(ai_items, final_items):
        ai_it = ai_it if isinstance(ai_it, dict) else {}
        fin_it = fin_it if isinstance(fin_it, dict) else {}
        changed = False
        for f in _DIFF_FIELDS:
            av, fv = ai_it.get(f), fin_it.get(f)
            if f in ("quantity", "unit_price", "amount"):
                av = float(av or 0)
                fv = float(fv or 0)
            elif f == "name":
                av = canonical_sku_name(av or "")
                fv = canonical_sku_name(fv or "")
            if av != fv:
                field_mod_counts[f] += 1
                changed = True
        ai_sku = ai_it.get("sku_id")
        if ai_sku != fin_it.get("sku_id"):
            changed = True
        if ai_sku and (ai_sku != fin_it.get("sku_id")
                       or canonical_sku_name(ai_it.get("name") or "")
                       != canonical_sku_name(fin_it.get("name") or "")):
            sku_changed += 1
        if changed:
            rows_modified += 1

    rows_added = len(final_items) - min(len(ai_items), len(final_items))
    rows_deleted = len(ai_items) - min(len(ai_items), len(final_items))
    return {
        "rows_added": rows_added,
        "rows_modified": rows_modified,
        "rows_deleted": rows_deleted,
        "sku_changed": sku_changed,
        "field_mod_counts": field_mod_counts,
        "total_ai": len(ai_items),
        "total_final": len(final_items),
    }
