# 16 · 综合索引：香港餐饮进货单 OCR 全链路与面试备战总览

> **文档定位**：本模块汇总了进货单 OCR 的全链路架构 Spec、8 大核心 Gap 与零散痛点治理复盘、AI 产品经理面试高频问答与实战金句库，与 `docs/05` 下现有 PRD 及组件 Spec 深度咬合。

---

## 模块文档矩阵导航

| 编号 | 文档名称 | 核心内容定位 |
| :--- | :--- | :--- |
| **Spec 11** | [`11-OCR全链路Gap治理与逐项修复计划.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/11-OCR%E5%85%A8%E9%93%BE%E8%B7%AFGap%E6%B2%BB%E7%90%86%E4%B8%8E%E9%80%90%E9%A1%B9%E4%BF%AE%E5%A4%8D%E8%AE%A1%E5%88%92.md) | 8 大 Gap 逐轮闭环交付计划与实施历史留证 |
| **Spec 12** | [`12-阶段性产品方向回顾与架构校准报告.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/12-%E9%98%B6%E6%AE%B5%E6%80%A7%E4%BA%A7%E5%93%81%E6%96%B9%E5%90%91%E5%9B%9E%E9%A1%BE%E4%B8%8E%E6%9E%B6%E6%9E%84%E6%A0%A1%E5%87%86%E6%8A%A5%E5%91%8A.md) | 对标 FR-1~12 与 NFR-1~6 的产品架构校准审查 |
| **Spec 13** | [`13-OCR全链路Workflow与架构全景Spec.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/13-OCR%E5%85%A8%E9%93%BE%E8%B7%AFWorkflow%E4%B8%8E%E6%9E%B6%E6%9E%84%E5%85%A8%E6%99%AFSpec.md) | 端到端 6 阶段流水线、三层互不信任体系、线性编排 |
| **Spec 14** | [`14-OCR全链路8大Gap与零散问题深度复盘(STAR模式).md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/14-OCR%E5%85%A8%E9%93%BE%E8%B7%AF8%E5%A4%A7Gap%E4%B8%8E%E9%9B%B6%E6%95%A3%E9%97%AE%E9%A2%98%E6%B7%B1%E5%BA%A6%E5%A4%8D%E7%9B%98%28STAR%E6%A8%A1%E5%BC%8F%29.md) | 8 大 Gap + HEIC/SKU 零散问题的 STAR 深度解构 |
| **Spec 15** | [`15-AI产品经理OCR面试高频攻防与实战金句库.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/15-AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86OCR%E9%9D%A2%E8%AF%95%E9%AB%98%E9%A2%91%E6%94%BB%E9%98%B2%E4%B8%8E%E5%AE%9E%E6%88%98%E9%87%91%E5%8F%A5%E5%BA%93.md) | 面试官高频 4 大必问考题、回答结构与降维打击金句 |

---

## 核心技术与业务成果一览表

| 核心专题模块 | 解决的关键业务/技术痛点 | 对应代码与 Prompt 资产 | 核心可量化提升指标 |
| :--- | :--- | :--- | :--- |
| **图像摄入与预处理** | iPhone 拍摄 HEIC 格式在浏览器预览黑屏；横竖拍摄 EXIF 倒置。 | `api_receipts.py`<br>`pillow_heif` + EXIF 自动扶正 | 跨端照片上传成功率 **100.0%**；预览黑屏与倒置率 **0.0%**。 |
| **品名纯净化 (SKU)** | 混入流水号（`有机菜心_1787140420`）导致 SKU 库爆炸与台账混乱。 | `v1_2_0_sku_clean.py`<br>`SmartSplitterTool v1.1.0` | 品名纯净率从 **36.8% 提升至 100.0%**；合法规格防误删率 **100.0%**。 |
| **Gap 1: 印章图层隔离** | 红色「现金收讫 / PAID」与司厨签名误提取为商品明细。 | `v1_2_1_anti_stamp.py`<br>`ItemSanitizerTool v1.0.0` | 印章污染明细率降为 **0.0%**；付款标记识别准确率 **100.0%**。 |
| **Gap 2: 免责条款拦截** | 「货物出门恕不退换」、「TEL:23881234」被误当做商品行。 | `v1_2_2_anti_disclaimer.py`<br>`ItemSanitizerTool v1.1.0` | 免责条款拦截率 **100.0%**；地名商品（九龙酱油）白名单保护 **100.0%**。 |
| **Gap 3: 附加费用解耦** | 整单折让（-$20）、押金（+$40）、运费（+$30）导致算术门禁报错。 | `v1_2_3_anti_fee.py`<br>`math_engine.py` 守恒升级 | 算术门禁误报率降为 **0.0%**；附加费用结构化拆解率 **100.0%**。 |
| **Gap 4: 包装乘数解耦** | `大豆油 5L*2樽` 被错乘为 10 樽，导致库存数量翻倍与单价冲突。 | `v1_2_4_multi_pack.py`<br>`SmartSplitterTool v1.2.0` | 包装规格拆解准确率 **100.0%**；计件单位（樽/罐/支）保护率 **100.0%**。 |
| **Gap 5: 港式日期解析** | 英式/港式 `06/08/2026` 发生美式倒置为 6月8日，造成对账月份错位。 | `v1_2_5_hk_date.py`<br>`DateNormalizerTool v1.0.0` | 港式日期解析准确率 **100.0%**；月份倒置率降为 **0.0%**。 |
| **Gap 6: 划线作废识别** | 验货手写红笔划线拒收黄花鱼被照常入库，造成库存虚增。 | `v1_2_6_strike_notes.py`<br>`ItemSanitizerTool v1.3.0` | 作废商品识别准确率 **100.0%**；验货注记沉淀召回率 **100.0%**。 |
| **Gap 7: 街市花码降权** | 街市花码（`〡〢〣`）模型产生幻觉脑补却自报 0.95+ 高置信度放行。 | `v1_2_7_huama_humility.py`<br>`HuamaEvaluatorTool v1.0.0` | 虚高置信度压降率 **100.0%**；花码单据 100% 触发黄色警示人工复核。 |
| **Gap 8: RAG 防注入** | 供应商记忆与单据备注植入黑客免单指令，大模型面临越权被控风险。 | `v1_2_8_anti_injection.py`<br>`PromptInjectionGuard v1.0.0` | 恶意指令拦截率 **100.0%**；XML 数据沙箱隔离率 **100.0%**。 |

---

## 备战面试推荐学习顺序

1. **第一步（整体架构）**：精读 [`13-OCR全链路Workflow与架构全景Spec.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/13-OCR%E5%85%A8%E9%93%BE%E8%B7%AFWorkflow%E4%B8%8E%E6%9E%B6%E6%9E%84%E5%85%A8%E6%99%AFSpec.md)，掌握三层互不信任体系（Zero-Trust）与为什么选择确定性线性编排；
2. **第二步（项目深挖与踩坑案例）**：精读 [`14-OCR全链路8大Gap与零散问题深度复盘(STAR模式).md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/14-OCR%E5%85%A8%E9%93%BE%E8%B7%AF8%E5%A4%A7Gap%E4%B8%8E%E9%9B%B6%E6%95%A3%E9%97%AE%E9%A2%98%E6%B7%B1%E5%BA%A6%E5%A4%8D%E7%9B%98%28STAR%E6%A8%A1%E5%BC%8F%29.md)，挑选 2-3 个最典型的案例（如 SKU 流水号剥离、折让押金算术升级、花码置信度压降）作为面试深挖亮点；
3. **第三步（回答技巧与金句演练）**：背诵 [`15-AI产品经理OCR面试高频攻防与实战金句库.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86OCR%E9%9D%A2%E8%AF%95%E9%AB%98%E9%A2%91%E6%94%BB%E9%98%B2%E4%B8%8E%E5%AE%9E%E6%88%98%E9%87%91%E5%8F%A5%E5%BA%93.md) 中的 4 大核心金句与标准问答结构。
