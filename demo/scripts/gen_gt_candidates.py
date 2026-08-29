# -*- coding: utf-8 -*-
"""GT 异构候选生成 —— Gap A2（T4）。

读 demo/evalsets/manifest.csv 指定 split 的样本 → 图片预处理（短边 ≥1000px，
长边压到 1600px 控制 payload）→ base64 调**异构视觉模型**生成候选 GT →
落 demo/evalsets/expected/<sample_id>.json（gt_status=draft + gt_source_model），
manifest 同步状态。

异构原则（用户已确认）：GT 生成模型必须与识别腿（SiliconFlow Qwen 家族）真异构：
    主选 zai-org/GLM-4.5V（智谱家族）
    备选 PaddlePaddle/PaddleOCR-VL-1.5（百度家族）
    禁止回落 Qwen 家族。

输出 JSON schema 与 run_eval 的 expected 消费格式（compare/normalize）对齐：
    {
      "supplier_name": str, "date": "YYYY-MM-DD", "total_amount": float,
      "doc_form": str,
      "items": [{"name": str, "qty": float, "unit": str,
                 "unit_price": float, "amount": float}],
      "gt_status": "draft", "gt_source_model": "<实际模型名>",
      "gt_reviewed_by": null, "gt_reviewed_at": null
    }

用法：
    python gen_gt_candidates.py --split test --dry-run        # 只打印计划
    python gen_gt_candidates.py --split test                  # 真实生成（顺序 + 重试）
    python gen_gt_candidates.py --split test --limit 5

密钥：
    siliconflow（默认）：读 demo/.env 的 OPENAI_API_KEY / OPENAI_BASE_URL（SiliconFlow
        OpenAI 兼容接口；base 可能已含 /chat/completions 后缀，脚本自动容错）。
    dashscope：读 demo/.env 的 DASHSCOPE_API_KEY，base_url 固定为
        https://dashscope.aliyuncs.com/compatible-mode/v1（OpenAI 兼容模式）。

provider 选择：--provider {siliconflow,dashscope}，默认 siliconflow（向后兼容）；
    --gt-model 传 qwen 系模型名（如 qwen3-vl-plus）且未显式指定 --provider 时自动切到 dashscope。
    注意：dashscope 的 GT 与识别腿（Qwen）同家族，异构性依赖人工抽检兜底。
"""

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
DEFAULT_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
DEMO_ENV_PATH = os.path.join(DEMO_DIR, ".env")

# 阈值：T10 的 app_settings 就绪后应迁移为配置
# TODO(T10): 迁移到 app_settings（settings_service.get_float / get_int）
DEFAULT_GT_MODEL = "zai-org/GLM-4.5V"
FALLBACK_GT_MODELS = ["PaddlePaddle/PaddleOCR-VL-1.5"]   # 禁止回落 Qwen 家族
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_DEFAULT_GT_MODEL = "qwen3-vl-plus"
DASHSCOPE_FALLBACK_GT_MODELS = ["qwen3-vl-flash"]
MIN_SHORT_SIDE = 1000        # 短边下限，低于此值影响小字识别（与 build_evalset 一致）
PREVIEW_LONG_SIDE = 1600     # API payload 上限：长边压到 1600（4:3 时短边约 1200）
JPEG_QUALITY = 85
MAX_ATTEMPTS = 3             # 每个模型最多尝试次数（1 次原始 + 2 次重试）
RETRY_BACKOFF_SECONDS = (5, 15)   # 429/5xx 退避
CHAT_TIMEOUT_SECONDS = 300
REQUEST_GAP_SECONDS = 2      # 温和限速：两次调用间隔

GT_PROMPT = """你是收据/送货单数字化专家。请逐字转录这张单据图片，输出严格的 JSON（不要输出任何其他文字、不要用 markdown 代码块）：
{
  "supplier_name": "供应商名称（图面原文，繁体照抄）",
  "date": "开单日期 YYYY-MM-DD（无法辨认则空字符串）",
  "total_amount": 总额数字（图面原文，禁止自行重算修正）,
  "doc_form": "printed_delivery_note|ncr_handwritten|thermal|weigh_slip|correction_note|monthly_statement 之一",
  "items": [{"name": "品名原文", "qty": 数字, "unit": "单位（斤/磅/扎/箱等）", "unit_price": 数字, "amount": 数字}]
}
要求：
1. 金额与数量必须逐字转录图面所见，严禁自行重算修正；
2. 明细行按图面顺序全部列出，不要遗漏，也不要把页脚/编号/合计行当作商品；
3. 只输出 JSON。"""


