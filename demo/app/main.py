# -*- coding: utf-8 -*-
"""FastAPI 入口：LangChain 精简版收据识别系统（完整版前端 + 精简后端）。

用法：
  python -m app.main                # 启动 API（http://127.0.0.1:15010）
  python -m app.main --smoke 图路径 # 冒烟：单图走通 AI 识别链路（不启服务）
"""

import sys
import os
import time
from pathlib import Path
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_DEMO_DIR = str(Path(__file__).resolve().parent.parent)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from app.services.domain_errors import DomainError

from app import db
from app import auth
from app import errors
from app import maintenance
from app import web_assets
from app.auth import EarlyAuthMiddleware
from app.api_admin import router as admin_router
from app.api_auth import router as auth_router
from app.api_dishes import router as dishes_router
from app.api_evalset import router as evalset_router
from app.api_export import router as export_router
from app.api_settings import router as settings_router
from app.api_finance import router as finance_router
from app.api_inventory import router as inventory_router
from app.api_memory import router as memory_router
from app.api_phase2 import router as phase2_router
from app.api_receipts import router as receipts_router
from app.api_receipt_lifecycle import router as lifecycle_router
from app.api_suppliers import router as suppliers_router

app = FastAPI(title="LangChain 收据识别精简版")
errors.install(app)
app.include_router(auth_router)
app.include_router(receipts_router)
app.include_router(lifecycle_router)
app.include_router(inventory_router)
app.include_router(suppliers_router)
app.include_router(finance_router)
app.include_router(dishes_router)
app.include_router(admin_router)
app.include_router(phase2_router)
app.include_router(evalset_router)
app.include_router(memory_router)
app.include_router(export_router)
app.include_router(settings_router)


@app.exception_handler(DomainError)
async def _domain_error_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


# D23：/api/* 早鉴权中间件——在路由分发（参数校验）之前强制「必须先有身份」，
# 消除匿名请求因请求体非法而返回 422（而非 401）所能暴露的端点探测面。白名单端点
# （/api/auth/login、/api/auth/me、/api/health、/api/receipt/{id}/image、
# /api/payment/voucher/{name}）保持匿名可用；端点内的角色阈值不受影响。详见
# app/auth.py 的 EarlyAuthMiddleware。
app.add_middleware(EarlyAuthMiddleware)
# 后加的中间件在外层：安全头在最外层覆盖所有响应（含 401/404），gzip 压缩 JSON 与静态文件。
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(web_assets.SecurityHeadersMiddleware)


@app.on_event("startup")
def _apply_schema_migrations():
    """启动时确保库结构为最新（有序迁移 + schema_version）；失败直接中止启动。"""
    db.ensure_schema_current()
    auth.load_revoked_from_store()


@app.on_event("startup")
def _encrypt_stored_api_keys():
    """存量明文 API key 启动时迁移为密文（见 app/secret_store.py）。"""
    from app import secret_store
    secret_store.migrate_plaintext_keys()


@app.on_event("startup")
def _start_maintenance():
    """定时任务：parsing 超时清扫、Job 淘汰、完整性检查、在线备份。"""
    maintenance.start()


@app.on_event("startup")
def _audit_duplicate_skus():
    """启动只读巡检：发现重复货品只记日志，不改任何业务数据。

    合并必须由老板确认：GET /api/inventory/sku-duplicates 查看，
    POST /api/inventory/sku-duplicates/merge 确认合并（owner 权限）。
    """
    try:
        import logging
        total = 0
        for t in db.list_sku_tenant_ids():
            total += len(db.find_duplicate_sku_groups(tenant_id=t) or [])
        if total:
            logging.getLogger("startup").warning(
                "[deduplicate] 发现 %d 组重复货品，未自动合并；请老板在库存页确认后合并", total)
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(f"[deduplicate] 启动巡检失败: {e}")


@app.on_event("startup")
def _hydrate_engine_key_from_env():
    """启动自愈：识别密钥为空/占位符时，从 .env 自动读取真实密钥装配识别引擎（重启无需手填）。"""
    db.hydrate_engine_config_from_env()


@app.on_event("startup")
def _start_experiment_guardian():
    """T9（Gap C3）：实验守护后台线程。

    间隔经 settings 键 'guard_interval_minutes' 配置（缺省 15 分钟）；
    巡检失败只告警不阻断。线程随进程退出（daemon），重启服务后生效。
    """
    try:
        from app.services import guardian
        guardian.start_background_thread()
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(
            f"[guardian] 实验守护线程启动失败(不阻断): {e}")


