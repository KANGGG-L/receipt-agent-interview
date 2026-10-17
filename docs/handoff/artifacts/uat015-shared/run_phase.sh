#!/usr/bin/env bash
# usage: run_phase.sh <phase dir name> <port>
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo refuse 15010; exit 2; }
PY=$A/eng05-uat/environment/venv/bin/python
mkdir -p "$ART"/{environment,logs,evidence,db,ui}
E=$ART/environment
mkdir -p "$E/uploads" "$E/artifacts_isolated" "$E/rag_chroma"
DB=$E/uat015.db
rm -f "$DB" "$DB-wal" "$DB-shm"

# pre-state: 15010 must stay free
{
  echo "phase=$1 port=$PORT ts=$(date '+%Y-%m-%d %H:%M:%S %z')"
  ss -tlnp 2>/dev/null | grep 15010 || echo "15010: FREE_RESERVED"
} | tee "$E/pre-state.txt"

cd "$ROOT/demo"
AUTH_ENABLED=0 APP_PORT=$PORT DB_PATH=$DB \
  RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
  UPLOAD_DIR=$E/uploads \
  nohup $PY -m uvicorn app.main:app --host 127.0.0.1 --port $PORT > "$ART/logs/uvicorn.log" 2>&1 &
echo $! > "$E/uvicorn.pid"
echo $PORT > "$E/port.txt"

for i in $(seq 60); do
  curl -sf "http://127.0.0.1:$PORT/api/health" > "$ART/logs/health.json" && break
  sleep 1
done
if ! curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "health_fail" | tee "$ART/logs/health_fail.txt"
  kill "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
  exit 1
fi

# 1) pytest directed suite on same isolate DB (fresh process inherits DB_PATH)
cd "$ROOT"
AUTH_ENABLED=0 DB_PATH=$DB \
  RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
  $PY -m pytest demo/tests/test_uat_015_currency_payables.py -q --tb=short \
  > "$ART/logs/pytest-uat015.txt" 2>&1
PYTEST_EXIT=$?
echo "pytest_exit=$PYTEST_EXIT" | tee -a "$ART/logs/pytest-uat015.txt"

# 2) live API probe against running isolate
cd "$ROOT/demo"
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT \
  $PY $A/uat015-shared/probe_currency_payables.py > "$ART/logs/probe-api.txt" 2>&1
API_EXIT=$?
echo "api_probe_exit=$API_EXIT" | tee -a "$ART/logs/probe-api.txt"

# 3) UI probe
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT \
  $PY $A/uat015-shared/probe_payables_ui.py > "$ART/logs/probe-ui.txt" 2>&1
UI_EXIT=$?
echo "ui_probe_exit=$UI_EXIT" | tee -a "$ART/logs/probe-ui.txt"

# static contract: finance.js / finance_ops.js / api_finance.py
$PY - <<'PY' > "$ART/logs/static-contract.txt" 2>&1
from pathlib import Path
root = Path("/workspace/receipt-agent-interview")
fj = (root / "demo/static/js/finance.js").read_text(encoding="utf-8")
fo = (root / "demo/static/js/finance_ops.js").read_text(encoding="utf-8")
api = (root / "demo/app/api_finance.py").read_text(encoding="utf-8")
checks = {
    "finance_js_fx_missing_label": "汇率未知" in fj,
    "finance_js_orig_currency": "原币" in fj,
    "finance_js_total_hkd": "total_hkd" in fj,
    "finance_ops_unknown_not_hard_hkd": "缺 snapshot legacy" in fo or ("未知" in fo and "原币" in fo),
    "finance_ops_fx_missing": "fx_missing" in fo,
    "api_fx_snapshot_rate": "fx_snapshot_rate" in api,
    "api_fx_missing": "fx_missing" in api,
    "api_total_hkd_none_on_missing": "total_hkd" in api and "None" in api,
}
print("\n".join(f"{k}={v}" for k, v in checks.items()))
print("ALL_PASS" if all(checks.values()) else "STATIC_FAIL")
PY

# DB backup
$PY - <<PYEOF
import sqlite3, time
from pathlib import Path
time.sleep(0.5)
src = Path("$DB"); dst = Path("$ART/db/uat015.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
print("db_backup_ok", dst)
PYEOF

# summary
$PY - <<PYEOF
import json
from pathlib import Path
art = Path("$ART")
api = json.loads((art / "evidence/currency-payables-probe.json").read_text()) if (art/"evidence/currency-payables-probe.json").exists() else {}
ui = json.loads((art / "evidence/payables-ui-probe.json").read_text()) if (art/"evidence/payables-ui-probe.json").exists() else {}
pytest_exit = $PYTEST_EXIT
api_exit = $API_EXIT
ui_exit = $UI_EXIT
static = (art / "logs/static-contract.txt").read_text()
static_ok = "ALL_PASS" in static
overall = "PASS" if (pytest_exit == 0 and api_exit == 0 and ui_exit == 0 and static_ok and api.get("verdict")=="PASS" and ui.get("verdict")=="PASS") else "FAIL"
summary = {
  "card": "UAT-015",
  "phase": "$1",
  "port": $PORT,
  "verdict": overall,
  "pytest_exit": pytest_exit,
  "api_probe": {"exit": api_exit, "verdict": api.get("verdict"), "passed": api.get("passed"), "total": api.get("total")},
  "ui_probe": {"exit": ui_exit, "verdict": ui.get("verdict"), "passed": ui.get("passed"), "total": ui.get("total")},
  "static_ok": static_ok,
  "product_edits": "none",
}
(art / "evidence/dev-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
PYEOF
SUMMARY_EXIT=$?

# cleanup uvicorn
kill "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
sleep 1
kill -9 "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
{
  echo "ts=$(date '+%Y-%m-%d %H:%M:%S %z')"
  ss -tlnp 2>/dev/null | grep 15010 || echo "15010: FREE_RESERVED"
  ss -tlnp 2>/dev/null | grep ":$PORT " || echo "port_$PORT: stopped"
} | tee "$E/cleanup.json"

# exit non-zero if any fail
test "$PYTEST_EXIT" -eq 0 -a "$API_EXIT" -eq 0 -a "$UI_EXIT" -eq 0
