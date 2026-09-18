# Issue: 新建餐品与配方 - 餐品分类 select 错位与无删除入口

**来源**: 用户反馈 2026-08-31 验收「餐品与消耗」Tab
**复现入口**: `demo/templates/index.html:2705` `#dishEditModal` → 基本信息网格 `grid-template-columns: repeat(3, 1fr)` → `INPUT #dishModalCategory` 关联 `datalist #dishModalCategoryList`

**现象 1 - 展示错位**:
- `SELECT #dishModalStatus` 高度 `38px` vs 三个 `INPUT (名称/分类/单价/描述)` 高度 `36px`，同一行基线差 2px，视觉错位 `demo/static/css/style.css:670` vs `demo/static/css/style.css:751`
- 三列网格在 `modal max-width:880px` 宽屏下每列 `~268px` 勉强对齐，但无响应式折行（`@media <1024px` 仍保持 `repeat(3,1fr)`），窄屏/小窗 `680px` 时每列挤至 `~202px`、长分类名（如“主食热菜”）与 `datalist` 下拉箭头占位叠加导致溢出与换行不均
- `INPUT[list]` 缺失 `select` 的原生下拉视觉，与 `SELECT` 并列时 affordance 不一致，用户误判为不可选

**现象 2 - 无删除分类**:
- 分类仅为 `dishes.category` 自由文本 `demo/app/db.py:400 DishRow.category` + 前端 `datalist` 动态追加 `demo/static/js/main.js:13302 updateDishCategoryDatalist`，无持久化分类实体表与管理入口
- 现存分类由已创建餐品 `dishLibraryCache.map(c=>category)` 去重聚合，`distinct = ["主食热菜","主菜"]` + 默认 9 项，无法删除历史脏分类（如测试残留“主食热菜”），`hasDeleteUI false` `hasCategoryAdmin false` 已由 `orca eval` 实证
- 无 `DELETE /api/dishes/categories/{name}` 或等效能力

**影响人群**:
高压场景阿叔/阿姨店员：错位增加认知负担，脏分类堆积导致下拉越来越长、选错分类

**验收标准 (AC)**:
1. 错位修复：三列在 `≥900px` 保持等宽，`768-900px` 折为 2 列，`<768px` 折为 1 列；所有 `form-control` 统一 `min-height: 38px` / `height: 38px`，`SELECT` 与 `INPUT` 视觉等高，`snapshot` 量测 `y` 差 ≤1px
2. 人话校验保持：空分类不阻断（分类现有 `*` 为视觉标记，提交时后端允许空，但前端应给“建议选择”而非硬错）
3. 分类可删除：新增「管理分类」入口（BOM 库工具栏或 Modal 内齿轮），列出所有 `distinct` 分类及引用计数，点击删除前 `CustomConfirmModal` 二次确认，已被餐品引用的分类删除时给出“将影响 X 道餐品，清空为‘其他’？”人话提示；删除后 `datalist` 与 `dishCategoryFilter` 同步刷新，无残留选项
4. 权限与回滚：仅 `owner` 可删除分类，`staff` 按钮隐藏；删除操作写 `audit_logs_json` 或前端 `toast` 可回溯
5. 回归：`loadDishesList` / `saveDishModal` / `filterDishBomLibrary` 不回归，`orca` 内置浏览器 `Playwright` 级实测 + 高压低教育 UX 文案检查通过

**关联**: E2E 体验修复方案的 U-10 文案去技术化、U-01 角色隔离
**处置**: 已按四角色流程闭环（2026-08-31）

## 处置记录

1. **QA 验收**（37/37 项浏览器实测）：复现分类错位 2px（SELECT 38px vs INPUT 36px）、无删除/管理入口、grid 390px 横向溢出；另发现 D-1~D-11 共 11 项问题。
2. **Product 定义 AC-1~AC-11** + 4 批有序任务 T-1~T-11。
3. **Dev 实现 T-1~T-11**（main.js / index.html / style.css / api_dishes.py + 测试）。
4. **Reviewer 终审打回**：`onclick="deleteDish(${id}, ${jsStr(name)}...)"` 双引号属性嵌入双引号字面量致按钮全失效（P1）；角色竞态（P2）；replace_with==name no-op（P2）。
5. **Dev 修复打回项**：单引号 onclick + `isOwnerRoleNow()` 同步判定 + replace 同名 400 + 步进按钮等高 + receipt-item-table th 对比度。
6. **Reviewer 复审通过**（approved:true, riskLevel:Low）；**QA Round-2 浏览器复验 10/10**：时区 0-8 点取本地日、1440/850/390 三档折行无溢出、等高 y 差 0、分类管理删除改写「其他」+同步刷新、staff 四类按钮隐藏、停用/删除 onclick 可点、空行跳过、403 内联映射、触控≥38px（最后修步进按钮 min-height 覆盖 44px）。
7. **最终 Dev 收敛**：`.dish-step-btn{min-height:38px;height:38px!important}` + DELETE 分类空名 400 + 测试用例。

**验证基线**：demo/tests 201 passed+1 skip、tests/ 183 passed、test_api_dishes+test_costing_fifo 14 passed、node --check OK、onclick 渲染 harness 6/6 + 16/16、orca 截图留证。
