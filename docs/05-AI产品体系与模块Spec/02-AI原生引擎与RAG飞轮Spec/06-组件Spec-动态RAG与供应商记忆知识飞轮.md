# 06 · 组件 Spec · 动态 RAG 与供应商记忆知识飞轮

> **模块定位**：系统的核心数据壁垒与自进化飞轮。承载基于 Chroma 向量数据库的 `VendorMemory` 供应商知识图谱、多租户硬隔离先验检索、人工审批后的事实沉淀回写，以及针对香港餐饮方言、别名与习惯单位的动态 RAG Prompt 注入。  
> **对标 14 步方案**：`step6-技术可行性`、`step8-产品方案`、`step9-AI技术方案`、`step13-安全合规`。  
> **实现代码**：[`services/rag.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/rag.py)、[`prompts/extract/`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/prompts/extract/)  

---

## 1. 业务背景与设计动机

### 1.1 香港餐饮方言与特定供应商“暗语”
香港餐饮行业长期存在特定供应商的私有表达习惯：
- **同物异名**：“通菜 / 蕹菜 / 空心菜”、“生菜 / 玻璃生菜”、“淡奶 / 黑白奶”；
- **特定习惯包装**：“大豆油 1樽 (实际为 5L)”、“鲜鸡蛋 1盘 (实际为 30只)”、“菜心 1扎 (实际为 5斤)”；
- **手写极简缩写**：“黑牛 (黑椒牛柳)”、“茄牛 (番茄牛肉)”。

通用大模型如果脱离具体供应商上下文，极难准确推断其标准品名与单位换算。**必须让系统具备“随着单据录入越多，对这家店与这家供应商越了解”的持续自学习能力**。

---

## 2. VendorMemory 知识图谱架构

```mermaid
flowchart TD
    subgraph "1. 知识沉淀飞轮 (Write-Back Pipeline)"
        Approve[老板审批通过单据 Approve] --> ExtractFacts[提取最终真实数据<br/>供应商名 + 品名 + 规格单位]
        ExtractFacts --> FormatMemory[构建知识文本片段<br/>VendorFact(supplier, aliases, units, layout)]
        FormatMemory --> ChromaWrite[(Chroma 向量库<br/>租户硬隔离 Collection)]
    end

    subgraph "2. 识别动态注入 (Inference RAG Pipeline)"
        NewReceipt[新进货单据上传] --> GuessSupplier[轻量预检供应商候选名]
        GuessSupplier --> VectorQuery[向量相似度检索 Top-K 相关记忆]
        VectorQuery --> BuildPrompt[组装动态 Prompt 注入 &lt;vendor_context&gt;]
        BuildPrompt --> VLMModel[VLM 模型精准结构化抽取]
    end

    ChromaWrite -. 持续更新 .-> VectorQuery
```

---

## 3. 核心功能规格 (Functional Spec)

### 3.1 供应商记忆四维沉淀模型
每次单据完成人工背书（Approve）时，系统提炼并沉淀 4 个维度的记忆：
1. **别名映射 (Alias Mapping)**：手写单缩写名称 $\to$ 门店标准 SKU 名称；
2. **习惯计量单位 (Habitual Unit)**：该供应商常开单位（如“箱”、“包”、“扎”、“樽”）与标准进销存单位的对应关系；
3. **版式特征 (Layout Quirk)**：该供应商单据特有版式（如“单号常印在右上角红色小字”、“总金额在右下角带现金收讫印章”）；
4. **历史协议价格参考区间 (Price Range)**：该食材的历史均价，辅助门禁识别小数点移位等识别差错。

---

### 3.2 Prompt 动态注入规范 (`<vendor_context>`)

在下发给 VLM 的 Prompt 中，系统动态注入检索出的先验知识片段：

```xml
<vendor_context>
【供应商历史先验知识 (VendorMemory)】
- 供应商标准名：新记蔬菜批发（常见单据抬头：新记、新记菜栏）
- 常见品名映射：
  * "菜心苗" -> 关联标准 SKU: "本地特级菜心苗"，常规单位: "斤"
  * "生菜" -> 关联标准 SKU: "玻璃生菜"，常规单位: "斤"
  * "番茄" -> 该供应商常以 "箱" 开单，每箱约 15 斤
- 版式注意事项：该供应商送货单左下角常有手写签名，右下角盖红色现金收讫章。
</vendor_context>
```

---

## 4. 技术实现与多租户硬隔离 ([`services/rag.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/rag.py))

### 4.1 租户硬隔离物理防线 (Tenant Isolation)
为防止不同门店（租户）之间的进货协议底价或私有品名发生穿透泄露，VectorDB 检索强制执行命名空间硬隔离：

```python
class VendorMemoryManager:
    def __init__(self, chroma_client):
        self.client = chroma_client

    def get_collection(self, tenant_id: str):
        # 严格按 tenant_id 隔离独立 collection
        collection_name = f"tenant_{tenant_id}_vendor_memory"
        return self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def retrieve_vendor_context(self, tenant_id: str, supplier_hint: str) -> str:
        collection = self.get_collection(tenant_id)
        results = collection.query(
            query_texts=[supplier_hint],
            n_results=3
        )
        # 格式化为 <vendor_context> 文本段
        return format_vendor_context(results)
```

---

## 5. 数据飞轮业务指标 (Flywheel Metrics)

| 阶段 | 历史审批单据量 | 关键品名/单位命中率 | 人工复核修改次数/单 |
| :--- | :---: | :---: | :---: |
| **冷启动期 (Day 1~7)** | 0 ~ 50 张 | 约 78% | 日均修改 3.2 处 |
| **成熟爬坡期 (Day 8~30)** | 50 ~ 300 张 | 约 92% | 日均修改 0.8 处 |
| **飞轮稳定期 (>30 Days)** | > 300 张 | **$\ge 97\%$** | **日均修改 $\le 0.2$ 处 (<30秒核对)** |
