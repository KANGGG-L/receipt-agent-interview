# -*- coding: utf-8 -*-
"""构建去标识化评测集（train / val / test 三分法）—— Gap A1。

把真实商户收据语料（默认 `~/Desktop/hk/My Drive/Receipts/batch1`，HEIC）转成
**可复现、不泄漏、不入库**的评测集：

    demo/evalsets/
        receipts/S001.png ...      # 去标识化图片（HEIC 经 sips 转 PNG）
        expected/S001.json ...     # GT（由 T4 gen_gt_candidates.py 生成 + 人工抽检确认）
        manifest.csv               # 可提交的元数据（无供应商名、无金额）
        supplier_map.csv           # supplier_id → 真实供应商名（去标识映射，**不提交**）

隐私红线：
- 样本文件名一律无语义 ID（S001），**禁止带供应商简称**；
- manifest 只留元数据列（sample_id / split / doc_form / layout_type / gt_status ...）；
- 供应商名只出现在 supplier_map.csv，与语料同目录、同样被 gitignore。

用法：
    python build_evalset.py --sources ~/Desktop/hk/My\\ Drive/Receipts/batch1
    python build_evalset.py --sources <dir> --out demo/evalsets --seed 20260828
"""

import argparse
import csv
import hashlib
import os
import random
import shutil
import subprocess
import sys

# app_settings 缺省读取收敛到 scripts/_settings.py 单一实现（失败回落默认值并 WARN 一次）
from _settings import get as _settings_value

# ---- 分层与配比（T10 收口：走 app_settings 读取，缺省值即迁移前现值）----
DEFAULT_SEED = 20260828
SPLIT_NAMES = ("train", "val", "test")

SPLIT_RATIOS = (
    float(_settings_value("evalset_split_train", 0.55)),
    float(_settings_value("evalset_split_val", 0.25)),
    float(_settings_value("evalset_split_test", 0.20)),
)
MIN_SPLIT_COVERAGE = int(_settings_value("evalset_min_split_coverage", 3))  # doc_form 样本数 ≥ 该值时，保证每个 split 至少 1 张
MIN_SHORT_SIDE = int(_settings_value("evalset_min_short_side", 1000))       # 短边分辨率下限（低于此值会影响小字识别）

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")

# 语料侧 doc_hint → demo 的 DocForm 枚举
DOC_HINT_MAP = {
    "printed": "printed_delivery_note",
    "printed_delivery_note": "printed_delivery_note",
    "delivery_note": "printed_delivery_note",
    "handwritten": "ncr_handwritten",
    "ncr": "ncr_handwritten",
    "ncr_handwritten": "ncr_handwritten",
    "thermal": "thermal",
    "thermal_receipt": "thermal",
    "weigh": "weigh_slip",
    "weigh_slip": "weigh_slip",
    "correction": "correction_note",
    "correction_note": "correction_note",
    "credit": "credit_note",
    "credit_note": "credit_note",
    "monthly": "monthly_statement",
    "statement": "monthly_statement",
    "monthly_statement": "monthly_statement",
}
DEFAULT_DOC_FORM = "printed_delivery_note"

# doc_form → 版面类型（layout_type）
LAYOUT_BY_DOC_FORM = {
    "printed_delivery_note": "table",
    "ncr_handwritten": "freeform",
    "thermal": "compact",
    "weigh_slip": "ticket",
    "correction_note": "table",
    "credit_note": "table",
    "monthly_statement": "table",
}

MANIFEST_COLUMNS = [
    "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
    "gt_status", "gt_source_model", "src_sha1", "short_side",
]


# ------------------------------------------------------------------
# 语料元信息
# ------------------------------------------------------------------
def load_source_meta(sources_dir):
    """读取语料自带的 classification_manifest.csv（filename / supplier / doc_hint）。

    不存在时返回空 dict，doc_form 走默认印刷送货单。
    """
    path = os.path.join(sources_dir, "classification_manifest.csv")
    if not os.path.exists(path):
        return {}
    meta = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            fname = (row.get("filename") or "").strip()
            if fname:
                meta[fname.lower()] = row
    return meta


