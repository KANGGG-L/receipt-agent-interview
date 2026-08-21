# Model Context Protocol (MCP) 服务目录

> 本目录遵循 Anthropic / 开源 MCP 标准规范，将系统的核心向量数据库、只读数据库及图像预处理服务标准化为 MCP Tools。

## 纳管服务列表
1. **`chroma_vendor_memory`**：基于租户硬隔离的供应商记忆先验知识检索与回写；
2. **`db_readonly_query`**：餐饮进销存与成本台账的安全只读查询通道；
3. **`receipt_vision_preprocess`**：后厨弱光自适应 CLAHE 增强与切片服务。
