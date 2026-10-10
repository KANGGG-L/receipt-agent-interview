# Codebase 全景架构与统一化重构蓝图

> 本文是 [`07-引擎配置高级参数统一展示与组件化重构探索任务.md`](07-引擎配置高级参数统一展示与组件化重构探索任务.md) 的交付物：对发票识别与审核智能体系统（Receipt Agent）全代码库的逆向摸底结果，以及引擎配置「高级参数统一展示 + 组件化」重构的落地蓝图。
>
> 结构：§0 基线与假设校正 → §1 全景架构与端到端数据流 → §2 配置契约层 → §3 网关与多模态协议层 → §4 调度与容灾层 → §5 六卡参数与呈现对比矩阵 → §6 前端组件化重构设计 → §7 前端 JS 事件模型统一方案 → §8 存量测试兼容性与回归防御清单 → §9 债务总表与分期路线。

---

## §0 基线声明与任务书假设校正

### 0.1 基线（revision anchor）

本文所有行号取自**本地工作树当前快照**（2026-10-06 03:42，未提交改动 32 文件 / +6376 -3008 行，HEAD=`7987e1d`）：

| 文件 | 行数 | 备注 |
| :--- | ---: | :--- |
| `demo/app/models.py` | 910 | 会话期间未变动 |
| `demo/app/api_admin.py` | 3909 | 会话期间 +1 行 |
| `demo/app/db.py` | 7845 | — |
| `demo/app/llm.py` | 1364 | 会话期间 +3 行（221 行以后整体下移 3 行） |
| `demo/app/chains/supervisor.py` | 1298 | — |
| `demo/app/chains/extract_chain.py` | 1059 | — |
| `demo/app/chains/audit_chain.py` | 318 | — |
| `demo/app/services/llm_logger.py` | 346 | — |
| `demo/static/js/main.js` | 22189 | 会话期间 +60 行 |
| `demo/templates/index.html` | 4961 | 会话期间 4902 → 4961 → 4962 → 4961 反复变动 |
| `demo/static/css/style.css` | 5404 | — |

**行号漂移警告**：本仓库正在被并行修改（尤其前端在做引擎卡片组件化迁移）。本文所有引用均采用「**符号名 + 行号**」双锚定；行号漂移时以符号名为准。若发现行号与代码不符，请以 `grep -n "<符号名>"` 结果为准。

### 0.2 任务书路径与前提校正

| 任务书假设 | 实际情况 | 证据 |
| :--- | :--- | :--- |
| `demo/app/engine_config.py` | **不存在**；`EngineConfig` 在 `models.py` | `models.py:395` `class EngineConfig(BaseModel)` |
| `demo/app/services.py` | **不存在**；`app/services/` 是包（34 个模块） | `demo/app/services/` |
| `EngineConfigBody` 在 `api_admin.py:147` | 成立 | `api_admin.py:147` |
| `ParseChain` 独立链文件 | **不存在**；解析腿是 `extract_chain.extract_receipt` 内的可选块（`extract_chain.py:694-723`） | `demo/app/chains/` 仅 `supervisor.py` / `extract_chain.py` / `audit_chain.py` |
| `_MAX_IMAGE_SIDE` 在 `llm.py` | 实际在 `extract_chain.py:223`（值 `1000`）；`llm.py:883` 的 `_path_to_data_url` 只是转发 | `extract_chain.py:223` |
| 「统一 LLM 网关在 `services/` 下」 | **未收敛**：网关 1364 行全在 `llm.py`；LLM 调用日志在 `services/llm_logger.py`；图像编码在 `chains/extract_chain.py` | `llm.py` 文件头注释自述 |
| 「其余卡片仍为原生 `<select>`」 | **已过期**：当前 6 卡预设控件**全部**为 Combobox（`name="engine_preset_combobox_<leg>"`） | `index.html:1752/1897/2039/2310/2444/2577` |
| 「测试按钮侵入表单网格」 | **已过期**：6 卡测试按钮已全部收敛到 header `.engine-preset-toolbar` | `index.html:1758/1903/2045/2316/2450/2582` |
| 「灰测审核卡片遗漏 0.0 锁定提示」 | **不成立**：C5 灰测审核卡已有 `readonly value="0.0"` + 绿色硬锁定提示 | `index.html:2511-2513` |
| 「审核/解析卡片删除切片粒度导致 2x2 网格坍塌」 | **不成立**：C2/C3/C5/C6 四个槽位齐全，切片粒度槽用 `disabled readonly` 占位（`[N/A · 纯文本风控]` / `[N/A · 纯文本自愈]`），格子未缺 | `index.html:1961/2103/2506/2639` |
| 「`data-engine-leg` 已存在」 | **不存在**：`index.html` 与 `main.js` 出现次数均为 **0**，事件委托改造尚未开始 | 见 §7 |

结论：任务书列举的「债务现象」中，前端部分**已被并行迭代修掉一半**（Combobox 普及、测试按钮归位、N/A 占位与 0.0 锁定已就位）；真正遗留的是**六卡之间仍存在的 12 项细节分裂**（§5）与**组件化缺失**（705 行重复 HTML，§6）。后端债务（§9）不受前端迭代影响，全部成立。

---

## §1 系统全景架构与端到端数据流转

### 1.1 分层架构

```
┌────────────────────────── 前端（Jinja2 模板 + 原生 JS） ──────────────────────────┐
│ templates/index.html  section#tab-engine(1677)                                    │
│   ├ engineSubView_01  常规三卡 C1 rec(1746) / C2 aud(1882) / C3 parse(2024)        │
│   ├ engineSubView_02  灰测驾驶舱 + adminGreyDrawer(2213) + 三卡 C4/C5/C6           │
│   └ engineSubView_03  模块 D（单据治理与供应商记忆）+ 算法阈值卡                     │
│ static/js/main.js  14016 loadAdminEngineConfig → 14329 collectEngineConfigBody     │
│                                    → (12947 unified | 14510 engine-only) PUT       │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    │ HTTP JSON（无 If-Match；body.version 乐观锁）
┌───────────────────────────────────▼───────────────────────────────────────────────┐
│ api_admin.py  147 EngineConfigBody(extra="forbid")                                 │
│   GET 1295 /engine-config     PUT 1481 /engine-config                             │
│   GET 1379 /routing           GET 1439 /history                                   │
│   PUT 707 /promote            PUT 956 /rollback      POST 1939 /reset             │
│   GET 1095 + POST 1143 + DELETE /engine-presets    PUT 1731 /unified-config       │
│   ├ 404 _normalize_and_sync_engine_updates（掩码回退/枚举归一/双向同步/三组 clamp） │
│   └ 404-535 → db.set_engine_config                                                │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────────────────┐
│ db.py  Legacy: app_settings 单行 JSON key='engine_config'（+ custom_engine_presets）│
│        engine_config_history 表（快照脱敏）                                        │
│        models.py EngineConfig: 395  + 6×clamp_*(246-336) + should_use_grey(884)    │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    │ db.get_engine_config()（每次读库，无进程缓存）
┌───────────────────────────────────▼───────────────────────────────────────────────┐
│ 业务调度  api_receipts.py:925 上传 → 497 _safe_start_recognition_job              │
│           → services/receipt_utils.py:147 后台线程（A/B 实验覆盖 238-260）          │
│           → chains/supervisor.py:731 run_pipeline                                 │
│              ├ 759 should_use_grey → grey_assigned 快照 775                        │
│              ├ 820 resolve_max_retry(rec) → while 821 重试阶梯（MAX_RETRY=3, :50） │
│              │   └ chains/extract_chain.py:475 extract_receipt                     │
│              │        ├ 375 build_prompt（多模态 image_url）→ 625 model.invoke     │
│              │        ├ 694-723 解析腿（纯文本，可选）                              │
│              │        └ 868 _parse_to_receipt（JSON/Schema 自愈）                   │
│              ├ 243 _run_gates（契约 + 算术，零 token）                              │
│              ├ 831 correct_receipt_with_feedback（纯文本修正，不重读原图）          │
│              └ 413 _run_audit → chains/audit_chain.py:105 run_audit                │
│                   ├ text 模式 220-260（零 LLM 调用）                               │
│                   └ vlm/ondemand → 64 build_audit_prompt（多模态）                  │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    │ llm.py:1185/1195/1208 build_*_model → 1268 _build
┌───────────────────────────────────▼───────────────────────────────────────────────┐
│ llm.py  OpenAIChatModel(449) ── Layer1 静态自适应 224/265                          │
│                            └── Layer2 动态 400 自愈 566（MAX_SELF_HEALING_ROUNDS=2）│
│         QwenChatModel(902) ── 无超时、无 Layer1/2 自愈（P6 告警）                    │
│         _calc_price 781/811 + token 归一 → services/llm_logger.py 记录              │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    │
                     db.py  llm_call_log（23 列，3 索引）
                            app_settings.system_audit_json（审计日志）
```

### 1.2 端到端时序（前端表单 → 校验 → 调度 → 多模态调用 → 落库）

```
[管理台保存]
表单 input/change ──► checkEngineDirty(12747) ──► updateEngineStickyFooterStatus(12783)
     │
     └─ 点击保存 ──► validateAllAdminConfigs(12835) ──► collectEngineConfigBody(14329)
                        │  18 单价 + 6×{base_url,api_key,model} + 6×{timeout,retry,reasoning}
                        │  + 12×{max_tokens,temperature} + 2×image_detail + 灰测三开关
                        ▼
        PUT /api/admin/unified-config (1731) 或 PUT /api/admin/engine-config (1481)
                        ▼
        require_admin(D23 早鉴权，auth.py:317) → 乐观锁(body.version vs stored_version)
                        ▼
        _normalize_and_sync_engine_updates(404)：掩码密钥回退 → 枚举归一 → 模型名双向同步
                                               → max_tokens/temperature/image_detail clamp
                        ▼
        SSRF 校验(1558) → 占位符拦截(1449) → clamp_call_timeout(1611) → clamp_retry_rounds(1623)
                        ▼
        EngineConfig(**merged, version=stored+1, config_source="manual")  ← 第二层 clamp 在 db
                        ▼
        db.set_engine_config(5467)：json.dumps 全文写 app_settings['engine_config']
                        ├ db.append_system_audit_log（system_audit_json 追加）
                        └ db.append_engine_config_history（engine_config_history，api_key 已脱敏）

[单据识别]
上传 api_receipts.py:925 → _safe_start_recognition_job(497) → receipt_utils.py:147 后台线程
  → A/B 实验覆盖(238-260，改写 cfg.openai_rec_model/base_url/api_key)
  → supervisor.run_pipeline(731)
       should_use_grey(759) ─► 快照 grey_assigned(775)
       while attempt < max_rounds(821)
         _run_extract(206) → build_recognition_model(1185) → extract_receipt(475)
              build_prompt(375) 组装 image_url(data URL, 最长边 1000, JPEG q85)
              model.invoke → OpenAIChatModel._generate(449)
                   Layer1 静态自适应(265) → POST /chat/completions(timeout=call_timeout)
                   Layer2 400 自愈循环(566) → 失败分类 _classify_http_error(410)
                   record_llm_call → llm_call_log
         _run_gates(243) 契约(contract.py) + 算术(math_engine)
         失败 → 快速反馈 break(1056) / parse 修正(1031) / continue 重跑(983)
       _run_audit(413) → audit_chain.run_audit(105)
       _finalize(1182) → Job 层写 receipt 状态 + 审计日志 + AI 决策履历
```

---

## §2 引擎配置与数据契约层

### 2.1 `EngineConfig`（`models.py:395`）字段全量表

`extra="forbid"`（`models.py:396`）。**六腿全部为扁平前缀字段**，无嵌套 `LegConfig`。命名前缀：常规 `openai_{rec,aud,parse}_`、灰测 `grey_openai_{rec,aud,parse}_`；另有无前缀别名（`rec_*` / `grey_rec_*`）与历史兼容字段。