@app.on_event("startup")
def _sync_evalset_receipts_startup():
    """启动自愈：自动同步 GT 评测集与单据库关联（补齐 source_receipt_id 与黄金基准集标记）。

    why 只在「活动库 == 部署默认库」时执行：评测集语料（demo/evalsets/，gitignore 的本地
    语料）与默认库是成对的，而 source_receipt_id 是「库内 id」。测试会把 DB_PATH 指到
    临时库（见 demo/conftest.py 的 autouse 隔离），此时自愈会为语料样本在临时库里新建
    单据、并把临时库的 id 回写进真实语料；临时库一丢，语料里的引用就全成了悬空脏数据。
    绑定到非默认库时跳过自愈，避免污染共享语料。
    """
    try:
        import logging
        # 非默认库（测试临时库等）不回写共享语料，理由见 docstring
        if os.path.abspath(str(db.DB_PATH)) != os.path.abspath(str(db._default_db_path)):
            logging.getLogger("startup").info(
                "[evalset_linkage] 跳过启动自愈：活动库非部署默认库（DB_PATH=%s）", db.DB_PATH)
            return
        from app.services.evalset_linkage import sync_evalset_receipts_linkage
        stats = sync_evalset_receipts_linkage()
        if stats and stats.get("linked"):
            logging.getLogger("startup").info(f"[evalset_linkage] startup sync completed: {stats}")
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(f"[evalset_linkage] startup sync failed (non-blocking): {e}")


@app.on_event("startup")
def _check_memory_consistency_startup():
    """启动自检：跑一次记忆表 ↔ 向量库一致性巡检，不一致时告警（只读，不阻断）。

    why：巡检工具 app/services/memory_consistency.py 早已实现（B0 审核 mustFix），
    但**零生产触发方** —— 它能检出 gap 05 §H2 那类「DB 有行无向量 / 向量有 mid 无行」
    的静默脱钩，却没人跑它，结果永远不会被任何人看到（本仓高发的「定义了没接线」）。
    启动是它的生产触发方：每次服务拉起都自动核一遍，不一致即在启动日志里告警。

    容错（不得阻断启动）：记忆库 / 向量库不存在是**正常状态**（全新环境、刚清退过
    向量库），巡检函数对二者缺席都返回 0 行而不是抛错；此外这里再兜一层 try/except，
    任何异常只 warning，绝不冒泡 —— 巡检是观测手段，不该把服务启不起来。
    只读：巡检以 sqlite3 mode=ro 打开两侧，不写记忆库、不写向量库、不创建向量目录。
    """
    try:
        import logging
        from app.services import memory_consistency as mc
        report = mc.check_memory_consistency(db_path=db.DB_PATH)
        log = logging.getLogger("startup")
        if report["consistent"]:
            log.info("[memory_consistency] 记忆表与向量库一致（active 行=%d，向量=%d）",
                     report["db_rows_active"], report["vectors"])
        else:
            log.warning(
                "[memory_consistency] 记忆表与向量库不一致：孤儿向量=%d、缺向量行=%d、"
                "交集=%d（记忆库=%s，向量库=%s）；只读巡检，不自动修复，"
                "可查 GET /api/admin/memory-consistency。",
                len(report["orphan_mids"]), len(report["missing_vector_mids"]),
                len(report["intersection"]), report["db_path"], report["rag_dir"])
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(
            f"[memory_consistency] 启动巡检失败(不阻断): {e}")


@app.on_event("startup")
def _sweep_stale_parsing_startup():
    """启动自愈：把**超期**卡在 parsing 的单据置为 error(parsing_timeout)。

    why：`receipt_utils.sweep_parsing_timeout` 早已实现（Gate-2），但**零生产触发方**
    —— 全仓 grep 只有它自己的定义，所以这条清扫从未跑过，超期单据状态永久假「在途」
    （live 曾积压 7 张、updated_at 距今 26–33 天，列表与看板把它们当在跑的任务）。
    本启动自检与只读端点 `GET /api/admin/parsing-stale` 是它的两个消费方，二者都复用
    同一识别口径 `receipt_utils.stale_parsing_query`（不允许任一侧重写扫描 SQL）。

    边界（不得误伤在跑的解析）：判定要求 created_at 早于「now - 阈值」，阈值由
    settings 键 parsing_timeout_seconds 配置（缺省 300 秒）。刚上传/刚开始解析的单据
    created_at 晚于 cutoff，不会被扫到。

    容错（不得阻断启动）：DB 不可用、表未迁移等任何异常只 warning，绝不冒泡 ——
    清扫是自愈手段，不该把服务启不起来。
    """
    try:
        import logging
        from app.services import receipt_utils as ru
        swept = ru.sweep_parsing_timeout()
        if swept:
            logging.getLogger("startup").info(
                "[parsing_sweep] 超期 parsing 单据置为 error(parsing_timeout): %s", swept)
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(
            f"[parsing_sweep] 启动清扫失败(不阻断): {e}")


