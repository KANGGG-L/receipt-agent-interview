# -*- coding: utf-8 -*-
"""单位换算：唯一的真相源。

结构：内置种子（SEED_CONVERSIONS）+ 租户自定义换算（units_store，按租户存储）组成一张
换算图，查询时 BFS 多跳求系数（如 箱 -> 瓶 -> 毫升）。租户自定义覆盖同一对单位的种子。

语义：换算 (from, to, factor) 表示 1 个 from = factor 个 to；反向换算由查询时自动推导。

「斤」在香港单据上指司马斤，统一为 0.6048 公斤；内地市斤用显式单位「市斤」（0.5 公斤）。
老板可以在设置里覆盖。

旧调用兼容：convert_unit_quantity / unit_to_kg_factor / check_unit_compatibility 的
tenant_id 都是可选参数；未传时取当前请求的租户上下文，再没有就只用内置种子。
"""

import logging
import re
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("unit_factors")


class UnitConversionError(ValueError):
    """两个单位之间没有换算关系。继承 ValueError，旧调用方的 except 不受影响。"""

    def __init__(self, from_unit, to_unit):
        self.from_unit = from_unit
        self.to_unit = to_unit
        super().__init__("「%s」和「%s」之间还没有换算关系" % (from_unit, to_unit))


# 同义写法 -> 规范名（规范名即界面上显示的名字）
ALIASES: Dict[str, str] = {
    "kg": "公斤", "千克": "公斤", "公斤": "公斤",
    "g": "克", "mg": "毫克",
    "司马斤": "斤", "司馬斤": "斤", "港斤": "斤", "catty": "斤",
    "司马两": "两", "司馬兩": "两", "港两": "两", "港兩": "两", "兩": "两",
    "市两": "市两", "市兩": "市两",
    "lb": "磅", "lbs": "磅", "pound": "磅",
    "t": "吨", "噸": "吨",
    "l": "升", "liter": "升", "litre": "升",
    "ml": "毫升",
    "個": "个", "隻": "只", "條": "条", "樽": "瓶", "btl": "瓶", "枝": "支",
    "case": "箱", "ctn": "箱", "pack": "包", "bag": "袋",
}

# (单位, 类别)
SEED_UNITS: List[Tuple[str, str]] = [
    ("公斤", "weight"), ("克", "weight"), ("毫克", "weight"), ("斤", "weight"),
    ("市斤", "weight"), ("两", "weight"), ("市两", "weight"), ("磅", "weight"),
    ("吨", "weight"),
    ("升", "volume"), ("毫升", "volume"),
    ("份", "count"), ("件", "count"), ("个", "count"), ("只", "count"),
    ("条", "count"), ("包", "count"), ("瓶", "count"), ("罐", "count"),
    ("盒", "count"), ("碗", "count"), ("杯", "count"), ("碟", "count"),
    ("支", "count"), ("粒", "count"), ("箱", "count"), ("袋", "count"),
]

# (from, to, factor, note)：1 个 from = factor 个 to
SEED_CONVERSIONS: List[Tuple[str, str, float, str]] = [
    ("公斤", "克", 1000.0, ""),
    ("克", "毫克", 1000.0, ""),
    ("吨", "公斤", 1000.0, ""),
    ("斤", "公斤", 0.6048, "港斤（司马斤），香港单据口径"),
    ("两", "公斤", 0.0378, "港两（司马两），1 斤 = 16 两"),
    ("市斤", "公斤", 0.5, "内地市斤"),
    ("市两", "公斤", 0.05, "内地市两"),
    ("磅", "公斤", 0.45359237, ""),
    ("升", "毫升", 1000.0, ""),
]

_KIND_BY_NAME = dict(SEED_UNITS)


