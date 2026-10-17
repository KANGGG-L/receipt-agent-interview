#!/usr/bin/env bash
set -euo pipefail
ROOT=/workspace/receipt-agent-interview
ART=$ROOT/docs/handoff/artifacts/uat032-dev
LOG=$ART/logs
ENVDIR=$ART/environment
PY=$ROOT/docs/handoff/artifacts/eng05-qa/environment/venv/bin/python
UVICORN=$ROOT/docs/handoff/artifacts/eng05-qa/environment/venv/bin/uvicorn
PORT=15132
DB=$ENVDIR/uat032.db
rm -f "$DB"
mkdir -p "$LOG" "$ENVDIR"

# Ensure 15010 stays free
if ss -tlnp 2>/dev/null | grep -q ':15010'; then
  echo "ERROR: port 15010 is in use" >&2
  exit 2
fi

export DB_PATH="$DB"
export AUTH_ENABLED=0
export PYTHONPATH="$ROOT/demo${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT/demo"
"$UVICORN" app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG/uvicorn.log" 2>&1 &
PID=$!
echo "$PID" >"$ENVDIR/uvicorn.pid"
cleanup() {
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 \
     || curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

export DEV_BASE="http://127.0.0.1:$PORT"
export UAT032_OUT="$LOG"
"$PY" "$ART/scripts/selfcheck_uat032.py"
RC=$?

cleanup
trap - EXIT
# verify ports free
sleep 0.3
if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
  echo "WARN: $PORT still listening" >&2
fi
if ss -tlnp 2>/dev/null | grep -q ':15010'; then
  echo "ERROR: 15010 occupied after cleanup" >&2
  exit 3
fi
echo "ports_ok port=$PORT free; 15010 free"
exit $RC