@app.on_event("startup")
def _sweep_orphan_preview_caches():
    """启动自愈：清理 uploads 下无主的 `<stem>_web.jpg` 预览缓存（幂等，源原图不删）。

    why: 换图（replace-image）的定向清理只覆盖「请求正常走完」的路径；换图后进程崩溃、
    外部直接改库/删文件等情况仍会留下无主缓存，长期在 uploads 累积。这里兜底扫描：
    源文件已不存在、或该源文件已不被任何单据（含软删）引用 → 删缓存。
    只删 `*_web.jpg` 缓存副本，任何源原图（.heic/.jpg/.png 等）一律不碰；引用集合与
    uploads 目录无交集时（库/目录不匹配、测试临时库等）退化为仅按源文件存在性清理，
    绝不误删可能仍有效的缓存。
    """
    try:
        from app.services import image_web
        referenced = image_web.referenced_preview_stems(db.list_all_image_paths())
        removed = image_web.sweep_orphan_previews(UPLOAD_DIR, referenced)
        if removed:
            import logging
            logging.getLogger("startup").info(
                f"[preview_cache] 清理无主预览缓存 {len(removed)} 个")
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(
            f"[preview_cache] 启动清理失败(不阻断): {e}")


@app.on_event("startup")
def _enforce_ai_startup_checks():
    """AI 管线启动自检：生产环境检测到测试专用变量 MOCK_QWEN_FLASH 即拒绝启动。"""
    from app.services.startup_guard import enforce_ai_startup_checks
    enforce_ai_startup_checks()


@app.on_event("startup")
def _warn_weak_token_secret():
    """启动期密钥强度处置：鉴权开启则强制，否者仅告警。

    AUTH_ENABLED=1（生产鉴权）时，Bearer 令牌是唯一身份凭据来源，弱密钥等同无鉴权，
    故调用 auth.enforce_production_secret() 拒绝启动（RuntimeError 直传，不吞）。

    AUTH_ENABLED=0（本地演示免密）时，密钥只护「匿名预览 URL 的 HMAC 签名」，拒绝启动会把
    「已上线但漏配环境变量」直接变成服务全不可用，影响面大于该签名本身的强度问题，故维持
    既有语义：仅告警不拒绝启动。DEV_MODE（非 production）下连告警都保持既有行为（直接返回）。
    """
    from app import auth
    # 生产强制项：AUTH_ENABLED=1 且仍是内置默认密钥或弱口令 → 抛错拒绝启动（演示态不触发）。
    auth.enforce_production_checklist()
    if DEV_MODE:
        return
    try:
        if auth.token_secret_is_weak():
            import logging
            logging.getLogger("startup").warning(
                "[auth] 生产环境未设置 DEMO_TOKEN_SECRET（或仍是默认值）："
                "单据原图预览 URL 的 HMAC 签名将使用仓库内置默认密钥，"
                "防 id 枚举强度等同混淆。请在部署环境显式设置一个随机长字符串。"
                "注意：密钥变更会使既有已签发的预览 URL 立即失效（重新拉取列表即可）。")
    except Exception as e:
        import logging
        logging.getLogger("startup").warning(f"[auth] 密钥强度检查失败(不阻断): {e}")


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# 开发期静态资源缓存控制：默认开发期（ENV/APP_ENV 非 production）给静态文件与首页
# 加 no-store，避免前端改动后浏览器仍跑旧 JS/HTML（“改了没效果”）。生产环境不设该头。
# 环境判定集中实现于 app.auth.env_name / is_production_env，两处同源，避免口径漂移。
DEV_MODE = not auth.is_production_env()


@app.get("/")
def index():
    return web_assets.html_response(TEMPLATES_DIR / "index.html", STATIC_DIR, DEV_MODE)


@app.get("/evalset")
def evalset_review_page():
    """GT 人工抽检队列页（T4 Gap A2，反馈③收敛后）。

    只保留导航/进度/过滤/批量原样确认/回流候选，无任何可编辑控件；
    单张 GT 校正唯一编辑表面是 /evalset/workbench/<sid>（复用店员复核界面，
    含付款标记控件，控制变量）。
    """
    return web_assets.html_response(TEMPLATES_DIR / "evalset.html", STATIC_DIR, DEV_MODE)


