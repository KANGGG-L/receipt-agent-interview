# -*- coding: utf-8 -*-
"""H05 CLI：对授权快照副本回算历史指标，输出 v2 报告（只读，不覆盖 v1）。"""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_SCRIPTS_DIR)
for _p in (_DEMO_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.metrics_history import (  # noqa: E402
    build_recomputed_report, load_old_report,
)


def main(argv=None):
    ap = argparse.ArgumentParser(description="H05 历史指标回算")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default="")
    ap.add_argument("--tenant", default="")
    ap.add_argument("--old-report", default="")
    args = ap.parse_args(argv)
    old = load_old_report(args.old_report) if args.old_report else None
    report = build_recomputed_report(
        args.db, as_of=args.as_of, old_report=old, tenant=args.tenant or None)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