# ------------------------------------------------------------------
# .env 读取（不覆盖已有环境变量）
# ------------------------------------------------------------------
def load_demo_env(path=DEMO_ENV_PATH):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def is_qwen_family(model_name):
    """是否 Qwen 家族模型名（qwen3-vl-plus / Qwen2.5-VL-72B-Instruct 等）。"""
    return "qwen" in (model_name or "").lower()


def resolve_api_endpoint(provider="siliconflow"):
    """从 demo/.env / 环境变量解析 (base_url, api_key)，容错 /chat/completions 后缀。

    siliconflow：OPENAI_BASE_URL + OPENAI_API_KEY；
    dashscope：固定 base_url + DASHSCOPE_API_KEY。
    """
    load_demo_env()
    if provider == "dashscope":
        base = DASHSCOPE_BASE_URL
        key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        if not key:
            raise SystemExit("缺少 DASHSCOPE_API_KEY（demo/.env 或环境变量）")
    else:
        base = (os.environ.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not base or not key:
            raise SystemExit("缺少 OPENAI_BASE_URL / OPENAI_API_KEY（demo/.env 或环境变量）")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    return url, key


# ------------------------------------------------------------------
# 图片预处理
# ------------------------------------------------------------------
def _image_size(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def prepare_image_payload(image_path, work_dir):
    """把样本图压成可直接发 API 的 JPEG：短边 ≥1000px、长边 ≤1600px。

    评测集 receipts/ 内是 build_evalset 转出的全分辨率 PNG（HEIC 经 sips 转换）。
    这里统一用 sips 重编码为 JPEG（长边 1600），既满足短边 ≥1000 又控制 base64
    体积；sips 失败或图片本身小于上限时回退发原文件。
    返回 (发送用文件路径, base64 字符串, 实际尺寸说明)。
    """
    w, h = _image_size(image_path)
    short_side = min(w, h) if w and h else 0
    if short_side and short_side < MIN_SHORT_SIDE:
        print("  [WARN] %s 短边 %dpx < %dpx，小字识别可能受影响" %
              (os.path.basename(image_path), short_side, MIN_SHORT_SIDE))

    long_side = max(w, h) if w and h else 0
    send_path = image_path
    if long_side > PREVIEW_LONG_SIDE:
        try:
            out = os.path.join(work_dir, "gt_preview_" + os.path.basename(image_path))
            root, _ = os.path.splitext(out)
            out = root + ".jpg"
            subprocess.run(
                ["sips", "-s", "format", "jpeg",
                 "-s", "formatOptions", str(JPEG_QUALITY),
                 "-Z", str(PREVIEW_LONG_SIDE), image_path, "--out", out],
                check=True, capture_output=True)
            if os.path.exists(out):
                send_path = out
        except Exception as e:
            print("  [WARN] sips 预处理失败（回退原图）：%s" % str(e)[:120])

    with open(send_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return send_path, b64, "%dx%d" % (w, h)


# ------------------------------------------------------------------
# 模型调用（stdlib urllib，OpenAI 兼容 chat/completions）
# ------------------------------------------------------------------
def call_vision_model(url, api_key, model, image_b64, mime="image/jpeg"):
    """调用视觉模型，返回 (解析后的 dict, token_usage dict)。失败抛 RuntimeError。"""
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:%s;base64,%s" % (mime, image_b64)}},
                {"type": "text", "text": GT_PROMPT},
            ],
        }],
        "temperature": 0,
        "max_tokens": 4096,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        raise RuntimeError("HTTP %d: %s" % (e.code, detail))
    except Exception as e:
        raise RuntimeError(str(e)[:300])

    content = ""
    try:
        content = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("响应缺少 choices/message：%s" % json.dumps(body)[:200])
    usage = body.get("usage") or {}
    gt = parse_gt_json(content)
    return gt, {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def parse_gt_json(content):
    """从模型输出提取 GT JSON（容错 markdown 代码块与思考文本）。"""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("输出中找不到 JSON：%s" % text[:200])
    try:
        return json.loads(text[start:end + 1])
    except Exception as e:
        raise RuntimeError("JSON 解析失败：%s | 原文：%s" % (e, text[:200]))


def normalize_gt_items(gt):
    """items 内 quantity → qty 键归一（存储/表单层统一用 qty，与 run_eval 消费口径对齐）。

    模型即使仍返回 quantity 也会在落盘前被转换；已有 qty 时 quantity（若同时存在）丢弃。
    """
    if isinstance(gt, dict) and isinstance(gt.get("items"), list):
        normalized = []
        for it in gt["items"]:
            if isinstance(it, dict):
                it = dict(it)
                if "qty" not in it and "quantity" in it:
                    it["qty"] = it.pop("quantity")
                else:
                    it.pop("quantity", None)
            normalized.append(it)
        gt["items"] = normalized
    return gt


def validate_gt(gt):
    """最小校验：必要字段齐全且 items 是列表（明细行统一用 qty 键）。返回错误文案或 None。"""
    if not isinstance(gt, dict):
        return "GT 不是 dict"
    for key in ("supplier_name", "date", "total_amount", "items"):
        if key not in gt:
            return "缺字段 %s" % key
    items = gt.get("items")
    if not isinstance(items, list):
        return "items 不是列表"
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return "items[%d] 不是对象" % i
        if "qty" not in it:
            return "items[%d] 缺 qty 字段（quantity 也不允许，需为 qty）" % i
    return None


# ------------------------------------------------------------------
# manifest 读写
# ------------------------------------------------------------------
def _load_manifest(evalset_dir):
    path = os.path.join(evalset_dir, "manifest.csv")
    if not os.path.exists(path):
        raise SystemExit("评测集 manifest 不存在：%s\n请先运行 build_evalset.py 构建" % path)
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save_manifest(evalset_dir, rows):
    path = os.path.join(evalset_dir, "manifest.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def generate(split, gt_model=DEFAULT_GT_MODEL, limit=None, evalset_dir=None,
             dry_run=False, api_url=None, api_key=None, provider="siliconflow"):
    """为指定 split 生成 GT 候选（顺序执行，重试退避，失败不阻断）。

    幂等：gt_status 已是 confirmed 的样本自动跳过（人工成果不被覆盖）。
    返回 summary dict：{total, ok, failed, skipped, tokens_total, model_used, failures[]}。
    """
    provider = provider or "siliconflow"
    evalset_dir = os.path.abspath(evalset_dir or DEFAULT_EVALSET_DIR)
    rows = [r for r in _load_manifest(evalset_dir) if r.get("split") == split]
    if not rows:
        raise SystemExit("manifest 中没有 split=%s 的样本" % split)

    todo = [r for r in rows if (r.get("gt_status") or "missing") != "confirmed"]
    if limit:
        todo = todo[:int(limit)]

    # dashscope 下备选也走百炼模型（SiliconFlow 模型名在百炼端点不可用）
    fallbacks = DASHSCOPE_FALLBACK_GT_MODELS if provider == "dashscope" else FALLBACK_GT_MODELS
    print("GT 候选生成 | provider=%s | split=%s | 候选模型=%s | 备选=%s" %
          (provider, split, gt_model, ", ".join(fallbacks) or "无"))
    if provider == "dashscope":
        # 用户显式选择百炼/Qwen 系作 GT，异构性靠人工抽检兜底
        print("[提醒] GT 与识别腿同家族（Qwen），异构性依赖人工抽检兜底")
    print("样本：%d 张（manifest 共 %d 张，已 confirmed 跳过 %d 张）" %
          (len(todo), len(rows), len(rows) - len(todo)))
    if dry_run:
        for r in todo:
            print("  [dry-run] %s %s %s" %
                  (r["sample_id"], r.get("doc_form", ""), r.get("image", "")))
        print("dry-run 结束，未调用 API。")
        return {"total": len(todo), "dry_run": True}

    url, key = api_url, api_key
    if not (url and key):
        url, key = resolve_api_endpoint(provider)

    work_dir = tempfile.mkdtemp(prefix="gt_gen_")
    summary = {"total": len(todo), "ok": 0, "failed": 0, "skipped": 0,
               "tokens_total": 0, "model_used": gt_model, "failures": []}
    expected_dir = os.path.join(evalset_dir, "expected")
    os.makedirs(expected_dir, exist_ok=True)
    t0 = time.time()

    try:
        for i, row in enumerate(todo, 1):
            sid = row["sample_id"]
            image_path = os.path.join(evalset_dir, row.get("image", ""))
            print("[%d/%d] %s（%s）" % (i, len(todo), sid, os.path.basename(image_path)))
            if not os.path.exists(image_path):
                print("  [FAIL] 图片缺失：%s" % image_path)
                summary["failed"] += 1
                summary["failures"].append({"sample_id": sid, "error": "图片缺失"})
                continue

            send_path, image_b64, dims = prepare_image_payload(image_path, work_dir)
            mime = "image/jpeg" if send_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            payload_kb = len(image_b64) * 3 // 4 // 1024
            print("  发送 %s | %s | %dKB" % (os.path.basename(send_path), dims, payload_kb))

            gt, err, used_model, usage = None, None, gt_model, {}
            candidates = [gt_model] + [m for m in fallbacks if m != gt_model]
            for model_name in candidates:
                summary["model_used"] = model_name
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    try:
                        gt, usage = call_vision_model(url, key, model_name, image_b64, mime)
                        gt = normalize_gt_items(gt)   # quantity → qty 归一后再校验/落盘
                        err = validate_gt(gt)
                        if err is None:
                            used_model = model_name
                            break
                        err = "校验失败：%s" % err
                    except RuntimeError as e:
                        err = str(e)
                        # 429/5xx 退避重试；其他错误（如 400/401）直接换下一个模型
                        retryable = ("HTTP 429" in err or "HTTP 5" in err
                                     or "timed out" in err or "URLError" in err)
                        if not retryable or attempt == MAX_ATTEMPTS:
                            break
                        wait = RETRY_BACKOFF_SECONDS[min(attempt - 1,
                                                         len(RETRY_BACKOFF_SECONDS) - 1)]
                        print("  [retry %d/%d] %s，%ds 后重试" % (attempt, MAX_ATTEMPTS, err[:120], wait))
                        time.sleep(wait)
                if err is None:
                    break
                print("  [model-switch] %s 失败：%s" % (model_name, err[:160]))
                if model_name != candidates[-1]:
                    print("  切换备选模型…")
                time.sleep(REQUEST_GAP_SECONDS)

            if err is not None or gt is None:
                print("  [FAIL] %s" % (err or "未知错误")[:200])
                summary["failed"] += 1
                summary["failures"].append({"sample_id": sid, "error": (err or "")[:300]})
                continue

            # 落盘：与 run_eval expected 消费格式对齐 + 抽检元数据
            body = {
                "supplier_name": gt.get("supplier_name", ""),
                "date": gt.get("date", ""),
                "total_amount": gt.get("total_amount"),
                "doc_form": gt.get("doc_form", ""),
                "items": gt.get("items", []),
            }
            body["gt_status"] = "draft"
            body["gt_source_model"] = used_model
            body["gt_reviewed_by"] = None
            body["gt_reviewed_at"] = None
            with open(os.path.join(expected_dir, sid + ".json"), "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)

            row["gt_status"] = "draft"
            row["gt_source_model"] = used_model
            summary["ok"] += 1
            summary["tokens_total"] += int(usage.get("total_tokens") or 0)
            print("  [OK] %s | %s | 总额 %s | 明细 %d 行 | tokens %s" %
                  (body["supplier_name"], body["date"], body["total_amount"],
                   len(body["items"]), usage.get("total_tokens")))

            time.sleep(REQUEST_GAP_SECONDS)   # 温和限速

        _save_manifest(evalset_dir, rows)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    elapsed = round(time.time() - t0, 1)
    print("-" * 60)
    print("完成：%d 张 | 成功 %d | 失败 %d | 耗时 %.1fs | tokens 合计 %d"
          % (summary["total"], summary["ok"], summary["failed"], elapsed,
             summary["tokens_total"]))
    if summary["failures"]:
        print("失败样本（不阻断，可重跑本命令续补）：%s" %
              ", ".join(f["sample_id"] for f in summary["failures"]))
    return summary


def main():
    ap = argparse.ArgumentParser(
        description="GT 异构候选生成（siliconflow: GLM-4.5V → PaddleOCR-VL；dashscope: qwen3-vl-plus）")
    ap.add_argument("--split", required=True, choices=["train", "val", "test"])
    ap.add_argument("--provider", default=None, choices=["siliconflow", "dashscope"],
                    help="GT 生成走哪个 provider（默认 siliconflow；--gt-model 为 qwen 系时自动切 dashscope）")
    ap.add_argument("--gt-model", default=None,
                    help="GT 生成模型（siliconflow 默认 %s，禁止 Qwen 家族；"
                         "dashscope 默认 %s，可用如 qwen3-vl-flash 覆盖）"
                         % (DEFAULT_GT_MODEL, DASHSCOPE_DEFAULT_GT_MODEL))
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 张")
    ap.add_argument("--evalset-dir", default=None, help="评测集目录（默认 demo/evalsets）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不调用 API")
    args = ap.parse_args()

    # provider 解析：显式 > 按模型名自动识别（qwen 系 → dashscope）> siliconflow
    provider = args.provider
    if provider is None and is_qwen_family(args.gt_model):
        provider = "dashscope"
        print("[auto] --gt-model 为 Qwen 家族模型，自动使用 provider=dashscope")
    provider = provider or "siliconflow"

    # 默认模型：dashscope 用百炼默认模型，siliconflow 保持原默认
    gt_model = args.gt_model
    if gt_model is None:
        gt_model = DASHSCOPE_DEFAULT_GT_MODEL if provider == "dashscope" else DEFAULT_GT_MODEL
    # 注意：provider=dashscope 时用户显式指定 Qwen 系模型，跳过「禁止 Qwen 家族」校验

    summary = generate(split=args.split, gt_model=gt_model, limit=args.limit,
                       evalset_dir=args.evalset_dir, dry_run=args.dry_run,
                       provider=provider)
    return 0 if (summary.get("dry_run") or summary["failed"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
