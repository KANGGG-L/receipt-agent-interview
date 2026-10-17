#!/usr/bin/env bash
# UAT-008 isolated live phase runner.
# usage: run_phase.sh <phase dir name under artifacts, e.g. uat008-dev> <port 151xx>
# DB / uploads / rag / venv live OUTSIDE the repo (/workspace/.<phase>-env); never under artifacts.
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo "refuse 15010"; exit 2; }
PY=${PY:-/workspace/.venvs/receipt/bin/python}
ENV=/workspace/.$1-env
rm -rf "$ENV"; mkdir -p "$ENV"/{uploads,artifacts_isolated,rag_chroma} "$ART"/{environment,logs,evidence,ui}
DB=$ENV/$1.db
E=$ART/environment
{
  echo "phase=$1 port=$PORT ts_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ') db=$DB"
  ss -tlnp 2>/dev/null | grep ':15010 ' || echo "15010: FREE_RESERVED (not listening)"
  ss -tlnp 2>/dev/null | grep ":$PORT " && { echo "port $PORT busy"; exit 3; } || echo "$PORT: free"
  echo "git_head=$(git -C $ROOT rev-parse --short HEAD)"
  git -C $ROOT status --short demo/
} | tee "$E/pre-state.txt"

cd "$ROOT/demo"
AUTH_ENABLED=0 DB_PATH=$DB UPLOAD_DIR=$ENV/uploads RAG_DIR=$ENV/rag_chroma RAG_EMBEDDING_PROVIDER=hash \
  ARTIFACTS_DIR=$ENV/artifacts_isolated \
  nohup $PY -m uvicorn app.main:app --host 127.0.0.1 --port $PORT > "$ART/logs/uvicorn.log" 2>&1 &
UV_PID=$!
echo $UV_PID > "$E/uvicorn.pid"; echo $PORT > "$E/port.txt"
for i in $(seq 60); do curl -sf "http://127.0.0.1:$PORT/api/health" > "$ART/logs/health.json" && break; sleep 1; done
LISTEN_PID=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
echo "uvicorn_pid=$UV_PID listen_pid=$LISTEN_PID" | tee -a "$E/pre-state.txt"
[ "$UV_PID" = "$LISTEN_PID" ] || { echo "listener is not our process"; kill $UV_PID 2>/dev/null; exit 4; }

cd "$ROOT"
$PY -m pytest demo/tests/test_uat_008_actual_qty_normalization.py demo/tests/test_uat_008_actual_qty_zero_arithmetic.py \
  -q --tb=short -p no:cacheprovider -W ignore > "$ART/logs/pytest-uat008.txt" 2>&1
echo "pytest_exit=$?" | tee -a "$ART/logs/pytest-uat008.txt"

cd "$ROOT/demo"
for probe in probe_actual_qty_api probe_actual_qty_normalization_api probe_actual_qty_ui; do
  BASE="http://127.0.0.1:$PORT" ART=$ART DB_PATH=$DB $PY $A/uat008-shared/$probe.py > "$ART/logs/$probe.txt" 2>&1
  echo "${probe}_exit=$?" | tee -a "$ART/logs/$probe.txt"
done

kill $UV_PID 2>/dev/null; sleep 2; kill -9 $UV_PID 2>/dev/null
{
  echo "cleanup_ts_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  ss -tlnp 2>/dev/null | grep ':15010 ' || echo "15010: FREE_RESERVED (not listening)"
  ss -tlnp 2>/dev/null | grep ":$PORT " || echo "isolate_port_$PORT: released"
  echo "db_kept_outside_repo=$DB"
} | tee "$E/cleanup.txt"
