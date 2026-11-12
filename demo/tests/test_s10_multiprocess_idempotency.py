# -*- coding: utf-8 -*-
"""S10 多进程并发：同 key checkout 只产生 1 个订单/1 个 attempt，不抛裸 500。

真实两个子进程同时调用服务层（不是线程），验证 SQLite 单库下的操作唯一性。
零外呼；金额为测试合成值。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_s10_mp_")
_DB = os.path.join(_TMP, "mp.db")
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))

os.environ["DB_PATH"] = _DB
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag")
os.environ["AUTH_ENABLED"] = "0"
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
           "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_MODEL"):
    os.environ.pop(_k, None)
for _p in (_DEMO_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402

db._make_engine()
db.ensure_schema_current()

_POLICY = {
    "policy_ref": {"version": "2026.10-mp", "approved_by": "owner@test",
                   "effective_at": "2026-10-26"},
    "trial": {"mechanism": "targeted_coupon", "eligibility": "new_merchant",
              "start_rule": "coupon", "bind_card_required": False},
    "plan": {"tiers": [{"ref": "PLACEHOLDER", "amount_minor": 9900,
                        "currency": "HKD", "period_days": 30}]},
    "channel": {"provider": "manual"},
    "renewal": {"auto_renew": False},
    "cancellation": {"effective_at": "immediate"},
    "refund": {"policy": "full_within_7d"},
}

_CHILD = r"""
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "demo"))
sys.path.insert(0, os.getcwd())
from app import db
db._make_engine()
from app.billing import lifecycle
try:
    r = lifecycle.create_checkout("mp", "PLACEHOLDER", "same-key")
    print(json.dumps({"ok": True, "order": r.get("order_no"),
                      "status": r.get("status")}))
except Exception as exc:
    print(json.dumps({"ok": False, "err": type(exc).__name__ + ":" + str(exc)[:160]}))
"""


def test_multiprocess_same_key_single_order():
    env = dict(os.environ)
    env.update({
        "DB_PATH": _DB,
        "RAG_DIR": os.path.join(_TMP, "rag"),
        "BILLING_POLICY_JSON": json.dumps(_POLICY),
        "BILLING_ENVIRONMENT": "sandbox",
        "BILLING_WEBHOOK_SECRET": "mp-secret",
        "AUTH_ENABLED": "0",
        "PYTHONPATH": os.pathsep.join([_DEMO_DIR, _ROOT]),
    })
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD], cwd=_ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
             for _ in range(2)]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        outs.append((p.returncode, out.strip(), err.strip()))

    parsed = []
    for rc, out, err in outs:
        line = [x for x in out.splitlines() if x.startswith("{")]
        assert line, "child produced no JSON: rc=%s out=%r err=%r" % (rc, out, err)
        parsed.append(json.loads(line[-1]))
    assert all(x["ok"] for x in parsed), parsed
    assert len({x["order"] for x in parsed}) == 1, parsed

    con = sqlite3.connect(_DB)
    try:
        assert con.execute("SELECT COUNT(*) FROM billing_orders").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM payment_attempts").fetchone()[0] == 1
    finally:
        con.close()