| 分组 | 字段（个数） | 类型 / 约束 | Clamp | 行号 |
| :--- | :--- | :--- | :--- | :--- |
| 治理 | `config_source` / `version` | `str` / `int` | 无 | 444 / 452 |
| 常规引擎 | `recognition_engine,audit_engine,parse_llm_engine,recognition_model,audit_model,audit_enabled,audit_mode` | `EngineKind` / `str` / `bool`；`audit_mode` 无 Literal | 无 | 461-495 |
| 常规通道 | `openai_{rec,aud}_base_url/_api_key/_model` + 8 个 `api_format` 死字段 | `str` | 无 | 497-513 |
| 超时（7） | `call_timeout_seconds`(60) + 6 腿 `[grey_]{rec,aud,parse}_timeout_seconds` | `Optional[int]`，无 pydantic ge/le | `clamp_call_timeout`（250-261，>60→60） | 620-648 |
| 思考强度（6） | `[grey_]openai_{rec,aud,parse}_reasoning_effort` | `str`，无 Literal；`model_validator` 归一非法值为 `""` | `VALID_REASONING_EFFORTS`(247) | 656-680 |
| 重跑上限（13） | `openai_*_max_retry_rounds`(6) + `[grey_]{rec,aud,parse}_max_retry_rounds`(6) + `max_retry_rounds`(1) | `Optional[int]`，默认 3/1/1，灰测 `None`=继承 | `clamp_retry_rounds`（319-336，[0,10]），运行期 `resolve_max_retry`（339-392）硬钳 [1,10] | 681-736 |
| 解析 LLM | `parse_llm_enabled/engine/model` + `openai_parse_*` | `bool`/`EngineKind`/`str` | 无 | 742-756 |
| 灰测分组 | `grey_enabled,grey_percent,grey_assign_mode,grey_supplier_ids,grey_recognition_*` | `bool`/`int`/`GreyAssignMode`/`list[int]`；`grey_percent` 无 ge/le | PUT 侧 `max(0,min(100,..))`（api_admin.py:471） | 762-772 |
| 高级超参（14） | 6×`max_tokens`；6×`temperature`；2×`image_detail` | `max_tokens` `ge=512,le=16384` 默认 4096；`temperature` `ge=0,le=0.5` 默认 0.0；`image_detail` `str` 默认 `"high"` | `clamp_max_tokens`(284) / `clamp_temperature`(295) / `clamp_image_detail`(307) | 795-820 |
| 供应商名（6，仅展示） | `vendor_name_{rec,aud,parse,grey_*}` | `str` | 无；自动建预设已禁用（api_admin.py:1475-1477） | 824-829 |
| 单价（18） | `price_{input,cached_input,output}_{rec,aud,parse,grey_*}` | `Optional[float] ge=0`，`None`=回落刊例价 | 无（仅 ge=0） | 835-866 |
| 回滚快照 | `rollback_snapshot` | `Optional[dict]` | 无 | 869 |

`EngineKind`（`models.py:174`）删剩 `openai` 单成员，`opencode`/`codebuddy` 由 `db._normalize_legacy_cli_leg` 在构造前改写（注释 172-186）。`GreyAssignMode`（169）：`receipt` / `supplier`。

### 2.2 `EngineConfig` vs `EngineConfigBody` 差异

| 项目 | `EngineConfig`（DB/内部） | `EngineConfigBody`（HTTP 入口，`api_admin.py:147`） |
| :--- | :--- | :--- |
| `extra` | `forbid`（396） | `forbid`（157） |
| pydantic 数值约束 | 6 max_tokens `ge/le`、6 temperature `ge/le`、18 price `ge=0` | **仅 18 price `ge=0`**（261-280）；其余字段无任何 ge/le |
| `model_validator(before)` | **有**（412-552）：消化 14 个 CLI 遗留键、枚举归一、retry 三对双轨同步、reasoning_effort 归一、四组 clamp | 无 |
| `__setattr__` 同步 | **有**（856-880）：`{openai_rec,rec}_max_retry_rounds` 等三对强同步 | 无 |
| 字段集合 | 全量（含 6 枚举 + 8 `api_format` 死字段） | 少 14 个（6 枚举 + 8 api_format），其余 1:1 |
| 默认值 | env 派生 + 业务默认（4096/0.0/3/1/1/`high`） | 一律 `= None`（`config_source: str = None`、`version: int = None` 类型注解与默认值不一致） |

**未知参数拦截行为**：未知字段 → Pydantic v2 `ValidationError` → FastAPI **HTTP 422** `{"detail":[{"type":"extra_forbidden","loc":["body","foo"],...}]}`。`EngineConfig` 侧同语义，但 `model_validator` 先消化 CLI 遗留键，故旧库快照可正常加载。

### 2.3 持久化与接口事务

**真实路由（`api_admin.py`，当前 revision 行号）**

| 方法 | 路径 | 行号 | 说明 |
| :--- | :--- | ---: | :--- |
| GET | `/api/admin/engine-config` | 1295 | 全量回显（`model_dump()` + `perf`），api_key 递归脱敏 |
| GET | `/api/admin/engine-config/routing` | 1379 | 分组瘦身 DTO（模块 A/B/C/D），仅暴露 `has_key` 布尔 |
| GET | `/api/admin/engine-config/history` | 1439 | 分页版本历史（`page_size` ≤ 100，**不含 snapshot**） |
| GET | `/api/admin/engine-config/diff` | 983 | 常规 vs 灰测字段级 diff |
| PUT | `/api/admin/engine-config` | 1481 | 保存（乐观锁 + 归一 + 版本历史） |
| PUT | `/api/admin/engine-config/promote` | 707 | 灰测→常规；推全前写 `rollback_snapshot` |
| PUT | `/api/admin/engine-config/rollback` | 956 | 用 `rollback_snapshot.prev` 覆盖常规组 |
| POST | `/api/admin/engine-config/reset` | 1939 | 复位为 .env 装配（`config_source=auto`） |
| POST | `/api/admin/test-engine-config` | 550 | 保存前自测探测 |
| PUT | `/api/admin/unified-config` | 1731 | 引擎 + 系统设置原子保存 |
| GET/POST/DELETE | `/api/admin/engine-presets[/{key}]` | 1095 / 1143 / 1263 | 自定义网关模版 |

无版本前缀、无资源式 `/engine-config/{id}` 路由。

**GET 读取优先级**：每次 `db.get_engine_config()`（5423-5466）开短会话查 SQLite → 命中 `app_settings['engine_config']` 走 `json.loads` + `EngineConfig(**stored)`（含遗留键改写）；未命中返回 `EngineConfig()`（env 默认装配）。**无进程级缓存、无 WS 广播**：读取方下一次调用即见新值。`hydrate_engine_config_from_env` 只在服务启动与 `reset` 触发。

**PUT 写入链（1481-1720）**：
1. `require_admin`（1482，D23 早鉴权中间件 `auth.py:317-348` 在校验前挡 401）
2. 读 `cfg`（1488）→ 乐观锁（1489-1505）：`If-Match` 头优先、`body.version` 兜底；不匹配 → **409 `VERSION_CONFLICT`**（附 `current_version` / `changed_keys` / `diff`）。`*` 或不可解析 → 不校验（避免代理缓存把保存变永久 409）
3. `_normalize_and_sync_engine_updates(body.model_dump(exclude_none=True), cfg)`（1506 → 404-546）：
   - 掩码密钥回退：`sk-****` 形态 → 回退 DB 真值 → 遍历 `custom_engine_presets` 用 `mask_secret_key` 比对 → 否则保留原值
   - `grey_assign_mode` → `GreyAssignMode(...)`；6 个 engine 字段一律置 `OPENAI`；`grey_percent` 钳 [0,100]
   - `openai_*_model` ↔ `recognition_model`/`audit_model`/`parse_llm_model` 双向同步
   - max_tokens / temperature / image_detail 三组 clamp
4. 从 `model_fields_set` 显式取超时/重试/单价字段（1513-1544），允许 `None` 恢复继承语义（绕开 `exclude_none=True`）
5. `version` / `config_source` 一律 pop（1546-1556）→ 后端自增 + 强置 `manual`
6. SSRF 校验 6 个 base_url（1558-1570）→ 占位符拦截（`_is_placeholder_api_key`，1450）→ `clamp_call_timeout`（1611）→ `clamp_retry_rounds`（1623）
7. `EngineConfig(**{**new_dict, config_source:"manual", version: stored+1})` → `db.set_engine_config(cfg, source="manual")`（5467）
8. `db.set_engine_config` 内**第二层 clamp**（防绕过 PUT 的构造，如回滚旧快照）→ `json.dumps(cfg.model_dump(), ensure_ascii=False)` 写 `app_settings['engine_config']` 单行、**无分字段行**
9. 副作用：`append_system_audit_log`（system_audit_json JSON 数组追加）+ `record_engine_config_history`（1695）
10. 返回体：`{status, data(脱敏), perf_note, version, changed_keys, timeout_advice?, warning?}`

### 2.4 Presets（自定义网关模版）

- 存储：`app_settings['custom_engine_presets']`，JSON 对象 keyed by 安全 key（`[^a-zA-Z0-9_\-]`→`_`，截 64；`api_admin.py:1163`）
- 结构（1217-1243）：`{key,label,base_url,api_key,**model/rec_model/aud_model/parse_model**,timeout_seconds[1,60],max_retry_rounds[0,10],price_input,price_cached,price_output,reasoning_effort,is_custom,updated_at}`
- **`api_key` 明文落库**，脱敏仅发生在响应体（`GET 1095-1116` 逐条 `mask_secret_key`）
- PUT `engine-config` 时若收到掩码密钥，会遍历 presets 反查真值（419-455）
- 与 `EngineConfig` 是**两份独立存储**；presets 内的 `price_*` 只是模版展示值
- `_auto_save_vendor_presets` 已被禁用（1475-1477，函数体只剩 `return`）

### 2.5 历史版本与回滚

- 表 `engine_config_history`（`db.py:671-690`）：`id, version(index), actor, action(String(16)), created_at, snapshot(Text JSON), changed_keys(Text JSON)`；**version 非唯一索引**
- 写入前 `_mask_engine_snapshot`（5558-5569）把 6 个 `*_api_key` 覆盖为掩码 → **history 快照脱敏**
- 动作白名单 `_HISTORY_ACTIONS = ("save","promote","rollback","reset")`（db.py:5551）；`unified-config` 写 `action="unified_save"` **越出白名单**（仅 warning，仍落库）
- 读接口 `list_engine_config_history`（5596）只回 5 个摘要字段，snapshot **无 HTTP 出口**
- diff 事实源 `services/engine_config_diff.py:24-43`，`_norm_value` 把 Enum 取 `.value`，`_side_value` 对 api_key 系脱敏

**Promote（707-952）**：
1. 未启用灰测 → 400
2. 手工枚举约 40 个「推全前常规组字段」写入 `snap.prev`，`meta={timestamp,operator,action}`
3. `new_dict` = 当前 cfg + 灰测→常规映射 + `rollback_snapshot=snap` → `set_engine_config`
4. 再构建 `grey_defaults`（清灰测组、关灰测、模型回 Qwen/GLM 默认）→ **第二次** `set_engine_config`
5. `record_engine_config_history(action="promote")`

**Rollback（956-976 → `services/guardian.py:132-186`）**：
- 无 `rollback_snapshot.prev` → 返回 `(False, None)` → 400
- `new_dict = {**cfg.model_dump(), **prev, "rollback_snapshot": None}` → `EngineConfig(**new_dict)` 走完整归一与 clamp → `version = old+1`（版本前进不回退）→ `set_engine_config(source="manual")`
- **回滚写回 EngineConfig 本体**（非旁路覆盖、非影子表），快照一次性清空（不可二次回滚）
- 管理台回滚与 guardian 自动回滚共用 `perform_engine_rollback`
- 快照兼容依赖 `@model_validator` 消化 `prev` 中的 CLI 遗留字段（models.py:417-420 注释：不摘掉会导致「回滚配置重建失败」静默失效）

---

## §3 LLM 统一网关与多模态协议层

### 3.1 调用入口与分层（`llm.py`）

| 函数 | 行号 | 职责 |
| :--- | ---: | :--- |
| `build_recognition_model` | 1185 | 识别腿工厂 |
| `build_audit_model` | 1195 | 审核腿工厂 |
| `build_parse_model` | 1208 | 解析/修正腿工厂（自带 `side="parse"` 超时解析） |
| `_build` | 1268 | 主装配：解析 timeout/reasoning/max_tokens/temperature/image_detail/price → 构造 `OpenAIChatModel`(449) 或 `QwenChatModel`(902)，按 key 缓存复用（`_MODEL_CACHE`） |
| `_resolve_engine` | 1074 | 通道判定：`engine_kind=="openai"` → OpenAI 兼容；模型名以 `qwen` 开头 → DashScope 原生 SDK（**忽略 cfg 的 base_url/api_key/timeout**，P6 告警 1063-1068） |
| `_lc_to_openai` | 855 | LangChain message → OpenAI wire `messages[]`；只做协议翻译，不做参数自适应、不做图像编码 |
| `check_leg_homogeneity` / `get_timeout_advice` | 1155 / 179 | 同源告警 / 超时建议 |

