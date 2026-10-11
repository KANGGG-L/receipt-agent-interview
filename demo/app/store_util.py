# -*- coding: utf-8 -*-
"""自建表存储共用的连接入口。

在 db.atomic() 事务里（例如 approve 一整张单的入账）自建表的读写必须加入同一个事务，
否则会和事务里已持有的写锁互相等待，报「database is locked」。事务外则各自开连接。
建表只在表缺失时才执行，表已存在时不写库。
"""

import threading
from contextlib import contextmanager

from sqlalchemy import text

from app import db


class Gate:
    def __init__(self, ddl, tables):
        self._ddl = list(ddl)
        self._tables = tuple(tables)
        self._ready_engine = None
        self._lock = threading.Lock()

    def __call__(self):
        return self

    def _missing(self, conn):
        have = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        return [t for t in self._tables if t not in have]

    def _ensure(self, ambient):
        if ambient is not None:
            # 事务内建表跟着事务走，事务回滚表也回滚，所以不记「已就绪」
            if self._missing(ambient):
                for ddl in self._ddl:
                    ambient.execute(text(ddl))
            return
        eng = db._engine
        if eng is self._ready_engine:
            return
        with self._lock:
            if eng is self._ready_engine:
                return
            with eng.connect() as conn:
                missing = self._missing(conn)
            if missing:
                with eng.begin() as conn:
                    for ddl in self._ddl:
                        conn.execute(text(ddl))
            self._ready_engine = eng

    @contextmanager
    def _open(self, begin):
        amb = db._ambient_session_ctx.get()
        self._ensure(amb)
        if amb is not None:
            yield amb
            return
        eng = db._engine
        with (eng.begin() if begin else eng.connect()) as conn:
            yield conn

    def connect(self):
        return self._open(False)

    def begin(self):
        return self._open(True)