@app.get("/evalset/workbench/{sample_id}")
def evalset_workbench_page(sample_id: str):
    """评测 GT 核对工作台（合并反馈①②）：与店员日常复核完全同源的 Side-by-Side 界面。

    直接复用 index.html 同一模板（同一套复核 DOM / main.js 渲染与提交函数），
    仅在服务端注入两个标志位：
      - window.__EVAL_WORKBENCH__.sampleId：待核对样本 ID
      - /static/js/eval_workbench.js 适配器（数据源换成评测集样本 + AI 候选 GT 预填，
        提交改走 /api/evalset/sample/<sid>/confirm）
    不复制、不改写任何复核表单 DOM（禁止平行表单）。
    """
    import json as _json

    def _inject(html):
        if "</body>" not in html:
            return html
        import html as _html
        payload = _json.dumps({"sampleId": sample_id, "autoNext": True}, ensure_ascii=False)
        inject = (
            '<meta name="eval-workbench" content="%s">\n'
            '<script src="/static/js/eval_workbench.js"></script>\n</body>'
            % _html.escape(payload, quote=True)
        )
        return html.replace("</body>", inject, 1)

    return web_assets.html_response(TEMPLATES_DIR / "index.html", STATIC_DIR, DEV_MODE,
                                    transform=_inject)


app.mount("/static", web_assets.AssetStaticFiles(directory=str(STATIC_DIR), dev_mode=DEV_MODE), name="static")
# 刻意不挂载 /uploads：静态直挂零鉴权、零租户校验，任何知道文件名的人都能读取全部
# 原图与付款凭证，属未授权文件面。原图与凭证分别改由签名端点
# GET /api/receipt/{id}/image 与 GET /api/payment/voucher/{name} 提供，
# 两条端点都做 HMAC 签名 + 租户归属 + basename/realpath 收敛。


@app.get("/api/health")
def health(request: Request):
    """健康状态。匿名只返回 ok/degraded；组件细节（库、备份、引擎、鉴权模式等）仅 admin 可见。

    不再每次请求做全库 integrity_check：完整性检查与备份由后台定时任务执行，
    这里只读它们缓存的结果；库本身用一条 SELECT 1 探活。
    """
    import os
    from datetime import datetime, timezone
    from sqlalchemy import text

    def _db_health():
        try:
            s = db.get_session()
            try:
                s.execute(text("SELECT 1")).scalar()
            finally:
                s.close()
        except Exception as e:
            return False, str(e)
        cached = maintenance.snapshot()["integrity"]
        if cached["ok"] is False:
            return False, cached["error"]
        return True, None

    def _wal_health():
        try:
            size_bytes = os.path.getsize(f"{db.DB_PATH}-wal")
        except OSError:
            size_bytes = 0
        if size_bytes > 64 * 1024 * 1024:
            return size_bytes, False, f"WAL 体积 {size_bytes} 字节 > 64MB（checkpoint 异常）"
        return size_bytes, True, None

    def _engine_health():
        from app import llm
        cfg = db.get_engine_config()
        base = getattr(cfg, "openai_rec_base_url", "") or ""
        key = getattr(cfg, "openai_rec_api_key", "") or ""
        valid_qwen = False
        try:
            valid_qwen = llm._is_valid_dashscope_config(base, key)
        except Exception:
            valid_qwen = False
        return {
            "openai": bool(base and key),
            "openai_valid_qwen": bool(valid_qwen),
        }

    def _backup_health():
        snap = maintenance.snapshot()
        b = snap["backup"]
        if b["ok"] is False:
            return False, b["error"], b
        if b["ok"] is None and snap["scheduler_running"]:
            return True, None, b
        if b["ok"] and b["last_at"]:
            try:
                age = (datetime.now() - datetime.fromisoformat(b["last_at"])).total_seconds()
            except ValueError:
                age = 0
            if age > 2 * maintenance.BACKUP_INTERVAL + 600:
                return False, "最近一次备份已超过两个备份周期，定时备份可能没有在跑", b
        return True, None, b

    db_ok, db_err = _db_health()
    wal_size, wal_ok, wal_err = _wal_health()
    engines = _engine_health()
    backup_ok, backup_err, backup_info = _backup_health()

    problems = []
    if not db_ok:
        problems.append(f"db: {db_err}")
    if not wal_ok:
        problems.append(f"wal: {wal_err}")
    if not any(v for v in engines.values() if isinstance(v, bool)):
        problems.append("engine: 无可用识别引擎")
    if not backup_ok:
        problems.append(f"backup: {backup_err}")
    status = "degraded" if problems else "ok"

    account = auth.resolve_account(request)
    if account.get("role") != "admin":
        return {"status": status}

    from app import auth as _auth
    return {
        "status": status,
        "problems": problems,
        "db": {"ok": db_ok, "error": db_err,
               "integrity_checked_at": maintenance.snapshot()["integrity"]["checked_at"]},
        "wal": {"size_bytes": wal_size, "ok": wal_ok, "error": wal_err},
        "engine": engines,
        "backup": {"dir": str(maintenance.backup_dir()), "dir_writable": backup_ok,
                   "last_at": backup_info["last_at"], "last_file": backup_info["last_file"],
                   "count": backup_info["count"], "keep": maintenance.BACKUP_KEEP,
                   "error": backup_err},
        "time": datetime.now(timezone.utc).isoformat(),
        "auth_enabled": _auth.auth_enabled(),
        "demo_mode": not _auth.auth_enabled(),
        # true = 仍是仓库内置默认密钥：预览/凭证的 HMAC 签名只防误撞、不防构造，不构成访问控制；
        # 请设置 DEMO_TOKEN_SECRET。
        "signature_secret_weak": _auth.token_secret_is_weak(),
    }


