# 修复任务：前端 PM 走查 8 项缺陷（U-01~U-08）

你现在负责修复 `receipt-agent-interview` 仓库（工作目录 `/Users/ethan/Documents/GitHub/receipt-agent-interview`）前端走查发现的 8 项缺陷。这些缺陷已由 AI 产品经理用 Orca 内置浏览器逐 Tab 实测确认，证据与代码定位如下。**验收标准极严：任何一项不达标都会被打回重做。**

## 全局约束（违反即打回）

1. **零 Emoji**：所有改动文件（py/js/html/css）不得含任何 Emoji（U+1F300-U+1FAFF, U+2600-U+27BF）。完成后自检：
   `python3 -c "import re,pathlib; pat=re.compile('[\U0001F300-\U0001FAFF\U00002600-\U000027BF]'); print(sum(1 for p in pathlib.Path('demo').rglob('*') if p.suffix in ('.py','.js','.html','.css') for l in p.read_text(errors='ignore').splitlines() if pat.search(l)))"` 必须输出 0。
2. **小步修改**：只改本任务列出的文件与位置，禁止顺手重构无关代码。
3. **测试必须全绿**：`PYTHONPATH=.:demo ~/.pyenv/versions/3.9.6/bin/python -m pytest tests -q` 当前基线 **77 passed**，你的改动后不得少于 77 passed 且 0 failed。若你新增了测试，数量应更多。
4. **不要 commit、不要 push**：改完留在工作区，由审查方验证。
5. 服务正跑在 `http://127.0.0.1:15010`（uvicorn，`PYTHONPATH=.:demo ~/.pyenv/versions/3.9.6/bin/python -m uvicorn app.main:app --port 15010`），可用 `curl` 做接口级验证；重启服务需先 `pkill -f "uvicorn app.main:app --port 15010"`。

---

## U-01（P0 权限泄漏）：staff 可见并进入 admin 专属页面

**现象**：角色切到 staff 后，侧边栏仍显示「引擎与灰测 admin」（`#adminEngineBtn`）和「黄金样本 57」（`#goldenBoardBtn`）按钮；点击黄金样本整页渲染（含「导入 10 张样本」「一键导入 57 张」按钮），仅数据请求 403 停在「加载中」。

**根因**：前端无按角色隐藏逻辑。
- `demo/templates/index.html:100`（adminEngineBtn）、`:105`（goldenBoardBtn）
- `demo/static/js/main.js:8865 initDemoRoleSwitch` 只做了变色（`applyDemoRoleColor:8897` 注释明说 "admin 按钮常驻可见"）
- API 层已有守卫：`demo/app/api_admin.py` 各端点 `require_admin`，403 文案正常

**修复要求**：
- 在 `applyDemoRoleColor`（或新建 `applyRoleVisibility(role)` 并在 `initDemoRoleSwitch` 与页面初始化两处调用）中：
  - role !== 'admin' 时隐藏 `#adminEngineBtn` 与 `#goldenBoardBtn`（`style.display='none'`）；若当前 active tab 是 `tab-engine` 或 `tab-golden`，自动切回 `tab-scan`。
  - role === 'admin' 时恢复显示。
- owner 角色行为：owner 不应看到这两个入口（API 层 owner 也被 403），一并隐藏。

## U-02（P0 数据口径矛盾）：同一库存页两个涨幅数字打架

**现象**：顶部「AI 发现」横幅称「检测到 1 项食材价格异常上涨，预估影响 HK$200」（口径 earliest→latest，本地新鲜菜心 +20%）；同页指标卡「涨价预警数 0」、表内该行无红标（口径 last vs avg30d = 9.1% < 10%）。

**根因**：两套算法并存。
- `demo/app/chains/review_chain.py:95-130`（ai-insights 数据源）：`earliest = prices[0]`，`change_pct = (latest-earliest)/earliest`，阈值 15%
- `demo/app/api_inventory.py:66 _compute_vs_avg_and_anomaly`：`vs = (last - avg30d)/avg30d`，阈值 10%（PRD FR-6 口径：较30天均价 >10% 标红）