调用点：`extract_chain.py:520/696/847`、`audit_chain.py:146`、`review_chain.py:40`、`supervisor.py:213`、`vendor_precheck.py:186`、`memory_extract.py:322`。

### 3.2 请求体装配（`llm.py:529-560`）

```python
payload_messages = [_lc_to_openai(m, detail=self.image_detail) for m in messages]
payload = {"model": self.model, "messages": payload_messages,
           "temperature": self.temperature, "max_tokens": max_tokens}
if self.reasoning_effort and self.reasoning_effort in {"low","medium","high","xhigh","max"}:
    payload["reasoning_effort"] = self.reasoning_effort
```

| wire 字段 | 业务来源（常规 / 灰测） | 解析函数 |
| :--- | :--- | :--- |
| `model` | `openai_{rec,aud}_model` → `recognition_model`/`audit_model`（`_openai_model_for`） | 1074 |
| `messages` | 上游 `extract_chain.build_prompt` 注入 | 855 |
| `temperature` | `openai_{rec,parse}_temperature`；**aud 腿恒 0.0** | 148 |
| `max_tokens` | `openai_{rec,aud,parse}_max_tokens`（env `OPENAI_REC_MAX_TOKENS` 可抬升，532-536） | 131 |
| `reasoning_effort` | `[grey_]openai_*_reasoning_effort` | 115 |
| `image_url.detail` | `openai_rec_image_detail` / `grey_openai_rec_image_detail`（**仅 rec 腿有槽**） | 169 |

### 3.3 多模态组装

- 唯一 base64 入口：`extract_chain._pil_jpeg_data_url`（`extract_chain.py:242-281`）——PIL 解码 → 最长边限到 `_MAX_IMAGE_SIDE = 1000`（223，`Image.BILINEAR`）→ `RGB` → JPEG `quality=85, optimize=True` → `data:image/jpeg;base64,`；`llm.py:883` 的 `_path_to_data_url` 只是转发（注释自述「P2 第三份副本收敛」）
- 像素/体积护栏：`image_web.MAX_IMAGE_PIXELS = 120_000_000`（image_web.py:66）；`_MAX_RAW_FALLBACK_BYTES = 8 MiB`（extract_chain.py:226）；非 Web 格式解码失败抛 `ImageDecodeError`
- **三套 JPEG 参数并存**（VLM 输入 q85/BILINEAR、预览 q92/LANCZOS `image_web.py:187`、预处理 q95 `preprocess.py:37`）
- `detail` 取值链：单腿 cfg 字段（仅 rec）→ `clamp_image_detail` 归一到 `{high,auto,low}` → 硬编码默认 `"high"`；`_lc_to_openai` 末段再兜底（874）。`classify_model_profile` **不返回** detail 相关字段，**不存在按模型推断 detail 的逻辑**
- **纯文本模型传图无前置防御**：`_lc_to_openai` 无条件写入 `image_url` block；唯一机制是 Layer 2 的 `strip_image_detail`（只剥 `detail`，**保留图像块**，不转 OCR、不丢弃）

### 3.4 Layer 1 静态前置自适应

`classify_model_profile(model, base_url)`（224-263）画像字段：

| 字段 | 判定谓词 | 是否被消费 |
| :--- | :--- | :--- |
| `is_deepseek_official` | base_url 含 `api.deepseek.com` **或**（model 含 `deepseek` 且 base_url 不含 `siliconflow`/`openrouter`/`aliyun`/`dashscope`） | 是 |
| `is_openai_reasoning` | `last_seg` 或 `m` 以 `o1`/`o3` 开头，或含 `-o1-`/`-o3-`/`-o1`/`-o3`/`/o1`/`/o3` | 是 |
| `is_deepseek_any`、`supports_reasoning_effort`、`supports_temperature`、`max_tokens_limit`、`max_tokens_field` | 派生 | **否（死字段）**——`_adapt_request_payload` 直接用字面量 |

`_adapt_request_payload(payload, profile)`（265-296）严格顺序动作：

| 动作 | 条件 | 变换 | 行号 |
| :--- | :--- | :--- | :--- |
| `strip_reasoning_effort` | `is_deepseek_official` 且 payload 有该键 | `del` | 272-274 |
| `bridge_max_completion_tokens` | `is_openai_reasoning` 且有 `max_tokens` | 键名改写 | 277-280 |
| `clamp_max_tokens_8192` | `elif is_deepseek_official` 且 `max_tokens > 8192` 且为 **int** | 静默改写为 8192（**字符串 `"9000"` 绕过**） | 281-284 |
| `strip_temperature` | `is_openai_reasoning` 且有 `temperature` | `del` | 287-289 |

**调用点唯一**：`OpenAIChatModel._generate`（564）：`payload, static_actions = _adapt_request_payload(payload, profile)`；**`static_actions` 赋值后即丢弃**，不并入 `adapted_params_history`、不落库、不打日志 → **Layer 1 全部变换不可观测**。`QwenChatModel` 不经过 Layer 1。

### 3.5 Layer 2 动态 400 就地自愈

- 循环骨架（566-643）：`MAX_SELF_HEALING_ROUNDS = 2`（**函数内局部变量**，566）→ `range(3)` 最多 3 次 `requests.post`；仅 `status_code == 400` 且 `attempt_idx < 2` 进入自愈
- 签名库 `_PARAM_ERROR_SIGNATURES`（313-350），5 组共 18 条正则（全部 `re.IGNORECASE` + `re.search`）：

| action key | 条数 | 变换 | 行号 |
| :--- | ---: | :--- | ---: |
| `strip_reasoning_effort` | 4 | `del payload["reasoning_effort"]` | 588 |
| `bridge_max_completion_tokens` | 4 | `payload["max_completion_tokens"] = payload.pop("max_tokens")` | 596 |
| `clamp_max_tokens_8192` | 3 | `payload["max_tokens"] = 8192` | 604 |
| `strip_temperature` | 4 | `del payload["temperature"]` | 612 |
| `strip_image_detail` | 3 | `_strip_detail_from_messages`（298-310，深拷贝剥 `image_url.detail`） | 628 |

- **防死循环三重**：① 外层 `range(3)` 硬顶；② 单动作全局幂等（`adapted_params_history` 记录**已做过的动作 key**，585/593/601/609/617）；③ 本轮无动作命中则 `break`（632-643）
- 同一轮内 5 个 `if` **无 else/elif**，可一次命中多个动作
- 未命中的错误 → `_classify_http_error`（410-436）：401/403→`EngineAuthError("auth")`、400→`EngineParamError("param")`、`status>=500` 或 `code∈{"50507"}`→`UpstreamServiceError("upstream")`、其他→`EngineCallError("engine_error")`。`_extract_error_code`（388-408）兼容 JSON `error.code`/`code` 与纯文本正则；`_engine_error_category`（439-443）惰性读取 `category`
- **`adapted_params` 流转**：产生于 566 → `response_metadata["adapted_params"]`（694-699）→ 上游读取（`extract_chain.py:628/766`、`audit_chain.py:172/205`、`supervisor.py:706/839/506`）→ `record_llm_call(adapted_params=...)`（成功 680、HTTP 失败 652；**通用 except 708 未传，自愈历史丢失**）→ `llm_logger.py:314-318` `json.dumps` → `llm_call_log.adapted_params VARCHAR(256)` → 读回 `_parse_json_safe`（db.py:6085/6126）

### 3.6 时效、计量与可观测

- **超时解析** `_resolve_timeout`（73-112）：`cfg.[grey_]{leg}_timeout_seconds` → `cfg.call_timeout_seconds` → env `ENGINE_CALL_TIMEOUT` → `DEFAULT_CALL_TIMEOUT = 30`（48）；每步 `min(v, 60)`（`MAX_CALL_TIMEOUT_SECONDS`，models.py:246）。实际落点 `requests.post(timeout=call_timeout, allow_redirects=False)`（570-575）
- **env 抬升仅识别腿**：`OPENAI_REC_CALL_TIMEOUT`（isdigit 且 >0，硬顶 600，542-547）、`OPENAI_REC_MAX_TOKENS`（无硬顶，535-539）
- **`QwenChatModel` 无超时**：类定义（902-1021）无 `call_timeout` 字段，`client.call(...)`（956-961）不传 timeout；无 Layer 1/2 自愈；error 分类仅识别 `50507`/`500`
- **重试无退避**：Layer 2 命中即 `continue`；`requests.post` 未设 `retries=`（连接级零重试）
- **三层重试乘积**：Layer 2（3 次/请求）× Layer 2.5（aud/parse `for` 循环，默认 1）× Layer 3（rec `while`，默认 3）= 默认 9 次 HTTP/张，拉满 30 次；60s 硬顶 × 9 ≈ 540s，**无端到端时间预算**
- **token 归一** `_normalize_token_usage`（723-773）：`prompt_tokens/completion_tokens/cached_tokens/total_tokens` 四字段，兼容 `input_tokens`/`cache_read_input_tokens`/`prompt_tokens_details.cached_tokens`；非法值按 0 记并 `warning`
- **成本** `_calc_cost_hkd`（811-852）：`(uncached*in + cached*cached + completion*out)/1e6`；单价优先级 自定义 `price_*`（≥0）→ 刊例价档位 `_REC_TOKEN_PRICE_TIERS_FULL`（781-786：`omni` 2.2/0.55/13.3、`vl-flash` 0.15/0.05/1.50、`glm-4.5v` 1.0/0.25/6.0，默认档 = omni）→ `cost==0 且 total>0` 时按 `total*in` 估算
- **`llm_call_log`（db.py:810-834，23 列 / 3 索引）**：`id,receipt_id,tenant_id,trace_id,leg,attempt,engine,model,prompt_messages_json,raw_output,prompt_tokens,completion_tokens,cached_tokens,total_tokens,cost_hkd,cost_estimated,cost_estimated_reason,elapsed_ms,status,error_msg,error_category,adapted_params(VARCHAR(256),无索引),created_at`
  - 成功路径（680-689）：`status="success"`、有 token/cost、带 `adapted_params`
  - HTTP 失败路径（652-662）：`status="error"`、`raw_output=""`、`error_category` 由分类器给、**仍带 `adapted_params`**
  - 通用异常路径（702-712）：`error_category=""`、**不带 `adapted_params`**
- **软失败**：`llm_logger.py:344-346` 内部吞异常仅 warning（消息不含 `receipt_id`）+ 调用侧再包 `try/except: pass` → **双重软失败，日志丢失无痕迹**
- **脱敏**：`sanitize_prompt_messages`（191-268）处理 `sk-*`/Bearer/身份证/敏感性商业数据；图像 base64 剥离为 `image_ref`（sha256+byte_size+mime+source_path）

---

## §4 多智能体业务流转与容灾调度层

### 4.1 四链契约

| 链 | 入口 | 关键输入（EngineConfig 字段） | 输出 |
| :--- | :--- | :--- | :--- |
| Supervisor | `run_pipeline`（`supervisor.py:731`） | `recognition_model` / `grey_recognition_model` / `audit_enabled` / `audit_mode` / 6 组 retry / `parse_llm_enabled` / 18 price / 6 timeout / 灰度四字段 | `data / raw / log / contract_error / audit_result / gate_warnings / status / success / tokens_* / cost_hkd / cost_estimated / elapsed_ms_dict` |
| ExtractChain（识别+解析+修正同文件） | `extract_receipt`（`extract_chain.py:475`） | rec 腿：`openai_rec_*`（temperature/reasoning_effort/max_tokens/image_detail）、`rec_timeout_seconds`、`price_input_rec`；parse 腿：`parse_llm_enabled`、`openai_parse_*`、`parse_timeout_seconds` | `data / raw / error / error_category / elapsed_ms / engine / vendor_context / parse_llm{...} / token_usage / vlm_token_usage / parse_token_usage / cost_hkd / adapted_params / cost_estimated*` |
| AuditChain | `run_audit`（`audit_chain.py:105`） | `audit_enabled / audit_mode / audit_model / openai_aud_max_retry_rounds / aud_timeout_seconds / grey_aud_*` | `overall_consistent / discrepancies[] / corrected_suggestions{} / trust / reason / skipped / mode / audit_ms / token_usage / adapted_params` |
| ParseChain | **无独立文件** | 解析腿 `extract_chain.py:694-723`；修正腿 `correct_receipt_with_feedback`（831） | 同 ExtractChain |

**模态分支事实**：
- 识别腿 = 多模态（`build_prompt` 375 注入 `image_url`，base64 data URL）
- 解析/修正腿 = **纯文本**（`_build_parse_prompt`/`_build_correction_prompt` 只放 `SystemMessage + HumanMessage(str)`；输入 `raw` 来自 VLM 输出文本，**从不回读原图**——833-836 注释「零 VLM 推理」）
- 审核腿 = 分模式：`text`（`run_text_audit`，audit_chain.py:221 起，**零 LLM 调用**，走契约+算术+字段完整性）/ `vlm` / `ondemand`（`_resolve_audit_mode`，audit_chain.py:90，按置信度回落 text）

