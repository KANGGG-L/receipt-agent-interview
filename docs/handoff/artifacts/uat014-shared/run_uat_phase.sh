#!/usr/bin/env bash
# usage: run_uat_phase.sh <phase dir name> <port>
set -u
ROOT=/workspace/receipt-agent-interview
A=$ROOT/docs/handoff/artifacts
ART=$A/$1
PORT=$2
[ "$PORT" = 15010 ] && { echo refuse 15010; exit 2; }
[ "$PORT" = 15439 ] && { echo refuse DEV port 15439; exit 2; }
[ "$PORT" = 15449 ] && { echo refuse QA port 15449; exit 2; }
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

TENANT_B=uat014uatb
HDR=(-H "X-Role: owner" -H "X-Tenant-Id: $TENANT_B")
HDR_DEF=(-H "X-Role: owner" -H "X-Tenant-Id: uat014-dev")

# Tenant B baseline before probe
curl -sS -o $ART/logs/tenant_b_before.json -w "%{http_code}" "${HDR[@]}" http://127.0.0.1:$PORT/api/dishes > $ART/logs/tenant_b_before.http
curl -sS -o $ART/logs/tenant_b_receipts_before.json -w "%{http_code}" "${HDR[@]}" "http://127.0.0.1:$PORT/api/receipts?limit=5" > $ART/logs/tenant_b_receipts_before.http

# Live HKID log masking via production record_llm_call -> llm_call_log
BASE=http://127.0.0.1:$PORT ART=$ART DB_PATH=$DB PORT=$PORT \
  $PY $A/uat014-shared/probe_hkid_live.py > $ART/logs/hkid-live-probe.txt 2>&1
echo "hkid_probe_exit=$?" >> $ART/logs/hkid-live-probe.txt

# Tenant B after probe
curl -sS -o $ART/logs/tenant_b_after.json -w "%{http_code}" "${HDR[@]}" http://127.0.0.1:$PORT/api/dishes > $ART/logs/tenant_b_after.http
curl -sS -o $ART/logs/tenant_b_receipts_after.json -w "%{http_code}" "${HDR[@]}" "http://127.0.0.1:$PORT/api/receipts?limit=5" > $ART/logs/tenant_b_receipts_after.http
curl -sS -o $ART/logs/tenant_default_dishes.json -w "%{http_code}" "${HDR_DEF[@]}" http://127.0.0.1:$PORT/api/dishes > $ART/logs/tenant_default_dishes.http

# DB-level Tenant B isolation + API summary
$PY - <<PYEOF
import json, sqlite3
from pathlib import Path
art = Path("$ART")
db = "$DB"
tb = "$TENANT_B"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

def count(sql, args=()):
    try:
        return conn.execute(sql, args).fetchone()[0]
    except Exception as e:
        return f"err:{e}"

n_b = count("SELECT COUNT(*) FROM llm_call_log WHERE tenant_id = ?", (tb,))
n_probe = count("SELECT COUNT(*) FROM llm_call_log WHERE trace_id = 'uat014-live-probe'")
rows_probe = [dict(r) for r in conn.execute(
    "SELECT id, tenant_id, trace_id, model, substr(raw_output,1,120) AS raw_sample FROM llm_call_log WHERE trace_id='uat014-live-probe'"
).fetchall()]
for r in rows_probe:
    s = r.get("raw_sample") or ""
    for needle in ["A123456(7)", "K1234567", "9123 4567", "sk-abcdefghijklmnopqrstuv"]:
        s = s.replace(needle, "<REDACTED>")
    r["raw_sample"] = s
all_tenants = [dict(r) for r in conn.execute("SELECT tenant_id, COUNT(*) AS c FROM llm_call_log GROUP BY tenant_id").fetchall()]
db_counts = {
    "llm_call_log": n_b,
    "receipts": count("SELECT COUNT(*) FROM receipts WHERE tenant_id = ?", (tb,)),
    "dishes": count("SELECT COUNT(*) FROM dishes WHERE tenant_id = ?", (tb,)),
    "vendor_memory": count("SELECT COUNT(*) FROM vendor_memory WHERE tenant_id = ?", (tb,)),
    "inventory_log": count("SELECT COUNT(*) FROM inventory_log WHERE tenant_id = ?", (tb,)),
}
conn.close()

def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:
        return {"_load_error": str(e)}

def http_code(p):
    try:
        return Path(p).read_text(encoding="utf-8").strip()
    except Exception:
        return "?"

def dishes_len(obj):
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        d = obj.get("data", obj.get("items", obj.get("dishes")))
        if isinstance(d, list):
            return len(d)
        if obj.get("error") or obj.get("_load_error"):
            return f"error:{obj.get('error') or obj.get('_load_error')}"
    return "unknown"

before = load(art / "logs/tenant_b_before.json")
after = load(art / "logs/tenant_b_after.json")
receipts_b = load(art / "logs/tenant_b_receipts_after.json")
default_d = load(art / "logs/tenant_default_dishes.json")
http_dishes = http_code(art / "logs/tenant_b_after.http")
http_receipts = http_code(art / "logs/tenant_b_receipts_after.http")
http_default = http_code(art / "logs/tenant_default_dishes.http")

