"""
A/B Experiment Manager (ai_registry/canary/experiment_manager.py)
管理 A/B 实验生命周期：创建、分流记录、状态流转与推全回滚。
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
EXP_FILE = BENCHMARKS_DIR / "ab_test_experiments.json"

class ExperimentManager:
    def __init__(self, exp_file: Path = EXP_FILE):
        self.exp_file = exp_file

    def _load_data(self) -> Dict[str, Any]:
        if self.exp_file.exists():
            with open(self.exp_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"experiments": []}

    def _save_data(self, data: Dict[str, Any]):
        with open(self.exp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list_experiments(self) -> List[Dict[str, Any]]:
        return self._load_data().get("experiments", [])

    def get_active_experiment(self) -> Optional[Dict[str, Any]]:
        for exp in self.list_experiments():
            if exp.get("status") == "running":
                return exp
        return None

    def promote_experiment(self, exp_id: str) -> Dict[str, Any]:
        data = self._load_data()
        for exp in data["experiments"]:
            if exp["id"] == exp_id:
                exp["status"] = "promoted"
                exp["promoted_at"] = datetime.now().isoformat()
                self._save_data(data)
                return exp
        raise KeyError(f"实验 {exp_id} 不存在")