**审核腿 temperature 强制 0.0 的四处硬编码**（真·强制点，配置无法覆盖）：
`_resolve_temperature` 早返回（`llm.py:154-155`）→ 缓存 key（1333）→ 实例构造（1343/1349/1355）→ 二次 `object.__setattr__`（1363）。`clamp_temperature`（models.py:295）只是次要防线（对 aud 不触达）。

### 4.2 容灾、重试与短路

**重试层级（三层 + 修正腿）**

| 层 | 位置 | 触发 | 上限 |
| :--- | :--- | :--- | :--- |
| Layer 3 整图重跑 | `supervisor.py:821` `while state["attempt"] < max_rounds` | 上一轮 `error` 为上游瞬时故障 | `resolve_max_retry(rec)`，默认 3，硬顶 10 |
| Layer 2.5 单腿就地重试 | `audit_chain.py:159` / `extract_chain.py:707` | `model.invoke` 抛异常（**不区分 category**） | `resolve_max_retry(aud/parse)`，默认 1 |
| Layer 2 参数自愈 | `llm.py:566-643` | HTTP 400 | 2（共 3 次 HTTP） |
| 修正腿 | `extract_chain.py:831-866` | 门禁失败 | **无循环**，单次 `invoke` |

`resolve_max_retry`（`models.py:339-392`）优先级：`grey_openai_{leg}` → `grey_{leg}` → `openai_{leg}` → `{leg}` →（仅 rec）`max_retry_rounds` → 默认（3/1/1）；出口 `min(iv, 10)`、`<1→1`。**`max_retry_rounds` 对 aud/parse 无效**（377-378 的 `leg == "rec"` 限定）。

**五大短路（均为 `break`，不消耗 Layer 3 轮次）**

| 短路 | 判定键 / 谓词 | 谓词行号 | 快照标签（行号） |
| :--- | :--- | ---: | :--- |
| 引擎降级失败 | `fallback_failed` / `fallback_triggered` / `engine` 含 `fallback` | 958 | `engine_fallback`（932） |
| 确定性引擎错误 | `error_category ∈ {"auth","param"}`（`_NON_RETRYABLE_ENGINE_CATEGORIES`，supervisor.py:57） | 104-113 | `deterministic_fail`（970） |
| 本地确定性失败 | `deterministic_error == True`（图片解码失败，`extract_chain.py:418`） | 111 | `deterministic_fail`（970） |
| 输出格式质量 | `error` 非空 **且 `"error_category" not in result`** | 88-110 | `output_reject_fast`（980） |
| 规则门禁快速反馈 | `_is_fast_feedback_gate(gate_err)` 字符串包含 `算术门禁`/`契约`/`总额`/`明细为空` | 75-87 | `gate_reject_fast`（1056） |

唯一 `continue`（985，引擎瞬时故障 → 下一轮）；唯一注入 `retry_feedback` 的路径是 gate_reject（1085）。

**`_is_fast_feedback_gate` 的字符串脆弱性（实测漏判）**：`services/contract.py` 中 6 条业务级文案不含任何匹配子串 → 被判「非快速」→ **多跑一轮 VLM**：
`contract.py:82`（AI 输出不是 JSON 对象）、`:99`（第 N 行数值非法）、`:101`（数量/单价非法）、`:103`（日期格式非法）、`:105`（非法单据形态）、`:107`（供应商为空）。另有 `"算术门禁: "` 前缀强依赖 `supervisor.py:251` 拼接、`"契约"` 前缀仅 Pydantic 分支（`contract.py:124`）具备。

### 4.3 灰度分流与流量路由

`should_use_grey(cfg, supplier_name="", supplier_id=None)`（`models.py:884-910`）实际语义：

```
grey_enabled=False                     → False
grey_supplier_ids 且 supplier_id 命中   → True       （884-897，仅判 ID）
grey_percent <= 0                      → False
assign_mode=SUPPLIER 且 supplier_name 非空
   → md5(supplier_name.encode("utf-8")) hex→int % 100 < pct
否则                                    → random()*100 < pct
```

- **生产调用点唯一**：`supervisor.py:758-759` `should_use_grey(config, supplier_name)` —— **从不传 `supplier_id`** → allowlist 分支永久 skip → **`grey_supplier_ids` 是「可配置、可展示、可持久化、运行时零消费」的死字段**
- **哈希细节**：`hashlib.md5`，输入**未做 trim/casefold**（大小写或空格变化 → 分流漂移）
- **边界**：`SUPPLIER` 模式且 `supplier_name==""` 时静默退化为每单随机，**破坏「同供应商一致命中」语义**；`percent=100` 恒命中；越界值被 `max(0,min(100,int()))` 归一
- **未记录分流依据**：`grey_assigned` 快照（775-777）只记 `assign_mode` 与 `percent`，不记 `supplier_name`/`supplier_id`/哈希桶/随机值
- **灰测腿覆盖优先级**（逐字段风格不一致）：
  - `model / base_url / api_key / price`：灰测字段**整体替换**，无字段级回落
  - `timeout`：`grey_*_timeout_seconds` → `call_timeout_seconds` → env → 30（四层回落）
  - `retry_rounds`：双前缀（`grey_openai_*` → `grey_*`）→ 常规 → 默认（**唯一有 None=继承语义的参数族**）
  - `reasoning_effort / max_tokens / temperature / image_detail`：严格按 `(side, use_grey)` 查表，**无 grey→normal 回落**（灰测字段未设置就取字段默认值）
- **A/B 实验与灰度的叠加顺序**（`receipt_utils.py:235-265` → `supervisor.py:759`）：实验先按 `receipt_id % 100 < target_percent` 分组并**直接改写 `cfg.openai_rec_model/base_url/api_key`**（常规字段）；随后 `use_grey=True` 的单据走 `grey_*` 字段 → **实验覆盖被完全旁路**。实验覆盖范围仅 model/base_url/api_key（不含 temperature/max_tokens/timeout/retry/price），`target_percent=0` → 全部 control、`=100` → 全部 treatment。

### 4.4 生效参数落点汇总（配置字段 → 运行时消费点）

| 配置字段 | 消费点 | 备注 |
| :--- | :--- | :--- |
| `openai_rec_image_detail` / `grey_openai_rec_image_detail` | `llm.py:169-179` → `_lc_to_openai` | 仅 rec 腿 |
| `openai_aud_temperature` / `grey_openai_aud_temperature` | **无**（`side=="aud"` 早返回 0.0） | **运行期死字段** |
| `max_retry_rounds` | `models.py:377-378`（仅 rec 兜底） | 对 aud/parse 无效 |
| `rec_timeout_seconds` 等 6 字段 | `llm.py:87-95` | >60 钳制 |
| `price_{input,cached_input,output}_*`（18） | `_build` 1291-1301 → `_calc_cost_hkd` 811 | `None` → 刊例价档位 |
| `audit_mode` | `audit_chain.py:90`（`_resolve_audit_mode`） | 无 Literal 校验 |
| `grey_supplier_ids` | **无** | 见 §4.3 |
| `vendor_name_*`（6） | **无**（自动建预设已禁用） | 仅管理台展示 |
| `api_format` 系（8） | `models.py` 双向同步兼容层 | Body 已下线，DB/回滚快照仍存 |

---

## §5 六个引擎卡片全量参数与前端呈现对比矩阵

### 5.1 卡片清单

| 编号 | 卡片 id | 容器 class | 行区间 | 行数 | 所属子视图 |
| :--- | :--- | :--- | :--- | ---: | :--- |
| C1 | `adminRecognitionCard` | `engine-card` | 1746-1879 | 134 | `#engineSubView_01` |
| C2 | `adminAuditCard` | `engine-card` | 1882-2021 | 140 | `#engineSubView_01` |
| C3 | `adminParseDrawer` | `engine-card` | 2024-2162 | 139 | `#engineSubView_01` |
| C4 | `adminGreyOpenaiRecBox` | `trx-leg-box` | 2299-2429 | 131 | `#engineSubView_02` → `#adminGreyDrawer` → `#adminGreyLegsContainer`（5 层嵌套） |
| C5 | `adminGreyOpenaiAudBox` | `trx-leg-box` | 2432-2562 | 131 | 同上 |
| C6 | `adminGreyParseBox` | `trx-leg-box` | 2565-2694 | 130 | 同上 |

合计 **705 行**，占 `#tab-engine`（1310 行）的 53.8%。

### 5.2 逐槽位对比矩阵（27 行）