def receipts_len(obj):
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        d = obj.get("data", obj.get("items", obj.get("receipts")))
        if isinstance(d, list):
            return len(d)
        if obj.get("status") == "success" and "data" in obj and obj["data"] == []:
            return 0
    return dishes_len(obj)

out = {
    "tenant_b": tb,
    "method": "curl with X-Role: owner + X-Tenant-Id (AUTH_ENABLED=0 demo)",
    "api_dishes_http": http_dishes,
    "api_receipts_http": http_receipts,
    "api_default_dishes_http": http_default,
    "dishes_b_len": dishes_len(after),
    "receipts_b_len": receipts_len(receipts_b),
    "dishes_default_len": dishes_len(default_d),
    "db_counts_tenant_b": db_counts,
    "llm_call_log_by_tenant": all_tenants,
    "llm_call_log_count_tenant_b": n_b,
    "probe_log_rows": n_probe,
    "probe_rows_redacted": rows_probe,
    "dishes_before_len": dishes_len(before),
    "dishes_after_len": dishes_len(after),
    "dishes_b_sample": after if isinstance(after, dict) else {"data": after},
    "note": "Probe wrote llm_call_log under tenant uat014-dev only; Tenant B tables remain empty / unchanged.",
}
ok = (
    n_b == 0
    and n_probe >= 1
    and dishes_len(before) == dishes_len(after)
    and http_dishes == "200"
    and http_receipts == "200"
    and dishes_len(after) == 0
)
out["verdict"] = "PASS" if ok else "FAIL"
(art / "evidence/tenant-b.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"verdict": out["verdict"], "n_b": n_b, "n_probe": n_probe, "http_dishes": http_dishes, "http_receipts": http_receipts}))
raise SystemExit(0 if ok else 1)
PYEOF
echo "tenant_b_exit=$?" | tee -a $ART/logs/tenant_b_check.txt
cp $ART/evidence/tenant-b.json $ART/logs/tenant_b_check.json 2>/dev/null || true

# Dump masked llm_call_log row for evidence (redacted)
$PY - <<PYEOF
import json, sqlite3
from pathlib import Path
art = Path("$ART")
conn = sqlite3.connect("$DB")
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT id, tenant_id, trace_id, model, prompt_messages_json, raw_output FROM llm_call_log WHERE trace_id='uat014-live-probe' ORDER BY id DESC LIMIT 1").fetchone()
conn.close()
FORBIDDEN = ["A123456(7)", "K1234567", "9123 4567", "91234567", "110101199003072345", "sk-abcdefghijklmnopqrstuv"]
def red(s):
    s = s or ""
    for p in FORBIDDEN:
        s = s.replace(p, "<REDACTED_PLAINTEXT>")
    return s
out = {}
if row:
    blob = f"{row['prompt_messages_json']}\n{row['raw_output']}"
    leaks = [k for k,p in [("hkid_paren","A123456(7)"),("hkid_noparen","K1234567"),("phone","9123 4567"),("mainland","110101199003072345"),("key","sk-abcdefghijklmnopqrstuv")] if p in blob]
    out = {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "trace_id": row["trace_id"],
        "model": row["model"],
        "prompt_redacted": red(row["prompt_messages_json"])[:800],
        "raw_redacted": red(row["raw_output"])[:400],
        "contains_HKID_MASKED": "[HKID_MASKED]" in blob,
        "contains_sk_mask": "sk-***" in blob,
        "plaintext_leak_kinds": leaks,
        "plaintext_leak_count": len(leaks),
        "verdict": "PASS" if (len(leaks)==0 and "[HKID_MASKED]" in blob and "sk-***" in blob) else "FAIL",
    }
else:
    out = {"verdict": "FAIL", "reason": "no probe row"}
(art / "evidence/db-llm-call-log.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"verdict": out.get("verdict"), "id": out.get("id")}))
PYEOF

# S2-19 + S2-11 regression
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

# No new UI
$PY $A/uat014-shared/check_no_new_ui.py $ART > $ART/logs/no-new-ui.txt 2>&1
echo "no_new_ui_exit=$?" >> $ART/logs/no-new-ui.txt

# Final Tenant B snapshot
curl -sS -o $ART/logs/tenant_b_final.json -w "%{http_code}" "${HDR[@]}" http://127.0.0.1:$PORT/api/dishes > $ART/logs/tenant_b_final.http

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
echo "=== hkid probe ==="
tail -5 $ART/logs/hkid-live-probe.txt
echo "=== tenant-b ==="
python3 -c "import json;print(json.load(open('$ART/evidence/tenant-b.json'))['verdict'])" 2>/dev/null || true
echo "=== db-llm ==="
python3 -c "import json;print(json.load(open('$ART/evidence/db-llm-call-log.json'))['verdict'])" 2>/dev/null || true
echo "=== pytest ==="
tail -8 $ART/logs/pytest-s2.txt
echo "=== no-new-ui ==="
tail -5 $ART/logs/no-new-ui.txt
echo "=== cleanup ==="
cat $E/cleanup.json
