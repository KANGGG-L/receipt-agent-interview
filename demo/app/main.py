# -*- coding: utf-8 -*-
"""FastAPI 入口：LangChain 精简版收据识别系统（完整版前端 + 精简后端）。

用法：
  python -m app.main                # 启动 API（http://127.0.0.1:15010）
  python -m app.main --smoke 图路径 # 冒烟：单图走通 AI 识别链路（不启服务）
"""

import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api_admin import router as admin_router
from app.api_auth import router as auth_router
from app.api_finance import router as finance_router
from app.api_inventory import router as inventory_router
from app.api_phase2 import router as phase2_router
from app.api_receipts import router as receipts_router
from app.api_suppliers import router as suppliers_router

app = FastAPI(title="LangChain 收据识别精简版")
app.include_router(auth_router)
app.include_router(receipts_router)
app.include_router(inventory_router)
app.include_router(suppliers_router)
app.include_router(finance_router)
app.include_router(admin_router)
app.include_router(phase2_router)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.get("/")
def index():
    return FileResponse(TEMPLATES_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/api/health")
def health():
    """结构化健康状态：组件异常汇总 → status=degraded（200），绝不 500（对齐完整版）。"""
    import os
    import shutil
    from datetime import datetime, timezone
    from sqlalchemy import text

    def _db_health():
        try:
            s = db.get_session()
            try:
                result = s.execute(text("PRAGMA integrity_check")).scalar()
            finally:
                s.close()
            if result == "ok":
                return True, None
            return False, f"integrity_check 结果异常: {result}"
        except Exception as e:
            return False, str(e)

    def _wal_health():
        try:
            size_bytes = os.path.getsize(f"{db.DB_PATH}-wal")
        except OSError:
            size_bytes = 0
        if size_bytes > 64 * 1024 * 1024:
            return size_bytes, False, f"WAL 体积 {size_bytes} 字节 > 64MB（checkpoint 异常）"
        return size_bytes, True, None

    def _cli_available(path):
        if not path:
            return False
        if os.path.exists(path):
            return True
        return shutil.which(path) is not None

    def _engine_health():
        from app import llm
        cfg = db.get_engine_config()
        return {
            "opencode": _cli_available(llm._get_opencode_bin()),
            "codebuddy": _cli_available(llm._get_codebuddy_bin()),
            "openai": bool(getattr(cfg, "openai_rec_base_url", "")
                           and getattr(cfg, "openai_rec_api_key", "")),
        }

    def _backup_health():
        backup_dir = os.environ.get("BACKUP_DIR") or os.path.join(str(BASE_DIR), "backups")
        try:
            os.makedirs(backup_dir, exist_ok=True)
            probe = os.path.join(backup_dir, f".health_write_probe_{os.getpid()}")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return True, None
        except Exception as e:
            return False, f"备份目录不可写（BACKUP_DIR={backup_dir}）: {e}"

    db_ok, db_err = _db_health()
    wal_size, wal_ok, wal_err = _wal_health()
    engines = _engine_health()
    backup_ok, backup_err = _backup_health()

    problems = []
    if not db_ok:
        problems.append(f"db: {db_err}")
    if not wal_ok:
        problems.append(f"wal: {wal_err}")
    if not any(engines.values()):
        problems.append("engine: 无可用识别引擎")
    if not backup_ok:
        problems.append(f"backup: {backup_err}")

    return {
        "status": "degraded" if problems else "ok",
        "db": {"ok": db_ok, "error": db_err},
        "wal": {"size_bytes": wal_size, "ok": wal_ok, "error": wal_err},
        "engine": engines,
        "backup": {"dir_writable": backup_ok, "error": backup_err},
        "time": datetime.now(timezone.utc).isoformat(),
    }


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
        print(f"✗ 识别失败: {result.get('contract_error') or result.get('last_error')}")
        return 1
    print(f"✓ {data.vendor} | {data.date} | {data.doc_form.value} | 总额 {data.total}")
    for it in data.items:
        print(f"  - {it.name} {it.qty}{it.unit} @{it.unit_price} = {it.amount}")
    audit = result.get("audit_result", {})
    if audit.get("skipped"):
        print(f"  审核: 跳过（{audit.get('reason')}）")
    else:
        print(f"  审核: 一致={audit.get('overall_consistent')} 分歧={len(audit.get('discrepancies', []))} trust={audit.get('trust')}")
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--smoke":
        sys.exit(_smoke(sys.argv[2]))
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=15010, reload=True)


if __name__ == "__main__":
    main()