| # | 槽位 | C1 rec | C2 aud | C3 parse | C4 grey_rec | C5 grey_aud | C6 grey_parse |
| ---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 容器 class | `engine-card` | `engine-card` | `engine-card` | `trx-leg-box` | `trx-leg-box` | `trx-leg-box` |
| 2 | 容器 `margin-bottom` | 20px | 20px | 20px | 无 | 无 | 无 |
| 3 | 标题元素 | `div.engine-card-title`(#1565c0, 1748) | `div.engine-card-title`(#2f6b4f, 1885) | `div.engine-card-title`(#6a1b9a, 2027) | 裸 `span`(#0f172a, 2302) | 裸 `span`(2435) | 裸 `span`(2568) |
| 4 | 标题附加 tag | — | — | — | `trx-tag-amber`（Candidate, 2303） | — | — |
| 5 | 卡片级开关 | 无 | `adminAuditEnabled`(1888, onchange=`updateAuditDisabledState`) | `adminParseEnabled`(2030, **无 onchange**) | 无 | `adminGreyAuditEnabled`(2436, onchange=`updateGreyAuditDisabledState`) | `adminGreyParseEnabled`(2569, onchange=`updateGreyParseDisabledState`) |
| 6 | 开关尺寸 | — | padding 2×8 / h28 | padding 2×8 / h28 | — | padding 2×6 / h26 | padding 2×6 / h26 |
| 7 | 预设控件 | Combobox `adminRecPresetSelect`(1752) | `adminAudPresetSelect`(1897) | `adminParsePresetSelect`(2039) | `adminGreyRecPresetSelect`(2310) | `adminGreyAudPresetSelect`(2444) | `adminGreyParsePresetSelect`(2577) |
| 8 | 保存模版按钮文案 | 保存 | 保存 | 保存 | 保存模版 | 保存模版 | 保存 |
| 9 | 测试按钮位置 | header `.engine-preset-toolbar`(1758) | 同(1903) | 同(2045) | 同(2316) | 同(2450) | 同(2582) |
| 10 | 探针状态元素 | `probe-status-tag`(1759) | (1904) | (2046) | (2317) | (2451) | (2583) |
| 11 | 基础 form-grid 首格 colspan | 2 | 2 | 2 | 2 | 2 | 2 |
| 12 | 高级抽屉 `margin-top` | 12px(1787) | 12px(1932) | 12px(2074) | **10px**(2341) | **10px**(2477) | **10px**(2610) |
| 13 | 高级抽屉默认态 | collapsed | collapsed | collapsed | collapsed | collapsed | collapsed |
| 14 | Badge 初始文案 | `[4096 tokens \| high detail \| T=0.0]`(1791) | `[4096 tokens \| T=0.0]`(1936) | `[4096 tokens \| T=0.0]`(2078) | `[4096 tokens \| high detail \| T=0.0]`(2345) | `[4096 tokens \| T=0.0]`(2481) | `[4096 tokens \| T=0.0]`(2614) |
| 15 | 模型特性提示盒 | 有(1796) | 有(1941) | 有(2083) | 有(2350) | 有(2486) | 有(2619) |
| 16 | 思考强度 select | 有(1800) | 有(1945) | 有(2087) | 有(2354) | 有(2490) | 有(2623) |
| 17 | Max Tokens 输入 | 512-16384 step128 | 同 | 同 | 同 | 同 | 同 |
| 18 | 视觉切片粒度 | `<select adminRecImageDetail>` high/auto/low(1817) | `[N/A · 纯文本风控]` disabled readonly(1961) | `[N/A · 纯文本自愈]` disabled readonly(2103) | `<select adminGreyRecImageDetail>`(2371) | `[N/A · 纯文本风控]`(2506) | `[N/A · 纯文本自愈]`(2639) |
| 19 | 温度 readonly | 否 | **是** value=0.0(1966) | 否 | 否 | **是** value=0.0(2511) | 否 |
| 20 | 温度硬锁定绿提示 | — | 有(1968) | — | — | 有(2513) | — |
| 21 | 温度 Warn 文案 | 数字幻觉(1827) | 强制 0.0 高于 0.2(1969) | 数字幻觉(2110) | 同 C1(2381) | 同 C2(2514) | 同 C3(2646) |
| 22 | price-grid class | `price-grid` | 同 | 同 | 同 | 同 | 同 |
| 23 | 单价 label 后缀 | (元/1M) | 同 | 同 | 同 | 同 | 同 |
| 24 | Bench radio 默认 checked | standard(1859) | audit_only(2009) | audit_only(2150) | standard(2415) | audit_only(2555) | audit_only(2687) |
| 25 | calcCost 货币 | ¥ | ¥ | ¥ | ¥ | ¥ | ¥ |
| 26 | calcBreakdown 初始数值 | 2.4k+1.6k+800Out(1873) | 1.0k+0.5k+300Out(2015) | 1.0k+0.5k+300Out(2156) | 2.4k+1.6k+800Out(2429) | 1.0k+0.5k+300Out(2561) | **0.9k+0.6k+300Out**(2693) |
| 27 | 卡片行数 | 134 | 140 | 139 | 131 | 131 | 130 |

### 5.3 结构分裂归因（12 项）

1. **容器 class 分裂**：core 三卡独立挂 `#engineSubView_01` 用 `engine-card`；灰测三卡是 canary matrix 的子格用 `trx-leg-box`，导致两者无法共享同一套外边距/标题样式。
2. **标题元素分裂**：core 用 `div.engine-card-title`（有专属配色 #1565c0/#2f6b4f/#6a1b9a）；grey 用裸 `span`（不继承 title 样式，颜色退化为 #0f172a）。
3. **卡片级开关存在性分裂**：C1/C4 无开关；C3 有 `adminParseEnabled` 但 **onchange 缺失**（唯一不一致）；C2/C5/C6 开关齐全且带置灰联动函数。
4. **开关尺寸分裂**：C2/C3 padding 2×8 / height 28；C5/C6 padding 2×6 / height 26。
5. **高级抽屉 `margin-top` 分裂**：core 12px vs grey 10px。
6. **视觉切片粒度槽分裂**：C1/C4 为可用 `<select>`；C2/C3/C5/C6 为 `disabled readonly` 占位，且占位文案两种（`[N/A · 纯文本风控]` / `[N/A · 纯文本自愈]`）。
7. **温度 readonly 分裂**：C2/C5 硬锁定（readonly + value=0.0 + 绿色提示）；C1/C3/C4/C6 可写。
8. **Badge 模板分裂**：rec 系含 `high detail` 段，aud/parse 系不含——角度正确但与 §6 的统一四段式规范不一致（缺段应显式显示占位而非省略）。
9. **calcBreakdown 数值漂移**：C6 初始文案 `0.9k+0.6k+300Out` 与 C3 的 `1.0k+0.5k+300Out` 不一致（灰测解析腿场景常量未被同步）。
10. **Header 布局**：6 卡已统一 `nowrap`；`calcCost` 货币已统一 ¥；单价 label 后缀已统一（元/1M）。
11. **测试按钮**：6 卡已统一收敛到 header `.engine-preset-toolbar`（任务书所述「侵入 form-grid」已不存在）。
12. **折叠控件与 Badge**：6 卡结构统一（`div.engine-advanced-params-drawer.collapsed` + `div.engine-adv-header[onclick=toggleAdvParams(X)]` + `span.engine-adv-badge`），仅文案与 margin-top 有差。

**右侧悬空留白**：`.engine-card-header`（`style.css:4309`）为 `justify-content:space-between; align-items:center`，`.engine-preset-toolbar`（4324）`flex-shrink:0`。当 title 宽度 + toolbar 宽度 < header 宽度时，中间空隙由 `space-between` 自然产生。**6 卡行为一致，非缺陷**；若要求消除，改为 `justify-content:flex-start; gap:16px` 或给 toolbar `flex:1`。

### 5.4 参数槽位与运行时字段的映射校验（暴露的死槽位）

| 槽位 | 前端是否采集 | 后端是否有字段 | 运行时是否消费 |
| :--- | :--- | :--- | :--- |
| 视觉切片粒度（C2/C3/C5/C6） | 否（占位） | aud/parse **无字段** | 不适用（纯文本腿） |
| 采样温度（C2/C5） | 是（readonly，值恒 0.0） | `openai_aud_temperature` / `grey_openai_aud_temperature` **存在** | **否**（`llm.py:154-155` 早返回 0.0）→ 死字段 |
| 卡片开关（C3 `adminParseEnabled`） | 是 | `parse_llm_enabled` | 是（`_parse_enabled`） |
| 灰测 retry | 是（空=null） | `grey_*_max_retry_rounds` | 是（None=继承常规） |
| 灰测 max_tokens / reasoning_effort / temperature / image_detail | 是 | `grey_openai_*` 对应字段 | 是，但**无 None=继承语义**（空值即取字段默认 4096/""/0.0/"high"）——与 UI 的「继承常规」心理预期不符 |

---

## §6 前端统一组件化重构设计

### 6.1 统一四层参数信息架构

| 层 | 内容 | 控件 | 现状问题 |
| :--- | :--- | :--- | :--- |
| **Tier 1 基础连接层** | Base URL / API Key / 模型名（+ 预设 Combobox 与保存/删除/测试按钮） | text / password / combobox | 已统一 Combobox；但 label 命名漂移（见 §7.4） |
| **Tier 2 容灾调度层** | 调用超时（1-60s）、重跑上限（0-10，灰测空=继承）、卡片级启用开关 | number / select | 开关存在性与 onchange 不一致（C3 缺 onchange）；灰测空值语义与真「继承」不一致 |
| **Tier 3 高级超参 2x2 四宫格** | 思考强度 / Max Tokens / 视觉切片粒度 / 采样温度 | select / number / select / number | 六卡格子数已齐，但占位文案、readonly、Badge 段数三者不统一 |
| **Tier 4 单元经济学与场景测算** | 3 个单价（元/1M）+ 场景 radio + 实时成本与公式 | number / radio / 只读文本 | C6 公式常量漂移；单价与场景常量的「继承」语义未与灰测腿对齐 |

### 6.2 统一 2x2 网格语义占位策略（Tier 3）

统一规则：**四个槽位在 6 卡中恒存在且顺序一致（思考强度 → Max Tokens → 视觉切片粒度 → 采样温度）**；不适用时用统一的 `disabled readonly` 占位控件承载语义，禁止删除格子（删格会导致网格坍塌与右侧留白差异）。

| 引擎类型 | 视觉切片粒度槽 | 采样温度槽 | 占位文案（统一） |
| :--- | :--- | :--- | :--- |
| 多模态 rec（C1/C4） | 可用 `<select>` high/auto/low | 可写（0.0-0.5，step 0.01） | — |
| 纯文本审核（C2/C5） | `disabled readonly` | `readonly value="0.0"` | 切片：`[N/A · 纯文本引擎]`；温度：`[0.0 · 确定性风控硬锁定]` + 说明「评估器必须确定性可复现，运行时强制忽略该配置」 |
| 纯文本解析（C3/C6） | `disabled readonly` | 可写（0.0-0.5） | 切片：`[N/A · 纯文本引擎]` |

落地要求：
1. 占位文案**单一来源**（Macro 参数），现存的两种文案（`纯文本风控`/`纯文本自愈`）收敛为一处配置项；
2. 审核腿温度统一徽章 `[0.0 · 确定性风控硬锁定]`，替代当前「readonly 输入框 + 绿色文字提示」的双实现；
3. 审核腿温度槽的三重语义（前端只读 / 后端字段存在 / 运行时忽略）必须在 UI 上显式声明为「运行期硬锁定」，并在 §9 决定「删除字段」还是「保留但标注忽略」。

### 6.3 Card Header 工具栏收拢

统一为 `title 区 + toolbar 区` 两段式（`display:flex; align-items:center; justify-content:flex-start; gap:16px`，title `flex:0 0 auto`，toolbar `flex:1 1 auto; display:flex; gap:8px; justify-content:flex-end`）：

1. 预设 Combobox（含 dropdown 容器）+ 保存 + 删除（仅当选中用户预设时可见）；
2. 测试按钮 + 探针状态标签；
3. 卡片级启用开关（Tier 2 归位到 header 右侧，避免在基础表单网格里制造特例）；
4. 灰测卡额外保留 `Candidate` tag 作为 toolbar 前导标记，不挂到标题上。

### 6.4 Jinja2 Macro 单点模板源设计

**重复度量化**：六卡 705 行中，逐字重复骨架 **480 行（68.1%）**——Header 6×14、基础 form-grid 6×18、高级抽屉 6×24、单价+calc 6×24；其余 225 行为 id/name/value/颜色/文案差异。

**Macro 签名（建议）**

```jinja
{% macro engine_profile_card(cfg) %}
  {# cfg: 见下表 18 个参数 #}
{% endmacro %}
```

| # | 参数 | 类型 | 必填 | 默认 | 说明 |
| ---: | :--- | :--- | :--- | :--- | :--- |
| 1 | `leg_key` | str | 有 | — | `rec`/`aud`/`parse`/`grey_rec`/`grey_aud`/`grey_parse`；驱动全部 id 派生 |
| 2 | `is_grey` | bool | — | true | 决定容器 class（`trx-leg-box` vs `engine-card`）与抽屉 margin-top（10 vs 12） |
| 3 | `card_id` | str | 有 | — | `adminRecognitionCard` / `adminGreyOpenaiRecBox` … |
| 4 | `title_text` | str | 有 | — | 「识别引擎」等 |
| 5 | `title_color` | str | — | `#0f172a` | core 传 #1565c0/#2f6b4f/#6a1b9a |
| 6 | `use_title_div` | bool | — | true | grey 传 false（当前为裸 span） |
| 7 | `title_tag` | str | — | none | 仅 C4 传 `Candidate` |
| 8 | `leg_kind` | str | 有 | — | `vision` / `text` / `text-audit` → 驱动 Tier 3 占位与温度锁 |
| 9 | `multi_modal` | bool | — | false | true 时渲染切片粒度 `<select>` |
| 10 | `temp_locked` | bool | — | false | true 时温度渲染 `[0.0 · 确定性风控硬锁定]` |
| 11 | `enable_switch` | bool | — | false | C2/C3/C5/C6 true |
| 12 | `switch_onchange` | str | — | none | 统一非空（修 C3 缺 onchange） |
| 13 | `advanced_id` | str | — | `adminAdvBody_${leg}` | 六卡已一致 |
| 14 | `badge_parts` | list | 有 | — | 四段式：tokens / temp / effort / detail（缺段显式占位） |
| 15 | `price_prefix` | str | 有 | — | `adminPrice…Rec` 等 18 单价 id 前缀 |
| 16 | `bench_name` | str | 有 | — | radio `name`，默认选中项作为参数 |
| 17 | `bench_default` | str | — | `standard` | aud/parse 传 `audit_only` |
| 18 | `calc_tokens` | dict | 有 | — | Tier 4 场景常量（in/cached/out），单一来源消除 C6 漂移 |

**可行性结论**：六卡差异均为**标量参数差异**（无结构性差异），Macro 化可行且可把 705 行压到宏定义 ~120 行 + 调用点 6×~12 行；唯一需要额外抽象的是 `engine-card` 与 `trx-leg-box` 的样式分裂（建议统一为 `engine-card`，灰测卡通过 `is_grey` 加修饰类 `engine-card--grey`）。

**迁移路径**：先做「单点渲染 + 保留旧 id」（零行为变化）→ 再统一占位文案与 Badge 段数（前端可见变化，需更新 DOM 断言测试）→ 最后收敛容器 class。第 2、3 步会打破现有 DOM 断言测试，见 §8。

---

## §7 前端 JS 事件模型统一与解耦方案

基线：`demo/static/js/main.js` 22189 行，引擎配置代码区段 **12324-15110（约 2786 行）**。

### 7.1 现状：事件模型与硬编码依赖

**绑定方式**：模板内联 `onclick/oninput/onchange` 为主（携带显式 leg 字符串），辅以 `bindAdminEngineEventsOnce`（13034）与 `bindAdvParamListeners`（13283）两处集中绑定。

**四张静态 leg→id 映射表（同一份信息抄了 4 遍）**

| 表 | 行号 | 字段数 | 内容 |
| :--- | ---: | ---: | :--- |
| `LEG_INPUT_MAP` | 12471-12513 | 5 | base_url / api_key / model / timeout id + label |
| `LEG_PRICE_INPUTS` | 12610-12656 | 5 | in / cached / out id + isAud + label |
| `getAdvInputPrefix` 内部 map | 13112-13121 | 1 | leg → 前缀字符串（`adminRec`/`adminAud`/`adminParse`/`adminGreyRec`/`adminGreyAud`/`adminGreyParse`），**fallback `'admin'+legId` 静默拼错 id** |
| `USER_ENGINE_LEG_MAP` | 13408-13488 | 12 | select / deleteBtn / baseUrl / apiKey / model / timeout / retry / price×3 / reasoningEffort id + title |

**量化**：`getElementById` 全文件约 470 次（引擎区约 155 次）；id 字符串拼接前缀 **15+ 种**（`adminAdvBadge_`、`adminAdvModelHint_`、`adminAdvParams_`、`adminAdvBody_`、`adminAdvToggleText_`、`btnProbe_`、`probeStatus_`、`calcCost_`、`calcBreakdown_`、`bench_`、7 个 `pfx+Suffix` 高级参数后缀、`engineSubView_`/`engineTabErr_`/`engineSubNav_`…）；leg 字面量散布 **约 80 处**（`'rec'` 15 / `'aud'` 13 / `'parse'` 13 / `'grey_rec'` 14 / `'grey_aud'` 13 / `'grey_parse'` 13）；按 leg 分支 ≥3 次的函数 **14 个**（`renderEngineModuleA/C`、`renderEngineOverview`、`switchGreyLegTab`、`bindAdvParamListeners`、`updateAllCostCalculators`、`updateAdvBadge`、`updateModelTraitHints`、`validateAllAdminConfigs`…）。

**`data-engine-leg` 当前出现次数：0**（模板与脚本均为 0）——事件委托改造尚未开始。

### 7.2 现状链路要点（供改造时保持语义等价）

| 能力 | 实现 | 关键常量 |
| :--- | :--- | :--- |
| 高级参数 Badge | `updateAdvBadge(legId)`（13157-13194）：四段 `tokens \| detail \| T=temp \| effort`，**`detail` 段仅 `rec`/`grey_rec` 输出**，缺省 `4096`/`0.0`/`high`/`''`，4 种文案组合，无 N/A 占位分支 | 1 位/2 位小数规则：`temp % 1 === 0 ? 1 : 2` |
| 模型特性提示 | `updateModelTraitHints(legId)`（13197-13267）：DeepSeek 官方 / o1-o3 推理 / 其他 三支，写 4 个元素（`adminAdvModelHint_<leg>`、`pfx+TokensHint`、`pfx+ReasoningHint`、`pfx+TempHint`） | 域名黑名单 5 子串：`aliyun/dashscope/volces/siliconflow/openrouter` |
| 温度告警 | `checkTempWarn(legId)`（13269-13281）：`> 0.2` 显示 `pfx+TempWarn` | 阈值 0.2 硬编码 |
| 成本测算 | `updateEngineCostCalculator(leg)`（12679-12713）：`(in*priceIn + cached*priceCached + out*priceOut)/1e6`；`priceCached` 空 → `priceIn*0.5`；写 `calcCost_<leg>`/`calcBreakdown_<leg>` | `BENCHMARK_TOKENS`（12658-12676）：standard 2400/1600/800、complex 4500/3000/1500、audit_only 1000/500/300 |
| 单腿探测 | `probeSingleEngineLeg(leg)`（12516-12608）：`POST /api/admin/probe-single-engine`，body `{leg_type, leg, base_url, api_key, model_name, model, timeout_seconds}`（**双写兼容**），空超时 → `8.0`；状态写 `probeStatus_<leg>`；4 类失败分支 | 8.0 |
| 脏状态机 | `captureEngineFormSnapshot`（12726）/`checkEngineDirty`（12747）/`updateEngineStickyFooterStatus`（12783）：快照 key 为元素 id（radio 用 name），**api_key 无 mask 感知** | — |

### 7.3 目标模型：`LEG_META` 单表 + `[data-engine-leg]` 事件委托

**命名与元数据单点**（唯一事实源，替代 4 张表 + 15 种拼接前缀）：

```js
const LEG_META = {
  rec:        { kind:'vision',     grey:false, label:'识别引擎',          pfx:'adminRec',           legacyIds:{...} },
  aud:        { kind:'text-audit', grey:false, label:'审核引擎',          pfx:'adminAud',           legacyIds:{...} },
  parse:      { kind:'text',       grey:false, label:'语义解析自愈引擎',   pfx:'adminParse',         legacyIds:{...} },
  grey_rec:   { kind:'vision',     grey:true,  label:'灰测识别引擎',      pfx:'adminGreyRec',       legacyIds:{...} },
  grey_aud:   { kind:'text-audit', grey:true,  label:'灰测审核引擎',      pfx:'adminGreyAud',       legacyIds:{...} },
  grey_parse: { kind:'text',       grey:true,  label:'灰测解析 LLM',      pfx:'adminGreyParse',     legacyIds:{...} },
};
```

**DOM 契约（模板侧）**：
- 卡片根：`data-engine-leg="<leg>"`、`data-leg-kind="vision|text|text-audit"`
- 每个受控元素：`data-field="base_url|api_key|model|timeout|retry|reasoning_effort|max_tokens|image_detail|temperature|price_input|price_cached|price_output"`
- 动作元素：`data-role="adv-toggle|adv-badge|adv-model-hint|probe-btn|probe-status|preset-input|preset-menu|preset-save|preset-delete|cost-display|cost-breakdown|bench-radio|card-switch"`

**JS 侧（3 个委托监听器取代 ~155 次 getElementById）**：

```js
const root = document.getElementById('tab-engine');
root.addEventListener('input',  onEngineFieldEvent);   // 值变化 → dirty + badge + tempWarn + traitHints + cost
root.addEventListener('change', onEngineFieldEvent);   // select / radio / switch
root.addEventListener('click',  onEngineActionEvent);  // toggle / probe / preset 菜单 / 保存删除模版
const legOf = el => el.closest('[data-engine-leg]')?.dataset.engineLeg;
const fieldOf = el => el.dataset.field || el.closest('[data-field]')?.dataset.field;
```

**分派规则**（单个 `switch(meta.kind)` 取代 14 处按 leg 分支）：
- `input/change`：按 `data-field` 映射到「写 dirty、刷新 Badge、刷新温度告警、刷新模型特性提示、刷新成本」五件事，只对声明了该 field 的元素生效；
- `click`：按 `data-role` 分派到 `toggleAdvParams(leg)` / `probeSingleEngineLeg(leg)` / 预设菜单 / 保存删除；
- 预设菜单渲染改为 `data-role="preset-item" data-preset-key="..."`，**删除 `innerHTML + onmousedown=selectEnginePresetItem('${leg}','${safeKey}')`**（13626/13637，当前 `safeKey` 只 escape 单引号，是 XSS 面）。

**Badge 统一纯函数**（消除 4 分支 + 缺段）：

```js
function renderAdvBadge({ tokens, tempStr, effort, detail, kind }) // 段序固定 tokens | detail | T= | effort
// detail 段：kind==='vision' ? value : '[N/A · 纯文本引擎]'
// 温度段：kind==='text-audit' ? '[0.0 · 确定性风控硬锁定]' : `T=${tempStr}`
```

### 7.4 迁移顺序与兼容策略

| 阶段 | 动作 | 兼容性影响 |
| :--- | :--- | :--- |
| S1 | 模板加 `data-engine-leg`/`data-field`/`data-role`，**保留全部现有 id 与内联 onclick** | 零行为变化；测试全绿 |
| S2 | 新增 `LEG_META` + 委托监听器，与旧绑定**双轨并行**（委托用 `dataset.bound` 去重，先只读不写） | 通过快照对比校验等价 |
| S3 | 移除内联 `onclick/oninput/onchange`，删除 4 张静态表与 id 拼接 helper，统一 `getEngineLegDom(leg, field)` | 需要 §8 的 DOM 断言清单同步更新 |
| S4 | 收敛容器 class（`trx-leg-box` → `engine-card--grey`）与命名漂移（parse 系列 `adminParseOpenai*` 反序） | 破坏性最大，最后做 |

**收益**：新增第 7 条引擎腿从「≥20 处代码点」降到「`LEG_META` 一行 + Macro 一次调用」；`getElementById` 从约 470 次降到个位数量级；Badge/成本/提示三条链从「按 leg 分支」变为「按 `data-field` 声明驱动」。

---

## §8 存量测试兼容性分析与回归防御清单

### 8.1 测试资产盘点

| 根目录 | 规模 | 与本次重构相关的主力文件 |
| :--- | ---: | :--- |
| `demo/tests/` | 195 项 | `test_engine_cards_unification_standard.py`(650)、`test_advanced_engine_params.py`(718)、`test_custom_engine_pricing.py`(655)、`test_distributed_retry_limits.py`(262)、`test_image_detail_localization.py`(210)、`test_model_adaptive_params.py`、`test_unified_config_and_probe.py`、`test_upstream_error_classification.py`、`test_engine_config_source.py`、`test_grey_leg_provider_alignment.py`、`test_call_timeout_normalization.py`、`test_cost_token_price_tiers.py`、`test_falsy_zero_passthrough.py`、`test_deterministic_error_no_retry.py`、`test_retry_ladder_convergence.py`、`test_engine_kind_timeout_policy.py`、`test_engine_subnav_pagination_qa.py`；Playwright：`test_playwright_ab_experiment_drawer.py`、`test_playwright_canary_ab_decoupled.py`、`test_playwright_create_ab_experiment_modal.py`、`test_admin_tabs_consolidation_e2e.py` |
| 仓库根 `tests/` | 110 项 | `test_engine_cards_unification_standard.py`（与 demo 版**逐字相同**，650 行）、`test_engine_config_versioning.py`、`test_api_key_mask_and_clear_ux.py`、`test_user_saved_engine_presets.py`、`test_engine_presets_masking.py`、`test_per_engine_timeout_and_reasoning.py`、`test_openai_only_engine_config.py`、`test_ssrf_and_credential_guard.py`、`test_xss_and_audit_sanitization.py` |

**重要发现：统一化重构的验收标准已经存在。** `test_engine_cards_unification_standard.py`（650 行，两份）以 AC-01..AC-16 形式把任务书提出的目标设计全部钉死，且当前模板已按它改造过半（见 §5 的 12 项残余分裂与 §0.2 的过期前提）。因此本次重构的性质是**「补齐 AC 未覆盖的部分（组件化/Macro/事件委托）+ 修掉 12 项细节分裂」**，而不是从零设计 UI 标准。

**实跑验证（2026-10-06，本地工作树）**：`python -m pytest demo/tests/test_engine_cards_unification_standard.py -q` → **17 passed, 16 warnings in 53.74s**（warnings 均为 `main.py` 的 `on_event` DeprecationWarning）。即 AC-01..AC-16 **当前全部通过**：任务书所列前端债务中「原生 select / 测试按钮侵入网格 / 2x2 坍塌 / 灰测卡缺 0.0 锁定」四项**已不成立**，下表「当前模板状态」列的「已达成」皆有该次运行背书。

AC-01..AC-16 摘要（该文件 102-650 行）：

| AC | 钉死内容 | 当前模板状态 |
| :--- | :--- | :--- |
| AC-01 | Header 工具栏组件化，测试按钮移出表单网格 | 已达成（6 卡 `.engine-preset-toolbar`） |
| AC-02 | Tier1/2 基础表单**恒定 5 项**（Base URL / API Key / 模型名 / 调用超时 (秒)+placeholder 60+min1max60 / 重跑上限 placeholder `0 - 10` 或 `继承常规`） | 已达成 |
| AC-03 | 高级抽屉默认 `collapsed`；内部**正好 4 个 form-group**，槽序固定（思考强度/Max Tokens/视觉切片粒度/采样温度） | 已达成 |
| AC-04 | 纯文本腿 Slot 3 = `[N/A · 纯文本风控]`（aud 系）/`[N/A · 纯文本自愈]`（parse 系），`disabled readonly` + 背景 `#f1f5f9` + `cursor:not-allowed` | 已达成 |
| AC-05 | 审核腿温度 `readonly value="0.0"` + 绿色 `#059669` 文案 `[运行时确定性硬锁定] 审核引擎强制维持 0.0 温度，防止校验幻觉`；**运行时** `_resolve_temperature(side="aud")==0.0` 且 `build_audit_model().temperature==0.0` | 已达成（前端 + 后端双层） |
| AC-06 | rec/parse 温度 > 0.2 显示 `[WARN]…数字幻觉…`，默认带 `hide` | 已达成 |
| AC-07 | 18 个单价输入 label 含 `(元/1M)` | 已达成 |
| AC-08/09 | 3 档场景 radio 完整、默认选中正确、初始 `约 ¥0.0000 / 张发票`（严禁 `$`）、公式与精度 | 已达成（C6 `calcBreakdown` 常量漂移见 §5.3-9） |
| AC-10 | 渐进折叠默认收起 + 四段式 Badge 状态流转 | 已达成 |
| AC-11 | 模型特性提示容器完整（顶部提示条 + 3 个独立提示气泡） | 已达成 |
| AC-12 | 24 个 `LEG_INPUT_MAP` DOM id 与 JS 映射 100% 吻合；`OPENAI_PRESETS` 不得复活 | 已达成 |
| AC-13 | 6 卡 Combobox 检索/保存/删除绑定 | 已达成 |
| AC-14 | `btnProbe_{leg}` + `probeStatus_{leg}` × 6 腿绑定 | 已达成 |
| AC-15 | 全仓 0 Emoji 红线 | 已达成 |
| AC-16 | API Key 掩码呈现 | 已达成 |

### 8.2 Zero Regression 硬约束清单（重构不得改动）

**A. 字段名契约（56 项）**

1. **14 个高级参数字段**（`test_advanced_engine_params.py:110-134` `ALL_14_ADV_FIELDS`）：`openai_{rec|aud|parse}_{max_tokens|temperature}` + `openai_rec_image_detail` + `grey_openai_{rec|aud|parse}_{max_tokens|temperature}` + `grey_openai_rec_image_detail`（灰测 aud/parse 无 image_detail 字段）
2. **18 个单价字段**（`test_custom_engine_pricing.py:47-56`）：`price_{input|cached_input|output}_{rec|aud|parse|grey_rec|grey_aud|grey_parse}` — 灰测为**中缀** `price_input_grey_rec`
3. **6 腿命名**：`rec / aud / parse / grey_rec / grey_aud / grey_parse`（顺序敏感）
4. **重试双轨**：`openai_{leg}_max_retry_rounds` + `grey_openai_{leg}_max_retry_rounds` + `{leg}_max_retry_rounds` + `grey_{leg}_max_retry_rounds` 双向同步，且顶层 `adminMaxRetryRounds`（DOM）与 `max_retry_rounds`（仅 rec 兜底）语义不变

**B. DOM id 契约（约 96 项；下文标「双依赖」者为被 ≥2 个测试文件共同依赖的硬约束）**

- 双依赖 `LEG_INPUT_MAP` 24 项：`adminOpenai{Rec|Aud}BaseUrl/ApiKey/Model`、`adminParseOpenai{BaseUrl|ApiKey|Model}`、`adminGreyOpenai{Rec|Aud}*`、`adminGreyParseOpenai*`、`admin{Rec|Aud|Parse|GreyRec|GreyAud|GreyParse}TimeoutSeconds`（AC-12:537-547）
- 双依赖 18 单价：`admin{Grey?}Price{Input|CachedInput|Output}{Rec|Aud|Parse}`（3 文件依赖）
- 双依赖 18 预设控件：`admin{Grey?}{Rec|Aud|Parse}{PresetSelect|SavePresetBtn|DeletePresetBtn}`（2 文件）
- 双依赖 6 探针：`btnProbe_{leg}` / `probeStatus_{leg}`（2 文件）
- 6 重跑上限：`admin{Grey?}{Rec|Aud|Parse}MaxRetryRounds`（`name` 属性亦被断言）
- 6 高级抽屉：`adminAdvParams_{leg}`、`adminAdvBody_{leg}`、`adminAdvBadge_{leg}`、`adminAdvToggleText_{leg}`、`adminAdvModelHint_{leg}`
- 2 视觉切片：`adminRecImageDetail` / `adminGreyRecImageDetail`（option 文案：`high→全切片·高精度`、`auto→自适应`、`low→低开销·粗粒度`，**option 文本禁止 ASCII**）
- E2E 选择器：`#demoRoleSelect`、`#adminEngineBtn`、`#tab-engine`、`#engineSubNav_02`、`#adminGreyDrawer`、`#adminExperimentDrawer`、`.engine-drawer-header/title`、`#adminExp*`、`#createExperimentModal`、`#analyticsBoardBtn`、`#tab-analytics`、`#sec-{telemetry|canary|experiment|eval}`、`#goldenBoardBtn`/`#evalsetReviewBtn`（断言 **不可见**）、`#analyticsExperimentsBody`/`#analyticsGreyBody`/`#goldenBoardBody` 等

**C. 响应键契约**：`status / data / code / msg / detail / perf / effective_call_timeout_seconds / timeout_advice / diffs / promote_snapshot / rollback_snapshot / version / changed_keys / VERSION_CONFLICT`；成功 `{"status":"success","data":…}`、错误 `{"status":"error","code":…,"msg":…}`、422 `{"detail":[{msg,loc,type}]}`。

**D. 语义契约（5 条硬锚 + 其余）**

1. 审核腿温度运行时硬锁 0.0（`test_advanced_engine_params.py:429-451`）
2. 纯文本腿 Slot 3 的两种 N/A 文案 + `disabled readonly`（AC-04:239-280）
3. `response_format` 彻底剥离（不在 `model_fields`、payload 不含；`test_advanced_engine_params.py:322-331/365-371`）
4. 顶层 `adminMaxRetryRounds` 已移除且不得复活（`test_distributed_retry_limits.py:48`）
5. `MAX_CALL_TIMEOUT_SECONDS == 60`（`test_call_timeout_normalization.py:209-215`）
6. 其余：`extra="forbid"` 422；`config_source` manual/auto 语义；掩码回写还原真密钥；falsy-zero 不被 `or` 吞；上游错误 4 类分类；未知 400 不假自愈；自愈轮次上限 2；`_resolve_token_price("")==(2.2,13.3)`；零 Emoji；legacy 端点与 `/unified-config` 共存；presets 无硬编码厂商

**E. 默认值/范围契约**：max_tokens `[512,16384]` 默认 4096；temperature `[0.0,0.5]`；retry `[0,10]`（运行期 `[1,10]`）；call_timeout 上限 60（0/-1 → 30）；DeepSeek 官方 8192；成本默认档 `omni (2.2, 13.3)`。

### 8.3 与统一化重构直接冲突的测试（必须同步改造）

| 测试:行号 | 钉死了什么 | 重构时的同步动作 |
| :--- | :--- | :--- |
| `demo/tests/test_unified_config_and_probe.py:157-183` | legacy `PUT /api/admin/engine-config`、`PUT /api/admin/settings` 必须与 `/api/admin/unified-config` **共存** | 保留 legacy 为 thin wrapper（转发层），不得退役；前端可按 §7 收敛为单一入口，但后端路径必须保留 |
| `demo/tests/test_advanced_engine_params.py:460-475` | 6 个 `adminAdvParams_{leg}` 抽屉 id 与 `collapsed` 正则 | 宏化后 id 仍由 `leg` 派生即通过；若改名为 `data-adv-drawer` 需同步改此断言 |
| `test_engine_cards_unification_standard.py:239-280`（AC-04） | 两种 N/A 文案**分别**为 `[N/A · 纯文本风控]`（aud）与 `[N/A · 纯文本自愈]`（parse） | §6.2 建议的「统一为 `[N/A · 纯文本引擎]`」**与此冲突**。二选一：① 保留两种文案（推荐，语义更准），Macro 用参数承载；② 统一文案则必须同时改 AC-04 断言 |
| `test_engine_cards_unification_standard.py:282-308`（AC-05） | 硬锁定提示文案逐字 `[运行时确定性硬锁定] 审核引擎强制维持 0.0 温度，防止校验幻觉` + 颜色 `#059669` | §6.2 建议的徽章 `[0.0 · 确定性风控硬锁定]` 必须**与现有文案并存**或同步改 AC-05；不得静默替换 |
| `test_engine_cards_unification_standard.py:148-203`（AC-02） | 基础 form-grid **恒定 5 项**、字段顺序、placeholder 值 | §6.3 建议把「卡片级开关」移入 header —— 与「恒定 5 项」不冲突（开关本不在 5 项内），但**不得新增第 6 项** |
| `demo/tests/test_distributed_retry_limits.py:60-73` | 重跑上限 input 的 `name` 属性为 `{openai|grey_openai}_*_max_retry_rounds` | 前端若停止双写 `*_max_retry_rounds`，需确认后端 `resolve_max_retry` 仍有主名字段；`name` 属性是提交契约的一部分 |
| `tests/test_engine_cards_unification_standard.py`（根目录副本） | 与 demo 版逐字相同 | **两份必须同步修改**，否则双份断言分裂 |
| `demo/tests/test_eval_baseline_freeze.py:102-110` | 冻结分母 `samples=33 / confirmed=5 / draft=28 / items_gt=211 / unit_present_gt=164` | **当前 manifest 已漂移为 33 条全 draft** → 该用例用真实 evalset 运行会失败；需按冻结纪律重冻结（换 `frozen_at` 并注明原因）或让用例走 tmp 副本 |

### 8.4 回归防御清单（重构期间与完成后必跑）

| 层级 | 用例/动作 | 防御目标 |
| :--- | :--- | :--- |
| L1 卡片标准 | `test_engine_cards_unification_standard.py`（AC-01..AC-16） | 6 卡对称性、槽位、占位、锁定、徽章、Combobox、探针、掩码、零 Emoji |
| L2 参数契约 | `test_advanced_engine_params.py`、`test_custom_engine_pricing.py`、`test_distributed_retry_limits.py`、`test_call_timeout_normalization.py`、`test_falsy_zero_passthrough.py` | 字段名、clamp 边界、falsy-zero、超时归一 |
| L3 自适应与分类 | `test_model_adaptive_params.py`、`test_upstream_error_classification.py`、`test_retry_ladder_convergence.py`、`test_deterministic_error_no_retry.py` | Layer1/2 行为、错误分类、重试阶梯终止 |
| L4 一致性/来源 | `test_unified_config_and_probe.py`、`test_engine_config_source.py`、`test_engine_config_versioning.py`、`test_user_saved_engine_presets.py`、`test_engine_presets_masking.py` | 双端点共存、乐观锁、预设与掩码 |
| L5 E2E | 4 个 Playwright 文件 | 子视图导航、灰测/A-B 抽屉、模态、大盘可见性互斥 |
| **新增（建议）** | Macro 单点渲染断言（6 卡 DOM 结构同构度）、`data-engine-leg` 覆盖度断言（每卡受控元素均带 `data-field`）、`renderAdvBadge` 纯函数单测（含 N/A 与 0.0 锁定）、成本常量单一来源断言（`calcBreakdown` 与 `BENCHMARK_TOKENS` 同源）、灰测腿「继承」语义断言（grey 字段为空 → 回落常规，当前仅 retry 满足） | 组件化与事件委托的长期防退化 |

### 8.5 评测数据集与 Ground Truth 体系

| 项 | 事实 |
| :--- | :--- |
| manifest | `demo/evalsets/manifest.csv` 34 行（33 样本）；列 `sample_id,image,split,doc_form,layout_type,supplier_id,gt_status,gt_source_model,src_sha1,short_side` |
| 分布 | `split=test` × 33；`gt_status=draft` × 33（**confirmed=0**）；`gt_source_model=deepseek/deepseek-v4.1-flash` × 33；`doc_form`：`printed_delivery_note` 32 + `ncr_handwritten` 1（S090）；`layout_type`：`table` 32 + `freeform` 1；`short_side=3024` × 33 |
| 语料 | `evalsets/receipts/` 163 png vs manifest 33 条 → 抽样比 33/163 ≈ 20%，与 `build_evalset.split_by_doc_form` 的 test ratio 0.20 吻合（train/val 未落盘） |
| GT 生成 | `scripts/gen_gt_candidates.py`：异构原则（GT 模型须与识别腿真异构）→ `deepseek/deepseek-v4.1-flash`；图像预处理短边 ≥1000、长边 ≤1600、JPEG base64；请求体 `temperature=0`、`max_tokens=4096`、`stream=False`、**无 `image_detail` 字段**（分辨率由 data URL 控制）；产物 `gt_status="draft"` |
| GT 校验 | `scripts/validate_gt_consistency.py`：以 **manifest 的 `gt_status` 列为唯一权威**；Rule A 算术 `qty*price==amount`（容差 0.01）、Rule B 三零占位（high）、Rule C 发票残留 `INV/`（大小写不敏感） |
| 基线冻结 | `scripts/freeze_eval_baseline.py`：`{name,frozen_at,split,sample_ids,denominators:{samples,confirmed,draft,items_gt,unit_present_gt},sha256}`；一次性冻结纪律 |
| 评分 | `scripts/run_eval.py`：返回 `accuracy/cer/per_field_accuracy/per_field_f1/field_f1_groups/edit_proxy/evidence_coverage/avg_tokens/p50_latency_ms/gt_status_breakdown/sample_ids`；**全文不引用 `image_detail`/`max_tokens`/`temperature`**——模型参数由调用方注入，评分脚本与引擎参数解耦 |
| **漂移告警** | `demo/tests/test_eval_baseline_freeze.py:102-110` 断言 `confirmed==5 / draft==28`，与当前 33 条全 draft 不符 → 冻结基线与 manifest 已失配（manifest 被回滚为全 draft 后未重冻结） |

**对多模态参数的评测影响**：GT 生成与评分都不依赖引擎侧 `image_detail`；因此 Tier 3「视觉切片粒度」的唯一运行时影响面是**识别腿请求参数与 token 成本**（`_lc_to_openai` 的 `detail` 字段 + 图像分辨率），评测口径不受其直接影响——这降低了统一化重构的风险面（改动 `detail` 相关 UI 不会改变评测分数口径）。

---

## §9 债务总表与分期路线

### 9.1 P0（会静默破坏数据/安全，或造成不可观测的运行时错误）

| # | 问题 | 位置 | 触发条件 | 重构必须处理 |
| ---: | :--- | :--- | :--- | :--- |
| 1 | Layer 1 的 `static_actions` 产生后丢弃，静态参数改写**完全不可观测** | `llm.py:564` | 任何 DeepSeek 官方 / o1-o3 请求 | 初始化 `adapted_params_history = list(static_actions)` 并打日志 |
| 2 | `record_llm_call` 双重软失败，日志丢失无痕迹（warning 不含 `receipt_id`） | `llm_logger.py:344-346` + `llm.py:652/680/708` | 任何 DB/序列化失败 | warning 补 `receipt_id/leg/attempt`；或改异步队列 + 死信表 |
| 3 | `QwenChatModel` 无 HTTP 超时、无 Layer1/2 自愈 | `llm.py:902-1021` | 模型名以 `qwen` 开头且 engine_kind≠openai | 强制走 `OpenAIChatModel` + DashScope 兼容 URL，或加 `threading` 超时 |
| 4 | 三层重试乘积（默认 9 次 HTTP / 张，拉满 30 次），**无端到端时间预算** | `llm.py:566` + `supervisor.py:821` + `models.py:339` | 上游持续 400/瞬时故障 | 引入 pipeline deadline，子层超预算即 fail-fast |
| 5 | `grey_supplier_ids` 全链未接线（配置可写、UI 可显、运行时零消费） | `models.py:895-897` + `supervisor.py:759` | 任何启用 allowlist 的配置 | 补 `supplier_id` 透传，或改 name allowlist，或三处同步删除 |
| 6 | history 快照脱敏 / `rollback_snapshot` 明文两套口径分裂 | `db.py:5558-5569` vs `api_admin.py:727-783` | 推全（promote）后回滚 | 明确为既定不变量并写进契约文档，或统一为一份带权限的快照库 |
| 7 | 掩码密钥回退链可能写空（命中不到 preset 且原值为空则清空密钥） | `api_admin.py:419-455` | 用户提交掩码形态但库内无真值 | 区分「主动清空」与「掩码未命中」（后者保留原值或 400） |
| 8 | 前端双保存入口并存，脏状态清零路径不对称；`version` 走 body 不走 `If-Match` | `main.js:12947` / `14510` / `14338` | 并发编辑、切换保存按钮 | 收敛单一入口 + 迁移到 `If-Match`（后端已支持头优先） |
| 9 | `should_use_grey` 在空 `supplier_name` 下静默退化为随机，破坏「同供应商一致命中」 | `models.py:901-906` | SUPPLIER 模式 + 供应商名为空 | 显式拒绝或校验；并记录分流依据（哈希桶/随机值）到快照 |
| 10 | `MOCK_QWEN_FLASH` mock 路径不打日志、不写 `llm_call_log`，静默返回假数据 | `llm.py:475-518` | 生产误设 env | mock 也走 `record_llm_call(engine="mock")` 或直接禁用 |

### 9.2 P1（阻断合法业务流或显著浪费资源）

| # | 问题 | 位置 | 影响 |
| ---: | :--- | :--- | :--- |
| 11 | `extra="forbid"` 冻结 Body 字段清单，新增参数即 422 | `api_admin.py:157` | 任何新超参（top_p/seed/top_k）无法下发 |
| 12 | `_is_fast_feedback_gate` 字符串子串匹配，**6 条契约文案漏判** | `supervisor.py:75-87` + `contract.py:82/99/101/103/105/107` | 每次漏判多跑一轮 VLM（40s 级浪费） |
| 13 | 业务层重试不区分 `EngineCallError.category`（鉴权错误也重试） | `audit_chain.py:159` / `extract_chain.py:707` | 无效重试 |
| 14 | 快照字段清单手写约 40 项，`promote`/`rollback` 双份维护 | `api_admin.py:727-783` | 新增字段易漏 → 回滚不完整 |
| 15 | 占位符判定两套实现且口径不同 | `api_admin.py:1450` vs `db.py:5637` | 概念漂移 |
| 16 | `unified-config` 写 `action="unified_save"` 越出历史白名单 | `api_admin.py:1886` vs `db.py:5551` | 历史列表按动作渲染落入「未知」分支 |
| 17 | `adapted_params VARCHAR(256)` 无溢出保护、无索引 | `db.py:832/891/898` | 新增长动作名将截断；按「是否自愈过」聚合需全表扫描 |
| 18 | 灰测「继承」语义只覆盖 retry；max_tokens/reasoning_effort/temperature/image_detail **空值即取默认** | `llm.py:115-179` | 灰测腿与常规腿参数静默不一致 |
| 19 | A/B 实验覆盖与灰度分流冲突（灰测启用时实验配置被旁路），且实验只覆盖 model/base_url/api_key | `receipt_utils.py:235-265` | 实验结论失真 |
| 20 | `audit` text 模式静默吞异常（降格为 `skipped`） | `audit_chain.py:217-219` | 审核链路故障不可见 |
| 21 | `correct_receipt_with_feedback` 失败一律返回 `None`，无法区分「未启用/模型故障/返回空」 | `extract_chain.py:831-866` | 排障困难 |
| 22 | 单动作幂等仅靠 `adapted_params_history`，无 backoff；`requests.post` 无连接级重试 | `llm.py:570-575/632-638` | 连续 400 触发上游限流 |
| 23 | 前端 `renderEnginePresetMenuItems` 用 `innerHTML + onmousedown`，`safeKey` 只 escape 单引号 | `main.js:13614-13679` | XSS 面；也是事件委托改造首选替换点 |
| 24 | 前端 leg 字面量散布约 80 处、14 个按腿分支函数、4 张重复映射表 | `main.js`（§7.1） | 新增腿需触达 ≥20 处 |
| 25 | `bindAdvParamListeners` 在 `loadAdminEngineConfig` 中被调用 2 次；`captureEngineFormSnapshot` 与 `loadAdminSettings` 双点调用存在竞态 | `main.js:14041/14081/15580` | 重复绑定/脏状态竞争 |
| 26 | `getAdvInputPrefix` fallback `'admin'+legId` 静默拼错 id | `main.js:13121` | 未知 leg 静默失效 |
| 27 | 评测冻结基线漂移（`confirmed=5/draft=28` vs 当前 33 全 draft） | `test_eval_baseline_freeze.py:102-110` | 基线用例失败；评测分母不可信 |

### 9.3 P2（契约漂移 / 冗余 / 可读性）

28 `openai_aud_temperature` / `grey_openai_aud_temperature` 为运行期死字段（`models.py:798/808` vs `llm.py:151-152`）；29 `vendor_name_*`(6) 无消费方（自动建预设禁用）；30 `api_format` 系(8) 死字段与双向同步兼容层；31 `_auto_save_vendor_presets` 空实现；32 `EngineKind` 枚举只剩单成员；33 `audit_mode` 无 Literal；34 `reasoning_effort` 无 Literal（Body 侧完全无校验）；35 `grey_percent` 无 pydantic 约束、归一职责重复（2 处）；36 `call_timeout_seconds` 默认 60 vs 运行期缺省 30（文案误导）；37 版本自增分散 4 处手工 `+1`；38 `engine_config_history.version` 无唯一约束；39 `MAX_RETRY_LIMIT` 两份定义（`supervisor.py:51` vs `models.py:313`）；40 `supervisor.resolve_max_retry` 无意义转发层；41 `system_audit_json` 单行 JSON 数组无上限无分页；42 `unified-config` 的 `custom_engine_presets` 字段无 SSRF/key 正则校验（可覆盖既有预设）；43 `_ROUTING_LEGS` 与 6 腿字段名硬编码 4 处需同步；44 六张卡片 705 行中 480 行逐字重复（68.1%）无 Macro；45 命名漂移（`adminParseOpenai*` 与 `adminOpenaiRec/Aud*` 反序；aud/parse 的 timeout/retry/maxTokens 无 `Openai` 前缀）；46 `renderEngineOverview` 只显 3 腿（灰测三腿缺席）；47 成本档位表与默认模型名硬编码在 `llm.py`（改价需改代码发版）；48 三套 JPEG 参数并存（q85/q92/q95）；49 `llm.py` 自称「第三份副本已收敛」但实际未收敛；50 `updateModelTraitHints` 域名黑名单 5 子串硬编码；51 `checkTempWarn` 阈值 0.2 硬编码；52 `advanced_engine_params` 里 `adminAdvParams_{leg}` 只覆盖 4 项（rec/aud/parse/grey）与 6 腿探针口径分裂；53 `run_eval` 与引擎参数解耦无显式文档（新增参数易被误认为影响评测）。

### 9.4 分期路线（建议）

| 阶段 | 目标 | 关键动作 | 验收 |
| :--- | :--- | :--- | :--- |
| P0 止血 | 观测与安全 | 债务 1、2、10（Layer1 可观测、日志不静默、mock 显式）；补 `receipt_id` 到软失败 warning | 新增单测：static_actions 落 `adapted_params`；mock 路径写日志 |
| P1 契约收敛 | 后端统一 | 债务 7、15、16、37、38、39、40、42；`extra="ignore"` 或字段同源生成（11）；`If-Match` 前端接入（8） | `test_unified_config_and_probe` / `test_engine_config_versioning` / `test_advanced_engine_params` 全绿 |
| P2 语义补齐 | 调度与分流 | 债务 5、9、13、18、19、20、21、22；`_is_fast_feedback_gate` 改结构化 kind（12）；pipeline deadline（4） | 新增：错判漏判用例、继承语义用例、deadline 用例 |
| P3 前端组件化 | 单点模板 + 事件委托 | §6 Macro（保留旧 id）→ §7 S1/S2/S3；债务 23、24、25、26、44、45、46 | AC-01..AC-16 全绿 + 新增同构度/委托覆盖断言 |
| P4 清尾 | 死字段与文档 | 债务 3、6、14、17、28-36、41、43、47-53；评测基线重冻结（27） | 全量测试 + 冻结基线换代 |

### 9.5 关键结论（一页话）

1. **任务书的前端假设已过半被实现**：AC-01..AC-16 就是本次重构的规格书，Combobox、测试按钮归位、2x2 四宫格、N/A 占位、0.0 锁定均已就位；真正的遗留是 **12 项细节分裂 + 705 行重复 HTML 无 Macro + 事件模型未委托化**（`data-engine-leg` 出现 0 次）。
2. **后端债务与前端迭代正交**，且集中在四处：**可观测缺口**（Layer1 静默、日志双软失败）、**重试预算失控**（三层乘积无 deadline）、**配置死字段/未接线**（aud 温度、`grey_supplier_ids`、`vendor_name_*`、`api_format`）、**契约漂移**（双轨重试字段、双保存入口、两套占位符判定、两套快照脱敏口径）。
3. **重构的最大风险不是前端布局，而是测试契约的广泛锚定**：约 96 个 DOM id、56 个字段名、5 条语义锚被 2-3 个文件共同钉死，其中 `test_engine_cards_unification_standard.py` 存在**两份逐字相同副本**，任何 DOM 改动必须双改。Macro 化必须走「保留旧 id」路线，占位文案与徽章文案的「统一」需同步改 AC-04/AC-05 断言，不可静默替换。
4. **评测体系与引擎参数解耦**（`run_eval` 不引用 image_detail/max_tokens/temperature），因此视觉切片粒度的 UI 重构对评测分数口径零影响——但冻结基线已漂移（`confirmed=5/draft=28` 已不成立），需在 P4 重冻结。