def iter_source_images(sources_dir):
    """列出语料图片（只看顶层，跳过 `_previews` 等下划线开头的目录与重复副本）。

    按文件名排序，保证同一语料每次构建顺序一致（可复现）。
    """
    out = []
    for name in sorted(os.listdir(sources_dir)):
        p = os.path.join(sources_dir, name)
        if not os.path.isfile(p) or name.startswith("."):
            continue
        if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
            out.append(p)
    return out


def sha1_of(path, length=12):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


# ------------------------------------------------------------------
# 图片转换
# ------------------------------------------------------------------
def convert_image(src_path, dst_path):
    """HEIC/HEIF 用 sips 转 PNG，其余格式直接复制。

    返回目标路径；转换失败时抛出，由 build() 捕获并跳过该样本（不阻断整轮）。
    """
    ext = os.path.splitext(src_path)[1].lower()
    if ext in (".heic", ".heif"):
        subprocess.run(
            ["sips", "-s", "format", "png", src_path, "--out", dst_path],
            check=True, capture_output=True,
        )
    else:
        shutil.copy2(src_path, dst_path)
    return dst_path


def short_side_of(path):
    """读取图片短边像素；读不到返回 0（不阻断）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(min(im.size))
    except Exception:
        return 0


# ------------------------------------------------------------------
# 分层抽样
# ------------------------------------------------------------------
def allocate_splits(n, ratios=SPLIT_RATIOS):
    """把 n 个样本按 ratios 分配到 train/val/test（最大余数法）。

    - n >= MIN_SPLIT_COVERAGE 且某 split 为 0 时，从张数最多的 split 借 1 张，
      保证「每个 split 覆盖全部 doc_form」（全局验收标准 #3）。
    - n 小于 3 时按 train → val → test 顺序各放 1 张，避免空 split。
    返回值 sum 恒等于 n，且过程完全确定（无随机、无并列歧义）。
    """
    counts = {name: 0 for name in SPLIT_NAMES}
    if n <= 0:
        return counts
    if n < len(SPLIT_NAMES):
        for i in range(n):
            counts[SPLIT_NAMES[i]] = 1
        return counts
    raw = [n * r for r in ratios]
    floors = [int(x) for x in raw]
    remainder = n - sum(floors)
    # 小数部分从大到小依次补 1（并列时按固定顺序，保证可复现）
    order = sorted(range(len(SPLIT_NAMES)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    for name, v in zip(SPLIT_NAMES, floors):
        counts[name] = v
    # 保底覆盖：任一分片为空则从最大的分片借 1 张
    if n >= MIN_SPLIT_COVERAGE:
        for name in SPLIT_NAMES:
            if counts[name] == 0:
                donor = max(SPLIT_NAMES,
                            key=lambda s: (counts[s], -SPLIT_NAMES.index(s)))
                counts[donor] -= 1
                counts[name] = 1
    return counts


def split_by_doc_form(rows, seed=DEFAULT_SEED):
    """按 doc_form 分层抽样：层内用 seed 洗牌后按 55/25/20 切分。

    同 seed + 同样本集合 → 完全一致的结果（可复现）。
    """
    strata = {}
    for r in rows:
        strata.setdefault(r["doc_form"], []).append(r["sample_id"])
    assignment = {}
    for doc_form in sorted(strata):
        ids = sorted(strata[doc_form])
        rnd = random.Random("%s:%s" % (seed, doc_form))
        rnd.shuffle(ids)
        counts = allocate_splits(len(ids))
        cursor = 0
        for name in SPLIT_NAMES:
            for sid in ids[cursor:cursor + counts[name]]:
                assignment[sid] = name
            cursor += counts[name]
    return assignment


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def build(sources_dir, out_dir, seed=DEFAULT_SEED):
    """构建评测集，返回 manifest.csv 路径。

    幂等：已存在的样本按 src_sha1 复用原 sample_id / split，重复构建不会打乱编号，
    也不会与 T6（线上样本 promote 追加行）冲突。
    """
    sources_dir = os.path.abspath(os.path.expanduser(sources_dir))
    out_dir = os.path.abspath(out_dir)
    receipts_dir = os.path.join(out_dir, "receipts")
    expected_dir = os.path.join(out_dir, "expected")
    os.makedirs(receipts_dir, exist_ok=True)
    os.makedirs(expected_dir, exist_ok=True)

    meta = load_source_meta(sources_dir)
    images = iter_source_images(sources_dir)
    if not images:
        raise SystemExit("语料目录没有可用图片：%s" % sources_dir)

    manifest_path = os.path.join(out_dir, "manifest.csv")
    existing = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("src_sha1"):
                    existing[row["src_sha1"]] = row

    # 供应商 → ID（排序后编号，保证同语料同一映射）
    suppliers = sorted({(m.get("supplier") or "").strip()
                        for m in meta.values() if (m.get("supplier") or "").strip()})
    supplier_ids = {name: "V%03d" % (i + 1) for i, name in enumerate(suppliers)}

    rows, low_res, failed = [], [], []
    used_ids = {r["sample_id"] for r in existing.values()}
    next_idx = 0
    for src in images:
        fname = os.path.basename(src)
        sha1 = sha1_of(src)
        prev = existing.get(sha1) or {}
        sample_id = prev.get("sample_id")
        if not sample_id:
            next_idx += 1
            sample_id = "S%03d" % next_idx
            while sample_id in used_ids:      # 与既有编号冲突则顺延
                next_idx += 1
                sample_id = "S%03d" % next_idx
        used_ids.add(sample_id)
        dst_ext = ".png" if os.path.splitext(fname)[1].lower() in (".heic", ".heif") \
            else os.path.splitext(fname)[1].lower()
        dst = os.path.join(receipts_dir, sample_id + dst_ext)
        try:
            convert_image(src, dst)
        except Exception as e:
            failed.append((fname, str(e)[:120]))
            continue

        src_meta = meta.get(fname.lower(), {})
        doc_form = DOC_HINT_MAP.get(
            (src_meta.get("doc_hint") or "").strip().lower(), DEFAULT_DOC_FORM)
        supplier = (src_meta.get("supplier") or "").strip()
        side = short_side_of(dst)
        if 0 < side < MIN_SHORT_SIDE:
            low_res.append((sample_id, side))

        rows.append({
            "sample_id": sample_id,
            "image": "receipts/" + os.path.basename(dst),
            "split": prev.get("split", ""),       # 稍后按分层结果覆盖
            "doc_form": doc_form,
            "layout_type": LAYOUT_BY_DOC_FORM.get(doc_form, "table"),
            "supplier_id": supplier_ids.get(supplier, ""),
            "gt_status": prev.get("gt_status", "missing"),
            "gt_source_model": prev.get("gt_source_model", ""),
            "src_sha1": sha1,
            "short_side": str(side),
        })

    # 分层抽样（已存在的样本沿用原 split，保证 T4/T6 的 GT 与切分不漂移）
    assignment = split_by_doc_form(rows, seed=seed)
    for r in rows:
        r["split"] = r["split"] or assignment[r["sample_id"]]

    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # 供应商名 → ID 映射单独存放（含真实商户名，绝不提交）
    with open(os.path.join(out_dir, "supplier_map.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["supplier_id", "supplier_name"])
        for name in suppliers:
            w.writerow([supplier_ids[name], name])

    counts = {s: sum(1 for r in rows if r["split"] == s) for s in SPLIT_NAMES}
    print("manifest → %s" % manifest_path)
    print("样本 %d 张 | train %d / val %d / test %d" %
          (len(rows), counts["train"], counts["val"], counts["test"]))
    forms = sorted({r["doc_form"] for r in rows})
    for form in forms:
        cov = {s: sum(1 for r in rows if r["doc_form"] == form and r["split"] == s)
               for s in SPLIT_NAMES}
        print("  %-24s train %d / val %d / test %d" % (form, cov["train"], cov["val"], cov["test"]))
    if low_res:
        print("[WARN] %d 张短边 < %dpx：%s" %
              (len(low_res), MIN_SHORT_SIDE, ", ".join("%s(%d)" % t for t in low_res[:10])))
    if failed:
        print("[WARN] %d 张转换失败已跳过：%s" % (len(failed), failed[:5]))
    return manifest_path


def main():
    ap = argparse.ArgumentParser(description="构建去标识化评测集（train/val/test 三分法）")
    ap.add_argument("--sources", required=True, help="语料目录（含 classification_manifest.csv）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "evalsets"),
                    help="输出目录（默认 demo/evalsets，已 gitignore）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="分层抽样随机种子")
    args = ap.parse_args()
    build(args.sources, args.out, seed=args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
