"""简体 → 繁体（香港）文字转换，供不经过浏览器 DOM 的输出（如 CSV 导出表头、下载文件名）使用。

与前端 demo/static/js/i18n/i18n.js 使用同一份数据（zh-hant-data.js 与 zh-hant-hk-glossary.js）
和同一算法，保证前后端结果一致；运行时不依赖 opencc。算法：
  1. 先按港式用语表做正向最长匹配切分，命中段直接输出用语表译文；
  2. 用语表之间的片段走 OpenCC：短语表最长匹配（至少 2 字）> 单字表；
  3. 片段结果再过一遍香港字形表（HKVariantsPhrases > HKVariants）。
本模块也被 demo/scripts/build_i18n_hant.py 按文件路径加载，用来校验生成数据的幂等性，
因此只依赖标准库。
"""

from __future__ import annotations

import json
import os
import re
import threading
from functools import lru_cache
from typing import Optional

SOURCE_LOCALE = "zh-Hans"
HANT_LOCALE = "zh-Hant-HK"

_I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "js", "i18n")
DATA_PATH = os.path.join(_I18N_DIR, "zh-hant-data.js")
GLOSSARY_PATH = os.path.join(_I18N_DIR, "zh-hant-hk-glossary.js")
CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def normalize_locale(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip().lower().replace("_", "-")
    if s in ("zh-hant-hk", "zh-hk", "zh-mo", "zh-tw", "hk") or s.startswith("zh-hant"):
        return HANT_LOCALE
    if s in ("zh-hans", "zh-cn", "zh-sg", "zh", "cn") or s.startswith("zh-hans"):
        return SOURCE_LOCALE
    return None


def parse_glossary_js(src: str) -> list:
    m = re.search(r"/\*JSON-BEGIN\*/(.*?)/\*JSON-END\*/", src, re.S)
    return json.loads(m.group(1)) if m else []


def parse_data_js(src: str) -> dict:
    m = re.search(r"I18N_HANT_DATA = (\{.*\});", src, re.S)
    return json.loads(m.group(1)) if m else {}


class _Index:
    __slots__ = ("map", "max_len")

    def __init__(self, mapping: dict):
        self.map = mapping
        self.max_len: dict = {}
        for k in mapping:
            if k and self.max_len.get(k[0], 0) < len(k):
                self.max_len[k[0]] = len(k)

    def longest(self, text: str, i: int, min_len: int):
        lim = min(self.max_len.get(text[i], 0), len(text) - i)
        for L in range(lim, min_len - 1, -1):
            hit = self.map.get(text[i:i + L])
            if hit is not None:
                return L, hit
        return None


def _pairs(s: str) -> dict:
    return {s[i]: s[i + 1] for i in range(0, len(s) - 1, 2)}


def _invert_map(mapping: dict) -> dict:
    """繁体或香港字形 -> 简体。一对多时保留先出现的简体，折叠键因此稳定。"""
    inv = {}
    for src, dst in mapping.items():
        if dst and dst not in inv:
            inv[dst] = src
    return inv


class Converter:
    def __init__(self, data: dict, glossary: list):
        self.chars = _pairs(data.get("chars", ""))
        self.phrases = _Index(dict(data.get("phrases", {})))
        self.hk_chars = _pairs(data.get("hkChars", ""))
        self.hk_phrases = _Index(dict(data.get("hkPhrases", {})))
        self.gloss = _Index({e["s"]: e["t"] for e in glossary if e.get("s") and e.get("t")})
        self.inv_chars = _invert_map(self.chars)
        self.inv_hk_chars = _invert_map(self.hk_chars)
        self.inv_phrases = _Index(_invert_map(self.phrases.map))
        self.inv_hk_phrases = _Index(_invert_map(self.hk_phrases.map))
        self.inv_gloss = _Index(_invert_map(self.gloss.map))

    def _hk(self, s: str) -> str:
        out, i = [], 0
        while i < len(s):
            m = self.hk_phrases.longest(s, i, 1)
            if m:
                out.append(m[1])
                i += m[0]
                continue
            out.append(self.hk_chars.get(s[i], s[i]))
            i += 1
        return "".join(out)

    def _opencc(self, s: str) -> str:
        out, i = [], 0
        while i < len(s):
            p = self.phrases.longest(s, i, 2)
            if p:
                out.append(p[1])
                i += p[0]
                continue
            out.append(self.chars.get(s[i], s[i]))
            i += 1
        return self._hk("".join(out))

    def convert(self, text: str) -> str:
        if not text or not CJK_RE.search(text):
            return text
        out, gap, i, n = [], [], 0, len(text)
        while i < n:
            g = self.gloss.longest(text, i, 1)
            if g:
                if gap:
                    out.append(self._opencc("".join(gap)))
                    gap = []
                out.append(g[1])
                i += g[0]
                continue
            gap.append(text[i])
            i += 1
        if gap:
            out.append(self._opencc("".join(gap)))
        return "".join(out)

    def fold_hans(self, text: str) -> str:
        """把繁体、香港字形折回简体，供主数据比对。已经是简体的字符保持原样。"""
        if not text:
            return text
        out, i, n = [], 0, len(text)
        while i < n:
            hit = self.inv_gloss.longest(text, i, 1)
            if hit is None:
                hit = self.inv_phrases.longest(text, i, 2)
            if hit is None:
                hit = self.inv_hk_phrases.longest(text, i, 1)
            if hit:
                out.append(hit[1])
                i += hit[0]
                continue
            ch = self.inv_hk_chars.get(text[i], text[i])
            out.append(self.inv_chars.get(ch, ch))
            i += 1
        return "".join(out)


_lock = threading.Lock()
_converter: Optional[Converter] = None


def _get_converter() -> Converter:
    global _converter
    if _converter is None:
        with _lock:
            if _converter is None:
                with open(DATA_PATH, encoding="utf-8") as fh:
                    data = parse_data_js(fh.read())
                with open(GLOSSARY_PATH, encoding="utf-8") as fh:
                    glossary = parse_glossary_js(fh.read())
                _converter = Converter(data, glossary)
    return _converter


@lru_cache(maxsize=4096)
def to_hant_hk(text: str) -> str:
    return _get_converter().convert(text)


def master_key(text: str) -> str:
    """主数据比对键：NFKC、去空白、再把繁体折成简体。转换表不可用时退回折叠前的文本。"""
    import unicodedata
    raw = unicodedata.normalize("NFKC", str(text or "")).strip().casefold()
    raw = "".join(raw.split())
    if not raw:
        return ""
    try:
        return _get_converter().fold_hans(raw)
    except (OSError, ValueError, KeyError):
        return raw


def prefer_stored_label(candidate: str, existing) -> str:
    """若候选与某条已有标签折成同一个键，沿用已有写法，避免简繁各存一条。"""
    raw = str(candidate or "").strip()
    if not raw:
        return raw
    key = master_key(raw)
    for item in existing:
        label = str(item or "").strip()
        if label and master_key(label) == key:
            return label
    return raw


def localize(text, locale: Optional[str]):
    """按语言转换界面文案；非字符串或目标为简体时原样返回。"""
    if not isinstance(text, str) or normalize_locale(locale) != HANT_LOCALE:
        return text
    return to_hant_hk(text)
