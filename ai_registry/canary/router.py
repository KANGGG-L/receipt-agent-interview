"""
Canary Traffic Router (ai_registry/canary/router.py)
实现多维度流量分流路由：白名单、单据随机概率、供应商 Hash 确定性分流。
"""

import hashlib
import random
from enum import Enum
from typing import Optional, List, Dict, Any

class GreyAssignMode(str, Enum):
    RECEIPT = "receipt"      # 按单据随机分配
    SUPPLIER = "supplier"    # 按供应商 Hash 分配 (同供应商一致命中)

class CanaryRouter:
    @staticmethod
    def should_use_grey(
        grey_enabled: bool,
        grey_percent: int,
        grey_assign_mode: GreyAssignMode = GreyAssignMode.RECEIPT,
        supplier_name: str = "",
        supplier_id: Optional[int] = None,
        grey_supplier_ids: Optional[List[int]] = None
    ) -> bool:
        """根据策略决定请求走对照组 A (False) 还是实验组 B (True)。"""
        if not grey_enabled or grey_percent <= 0:
            return False

        # 1. 白名单检查 (优先)
        if grey_supplier_ids and supplier_id is not None:
            if supplier_id in grey_supplier_ids:
                return True

        # 2. 单据随机模式
        if grey_assign_mode == GreyAssignMode.RECEIPT:
            return (random.random() * 100) < grey_percent

        # 3. 供应商 Hash 模式 (确定性哈希)
        if grey_assign_mode == GreyAssignMode.SUPPLIER:
            key = supplier_name.strip() if supplier_name else "default"
            hash_val = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100
            return hash_val < grey_percent

        return False
