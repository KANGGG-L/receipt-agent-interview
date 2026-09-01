# 01 · 组件 Spec · 收据智能采集与 Side-by-Side 复核工作台

> **模块定位**：系统第一道人机协同门户。承载移动端/Web端多模态单据采集、弱光自适应增强、异步任务调度，以及核心的 **Side-by-Side 左右原图对照与生成式预填复核工作台**。  
> **对标 14 步方案**：`step4-用户画像`、`step8-产品方案`、`step11-PRD定稿`、`step13-安全合规`。  
> **实现代码**：[`api_receipts.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/api_receipts.py)、[`templates/index.html`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/templates/index.html) (Tab 1)、[`services/receipt_utils.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/receipt_utils.py)  

---

## 1. 业务场景与用户痛点

### 1.1 场景细分与痛点
- **清晨后厨连拍场景 (Staff)**：
  - 早上 6:00~8:00，供应商货车集中送达。后厨光线昏暗、台面油污、手机镜头常有水雾。
  - 店员需要一键连续拍照 5~20 张单据，系统必须瞬间响应，**不能让店员在现场等待大模型逐张解析完成才能备料**。
- **极速核验场景 (Staff / Owner)**：
  - 手写街市单繁体草书潦草、品名与数量连笔混写（如“大豆油 5L*2樽”）。
  - 传统界面需要反复放大原图、低头在表格敲数字，眼力负担极重。

---

## 2. 核心功能规格 (Functional Spec)

### 2.1 多模态智能采集与图像预处理
1. **多端连拍与批量上传**：
   - 支持移动端调用原生相机连续拍摄（支持 JPG、PNG、HEIC 格式）；
   - 自动读取 EXIF 旋转角并将图片自动扶正，并在此基础上执行**正交自动纠正（90°/180°/270°）**：后端 `services/preprocess.py:146` `_orthogonal_correct` 以四假设（0/90/180/270）行投影方差 + 顶部重心启发式挑最优方向，`90°/270°` 换边 `cv2.rotate` 避免裁切，`180°` 翻转；开关 `preprocess_orthogonal_enabled`（`services/settings_service.py:41` 默认 `true`）不受 `preprocess_enabled` 限制，致命正交错误必纠，无 EXIF 的侧倒/倒置亦可扶正；
   - 客户端进行等比缩放压缩（长边限制 1600px，兼顾清晰度与上传带宽），**前端“旋转 90°”按钮（`static/js/main.js:3425` `bakeCurrentRotation`）将视觉旋转烤入文件**（`canvas` 交换 `w/h` → `toBlob` → `new File` 覆盖 `selectedFile`/`photo.file`，`currentRotation` 归零），避免仅 CSS 导致上传仍是原文件；`applyCropSelection:3210` 在 `currentRotation!=0` 时强制先烤后重选。
2. **弱光与阴影自适应增强 + 小角度纠偏**：
   - 针对后厨暗光环境，服务端在图像进入大模型前执行轻量 CLAHE（对比度受限自适应直方图均衡化）预处理，提升手写笔迹对比度；小角度倾斜（`deskew:273` `±1°` 死区，`±12°` 可还原，`enhance:291`）仍由 `preprocess_enabled`（默认 `OFF`）控制，`tests/test_settings_and_preprocess.py:110` 已覆盖。
3. **异步解耦任务机制**：
   - 上传接口 `POST /api/upload_batch` 创建异步 Job 后立即返回 Batch ID，前端呈现上传进度抽屉，店员可即刻关闭页面。

---