**修复要求**：
- 统一为 PRD 口径（last vs avg_30d，阈值 10%）。修改 `review_chain.py` 中 ai-insights 的涨幅计算：取 `db.price_history(sku_id)` 的 `avg_30d/is_anomaly/vs_avg_pct`（可直接复用 `_compute_vs_avg_and_anomaly` 的逻辑或抽公共 helper 放 `demo/app/services/` 下供两边引用，避免循环 import）。
- 「AI 发现」横幅只在 `is_anomaly=True` 的 SKU 上触发；`impact_amount` 改为 `(latest - avg_30d) * current_stock`。
- 阈值常量抽到一处（如 `demo/app/models.py` 加 `PRICE_ANOMALY_THRESHOLD_PCT = 10.0`），inventory 与 review_chain 共用。
- 修复后自检：`curl -s "http://127.0.0.1:15010/api/ai-insights" -H "X-Role: admin"` 的 `alert_count` 必须等于 `curl -s "http://127.0.0.1:15010/api/inventory" -H "X-Role: owner"` 的 `meta.price_anomaly_count`。

## U-03（P1 调试残留）：删除「反馈探针」卡

**现象**：每个 Tab 底部都有「反馈探针（静态可见性校验）」卡片（示例点赞/点踩按钮 + textarea），是开发期给 snapshot 验收用的脚手架。

**位置**：`demo/templates/index.html:1442` `<div class="card" id="feedbackSnapshotProbe">...</div>` 整块删除。

**注意**：真实明细行内的反馈控件（`.feedback-cell`，`appendTableRow`/`appendArcTableRow` 渲染）**必须保留**——那是 FR-8 正式功能。删除前先 grep 确认 `feedbackSnapshotProbe` 无 JS 依赖（`grep -n feedbackSnapshotProbe demo/static/js/main.js` 应无结果；若有引用一并清理）。

## U-04（P1 差异表零信息行）：AI 建议 vs 用户确认表只显示真差异

**现象**：差异表罗列「单位 斤→斤」「单价 40→40」等无变化字段，噪音占多数。

**位置**：渲染差异表的 JS 函数（grep `差异` / `采纳 AI` 定位，约在 `renderDiffTable` 或类似函数，`demo/static/js/main.js` 内搜 `AI 建议 vs 用户确认`）。

**修复要求**：构建行集合时过滤掉「AI 值与当前值语义相等」的行（字符串 trim 后全等，或数值 parseFloat 相等）。若过滤后为空，表格区域显示一行「AI 与当前填写一致，无差异」。后端 diff 数据源不动。

## U-05（P1 付款标记自由文本 → 枚举下拉）

**现象**：「付款标记」是 `textbox`（placeholder：印章 / 手写批注 / 仅签名 / 无），用户可填任意串。

**位置**：`demo/templates/index.html` 搜 `付款标记`（Tab1 表单区，当前是 `<input type="text" id="inpPaymentMark">` 一类）。

**修复要求**：改为 `<select>`，枚举：`无 / 印章 / 手写批注 / 仅签名 / 已付款`（与现有 placeholder 及后端 `payment_mark_from_image` 返回值兼容：空串、`stamp`、`已付款` 均可被选中或映射）。归档弹窗（arc 区）如有同样输入一并统一。保存链路（`save_edited` 的 `payment_mark` 字段）无需改后端。注意读取回显：详情加载时把后端值映射到最近枚举项，未匹配则追加一个临时 option 显示原值（避免丢数据）。

## U-06（P1 供应商变体未归一 + 脏名混入下拉）

**现象**：归档供应商下拉同时出现「新协兴食品贸易有限公司」「新鴻興食品貿易有限公司 (New Hip Hing Foods & Trading Co Ltd)」「新鴻興食品貿易有限公司」，以及历史脏名「（看不清）/（字迹模糊）/（字跡模糊無法辨識）/（字迹无法辨认）」。

