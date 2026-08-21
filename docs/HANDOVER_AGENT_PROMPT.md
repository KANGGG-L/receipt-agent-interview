    你现在作为一名完全独立的第三方 AI 系统架构师与首席质量审计
  Agent（Independent Principal AI Architect & QA
  Auditor），对当前代码仓（`receipt-agent-
  interview`）开展全链路独立审计。

  在开始前，你对本次治理的具体细节没有任何既定偏见。你需要通过查阅仓库文
  档、分析代码与执行测试，自主完成背景理解、问题真实性评估、修复有效性验
  证以及潜在遗漏缺陷的挖掘。

    ---

    ### 一、文档与业务背景查阅指引 (Documentation Path)

    代码仓内沉淀了完整的香港餐饮进货单 OCR
  与库存中台的产品技术文档，请首先自行查阅以下核心路径建立完整业务认知：

    1. **产品方法论与业务洞察**：
       - `docs/01-产品方案(14步)/`（深入香港后厨调研、7 种单据形态、8
  大业务痛点与大厂 14 步 PRD 全流程）；
       - `docs/README.md`（项目总览与候选人作品集导航）。
    2. **专项方案与技术选型**：
       - `docs/03-专项技术与业务方案/`（17
  个核心业务场景、四层安全沙箱、多租户硬隔离）；
       - `docs/04-AI技术选型与评测/`（L0~L9 算法选型、163
  张真实收据评测与成本模型）。
    3. **产品 PRD 规格说明书与 OCR 体系**：
       - `docs/05-AI产品体系与模块Spec/README.md`（Spec
  总目录与结构导航）；
       - `docs/05-AI产品体系与模块Spec/00-产品总纲与PRD主架构/`（FR-1~12
  功能需求与全局状态机）；
       - `docs/05-AI产品体系与模块Spec/04-
  OCR专项治理与面试攻防体系/`（OCR 6 阶段流水线、8 大 Gap
  复盘、面试攻防与治理计划）。

    ---

    ### 二、本次核心改动位置索引 (Code Changes & Assets)


  本次开发与治理主要涉及以下代码与资产库路径，供你在审计代码实现时查验：

    1. **视觉提示词资产库 (Prompt Registry)**：
       - 路径：`ai_registry/prompts/extract/`
       - 包含版本：`v1_2_0_sku_clean.py` 至 `v1_2_8_anti_injection.py`
  及元数据 `metadata.json`。
    2. **确定性后处理工具库 (Deterministic Tools)**：
       - 路径：`ai_registry/tools/`
       - 包含工具：
         - `smart_splitter/`（流水号剥离与复合包装乘数解耦）；
         - `item_sanitizer/`（印章、免责条款、附加费用与验货注记清洗）；
         - `date_normalizer/`（港式英式日期归一化）；
         - `huama_evaluator/`（街市花码苏州码子检测与置信度硬性压降）；
         - `prompt_injection_guard/`（写时敏感词清洗与 XML
  数据沙箱封装）。
    3. **运行时链路集成与模型层**：
       - `demo/app/chains/extract_chain.
  py`（多阶段确定性后处理管线串联）；
       - `demo/app/models.py`（Pydantic 契约扩展：`is_void`, `actual_qty`,
  `adjustment_notes`, `discount_amount`, `deposit_amount`,
  `delivery_fee`）；
       - `demo/app/services/math_engine.py`（纯 Python 零 Token
  算术守恒核验引擎升级）。
    4. **自动化测试与端到端验收脚本**：
       - `tests/`（包含 `test_sku_cleaning_benchmark.py`、`test_gap1_*.
  py` 至 `test_gap8_*.py` 等 36 个测试用例）；
       - `demo/`（包含 `test_gap1_browser_live.py` 至
  `test_gap8_browser_live.py` 浏览器自动化验收脚本）。

    ---

    ### 三、你的独立审计与核查任务 (Audit Tasks)


  请作为独立审计者，按顺序完成以下四大维度的批判性核查，并出具独立的审计
  结论：

    #### 任务 1：问题真实性与合理性审查 (Problem Authenticity Audit)
    -
  结合香港餐饮供应链实际运作（油麻地果栏手写单、茶餐厅热敏小票、粮油调味
  品复合包装、街市花码等），**自主评估此前定义的 8 大
  Gap（印章污染、免责条款、折让押金、包装乘数、日期倒置、划线作废、花码幻
  觉、RAG注入）以及 SKU 流水号污染是否为真实存在的高频痛点？**
    - 评估此前所提的痛点是否存在过度设计或假设不成立的情况。

    #### 任务 2：执行全量自动化测试与回归验证 (Automated Testing)
    在根目录下运行全量测试套件，核验 36 个测试用例的通过情况与覆盖范围：
    ```bash
    PYTHONPATH=.:demo pytest tests/test_*.py -v

  #### 3. 任务 3：执行端到端浏览器真实环境验收 (Live Browser Acceptance)

  在本地 Web 服务（http://127.0.0.1:15010）就绪状态下，运行各 Gap 的
  Playwright 真实浏览器实测脚本，实地检验 UI
  交互、高亮警示底色、算术门禁拦截与数据提取效果：

    PYTHONPATH=. python -u demo/test_gap1_browser_live.py
    PYTHONPATH=. python -u demo/test_gap2_browser_live.py
    PYTHONPATH=. python -u demo/test_gap3_browser_live.py
    PYTHONPATH=. python -u demo/test_gap4_browser_live.py
    PYTHONPATH=. python -u demo/test_gap5_browser_live.py
    PYTHONPATH=. python -u demo/test_gap6_browser_live.py
    PYTHONPATH=. python -u demo/test_gap7_browser_live.py
    PYTHONPATH=. python -u demo/test_gap8_browser_live.py

  #### 任务 4：批判性盲区挖掘与潜在风险排查 (Critical Blindspot
  Investigation)

  •
  审视当前方案是否存在新的死角或副作用（例如：地名/品牌名保护白名单是否会
  误放行脏数据？花码降权规则是否会误伤正常数字？金额守恒公式是否覆盖了所
  有的税费/运费边界？）；
  • 挖掘是否存在未被发现的第 9 个、第 10 个潜在 Gap；
  • 检查代码、注释、文档是否严格遵守零 Emoji 纪律。
  ──────
  ### 四、交付物要求

  请在完成上述四项审计任务后，以结构化报告形式输出你的**《独立架构与质量
  审计评估报告》**，清晰说明：

  1. 问题真实性裁决（Real vs Synthetic）；
  2. 测试与实测执行数据记录；
  3. 代码实现的严谨性与鲁棒性评分；
  4. 你独立发现的潜在盲区、边缘案例或进一步优化建议。