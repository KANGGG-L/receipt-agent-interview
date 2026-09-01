#!/usr/bin/env bash
# 面试现场一键演示：完整版前端 + LangChain 精简后端
#
# 用法：
#   ./demo.sh run                 # 启动 API（http://127.0.0.1:15010）
#   ./demo.sh smoke <图片路径>    # 单图冒烟（不走服务，快速验证 AI 链路）
#   ./demo.sh test                # 跑确定性单测
#   ./demo.sh workflow            # 跑完整业务流测试（上传→识别→复核→审批→库存→成本→复盘→对账→支付）
#   ./demo.sh import [N]          # 导入 N 张黄金样本（默认 8，含图+人工标注）
#   ./demo.sh approve-imported    # 把已导入的 edited 收据全部 approve 入库
#
set -euo pipefail
PY=~/.pyenv/versions/3.9.6/bin/python
IMG="${2:-samples/20161001_711_thermal_receipt.jpg}"

case "${1:-}" in
  run)
    echo "启动 API: http://127.0.0.1:15010 （完整版前端 + LangChain 精简后端，已开启热重载）"
    $PY -m uvicorn app.main:app --port 15010 --reload \
      --reload-dir app --reload-dir ../ai_registry --reload-dir templates --reload-dir static \
      --reload-include "*.py" --reload-include "*.js" --reload-include "*.css" --reload-include "*.html"
    ;;
  smoke)
    $PY -m app.main --smoke "$IMG"
    ;;
  test)
    $PY -m pytest tests/test_demo.py -v
    ;;
  workflow)
    $PY tests/workflow_test.py
    ;;
  import)
    N="${2:-8}"
    $PY scripts/import_golden.py --limit "$N"
    ;;
  approve-imported)
    $PY -c "
import json, urllib.request, urllib.error
def call(p, m='GET', b=None):
    h={'X-Role':'owner'}; d=None
    if b is not None: d=json.dumps(b).encode(); h['Content-Type']='application/json'
    req=urllib.request.Request('http://127.0.0.1:15010'+p, data=d, headers=h, method=m)
    try: return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e: return json.load(e)
rows=call('/api/receipts')['data']
n=0
for r in rows:
    if r['status']!='edited': continue
    ver=call('/api/receipt/%d'%r['id'])['data']['version']
    res=call('/api/receipt/%d/approve'%r['id'],'POST',{'version':ver})
    if res.get('status')=='success': n+=1; print('  ✓ approve #%d %s'%(r['id'], r['supplier_name'][:16]))
print('共 approve %d 张'%n)
"
    ;;
  *)
    echo "用法: $0 [run | smoke <图> | test | workflow | import [N] | approve-imported]"
    exit 1
    ;;
esac
