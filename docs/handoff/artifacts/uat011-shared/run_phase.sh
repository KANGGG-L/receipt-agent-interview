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
mkdir -p "$E/uploads" "$E/artifacts_isolated" "$E/evalsets"
DB=$E/uat011.db
EVALSET_DIR=$E/evalsets

# synthetic U001 (+ U002 for double-click)
EVALSET_DIR=$EVALSET_DIR $PY $A/uat011-shared/setup_synth_u001.py | tee "$ART/logs/setup-u001.txt"

cd "$ROOT/demo"
AUTH_ENABLED=0 APP_PORT=$PORT DB_PATH=$DB EVALSET_DIR=$EVALSET_DIR \
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

# smoke: sample U001 reachable as admin
curl -sf -H 'X-Role: admin' -H 'X-Tenant-Id: uat011-a' \
  "http://127.0.0.1:$PORT/api/evalset/sample/U001" > "$ART/logs/sample-u001.json" || true

BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB EVALSET_DIR=$EVALSET_DIR PORT=$PORT \
  $PY $A/uat011-shared/gt_single_click.py > "$ART/logs/gt-single-click.txt" 2>&1
PROBE_EXIT=$?
echo "gt_single_click_exit=$PROBE_EXIT" >> "$ART/logs/gt-single-click.txt"

# static contract: HEAD already isolates GT confirm from save_edited
$PY - <<'PY' > "$ART/logs/static-contract.txt" 2>&1
from pathlib import Path
root = Path("/workspace/receipt-agent-interview")
ew = (root / "demo/static/js/eval_workbench.js").read_text(encoding="utf-8")
rc = (root / "demo/static/js/review_core.js").read_text(encoding="utf-8")
api = (root / "demo/app/api_evalset.py").read_text(encoding="utf-8")
checks = {
    "eval_gtConfirmLock": "gtConfirmLock" in ew,
    "eval_replaceWithoutBusinessSave": "replaceWithoutBusinessSave" in ew,
    "eval_confirm_url": "/api/evalset/sample/" in ew and "/confirm" in ew,
    "eval_no_save_edited_in_send": "save_edited" not in ew.split("function sendEvalConfirm")[1].split("function enter")[0] if "function sendEvalConfirm" in ew else False,
    "review_isEvalWorkbenchContext": "isEvalWorkbenchContext" in rc,
    "review_early_return": "if (isEvalWorkbenchContext()) return;" in rc,
    "api_no_business_write_comment": "不调用 link_or_create" in api or "不写 receipts" in api,
}
print("\n".join(f"{k}={v}" for k, v in checks.items()))
print("ALL_PASS" if all(checks.values()) else "STATIC_FAIL")
PY

# DB backup after WAL settle
$PY - <<PYEOF
import sqlite3, time
from pathlib import Path
time.sleep(0.5)
src = Path("$DB"); dst = Path("$ART/db/uat011.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
print("db_backup_ok", dst)
PYEOF

# copy evalset evidence
mkdir -p "$ART/evidence/evalset"
cp -a "$EVALSET_DIR/expected/U001.json" "$ART/evidence/evalset/" 2>/dev/null || true
cp -a "$EVALSET_DIR/expected/U002.json" "$ART/evidence/evalset/" 2>/dev/null || true
cp -a "$EVALSET_DIR/manifest.csv" "$ART/evidence/evalset/" 2>/dev/null || true

kill "$(cat "$E/uvicorn.pid")" 2>/dev/null || true
sleep 2
S15010=$(ss -ltn | rg -q ":15010 " && echo LISTENING_PREEXISTING || echo FREE_RESERVED)
SP=$(ss -ltn | rg -q ":$PORT " && echo STILL_LISTENING || echo STOPPED)
VERDICT=$(python3 -c "import json; print(json.load(open('$ART/evidence/gt-single-click.json')).get('verdict','FAIL'))" 2>/dev/null || echo FAIL)
printf '{"cleaned_at":"%s","port":%s,"port_status":"%s","15010_status":"%s","no_git_push":true,"product_edits":"none","probe_exit":%s,"verdict":"%s"}\n' \
  "$(date '+%F %T CST')" "$PORT" "$SP" "$S15010" "$PROBE_EXIT" "$VERDICT" > "$E/cleanup.json"

echo '=== gt-single-click tail ==='
tail -40 "$ART/logs/gt-single-click.txt"
echo '=== static ==='
cat "$ART/logs/static-contract.txt"
echo '=== cleanup ==='
cat "$E/cleanup.json"
exit $PROBE_EXIT
