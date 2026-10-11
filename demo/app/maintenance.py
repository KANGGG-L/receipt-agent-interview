# -*- coding: utf-8 -*-
"""后台定时维护：parsing 超时清扫、Job 过期淘汰、完整性检查（缓存）、在线备份。

一个守护线程按各自间隔轮流执行，不占用请求线程。健康检查只读本模块缓存的结果，
不再每次请求都做全库扫描。
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from app import db

logger = logging.getLogger("app.maintenance")

BASE_DIR = Path(__file__).resolve().parent.parent

SWEEP_INTERVAL = int(os.environ.get("PARSING_SWEEP_INTERVAL_SECONDS", "60") or 60)
JOB_PURGE_INTERVAL = 60
INTEGRITY_INTERVAL = int(float(os.environ.get("INTEGRITY_CHECK_INTERVAL_HOURS", "6") or 6) * 3600)
BACKUP_INTERVAL = int(float(os.environ.get("BACKUP_INTERVAL_HOURS", "6") or 6) * 3600)
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7") or 7)

_state = {
    "integrity": {"ok": None, "checked_at": None, "error": None},
    "backup": {"ok": None, "last_at": None, "last_file": None, "error": None, "count": 0},
    "scheduler_running": False,
}
_state_lock = threading.Lock()


def backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR") or (BASE_DIR / "backups"))


def snapshot() -> dict:
    with _state_lock:
        return {
            "integrity": dict(_state["integrity"]),
            "backup": dict(_state["backup"]),
            "scheduler_running": _state["scheduler_running"],
        }


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def run_integrity_check() -> dict:
    """全库 integrity_check，结果写入缓存。"""
    try:
        s = db.get_session()
        try:
            from sqlalchemy import text
            result = s.execute(text("PRAGMA integrity_check")).scalar()
        finally:
            s.close()
        ok = result == "ok"
        rec = {"ok": ok, "checked_at": _now_iso(),
               "error": None if ok else f"完整性检查结果异常: {result}"}
    except Exception as e:  # noqa: BLE001
        rec = {"ok": False, "checked_at": _now_iso(), "error": str(e)}
    with _state_lock:
        _state["integrity"] = rec
    return rec


def run_backup(dest_dir=None, keep=None) -> dict:
    """用 SQLite 在线备份接口（不锁写）生成一份快照，并只保留最近 keep 份。"""
    dest = Path(dest_dir) if dest_dir else backup_dir()
    keep = BACKUP_KEEP if keep is None else int(keep)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = dest / f"receipt_{stamp}.db"
        src = sqlite3.connect(str(db.DB_PATH), timeout=30)
        try:
            out = sqlite3.connect(str(target))
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        files = sorted(dest.glob("receipt_*.db"))
        for old in files[:-keep] if keep > 0 else []:
            try:
                old.unlink()
            except OSError:
                logger.warning("[backup] 删除旧备份失败: %s", old)
        rec = {"ok": True, "last_at": _now_iso(), "last_file": target.name,
               "error": None, "count": len(list(dest.glob("receipt_*.db")))}
    except Exception as e:  # noqa: BLE001
        logger.warning("[backup] 备份失败: %s", e)
        rec = {"ok": False, "last_at": _now_iso(), "last_file": None,
               "error": f"备份失败: {e}", "count": 0}
    with _state_lock:
        _state["backup"] = rec
    return rec


def sweep_parsing():
    from app.services import receipt_utils as ru
    swept = ru.sweep_parsing_timeout()
    if swept:
        logger.info("[parsing_sweep] 超期 parsing 单据置为 error(parsing_timeout): %s", swept)
    return swept


def purge_jobs():
    from app.services import receipt_utils as ru
    return ru.purge_expired_jobs()


def _tasks():
    return [
        ("parsing_sweep", SWEEP_INTERVAL, sweep_parsing),
        ("job_purge", JOB_PURGE_INTERVAL, purge_jobs),
        ("integrity_check", INTEGRITY_INTERVAL, run_integrity_check),
        ("backup", BACKUP_INTERVAL, run_backup),
    ]


_thread = None
_stop = threading.Event()


def _loop():
    next_run = {name: 0.0 for name, _, _ in _tasks()}
    while not _stop.is_set():
        now = time.time()
        for name, interval, fn in _tasks():
            if now >= next_run[name]:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 一个任务失败不影响其它任务
                    logger.warning("[maintenance] 任务 %s 失败: %s", name, e)
                next_run[name] = time.time() + max(5, interval)
        _stop.wait(5)


def start() -> bool:
    """启动后台线程（幂等）。测试进程或 DISABLE_BACKGROUND_JOBS=1 时不启动。"""
    global _thread
    if os.environ.get("DISABLE_BACKGROUND_JOBS") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="maintenance", daemon=True)
    _thread.start()
    with _state_lock:
        _state["scheduler_running"] = True
    return True


def stop():
    _stop.set()
    with _state_lock:
        _state["scheduler_running"] = False
