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
DB=$E/uat001.db
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

# 1) pytest directed suite (TestClient; uses its own DB_PATH via env)
cd "$ROOT"
AUTH_ENABLED=0 DB_PATH=$DB \
  RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
  $PY -m pytest demo/tests/test_uat_001_edited_receipt_has_original_image.py -q --tb=short \
  > "$ART/logs/pytest-uat001.txt" 2>&1
PYTEST_EXIT=$?
echo "pytest_exit=$PYTEST_EXIT" | tee -a "$ART/logs/pytest-uat001.txt"

# 2) live API probe
cd "$ROOT/demo"
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT UPLOAD_DIR=$UPLOAD_DIR \
  $PY $A/uat001-shared/probe_original_image_api.py > "$ART/logs/probe-api.txt" 2>&1
API_EXIT=$?
echo "api_probe_exit=$API_EXIT" | tee -a "$ART/logs/probe-api.txt"

# 3) UI probe (needs seed-ids.json from API probe)
BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB PORT=$PORT UPLOAD_DIR=$UPLOAD_DIR \
  $PY $A/uat001-shared/probe_original_image_ui.py > "$ART/logs/probe-ui.txt" 2>&1
UI_EXIT=$?
echo "ui_probe_exit=$UI_EXIT" | tee -a "$ART/logs/probe-ui.txt"

# static contract
$PY - <<'PY' > "$ART/logs/static-contract.txt" 2>&1
from pathlib import Path
root = Path("/workspace/receipt-agent-interview")
rc = (root / "demo/static/js/review_core.js").read_text(encoding="utf-8")
al = (root / "demo/static/js/archive_list.js").read_text(encoding="utf-8")
api = (root / "demo/app/api_receipts.py").read_text(encoding="utf-8")
checks = {
    "api_data_image_url": 'data["image_url"]' in api or "data['image_url']" in api,
    "api_has_original_image": "has_original_image" in api,
    "api_public_image_url_detail": "public_image_url" in api and "UAT-001" in api,
    "review_uat001_bound": "__uat001Bound" in rc,
    "review_load_fail_label": "原图加载失败" in rc,
    "review_no_pretend_manual_on_error": "原图加载失败" in rc and "无原图（手工录入）" in rc,
    "archive_uat001_bound": "__uat001Bound" in al,
    "archive_load_fail_label": "原图加载失败" in al,
    "archive_uses_ret_image_url": "ret.image_url" in al or "ret.data && ret.data.image_url" in al,
}
print("\n".join(f"{k}={v}" for k, v in checks.items()))
print("ALL_PASS" if all(checks.values()) else "STATIC_FAIL")
PY

# DB backup
$PY - <<PYEOF
import sqlite3, time
from pathlib import Path
time.sleep(0.3)
src = Path("$DB"); dst = Path("$ART/db/uat001.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
print("db_backup_ok", dst)
PYEOF

# summary json
$PY - <<PYEOF
import json
from pathlib import Path
art = Path("$ART")
api = json.loads((art/"evidence/original-image-api-probe.json").read_text()) if (art/"evidence/original-image-api-probe.json").exists() else {}
ui = json.loads((art/"evidence/original-image-ui-probe.json").read_text()) if (art/"evidence/original-image-ui-probe.json").exists() else {}
static = (art/"logs/static-contract.txt").read_text() if (art/"logs/static-contract.txt").exists() else ""
summary = {
  "card": "UAT-001",
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
sleep 2
kill -9 "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
sleep 1
S15010=$(ss -ltn | rg -q ":15010 " && echo LISTENING_PREEXISTING || echo FREE_RESERVED)
SP=$(ss -ltn | rg -q ":$PORT " && echo STILL_LISTENING || echo STOPPED)
VERDICT=$(python3 -c "import json; print(json.load(open('$ART/evidence/dev-summary.json')).get('verdict','FAIL'))" 2>/dev/null || echo FAIL)
printf '{"cleaned_at":"%s","port":%s,"port_status":"%s","15010_status":"%s","no_git_push":true,"product_edits":"none","pytest_exit":%s,"api_exit":%s,"ui_exit":%s,"verdict":"%s"}\n' \
  "$(date '+%F %T CST')" "$PORT" "$SP" "$S15010" "$PYTEST_EXIT" "$API_EXIT" "$UI_EXIT" "$VERDICT" > "$E/cleanup.json"

echo '=== pytest tail ==='
tail -20 "$ART/logs/pytest-uat001.txt"
echo '=== api tail ==='
tail -30 "$ART/logs/probe-api.txt"
echo '=== ui tail ==='
tail -40 "$ART/logs/probe-ui.txt"
echo '=== static ==='
cat "$ART/logs/static-contract.txt"
echo '=== cleanup ==='
cat "$E/cleanup.json"
# exit non-zero if any stage failed
[ "$PYTEST_EXIT" = 0 ] && [ "$API_EXIT" = 0 ] && [ "$UI_EXIT" = 0 ] && [ "$VERDICT" = PASS ]
