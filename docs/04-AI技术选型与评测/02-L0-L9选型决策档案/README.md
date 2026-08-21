# 02 · L0–L9 技术路径选型决策档案

> 定位：收据识别技术的**逐级决策档案与实验论证**。  
> 评测依据：163 张真实香港餐厅单据（batch1 测试集）+ 统一评测台（Runner）。

---

## 选型阶梯决策总览

| 阶梯 | 架构路径 | 评测状态 | 决策结论 | 核心指标 | 决策文档 |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **L0** | Tesseract + 正则规则 | [PASS]  已测 | [ALERT]  **否决** | 成功率 3.1% / 无法处理复杂手写版面 | [L0-传统OCR-Tesseract(否决).md](L0-传统OCR-Tesseract(否决).md) |
| **L1** | PaddleOCR + 文本 LLM | [PASS]  已测 | [PASS]  **离线备选** | 成功率 84.0% / 零出网本地部署方案 | [L1-本地PaddleOCR-LLM(备选).md](L1-本地PaddleOCR-LLM(备选).md) |
| **L2** | PaddleOCR + Prompt 调优 | [PASS]  已测 | [WARN]  **可优化** | 成功率 87.7% / 耗时 30.9s | [L2-PaddleOCR-Prompt调优.md](L2-PaddleOCR-Prompt调优.md) |
| **L3** | 商业云 OCR + LLM | [FAIL]  未测 |  **规划中** | Textract / Azure DI | [L3-商业云OCR(规划).md](L3-商业云OCR(规划).md) |
| **L4** | **云端多模态 VLM 直识** | [PASS]  已测 | [PASS]  **定稿冠军** | **成功率 100% / P50 9.0s / 成本 0.37元** | [L4-多模态VLM直识(定稿冠军).md](L4-多模态VLM直识(定稿冠军).md) |
| **L5** | 多模态 + 3 模板路由 | [PASS]  已测 | [ALERT]  **否决** | 成功率 78.5% / 引入两轮调用级联错误 | [L5-多模态3模板路由(否决).md](L5-多模态3模板路由(否决).md) |
| **L6** | 多模态 + 交叉审核 Agent | [PASS]  已测 | [WARN]  **条件启用** | 成功率 82.2% / Token翻倍，仅高疑点触发 | [L6-多模态交叉审核Agent(条件启用).md](L6-多模态交叉审核Agent(条件启用).md) |
| **L7** | 看想分离（OCR 读图 + LLM） | [PASS]  已测 | [ALERT]  **否决** | 成功率 26.4% / 丢失版面空间语义 | [L7-看想分离架构(否决).md](L7-看想分离架构(否决).md) |
| **L8** | 端侧本地 VLM | [WARN]  部分 |  **探索中** | CodeBuddy CLI / 离线大模型 | [L8-端侧本地VLM(探索).md](L8-端侧本地VLM(探索).md) |
| **L9** | 版面感知 OCR-Free | [FAIL]  未测 |  **规划中** | Donut / LayoutLMv3 | [L9-版面感知OCR-Free(规划).md](L9-版面感知OCR-Free(规划).md) |

---

## 四维评测口径

1. **流程时长**：单据端到端 P50 / P95 耗时（ms）。
2. **准确率**：字段级 F1 + 单据级全对率，按 7 种单据形态分桶统计。
3. **Token 消耗**：实测 Input / Output Token 统计。
4. **成本 (HKD/RMB)**：单张 API 调用成本（免费开源模型记 0）。
