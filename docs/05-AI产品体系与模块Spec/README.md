# 05 · AI 产品体系与模块规格说明书 (Spec) 总目录

> **模块定位**：本目录归档了 **PGM 香港餐饮 AI 进货收据与库存中台** 的完整产品 PRD 总纲、5 大业务核心组件 Spec、AI 原生感知与 RAG 飞轮 Spec、治理运维与 A/B 实验 Spec，以及 **OCR 全链路专项治理与面试攻防体系**。

---

## 文件夹架构全景图

```
docs/05-AI产品体系与模块Spec/
├── README.md                                          <-- [当前文档] 总目录与快速索引
│
├── 00-产品总纲与PRD主架构/
│   └── 00-AI产品体系总纲与产品PRD主架构.md            <-- 12 大功能需求 (FR-1~12)、6 大非功能指标与全局状态机
│
├── 01-业务核心组件Spec/
│   ├── 01-组件Spec-收据智能采集与Side-by-Side复核工作台.md <-- 弱光连拍、左右比对视窗、SmartSplitter、乐观锁并发
│   ├── 02-组件Spec-实时库存与SKU生命周期中心.md       <-- 核心词归一、Append-Only 进销存台账、加权成本、价格预警
│   ├── 03-组件Spec-供应商协同与月结对账引擎.md       <-- 供应商档案、红蓝印章判定、AP 应付账款、月结总单核销
│   └── 04-组件Spec-部门成本核算与智能分析中心.md       <-- 厨房/水吧分账、环比异动、AI 对话式查账 Agent (粤语/俗称)
│
├── 02-AI原生引擎与RAG飞轮Spec/
│   ├── 05-组件Spec-三层互不信任AI原生感知引擎与编排管线.md <-- 三层 Zero-Trust、线性编排、算术自愈、交叉审核 Agent
│   └── 06-组件Spec-动态RAG与供应商记忆知识飞轮.md     <-- 供应商知识图谱、Chroma 向量沉淀、租户硬隔离、数据飞轮
│
├── 03-治理运维与AB实验Spec/
│   ├── 07-组件Spec-AI治理、多模型灰度发布与评测控制台.md <-- 引擎统一接口、概率/Hash 灰度分流、57 张黄金样本自动化评测
│   ├── 08-组件Spec-安全合规、隐私保护与系统运维保障.md <-- 香港 PDPO 合规、多租户隔离、数据脱敏、不可篡改审计日志
│   ├── 09-组件Spec-AB测试与全链路灰测分流体系.md     <-- 四级分流路由、参数物理隔离、统计显著性检验、快照回滚
│   └── 10-组件Spec-Admin端AB测试与脱敏观测大盘方案.md <-- 数据脱敏中间件、A/B 实时看板、显著性裁决、下钻抽样
│
└── 04-OCR专项治理与面试攻防体系/
    ├── 00-香港餐饮进货单OCR全链路与面试备战总览.md   <-- OCR 专项导航、核心成果一览表与面试通关路径
    ├── 01-OCR全链路Workflow与架构全景Spec.md          <-- 6 阶段流水线、三层互不信任体系、线性 vs 图编排对比
    ├── 02-OCR全链路8大Gap与零散问题深度复盘(STAR模式).md <-- 8 大 Gap + iPhone HEIC/EXIF + SKU 剥离深度复盘
    ├── 03-AI产品经理OCR面试高频攻防与实战金句库.md   <-- 面试官必问 4 大考题、标准回答脚本与降维打击金句
    ├── 04-OCR全链路Gap治理与逐项修复计划.md          <-- 8 大 Gap 逐轮闭环交付计划与实施历史留证
    └── 05-阶段性产品方向回顾与架构校准报告.md         <-- 对标 FR-1~12 与 NFR-1~6 的阶段性产品架构校准审查
```

---

## 核心专题快速直达

1. **想看完整产品 PRD 与需求架构**：直达 [`00-产品总纲与PRD主架构/00-AI产品体系总纲与产品PRD主架构.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/00-%E4%BA%A7%E5%93%81%E6%80%BB%E7%BA%B2%E4%B8%8EPRD%E4%B8%BB%E6%9E%B6%E6%9E%84/00-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E6%80%BB%E7%BA%B2%E4%B8%8E%E4%BA%A7%E5%93%81PRD%E4%B8%BB%E6%9E%B6%E6%9E%84.md)；
2. **想看 OCR 技术架构与 6 阶段流水线**：直达 [`04-OCR专项治理与面试攻防体系/01-OCR全链路Workflow与架构全景Spec.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/04-OCR%E4%B8%93%E9%A1%B9%E6%B2%BB%E7%90%86%E4%B8%8E%E9%9D%A2%E8%AF%95%E6%94%BB%E9%98%B2%E4%BD%93%E7%B3%BB/01-OCR%E5%85%A8%E9%93%BE%E8%B7%AFWorkflow%E4%B8%8E%E6%9E%B6%E6%9E%84%E5%85%A8%E6%99%AFSpec.md)；
3. **想看 8 大 Bad Case 如何解决与量化提升**：直达 [`04-OCR专项治理与面试攻防体系/02-OCR全链路8大Gap与零散问题深度复盘(STAR模式).md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/04-OCR%E4%B8%93%E9%A1%B9%E6%B2%BB%E7%90%86%E4%B8%8E%E9%9D%A2%E8%AF%95%E6%94%BB%E9%98%B2%E4%BD%93%E7%B3%BB/02-OCR%E5%85%A8%E9%93%BE%E8%B7%AF8%E5%A4%A7Gap%E4%B8%8E%E9%9B%B6%E6%95%A3%E9%97%AE%E9%A2%98%E6%B7%B1%E5%BA%A6%E5%A4%8D%E7%9B%98%28STAR%E6%A8%A1%E5%BC%8F%29.md)；
4. **想快速准备 AI 产品经理面试**：直达 [`04-OCR专项治理与面试攻防体系/03-AI产品经理OCR面试高频攻防与实战金句库.md`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/docs/05-AI%E4%BA%A7%E5%93%81%E4%BD%93%E7%B3%BB%E4%B8%8E%E6%A8%A1%E5%9D%97Spec/04-OCR%E4%B8%93%E9%A1%B9%E6%B2%BB%E7%90%86%E4%B8%8E%E9%9D%A2%E8%AF%95%E6%94%BB%E9%98%B2%E4%BD%93%E7%B3%BB/03-AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86OCR%E9%9D%A2%E8%AF%95%E9%AB%98%E9%A2%91%E6%94%BB%E9%98%B2%E4%B8%8E%E5%AE%9E%E6%88%98%E9%87%91%E5%8F%A5%E5%BA%93.md)。
