# -*- coding: utf-8 -*-
"""已吊销登录令牌的落库存取（登出后重启服务，旧令牌依然失效）。"""

import time

from sqlalchemy import text

from app import db


def _ensure(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS revoked_tokens ("
        " jti VARCHAR(64) PRIMARY KEY, exp INTEGER NOT NULL)"))


def persist_revoked(jti: str, exp: int) -> None:
    with db._engine.begin() as conn:
        _ensure(conn)
        conn.execute(text("INSERT OR REPLACE INTO revoked_tokens (jti, exp) VALUES (:j, :e)"),
                     {"j": jti, "e": int(exp)})
        conn.execute(text("DELETE FROM revoked_tokens WHERE exp < :now"),
                     {"now": int(time.time())})


def load_revoked() -> dict:
    with db._engine.begin() as conn:
        _ensure(conn)
        rows = conn.execute(text(
            "SELECT jti, exp FROM revoked_tokens WHERE exp >= :now"),
            {"now": int(time.time())}).fetchall()
    return {r[0]: int(r[1]) for r in rows}