@app.post(web_assets.CSP_REPORT_PATH, include_in_schema=False)
async def csp_report(request: Request):
    """浏览器上报的内容安全策略违例（Report-Only 阶段只统计，不拦截）。匿名可达，体积受限。"""
    import json as _json
    raw = await request.body()
    if len(raw) <= 8192:
        try:
            web_assets.record_csp_report(_json.loads(raw.decode("utf-8", "ignore") or "{}"))
        except ValueError:
            pass
    from fastapi import Response
    return Response(status_code=204)


@app.get("/api/admin/csp-violations")
def csp_violations(request: Request):
    """admin：当前进程收到的 CSP 违例统计。"""
    auth.require_admin(request)
    return {"status": "success", "data": web_assets.csp_report_summary()}


def _smoke(image_path: str):
    """单图冒烟：走 AI 识别全链路并打印结构化结果。"""
    from app.chains import supervisor

    cfg = db.get_engine_config()
    print(f"\n=== 冒烟: {image_path} ===")
    print(f"引擎配置: {cfg.model_dump()}")
    t0 = time.time()
    result = supervisor.run_pipeline(image_path, config=cfg)
    print(f"耗时: {round(time.time()-t0, 1)}s | 决策履历:")
    for entry in result.get("log", []):
        print(f"  [{entry['ts']}] attempt={entry['attempt']} {entry['action']}: {entry['reason']}")
    data = result.get("data")
    if data is None:
        print(f"[FAIL]  识别失败: {result.get('contract_error') or result.get('last_error')}")
        return 1
    print(f"[PASS]  {data.vendor} | {data.date} | {data.doc_form.value} | 总额 {data.total}")
    for it in data.items:
        print(f"  - {it.name} {it.qty}{it.unit} @{it.unit_price} = {it.amount}")
    audit = result.get("audit_result", {})
    if audit.get("skipped"):
        print(f"  审核: 跳过（{audit.get('reason')}）")
    else:
        print(f"  审核: 一致={audit.get('overall_consistent')} 分歧={len(audit.get('discrepancies', []))} trust={audit.get('trust')}")
    return 0


def resolve_bind_host(environ=None) -> str:
    """服务绑定地址：缺省只绑本机回环。

    AUTH_ENABLED=0（免密演示）时任何请求带角色头就是超管，绝不允许监听非回环地址；
    要对局域网/公网开放必须先开启鉴权（AUTH_ENABLED=1）并显式设置 APP_HOST。
    """
    import ipaddress
    env = os.environ if environ is None else environ
    host = (env.get("APP_HOST") or "127.0.0.1").strip()
    if host in ("localhost", ""):
        return "127.0.0.1"
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    auth_on = str(env.get("AUTH_ENABLED", "0")).strip().lower() in ("1", "true", "yes")
    if not is_loopback and not auth_on:
        raise SystemExit(
            f"拒绝启动：当前是免密演示模式（AUTH_ENABLED=0），不能监听非本机地址 {host}。"
            "请先设置 AUTH_ENABLED=1 并配置 DEMO_TOKEN_SECRET，或把 APP_HOST 改回 127.0.0.1。")
    return host


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--smoke":
        sys.exit(_smoke(sys.argv[2]))
    import uvicorn
    host = resolve_bind_host()
    reload_on = os.environ.get("APP_RELOAD", "0").strip().lower() in ("1", "true", "yes")
    uvicorn.run("app.main:app", host=host, port=int(os.environ.get("APP_PORT", "15010")),
                reload=reload_on)


if __name__ == "__main__":
    main()
