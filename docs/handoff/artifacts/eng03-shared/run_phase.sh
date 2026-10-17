#!/usr/bin/env bash
# usage: run_phase.sh <phase dir name> <port>
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo refuse 15010; exit 2; }
PY=$A/eng05-uat/environment/venv/bin/python
mkdir -p $ART/{environment,logs,evidence,screens,db}
if [ ! -f $ART/evidence/sharp.jpg ]; then
  cp $A/dep-model-uat/evidence/sample_711.jpg $ART/evidence/sharp.jpg
fi
set -a
source /home/box/.config/receipt-agent/openai_rec.env
source /home/box/.config/receipt-agent/model.env
set +a
env | cut -d= -f1 | rg -i 'key|model|base' | sed 's/$/=SET/' > $ART/logs/env-keys-present.txt
E=$ART/environment
mkdir -p $E/uploads $E/artifacts_isolated
DB=$E/eng03.db
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
  exit 1
fi
BASE=http://127.0.0.1:$PORT ART=$ART SHARP=$ART/evidence/sharp.jpg DB_PATH=$DB \
  $PY $A/eng03-shared/probe_real.py > $ART/logs/real-probe.txt 2>&1
echo "real_probe_exit=$?" >> $ART/logs/real-probe.txt
# supporting regression (workbench e2e; AC3 non-manual path uses syncReviewSourceBadge evaluate — NOT counted as model AC)
mkdir -p $ART/screens/pytest
PW_TEST_BASE_URL=http://127.0.0.1:$PORT ARTIFACTS_DIR=$ART/screens/pytest \
  timeout 600 $PY -m pytest $ROOT/tests/test_workbench_e2e_playwright.py -q -p no:cacheprovider \
  --junitxml=$ART/logs/pytest.xml > $ART/logs/pytest.txt 2>&1
echo "pytest_exit=$?" >> $ART/logs/pytest.txt
# copy db snapshot for evidence
cp -f $DB $ART/db/eng03.db 2>/dev/null || true
kill $(cat $E/uvicorn.pid) 2>/dev/null
sleep 2
S15010=$(ss -ltn | rg -q ":15010 " && echo LISTENING_PREEXISTING || echo FREE_RESERVED)
SP=$(ss -ltn | rg -q ":$PORT " && echo STILL_LISTENING || echo STOPPED)
printf '{"cleaned_at":"%s","port":%s,"port_status":"%s","15010_status":"%s","no_git_push":true}\n' \
  "$(date '+%F %T CST')" $PORT "$SP" "$S15010" > $E/cleanup.json
rm -rf $E/rag_chroma $E/uploads
tail -3 $ART/logs/real-probe.txt
tail -5 $ART/logs/pytest.txt
cat $E/cleanup.json
