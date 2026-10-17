#!/usr/bin/env bash
# usage: run_phase.sh <phase dir name> <port>
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo refuse 15010; exit 2; }
PY=$A/eng05-uat/environment/venv/bin/python
mkdir -p $ART/{environment,logs,evidence,db,ui}
E=$ART/environment
mkdir -p $E/uploads $E/artifacts_isolated
DB=$E/uat016.db
cd $ROOT/demo
AUTH_ENABLED=0 APP_PORT=$PORT DB_PATH=$DB RAG_DIR=$E/rag_chroma RAG_EMBEDDING_PROVIDER=hash ARTIFACTS_DIR=$E/artifacts_isolated \
  nohup $PY -m uvicorn app.main:app --host 127.0.0.1 --port $PORT > $ART/logs/uvicorn.log 2>&1 &
echo $! > $E/uvicorn.pid
echo $PORT > $E/port.txt
for i in $(seq 60); do
  curl -sf http://127.0.0.1:$PORT/api/health > $ART/logs/health.json && break
  sleep 1
done
if ! curl -sf http://127.0.0.1:$PORT/api/health >/dev/null; then
  echo "health_fail" | tee $ART/logs/health_fail.txt
  kill $(cat $E/uvicorn.pid) 2>/dev/null || true
  exit 1
fi

# Live owner recycle/restore/purge API
BASE=http://127.0.0.1:$PORT ART=$ART \
  $PY $A/uat016-shared/probe_trash_live.py > $ART/logs/trash-live-probe.txt 2>&1
echo "live_probe_exit=$?" >> $ART/logs/trash-live-probe.txt

# Live browser trash UI
BASE=http://127.0.0.1:$PORT ART=$ART \
  $PY $A/uat016-shared/probe_trash_browser.py > $ART/logs/trash-browser-probe.txt 2>&1
echo "browser_probe_exit=$?" >> $ART/logs/trash-browser-probe.txt

# SPEC_KEEP documentation / contract scan
ART=$ART $PY $A/uat016-shared/check_spec_keep.py > $ART/logs/spec-keep.txt 2>&1
echo "spec_keep_exit=$?" >> $ART/logs/spec-keep.txt

# Regression: S1-P1-12 / S1-P1-13 (TestClient; uses its own DB via app import — run against isolate DB)
cd $ROOT/demo
DB_PATH=$DB AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
  timeout 300 $PY -m pytest \
  tests/test_b1_lifecycle_reverse_trash.py::test_trash_list_restore_purge_owner_only_and_tenant_scoped \
  tests/test_b1_lifecycle_reverse_trash.py::test_purge_refuses_approved_receipts \
  tests/test_b1_lifecycle_reverse_trash.py::test_discard_flagged_is_owner_only \
  tests/test_b1_lifecycle_reverse_trash.py::test_old_admin_trash_endpoints_removed \
  tests/test_b1_lifecycle_reverse_trash.py::test_experiment_conclude_is_admin_only_and_concluded_by_from_identity \
  -q -p no:cacheprovider --junitxml=$ART/logs/pytest-s1.xml \
  > $ART/logs/pytest-s1.txt 2>&1
echo "pytest_s1_exit=$?" >> $ART/logs/pytest-s1.txt

# S3-07 (Playwright f1a — own server; regression of card case)
cd $ROOT/demo
F1A_SHOT_DIR=$ART/ui/f1a ARTIFACTS_DIR=$E/artifacts_isolated \
  timeout 420 $PY -m pytest \
  tests/test_f1a_flows.py::test_s3_07_discard_uses_in_app_dialog_and_owner_gets_undo \
  tests/test_f1a_flows.py::test_s3_07_staff_discard_does_not_promise_undo \
  tests/test_f1a_flows.py::test_s3_07_flag_uses_in_app_dialog_with_reason \
  -q -p no:cacheprovider --junitxml=$ART/logs/pytest-s3.xml \
  > $ART/logs/pytest-s3.txt 2>&1
echo "pytest_s3_exit=$?" >> $ART/logs/pytest-s3.txt

# S4-12 related: trash UI playwright (f1b own server) + note shared dialog
cd $ROOT/demo
F1B_TEST_PORT=15479 F1B_SHOT_DIR=$ART/ui/f1b ARTIFACTS_DIR=$E/artifacts_isolated \
  timeout 420 $PY -m pytest \
  tests/test_f1b_archive_finance.py::test_trash_list_restore_and_purge \
  tests/test_f1b_archive_finance.py::test_trash_hidden_for_staff \
  -q -p no:cacheprovider --junitxml=$ART/logs/pytest-f1b-trash.xml \
  > $ART/logs/pytest-f1b-trash.txt 2>&1
echo "pytest_f1b_exit=$?" >> $ART/logs/pytest-f1b-trash.txt

$PY - <<PYEOF
import sqlite3
from pathlib import Path
src = Path("$DB"); dst = Path("$ART/db/uat016.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
PYEOF
kill $(cat $E/uvicorn.pid) 2>/dev/null
sleep 2
# also ensure f1b port freed
ss -ltn | rg -q ':15479 ' && { fuser -k 15479/tcp 2>/dev/null || true; }
S15010=$(ss -ltn | rg -q ":15010 " && echo LISTENING_PREEXISTING || echo FREE_RESERVED)
SP=$(ss -ltn | rg -q ":$PORT " && echo STILL_LISTENING || echo STOPPED)
printf '{"cleaned_at":"%s","port":%s,"port_status":"%s","15010_status":"%s","no_git_push":true}\n' \
  "$(date '+%F %T CST')" $PORT "$SP" "$S15010" > $E/cleanup.json
rm -rf $E/rag_chroma $E/uploads
echo '--- live ---'; tail -5 $ART/logs/trash-live-probe.txt
echo '--- browser ---'
tail -8 $ART/logs/trash-browser-probe.txt
echo '--- spec ---'
tail -5 $ART/logs/spec-keep.txt
echo '--- pytest s1 ---'
tail -15 $ART/logs/pytest-s1.txt
echo '--- pytest s3 ---'
tail -20 $ART/logs/pytest-s3.txt
echo '--- pytest f1b ---'
tail -20 $ART/logs/pytest-f1b-trash.txt
cat $E/cleanup.json
