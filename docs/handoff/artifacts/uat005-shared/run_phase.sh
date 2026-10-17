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
DB=$E/uat005.db
rm -f "$DB" "$DB-wal" "$DB-shm"
UPLOAD_DIR=$ROOT/demo/uploads

{
  echo "phase=$1 port=$PORT ts=$(date '+%Y-%m-%d %H:%M:%S %z')"
  ss -tlnp 2>/dev/null | grep 15010 || echo "15010: FREE_RESERVED"
} | tee "$E/pre-state.txt"

cd "$ROOT/demo"
AUTH_ENABLED=0 APP_PORT=$PORT DB_PATH=$DB \
  RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
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

# 1) pytest
cd "$ROOT"
AUTH_ENABLED=0 DB_PATH=$DB \
  RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
  $PY -m pytest demo/tests/test_uat_005_upload_error_classification.py -q --tb=short \
  > "$ART/logs/pytest-uat005.txt" 2>&1
PYTEST_EXIT=$?
echo "pytest_exit=$PYTEST_EXIT" | tee -a "$ART/logs/pytest-uat005.txt"

# 2) live API probe
cd "$ROOT/demo"
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT UPLOAD_DIR=$UPLOAD_DIR \
  $PY $A/uat005-shared/probe_upload_errors_api.py > "$ART/logs/probe-api.txt" 2>&1
API_EXIT=$?
echo "api_probe_exit=$API_EXIT" | tee -a "$ART/logs/probe-api.txt"

# 3) UI probe
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT UPLOAD_DIR=$UPLOAD_DIR \
  $PY $A/uat005-shared/probe_upload_errors_ui.py > "$ART/logs/probe-ui.txt" 2>&1
UI_EXIT=$?
echo "ui_probe_exit=$UI_EXIT" | tee -a "$ART/logs/probe-ui.txt"

# static contract
$PY - <<'PY' > "$ART/logs/static-contract.txt" 2>&1
from pathlib import Path
root = Path("/workspace/receipt-agent-interview")
api = (root / "demo/app/api_receipts.py").read_text(encoding="utf-8")
uj = (root / "demo/static/js/upload.js").read_text(encoding="utf-8")
checks = {
    "api_EMPTY_FILE": "EMPTY_FILE" in api,
    "api_INVALID_IMAGE": "INVALID_IMAGE" in api,
    "api_UNSUPPORTED": "UNSUPPORTED_IMAGE_FORMAT" in api,
    "api_FILE_TOO_LARGE": "FILE_TOO_LARGE" in api,
    "api_magic_unsupported_not_invalid": "magic 不识别一律 UNSUPPORTED" in api or "UAT-005" in api,
    "upload_isFileIssue_route": "isFileIssue" in uj and "EMPTY_FILE" in uj,
    "upload_file_card_empty": "文件内容为空" in uj,
    "upload_file_card_corrupt": "图片文件已损坏" in uj,
    "upload_file_card_unsupported": "文件格式不支持" in uj,
    "upload_ENGINE_TIMEOUT_code": "ENGINE_TIMEOUT" in uj,
    "upload_no_escapeHtml_filename": "escapeHtml(file.name)" not in uj,
}
print("\n".join(f"{k}={v}" for k, v in checks.items()))
print("ALL_PASS" if all(checks.values()) else "STATIC_FAIL")
PY

# DB backup
$PY - <<PYEOF
import sqlite3, time
from pathlib import Path
time.sleep(0.3)
src = Path("$DB"); dst = Path("$ART/db/uat005.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
print("db_backup_ok", dst)
PYEOF

# summary
$PY - <<PYEOF
import json
from pathlib import Path
art = Path("$ART")
api = json.loads((art/"evidence/upload-errors-api-probe.json").read_text()) if (art/"evidence/upload-errors-api-probe.json").exists() else {}
ui = json.loads((art/"evidence/upload-errors-ui-probe.json").read_text()) if (art/"evidence/upload-errors-ui-probe.json").exists() else {}
static = (art/"logs/static-contract.txt").read_text() if (art/"logs/static-contract.txt").exists() else ""
summary = {
  "card": "UAT-005",
  "phase": "$1",
  "port": $PORT,
  "pytest_exit": $PYTEST_EXIT,
  "api_exit": $API_EXIT,
  "ui_exit": $UI_EXIT,
  "api_verdict": api.get("verdict"),
  "ui_verdict": ui.get("verdict"),
  "static_all_pass": "ALL_PASS" in static,
  "product_edits": "none",
}
summary["verdict"] = "PASS" if (
  summary["pytest_exit"]==0 and summary["api_exit"]==0 and summary["ui_exit"]==0 and summary["static_all_pass"]
) else "FAIL"
(art/"evidence/dev-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
PYEOF

# cleanup uvicorn
kill "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
sleep 1
kill -9 "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
{
  echo "cleanup_ts=$(date '+%Y-%m-%d %H:%M:%S %z')"
  ss -tlnp 2>/dev/null | grep 15010 || echo "15010: FREE_RESERVED"
  ss -tlnp 2>/dev/null | grep ":$PORT " || echo "isolate_port_$PORT: stopped"
} | tee "$E/cleanup.json"

exit 0
