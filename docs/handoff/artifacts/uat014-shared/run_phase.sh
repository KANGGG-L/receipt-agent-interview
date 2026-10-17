#!/usr/bin/env bash
# usage: run_phase.sh <phase dir name> <port>
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo refuse 15010; exit 2; }
PY=$A/eng05-uat/environment/venv/bin/python
mkdir -p $ART/{environment,logs,evidence,db}
set -a
source /home/box/.config/receipt-agent/openai_rec.env
source /home/box/.config/receipt-agent/model.env
set +a
env | cut -d= -f1 | rg -i 'key|model|base' | sed 's/$/=SET/' > $ART/logs/env-keys-present.txt
E=$ART/environment
mkdir -p $E/uploads $E/artifacts_isolated
DB=$E/uat014.db
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

# Live HKID log masking via production record_llm_call -> llm_call_log
BASE=http://127.0.0.1:$PORT ART=$ART DB_PATH=$DB PORT=$PORT \
  $PY $A/uat014-shared/probe_hkid_live.py > $ART/logs/hkid-live-probe.txt 2>&1
echo "hkid_probe_exit=$?" >> $ART/logs/hkid-live-probe.txt

# S2-19 + S2-11 regression (card cases) against isolate DB
cd $ROOT/demo
DB_PATH=$DB AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
  timeout 300 $PY -m pytest \
  tests/test_p_ai_pipeline_fixes.py::test_s2_19_llm_logger_masks_hkid_phone_and_mainland_id \
  tests/test_p_ai_pipeline_fixes.py::test_s2_19_llm_logger_uses_registry_masker_single_source \
  tests/test_p_ai_pipeline_fixes.py::test_s2_19_masker_does_not_eat_ordinary_numbers \
  tests/test_p_ai_pipeline_fixes.py::test_s2_11_unconfirmed_warnings_do_not_write_vendor_memory \
  tests/test_p_ai_pipeline_fixes.py::test_s2_11_clean_receipt_still_writes_vendor_memory \
  -q -p no:cacheprovider --junitxml=$ART/logs/pytest-s2.xml \
  > $ART/logs/pytest-s2.txt 2>&1
echo "pytest_s2_exit=$?" >> $ART/logs/pytest-s2.txt

# Prove no new memory-recovery / consistency / security-event UI was added this card
$PY $A/uat014-shared/check_no_new_ui.py $ART > $ART/logs/no-new-ui.txt 2>&1
echo "no_new_ui_exit=$?" >> $ART/logs/no-new-ui.txt

$PY - <<PYEOF
import sqlite3
from pathlib import Path
src = Path("$DB"); dst = Path("$ART/db/uat014.db")
s = sqlite3.connect(str(src)); d = sqlite3.connect(str(dst)); s.backup(d); d.close(); s.close()
PYEOF
kill $(cat $E/uvicorn.pid) 2>/dev/null
sleep 2
S15010=$(ss -ltn | rg -q ":15010 " && echo LISTENING_PREEXISTING || echo FREE_RESERVED)
SP=$(ss -ltn | rg -q ":$PORT " && echo STILL_LISTENING || echo STOPPED)
printf '{"cleaned_at":"%s","port":%s,"port_status":"%s","15010_status":"%s","no_git_push":true}\n' \
  "$(date '+%F %T CST')" $PORT "$SP" "$S15010" > $E/cleanup.json
rm -rf $E/rag_chroma $E/uploads
tail -5 $ART/logs/hkid-live-probe.txt
tail -8 $ART/logs/pytest-s2.txt
tail -20 $ART/logs/no-new-ui.txt
cat $E/cleanup.json
