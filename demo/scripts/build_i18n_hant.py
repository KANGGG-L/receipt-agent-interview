#!/usr/bin/env python3
"""生成前端繁体（香港）转换数据 demo/static/js/i18n/zh-hant-data.js。

数据来源：opencc-python-reimplemented 自带的 s2hk 字典
（STCharacters + STPhrases + HKVariants + HKVariantsPhrases）。

体积控制：字符表全量；短语表只保留在本项目语料中实际出现的短语
（templates、static/js、demo/app 的 Python 源码、样本 JSON、i18n 目录与用语表）。
输出按键排序，重复运行结果一致。

用法：
    python demo/scripts/build_i18n_hant.py            # 重新生成
    python demo/scripts/build_i18n_hant.py --check    # 只校验已生成文件是否最新（CI 可用）
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
I18N_DIR = os.path.join(DEMO_DIR, "static", "js", "i18n")
OUT_PATH = os.path.join(I18N_DIR, "zh-hant-data.js")
GLOSSARY_PATH = os.path.join(I18N_DIR, "zh-hant-hk-glossary.js")
CONVERTER_PATH = os.path.join(DEMO_DIR, "app", "i18n_text.py")

# HKVariants 中不采用的字形映射（及原因）。字典默认值来自香港政府《常用字字形表》，
# 与本地商业软件和餐牌的实际用字有出入，这里只停用会与用语表冲突或明显不符合餐饮场景的几项。
VARIANT_EXCLUDE = {
    "戶": "全站统一用「戶」（戶口、客戶、用戶），见用语表「用户」条目",
}
# 输出值中的字形替换：OpenCC 部分短语输出「麪」，香港店铺与餐牌普遍写「麵」。
OUTPUT_REPLACE = {"麪": "麵"}

CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
CJK_RUN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002ffff]+")


def opencc_dict_dir() -> str:
    try:
        import opencc  # noqa: F401
    except ImportError:
        sys.exit("需要 opencc-python-reimplemented：pip install opencc-python-reimplemented")
    import opencc as _oc
    return os.path.join(os.path.dirname(_oc.__file__), "dictionary")


def read_dict(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            key, vals = line.split("\t", 1)
            first = vals.split(" ")[0].strip()
            if key and first:
                out[key] = first
    return out


def load_converter_module():
    # 按文件路径加载，避免导入 app 包（会连带初始化数据库等重依赖）。
    spec = importlib.util.spec_from_file_location("_i18n_text", CONVERTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_glossary(path: str = GLOSSARY_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"/\*JSON-BEGIN\*/(.*?)/\*JSON-END\*/", src, re.S)
    if not m:
        raise SystemExit("用语表缺少 /*JSON-BEGIN*/ ... /*JSON-END*/ 标记：%s" % path)
    entries = json.loads(m.group(1))
    seen = set()
    for e in entries:
        if not e.get("s") or not e.get("t"):
            raise SystemExit("用语表条目缺少 s/t：%r" % e)
        if e["s"] in seen:
            raise SystemExit("用语表重复条目：%s" % e["s"])
        seen.add(e["s"])
    return entries


def iter_corpus_files():
    roots = [
        (os.path.join(DEMO_DIR, "templates"), (".html",)),
        (os.path.join(DEMO_DIR, "static", "js"), (".js",)),
        (os.path.join(DEMO_DIR, "app"), (".py", ".json")),
        (os.path.join(DEMO_DIR, "samples"), (".json",)),
        (os.path.join(DEMO_DIR, "sample_receipts"), (".json",)),
    ]
    for root, exts in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for f in sorted(files):
                p = os.path.join(dirpath, f)
                # 生成文件本身与 i18n 核心（只有注释含中文）不计入语料，避免改注释就让数据过期。
                if os.path.abspath(p) in (OUT_PATH, os.path.join(I18N_DIR, "i18n.js")):
                    continue
                if f.endswith(exts):
                    yield p


def corpus_runs() -> set[str]:
    runs: set[str] = set()
    for p in iter_corpus_files():
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        runs.update(CJK_RUN_RE.findall(text))
    return runs


def corpus_ngrams(runs: set[str], max_len: int) -> set[str]:
    grams: set[str] = set()
    for run in runs:
        n = len(run)
        for i in range(n):
            for L in range(2, min(max_len, n - i) + 1):
                grams.add(run[i:i + L])
    return grams


def fix_output(v: str) -> str:
    for a, b in OUTPUT_REPLACE.items():
        v = v.replace(a, b)
    return v


def build() -> tuple[str, dict]:
    d = opencc_dict_dir()
    st_chars = read_dict(os.path.join(d, "STCharacters.txt"))
    st_phrases = read_dict(os.path.join(d, "STPhrases.txt"))
    hk_chars = read_dict(os.path.join(d, "HKVariants.txt"))
    hk_phrases = read_dict(os.path.join(d, "HKVariantsPhrases.txt"))
    glossary = load_glossary()

    for k in VARIANT_EXCLUDE:
        hk_chars.pop(k, None)

    char_pairs = []
    astral_chars = {}
    for k in sorted(st_chars):
        v = fix_output(st_chars[k])
        if k == v:
            continue
        if len(k) == 1 and len(v) == 1 and ord(k) <= 0xFFFF and ord(v) <= 0xFFFF:
            char_pairs.append(k + v)
        else:
            astral_chars[k] = v

    max_phrase_len = max(len(k) for k in st_phrases)
    runs = corpus_runs()
    grams = corpus_ngrams(runs, max_phrase_len)
    phrases = {}
    for k in sorted(st_phrases):
        if k in grams:
            phrases[k] = fix_output(st_phrases[k])
    phrases.update(astral_chars)

    hk_pairs = []
    hk_extra = {}
    for k in sorted(hk_chars):
        v = hk_chars[k]
        if k == v:
            continue
        if len(k) == 1 and len(v) == 1 and ord(k) <= 0xFFFF and ord(v) <= 0xFFFF:
            hk_pairs.append(k + v)
        else:
            hk_extra[k] = v
    hk_phr = {k: hk_phrases[k] for k in sorted(hk_phrases)}
    hk_phr.update(hk_extra)

    # 幂等：DOM 翻译器会再次处理被脚本读回又写入的已转换文本，转换结果必须是不动点。
    # OpenCC 的部分繁体输出含有同时也是简体键的字（干預 的「干」、批准 的「准」），
    # 脱离原短语后会被单字表二次改写。对这类输出补一条「繁体 → 自身」的恒等短语。
    mod = load_converter_module()
    identity_added = 0
    for _round in range(3):
        conv = mod.Converter({"chars": "".join(char_pairs), "phrases": phrases,
                              "hkChars": "".join(hk_pairs), "hkPhrases": hk_phr}, glossary)
        candidates = set(v for v in phrases.values() if len(v) >= 2)
        candidates.update(conv.convert(r) for r in runs)
        added = 0
        for v in sorted(candidates):
            if len(v) >= 2 and conv.convert(v) != v and v not in phrases:
                phrases[v] = v
                added += 1
        identity_added += added
        if not added:
            break
    conv = mod.Converter({"chars": "".join(char_pairs), "phrases": phrases,
                          "hkChars": "".join(hk_pairs), "hkPhrases": hk_phr}, glossary)
    bad_gloss = [e["s"] for e in glossary if conv.convert(e["t"]) != e["t"]]
    if bad_gloss:
        raise SystemExit("用语表译文二次转换会变样（违反幂等）：%s" % ", ".join(bad_gloss))
    non_idempotent = sorted(r for r in runs if conv.convert(conv.convert(r)) != conv.convert(r))

    data = {
        "chars": "".join(char_pairs),
        "phrases": dict(sorted(phrases.items())),
        "hkChars": "".join(hk_pairs),
        "hkPhrases": dict(sorted(hk_phr.items())),
    }
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    header = (
        "/* 由 demo/scripts/build_i18n_hant.py 生成，勿手改。\n"
        " * 来源：OpenCC s2hk（STCharacters + STPhrases + HKVariants + HKVariantsPhrases），\n"
        " * 短语表按本项目语料裁剪。重新生成：python demo/scripts/build_i18n_hant.py\n"
        " * stats: chars=%d phrases=%d hkChars=%d hkPhrases=%d sha=%s\n"
        " */\n"
    ) % (len(char_pairs), len(phrases), len(hk_pairs), len(hk_phr), digest)
    js = header + "(function (root) { root.I18N_HANT_DATA = " + body + "; })(typeof window !== 'undefined' ? window : globalThis);\n"
    stats = {
        "chars": len(char_pairs),
        "phrases": len(phrases),
        "identity_phrases": identity_added,
        "non_idempotent_corpus_runs": len(non_idempotent),
        "hkChars": len(hk_pairs),
        "hkPhrases": len(hk_phr),
        "bytes": len(js.encode("utf-8")),
        "sha": digest,
    }
    return js, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="只校验，不写文件；过期时返回 1")
    args = ap.parse_args(argv)
    js, stats = build()
    if args.check:
        try:
            with open(OUT_PATH, encoding="utf-8") as fh:
                cur = fh.read()
        except OSError:
            cur = ""
        if cur != js:
            print("zh-hant-data.js 已过期，请重新运行 build_i18n_hant.py", file=sys.stderr)
            return 1
        print("zh-hant-data.js 已是最新", json.dumps(stats, ensure_ascii=False))
        return 0
    os.makedirs(I18N_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(js)
    print("已生成", os.path.relpath(OUT_PATH, DEMO_DIR), json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