### 2.2 Side-by-Side 左右对照可信核对交互规范

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                   Side-by-Side 左右对照可信核对交互界面（核心 UI 原型）                │
├──────────────────────────────────────────┬─────────────────────────────────────────────┤
│  【左侧：原始单据高亮视窗 (Original Image)】│  【右侧：AI 结构化预填表单 (Verified Form)】│
├──────────────────────────────────────────┼─────────────────────────────────────────────┤
│  ┌────────────────────────────────────┐  │  供应商: [ 新记蔬菜批发 (已匹配 98%)  ▼ ]  │
│  │  2026-08-20  单号: NO.883921     │  │  日  期: [ 2026-08-20 ]  结算: (●)现金 (○)月结 │
│  │ ────────────────────────────────── │  │  ────────────────────────────────────────── │
│  │ 菜心苗 ........... 20斤 × $8.5 = 170 │  │  明细行:                                    │
│  │ ┌────────────────────────────────┐ │  │  1. [菜心苗 ]  [20.0] [斤▼] × [$8.50] = [$170.00]│
│  │ │ 特级生菜 .... 10斤 × $6.0 = 60 │ │◄─┼──► (Hover 联动高亮左侧原图第 2 行切片)      │
│  │ └────────────────────────────────┘ │  │  2. [特级生菜]  [10.0] [斤▼] × [$6.00] = [$60.00] │
│  │ 番茄一箱 ......... 1箱 × $95 = 95  │  │  3. [番茄 (箱)] [ 1.0] [箱▼] × [$95.0] = [$95.00] │
│  │ ────────────────────────────────── │  │  ────────────────────────────────────────── │
│  │ 合计金额: $325.00  [现金收讫章[ALERT] ]   │  │  【算术校验】: [PASS]  数量×单价=金额 100% 吻合  │
│  └────────────────────────────────────┘  │  【价格异动】: [WARN]  菜心苗较30日均价上涨 +12% │
│  [  放大 ] [  旋转 ] [  局部裁剪 ]   │  总金额: HK$ 325.00   [ 付款标记: 现金收讫章 ] │
├──────────────────────────────────────────┴─────────────────────────────────────────────┤
│  [ [FAIL]  标记异常 Flag ]              [  保存草稿 ]              [  老板一键批准 Approve ] │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 关键人机协同交互细节：
1. **视窗联动与画框聚焦 (Visual Anchoring)**：
   - 鼠标悬停右侧明细行，左侧原图自动平滑移动居中，并用黄色半透明高亮框圈出对应位置。
   - 原图视窗支持鼠标滚轮平滑缩放（50%~300%）、原尺寸自适应与 90° 无损旋转（前端 `rotateImg` 烤入文件，后端 `orthogonal_correct` 兜底，双重保障）。
2. **Smart Splitter 智能剥离魔棒 ( 一键解耦)**：
   - 当手写单品名混写规格（如“鲜鸡蛋 30只*3盘”）时，点击行首  魔棒，前端确定性正则自动解耦为：品名 `鲜鸡蛋 30只`、数量 `3.0`、单位 `盘`，并自动倒算单价。
3. **算术不自洽强提示横幅 (Non-Silent Math Warning)**：
   - 若原单手写算错（如 $10 \times 130 = 130$），算术门禁触发红色醒目横幅：“数量×单价计算值为 $1300.00，票面为 $130.00”，提供【采纳计算值】或【保留票面值】双按钮，严禁静默篡改。
4. **内联快捷新建 SKU (Inline SKU Creation)**：
   - 下拉搜索未收录食材时，底部常驻 `+ 快捷新建 SKU`，弹出 3 字段极简弹窗，保存后乐观更新（Optimistic UI），无需跳出核验页面。

---

### 2.3 挽回动作栏（重新解析 / 重拍）

用户对解析结果不满意时，除「编辑保存」「点踩」外，复核工作台在操作栏（「确认上传单据」右侧、点踩按钮左侧）常驻两个挽回按钮，用于区分不满根因——**图拍坏了（输入问题）**还是**模型解析错了（模型问题）**：

| 按钮 | 语义 | 归因 | 后端动作 |
| :--- | :--- | :--- | :--- |
| 重新解析 | 复用**原图**重跑识别管线 | 模型归因 | `POST /api/receipt/{id}/retry`（原单据重跑，不新建单据） |
| 重拍 | 换一张**新图替换原图**并重跑识别管线 | 输入归因 | `POST /api/receipt/{id}/replace-image`（原单据换图重跑） |

#### 关键交互口径