**修复要求**（两层）：
- 归一层：在创建/更新 supplier 处做核心词归一——去括号英文名、繁转简比对（可用简单映射表或复用 `ai_registry/tools/smart_splitter` 的 sanitize 思路），命中已有供应商则复用其 id（参考 `demo/app/db.py find_supplier_by_name`，新增 `find_supplier_by_canonical_name`）。对本次数据，「新鴻興…(New Hip Hing…)」与「新鴻興食品貿易有限公司」「新协兴食品贸易有限公司」应归并为一条（保留最早创建者为主档，其余合并：`db.py` 已有 `merge_suppliers`，`demo/app/api_suppliers.py:82` 有 `/api/suppliers/merge` 端点可调用）。
- 展示层：写一个一次性清理脚本 `demo/scripts/cleanup_placeholder_suppliers.py`：把名字匹配 `^(（)?[字看不清无法辨认跡模]+(）)?$` 的占位供应商软停用（active=0，收据保留但 supplier 显示「未识别供应商」聚合项），并在脚本里打印迁移前后对照。跑一遍验证下拉不再出现脏名。
- 下拉渲染处（`openSupplierMenu` 与归档 combobox 数据源）过滤 `active=0`。

## U-07（P2 空态月份误导）：部门花销默认跳最近有数据月

**位置**：Tab4 初始化（`demo/static/js/main.js` 搜 `部门花销` 或 `cost_report` 的加载函数）。

**修复要求**：默认查询月改为「最近有已入库单据的月份」（首次加载时可先调 `GET /api/receipts` 取 `status=approved` 的最大 `receipt_date` 月份；若全库为空才回落当前月）。空态文案改为「该月暂无进货记录，已为你切换到最近有记录的月份：YYYY-MM」样式的人话提示（仅在发生自动切换时显示）。

## U-08（P2 明细表 8 列过载）：反馈列收进弹层

**现象**：明细表每行内嵌 textarea 反馈窗，行高 ~140px，复核效率下降。

**修复要求**：明细表（Tab1 `itemTableBody` 与归档 `arcTableBody` 两处）的「AI 效果反馈」列改为一个「反馈」按钮；点击弹出轻量 modal（复用现有 `.modal-backdrop/.modal-card` 样式，参考 `stocktakeModal` 结构）：内容为 点赞/点踩 二选一 + textarea + 提交按钮 + 状态行，提交逻辑复用现有 `submitRowFeedback`/`submitArcFeedback` 的 fetch（`POST /api/receipt/{id}/feedback`）。行内保留一个已反馈状态小徽标（如「已点赞/已点踩」文本 badge）。列宽从 20%/18% 缩到 ~8%。**后端不动。**

---

## 交付物与自验清单（审查方将逐条复验）

1. `git status --porcelain` 显示且仅显示上述涉及文件的修改；新增文件仅限 `demo/scripts/cleanup_placeholder_suppliers.py`（及可能的公共 helper）。
2. Emoji 扫描 = 0；`pytest tests -q` ≥77 passed 0 failed。
3. curl 自验输出贴在最终回复里：
   - staff 角色下 `orca snapshot`（或说明用 DOM 检查）确认导航不含 引擎与灰测/黄金样本；
   - ai-insights `alert_count` == inventory `meta.price_anomaly_count`；
   - 页面 HTML 中 `feedbackSnapshotProbe` 不存在；
   - 付款标记为 select 且含枚举项；
   - cleanup 脚本执行前后 suppliers 对照。
4. 最终回复格式：按 U-01~U-08 逐项列出 [改动文件:行号] + [自验方式与结果]，任何一项没做到就明说，不许谎报。

## 背景文档（可选阅读，帮助理解口径）

- `artifacts/pm_ux_audit.md`：本次走查原始报告
- `docs/05-AI产品体系与模块Spec/00-产品总纲与PRD主架构/00-AI产品体系总纲与产品PRD主架构.md`：FR-5/FR-6 定义
- `e2e_pm_audit_report.md`：历史审计，勿改动