def norm_unit_name(raw) -> str:
    """单位名归一：全半角、空白、大小写，去掉结尾的括号规格（箱（48罐）-> 箱），并映射同义写法。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).strip()
    s = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", s)
    s = re.sub(r"\s+", "", s).lower()
    if not s:
        return ""
    return ALIASES.get(s, s)


def seed_kind(name: str) -> Optional[str]:
    return _KIND_BY_NAME.get(norm_unit_name(name))


def _seed_edges() -> List[Tuple[str, str, float]]:
    return [(a, b, f) for a, b, f, _n in SEED_CONVERSIONS]


def build_graph(extra_edges: Optional[List[Tuple[str, str, float]]] = None) -> Dict[str, Dict[str, float]]:
    """种子 + 租户边 -> 邻接表。租户边覆盖种子里同一对单位（任一方向）。"""
    extra = [(norm_unit_name(a), norm_unit_name(b), float(f)) for a, b, f in (extra_edges or [])]
    overridden = set()
    for a, b, _f in extra:
        overridden.add((a, b))
        overridden.add((b, a))
    graph: Dict[str, Dict[str, float]] = {}

    def add(a, b, f):
        if not a or not b or a == b or not f or f <= 0:
            return
        graph.setdefault(a, {})[b] = f
        graph.setdefault(b, {})[a] = 1.0 / f

    for a, b, f in _seed_edges():
        if (a, b) not in overridden:
            add(a, b, f)
    for a, b, f in extra:
        add(a, b, f)
    return graph


def find_path(graph, src: str, dst: str):
    """BFS 最少跳数。返回 (factor, [单位链]) 或 None。src==dst 时 factor=1。"""
    if src == dst:
        return 1.0, [src]
    if src not in graph or dst not in graph:
        return None
    prev = {src: None}
    queue = [src]
    while queue:
        cur = queue.pop(0)
        for nxt in sorted(graph.get(cur, {})):
            if nxt in prev:
                continue
            prev[nxt] = cur
            if nxt == dst:
                chain = [dst]
                while prev[chain[-1]] is not None:
                    chain.append(prev[chain[-1]])
                chain.reverse()
                factor = 1.0
                for x, y in zip(chain, chain[1:]):
                    factor *= graph[x][y]
                return round(factor, 10), chain
            queue.append(nxt)
    return None


_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3.0


def invalidate_cache():
    with _cache_lock:
        _cache.clear()


def _resolve_tenant(tenant_id):
    if tenant_id:
        return str(tenant_id)
    try:
        from app import db
        return db.get_current_tenant_id() or None
    except Exception:
        return None


def graph_for_tenant(tenant_id=None):
    tid = _resolve_tenant(tenant_id)
    if not tid:
        return _SEED_GRAPH
    now = time.time()
    try:
        from app import db
        engine_ref = db._engine
    except Exception:
        engine_ref = None
    with _cache_lock:
        hit = _cache.get(tid)
        if hit and hit[0] is engine_ref and now - hit[1] < _CACHE_TTL:
            return hit[2]
    try:
        from app import units_store
        edges = units_store.tenant_edges(tid)
    except Exception as e:
        logger.warning("读取租户单位换算失败，退回内置种子：%s", e)
        return _SEED_GRAPH
    g = build_graph(edges)
    with _cache_lock:
        _cache[tid] = (engine_ref, now, g)
    return g


_SEED_GRAPH = build_graph()


def conversion_factor(from_unit, to_unit, tenant_id=None):
    """1 个 from_unit 等于多少个 to_unit；换算不了返回 None。"""
    a, b = norm_unit_name(from_unit), norm_unit_name(to_unit)
    if not a or not b:
        return None
    res = find_path(graph_for_tenant(tenant_id), a, b)
    return res[0] if res else None


def conversion_path(from_unit, to_unit, tenant_id=None):
    a, b = norm_unit_name(from_unit), norm_unit_name(to_unit)
    if not a or not b:
        return None
    return find_path(graph_for_tenant(tenant_id), a, b)


def _legacy_table(target: str) -> Dict[str, float]:
    """由种子推出旧版的「名字 -> 系数」表（含同义写法），保证只有一份定义。"""
    out: Dict[str, float] = {}
    names = set(ALIASES) | {n for n, _k in SEED_UNITS}
    for nm in names:
        res = find_path(_SEED_GRAPH, norm_unit_name(nm), target)
        if res:
            out[nm] = res[0]
    return out


UNIT_WEIGHT_TO_KG: Dict[str, float] = _legacy_table("公斤")
UNIT_VOLUME_TO_L: Dict[str, float] = {k: round(v / 1000.0, 10) for k, v in _legacy_table("毫升").items()}
DIMENSION_WEIGHT: Set[str] = set(UNIT_WEIGHT_TO_KG.keys())
DIMENSION_VOLUME: Set[str] = set(UNIT_VOLUME_TO_L.keys())
DIMENSION_COUNT: Set[str] = {n for n, k in SEED_UNITS if k == "count"} | {
    "個", "隻", "條"}


def unit_to_kg_factor(unit: Optional[str], tenant_id=None) -> Optional[float]:
    """重量单位折算为公斤的系数；换算不了返回 None（调用方不得假装折算）。"""
    return conversion_factor(unit, "公斤", tenant_id)


def check_unit_compatibility(u1: str, u2: str, tenant_id=None) -> Tuple[bool, str]:
    """两个单位是否存在换算关系。"""
    if not norm_unit_name(u1) or not norm_unit_name(u2):
        return False, "单位不能为空"
    if conversion_factor(u1, u2, tenant_id) is not None:
        return True, ""
    return False, "「%s」和「%s」之间还没有换算关系" % (u1, u2)


def convert_unit_quantity(qty: float, from_unit: str, to_unit: str, tenant_id=None) -> float:
    """把数量从 from_unit 换算成 to_unit。

    单位相同直接返回；有换算路径（含多跳）则换算；否则抛 UnitConversionError（ValueError 子类）。
    """
    a, b = norm_unit_name(from_unit), norm_unit_name(to_unit)
    if a == b:
        return float(qty)
    if qty == 0.0:
        return 0.0
    factor = conversion_factor(from_unit, to_unit, tenant_id)
    if factor is None:
        raise UnitConversionError(from_unit, to_unit)
    return round(float(qty) * factor, 6)