1. **显隐规则**：与点踩按钮同一谓词——「收据识别」Tab 激活且已有识别结果（`ai_prefill.items` 非空、非手工单态、非解析中/待确认态）即**常驻**；与是否点踩无关。解析中隐藏防并发双击（后端 `parsing` 409 兜底）。手工单/无图单隐藏两按钮。
2. **重拍语义 = 原单据换图重跑**：区别于批量侧栏「更改图片」（移除照片回到上传入口、新建单据）——重拍保留原 `receipt_id`、审计履历与反馈，仅替换 `image_path` 并重跑解析；旧图文件保留不删除（可审计），换图动作落 `audit_logs_json` 与 `image_replaced` 埋点（含 `old_image`）。
3. **重拍二次确认**：重拍会覆盖当前解析结果与已编辑内容，点击后先弹确认框（「将用新图替换原图并重新解析，当前识别结果与已编辑内容会被覆盖」），确认后才弹文件选择。
4. **与点踩/编辑保存并存不阻塞**：三个动作互不排斥——用户可先点踩再重拍、重拍后再编辑保存；反馈按单据幂等保留，事件同 `receipt_id` 关联，构成「踩 → 挽回 → 确认」完整闭环（观测口径见 [`11-组件Spec-全链路埋点与体验反馈体系`](../03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md)）。
5. **状态门口径**（`retry` 与 `replace-image` 共用）：
   - 放行 `uploaded / parsed / edited / error`——其中 `edited` 必须放行：解析完成后自动保存（`autoSaveParsedPhoto`）会把状态置为 `edited`，这是自动保存的正常产物，正是常驻挽回按钮的主场景；
   - 拒绝 `parsing`（识别任务在途，防并发重跑）、`approved`（已背书入账，修正走冲销语义，与 `flag` 口径一致）、`flagged`（人工异常须显式处置）；
   - 手工单（`doc_form = 'manual_entry'`）无原图，两动作一律拒绝。

---

## 3. 技术规格与并发控制 (Technical Spec)

### 3.1 乐观锁并发保护 (Optimistic Concurrency Control)
为防止多名店员或老板同时打开同一单据编辑导致互相覆盖，引入 `version` 乐观锁机制：

```python
# 核心提交逻辑
def save_edited_receipt(db, receipt_id: str, payload: dict, expected_version: int):
    current = db.get_receipt(receipt_id)
    if current["version"] != expected_version:
        # 触发 409 状态冲突
        raise HTTPException(
            status_code=409, 
            detail="单据已被他人更新，请刷新页面获取最新内容后再试"
        )
    # 状态转移为 edited，版本号原子递增
    payload["version"] = expected_version + 1
    payload["status"] = "edited"
    db.update_receipt(receipt_id, payload)
```

---

## 4. API 接口契约一览

| 方法 | 路径 | 入参 | 返回 / 行为 | 鉴权角色 |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/receipts/upload` | `file: UploadFile` | 返回 `job_id`, `receipt_id`，创建后台识别任务 | Staff / Owner |
| `GET` | `/api/receipts` | `status, date_range, page` | 单据分页列表，按创建时间倒序 | Staff / Owner |
| `GET` | `/api/receipts/{id}` | `id: str` | 单据详情、原图 URL、JSON 预填项、门禁校验结果 | Staff / Owner |
| `PUT` | `/api/receipts/{id}` | `ReceiptEditPayload, version` | 保存编辑草稿，校验乐观锁，状态变为 `edited` | Staff / Owner |
| `POST` | `/api/receipts/{id}/approve` | `id: str, version: int` | **唯一写库边**：触发库存入库与台账写入 | **Owner 独占** |
| `POST` | `/api/receipts/{id}/flag` | `id: str, reason: str` | 标记可疑单据，状态变为 `flagged`，不入账 | **Owner 独占** |
| `POST` | `/api/receipt/{id}/retry` | 无 body | 复用原图重跑识别（挽回动作「重新解析」）；状态门 `uploaded/parsed/edited/error`，返回 `queued + job_id`（§2.3） | Staff / Owner |
| `POST` | `/api/receipt/{id}/replace-image` | `receipt: UploadFile`（multipart，可携 `force`） | 新图替换原图并重跑识别（挽回动作「重拍」）；同 `retry` 状态门与画质拦截；旧图文件保留，返回 `queued + job_id + image_url`（§2.3） | Staff / Owner |

---

## 5. 异常分支与边界防护 (Edge Cases)

1. **极端反光/全黑图片**：
   - 图像预检若方差极小（平均灰度 < 20 或 > 240），直接拒绝创建任务，提示用户“图片过暗或过曝，请重新拍摄”。
2. **多联复写纸透印 (NCR Leakage)**：
   - 红色或蓝色底联常透出上层单据笔迹，Prompt 强化约束“只提取当前单据最深笔触，忽略底层透光浅色字迹”；右侧界面提供一键“删除多余行”快捷键。
3. **大模型提取超时 (Timeout)**：
   - 异步 Worker 设置 120 秒超时熔断；超时自动重试 1 次，仍超时流转至 `error` 状态并保留原图，用户可直接在 UI 端进行极简手工补录。

---

## 6. 产品决策记录 (Decision Log)

- **D-2026-08-28-1 移除手动「作废」入口、作废态只读**：完整决策记录（背景/口径/埋点克制/取舍）见
  [`03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md`](../03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md) §11。
