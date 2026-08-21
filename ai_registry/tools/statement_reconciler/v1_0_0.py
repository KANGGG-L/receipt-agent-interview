"""
Statement Reconciler Tool v1.0.0 (Production Active)
月结对账单 (Statement) 散单逐笔勾稽比对与四向差异生成工具。
"""

from typing import List, Dict, Any

class StatementReconcilerTool:
    def reconcile(self, statement_items: List[Dict[str, Any]], local_receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
        matched = []
        missing_in_local = []
        price_discrepancies = []
        
        local_map = {r.get("receipt_no", "").strip(): r for r in local_receipts if r.get("receipt_no")}
        
        for stmt_item in statement_items:
            rec_no = stmt_item.get("receipt_no", "").strip()
            stmt_amt = float(stmt_item.get("amount", 0))
            
            if rec_no in local_map:
                local_rec = local_map[rec_no]
                local_amt = float(local_rec.get("total_amount", 0))
                if abs(stmt_amt - local_amt) < 0.05:
                    matched.append({
                        "receipt_no": rec_no,
                        "date": stmt_item.get("date"),
                        "amount": stmt_amt,
                        "status": "matched"
                    })
                else:
                    price_discrepancies.append({
                        "receipt_no": rec_no,
                        "date": stmt_item.get("date"),
                        "statement_amount": stmt_amt,
                        "local_amount": local_amt,
                        "diff": round(stmt_amt - local_amt, 2)
                    })
            else:
                missing_in_local.append({
                    "receipt_no": rec_no,
                    "date": stmt_item.get("date"),
                    "amount": stmt_amt,
                    "reason": "供应商月结单已列出，但门店系统未检索到该单据"
                })

        return {
            "summary": {
                "total_statement_count": len(statement_items),
                "matched_count": len(matched),
                "missing_count": len(missing_in_local),
                "price_diff_count": len(price_discrepancies)
            },
            "matched": matched,
            "missing_in_local": missing_in_local,
            "price_discrepancies": price_discrepancies
        }

Tool = StatementReconcilerTool
