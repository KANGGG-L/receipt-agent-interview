# -*- coding: utf-8 -*-
"""评测资产备份：GT（evalsets/manifest.csv + expected/*.json）与冻结基线。

why：evalsets 是业务数据，不入 git；2026-10-11 的基线重冻结就因语料被批量重写、没有备份，
S090/S136 的 GT 永久丢失。本脚本在 run_eval 出分前与基线重冻结前各做一份带 sha256 清单的快照，
放在与数据库备份同一个 BACKUP_DIR 下，只保留最近 N 份。

用法：
    python demo/scripts/eval_assets_backup.py                 # 备份
    python demo/scripts/eval_assets_backup.py --verify <目录>  # 校验某份备份未损坏
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, ".."))
DEFAULT_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
DEFAULT_BASELINES_DIR = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "eval_baselines")
KEEP_DEFAULT = 10
MANIFEST_NAME = "MANIFEST.sha256.json"


def default_dest_dir():
    root = os.environ.get("BACKUP_DIR") or os.path.join(DEMO_DIR, "backups")
    return os.path.join(root, "eval_assets")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect(evalset_dir, baselines_dir):
    pairs = []
    manifest = os.path.join(evalset_dir, "manifest.csv")
    if os.path.exists(manifest):
        pairs.append((manifest, "evalsets/manifest.csv"))
    expected = os.path.join(evalset_dir, "expected")
    if os.path.isdir(expected):
        for name in sorted(os.listdir(expected)):
            if name.endswith(".json"):
                pairs.append((os.path.join(expected, name), "evalsets/expected/" + name))
    if os.path.isdir(baselines_dir):
        for name in sorted(os.listdir(baselines_dir)):
            full = os.path.join(baselines_dir, name)
            if os.path.isfile(full):
                pairs.append((full, "eval_baselines/" + name))
    return pairs


def backup_eval_assets(evalset_dir=None, baselines_dir=None, dest_dir=None, keep=KEEP_DEFAULT):
    """复制 GT 与基线到 <dest>/eval_assets_<时间戳>/ 并写 sha256 清单；返回 {"dir","files"}。"""
    evalset_dir = os.path.abspath(evalset_dir or DEFAULT_EVALSET_DIR)
    baselines_dir = os.path.abspath(baselines_dir or DEFAULT_BASELINES_DIR)
    dest_root = os.path.abspath(dest_dir or default_dest_dir())
    pairs = _collect(evalset_dir, baselines_dir)
    if not pairs:
        raise RuntimeError("没有找到可备份的评测资产：%s / %s" % (evalset_dir, baselines_dir))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = os.path.join(dest_root, "eval_assets_" + stamp)
    files = {}
    for src, rel in pairs:
        out = os.path.join(target, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(src, out)
        files[rel] = _sha256(out)
    with open(os.path.join(target, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.now().isoformat(timespec="seconds"),
                   "source_evalset_dir": evalset_dir, "files": files}, f, ensure_ascii=False, indent=2)
    _prune(dest_root, keep)
    return {"dir": target, "files": len(files)}


def _prune(dest_root, keep):
    if not keep or keep < 1 or not os.path.isdir(dest_root):
        return
    snaps = sorted(d for d in os.listdir(dest_root) if d.startswith("eval_assets_"))
    for old in snaps[:-keep]:
        shutil.rmtree(os.path.join(dest_root, old), ignore_errors=True)


def verify_backup(backup_dir):
    """逐文件复核 sha256；返回 (ok, 问题列表)。"""
    mpath = os.path.join(backup_dir, MANIFEST_NAME)
    if not os.path.exists(mpath):
        return False, ["缺少清单文件 %s" % MANIFEST_NAME]
    with open(mpath, encoding="utf-8") as f:
        meta = json.load(f)
    problems = []
    for rel, digest in (meta.get("files") or {}).items():
        full = os.path.join(backup_dir, rel)
        if not os.path.exists(full):
            problems.append("缺文件：" + rel)
        elif _sha256(full) != digest:
            problems.append("内容被改动：" + rel)
    return (not problems), problems


def gt_item_count(evalset_dir, split=None):
    """当前 GT 明细行总数与样本数（重冻结前后对比用，防止 GT 在不知情时变少）。"""
    import csv
    evalset_dir = os.path.abspath(evalset_dir or DEFAULT_EVALSET_DIR)
    manifest = os.path.join(evalset_dir, "manifest.csv")
    ids = []
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8", newline="") as f:
            ids = [r["sample_id"] for r in csv.DictReader(f) if not split or r.get("split") == split]
    items = readable = 0
    for sid in ids:
        path = os.path.join(evalset_dir, "expected", sid + ".json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    items += len((json.load(f) or {}).get("items") or [])
                readable += 1
            except Exception:
                pass
    return {"samples": len(ids), "readable_gt": readable, "items": items}


def main():
    ap = argparse.ArgumentParser(description="备份评测资产（GT + 冻结基线）")
    ap.add_argument("--evalset-dir", default=None)
    ap.add_argument("--dest", default=None)
    ap.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    ap.add_argument("--verify", default=None, help="校验指定备份目录")
    args = ap.parse_args()
    if args.verify:
        ok, problems = verify_backup(args.verify)
        print("校验通过" if ok else "校验失败：\n" + "\n".join(problems))
        return 0 if ok else 1
    info = backup_eval_assets(evalset_dir=args.evalset_dir, dest_dir=args.dest, keep=args.keep)
    print("已备份 %d 个文件 → %s" % (info["files"], info["dir"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
