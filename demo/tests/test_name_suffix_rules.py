# -*- coding: utf-8 -*-
"""名称后缀剥离测试（SPEC_PHASE2A §4.5 合成夹具）。

第一验收项是 §4.2 阴性对照 24 条零误伤（含 S11 的 10司馬斤 守卫——该测试文件
tests/test_fix_b_p0_sku_explosion.py 不许改动，其守卫在这里镜像钉一遍）；其次是
正例命中、迭代至稳定（无死循环）、fail-fast（缺文件/坏 YAML/非法正则不得静默降级）、
单点接线与主数据无碰撞。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="name_suffix_rules_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_suffix.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from app.services import name_suffix_rules as nsr  # noqa: E402
from app.services.receipt_utils import _normalize_sku_name  # noqa: E402

RULES = nsr.load_rules()

# §4.2 阴性对照 24 条（校准实测零误伤，逐条钉死；宁窄勿宽的判别面）
NEGATIVES = [
    "特級菜心", "菜心苗", "半殼生蠔", "江團魚", "西蘭花", "急凍豬頸肉",
    "鮮牛油菠蘿包", "乾炒牛河", "菜冧", "特濃凍奶茶", "有機菜心", "有机菜心",
    "五香肉丁", "天大龍牌五香肉丁", "本地白菜", "鮮活草蝦", "白蘿蔔絲",
    "好味豬油", "朱古力", "大天鵝鴨五花肉",
    "本地新鲜菜心 10司馬斤",   # S11 守卫：斤/司馬斤 重量族禁入剥离表
    "TS-4X8",                  # 货号：无容器词的 NxM 禁入
    "WTC-14C",
    "H12+8-2K",
]

# §4.2 正例（必须命中剥离）
POSITIVES = [
    ("維他檸檬茶500毫升裝", "維他檸檬茶"),
    ("維他檸檬茶500毫升瓶", "維他檸檬茶"),
    ("維他檸檬茶500毫升膠", "維他檸檬茶"),
    ("維他檸檬茶500毫升罐", "維他檸檬茶"),
    ("獅球嘜粟米油5L*2樽", "獅球嘜粟米油"),
    ("雙喜牌特級香米25kg", "雙喜牌特級香米"),
    ("雙喜牌特級香米 25kg", "雙喜牌特級香米"),     # 含空格变体
    ("金彩脆香米(粒裝)X25kg", "金彩脆香米(粒裝)"),  # 括号规格在结尾之前的形态
]


# -------------------------------------------------------------
# F-阴性：零误伤（第一验收项）
# -------------------------------------------------------------
@pytest.mark.parametrize("name", NEGATIVES)
def test_negative_no_false_strip(name):
    assert nsr.strip_suffix(name, RULES) == name
    # 接线形态：归一输出再过剥离不变（归一不产生新的可剥尾巴）
    assert nsr.strip_suffix(_normalize_sku_name(name), RULES) == _normalize_sku_name(name)


def test_negative_count_pinned():
    assert len(NEGATIVES) == 24, "阴性对照必须满 24 条（§4.2）"


@pytest.mark.parametrize("raw,core", POSITIVES)
def test_positive_strips(raw, core):
    assert nsr.strip_suffix(raw, RULES) == core
    # 接线后：_normalize_sku_name 单点消费，正例经归一同样剥到核心词
    assert _normalize_sku_name(raw) == core


# -------------------------------------------------------------
# F-稳定：复合后缀迭代剥离至稳定，≤max_repeat，无死循环
# -------------------------------------------------------------
def test_iterative_strip_multi_suffix():
    # 手算两轮：第 1 轮模式 5 剥「6包裝」，第 2 轮模式 1 剥「500毫升裝」→ 維他檸檬茶
    assert nsr.strip_suffix("維他檸檬茶500毫升裝 6包裝", RULES) == "維他檸檬茶"


def test_iterative_strip_reaches_fixed_point():
    # 复合「…2樽装」：模式表只匹配单个容器词（樽/装 各自成词），樽装连写不在
    # 校准五条模式内（宁窄勿宽，禁入清单不放宽）——期望是立即稳定、结果为不动点。
    out = nsr.strip_suffix("大豆油 5L*2樽装", RULES)
    assert nsr.strip_suffix(out, RULES) == out, "必须是不动点（至多 max_repeat 轮）"
    assert RULES["max_repeat"] == 3


# -------------------------------------------------------------
# F-fail-fast：缺文件 / 坏 YAML / 非法正则 → SystemExit 指明路径
# -------------------------------------------------------------
def test_fail_fast_missing_file(tmp_path):
    missing = str(tmp_path / "nope.yaml")
    with pytest.raises(SystemExit) as ei:
        nsr.load_rules(missing)
    assert missing in str(ei.value)


def test_fail_fast_bad_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("suffix_patterns: [unclosed", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        nsr.load_rules(str(bad))
    assert "bad.yaml" in str(ei.value)


def test_fail_fast_illegal_regex(tmp_path):
    bad = tmp_path / "badre.yaml"
    bad.write_text('version: "t"\nmax_repeat: 1\nsuffix_patterns:\n  - "("\n',
                   encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        nsr.load_rules(str(bad))
    assert "badre.yaml" in str(ei.value)


def test_fail_fast_no_silent_degrade():
    """接线侧不得静默降级：规则表被挪走后 _normalize_sku_name 必须 fail-fast。"""
    import app.services.receipt_utils as ru
    backup = ru._SUFFIX_RULES
    try:
        ru._SUFFIX_RULES = None
        real_exists = os.path.exists

        def fake_exists(p):
            if str(p).endswith("name_suffix_rules.yaml"):
                return False
            return real_exists(p)

        os_path = os.path
        orig_exists = os_path.exists
        os.path.exists = fake_exists
        try:
            with pytest.raises(SystemExit):
                ru._normalize_sku_name("維他檸檬茶500毫升裝")
        finally:
            os.path.exists = orig_exists
    finally:
        ru._SUFFIX_RULES = backup


# -------------------------------------------------------------
# F-接线：单点接线生效且旧能力无损
# -------------------------------------------------------------
def test_wiring_suffix_stripped_via_normalize():
    assert _normalize_sku_name("維他檸檬茶500毫升裝") == "維他檸檬茶"


def test_wiring_legacy_capabilities_intact():
    # 旧行为不变（流水号剥离 / 数量前缀 / 结尾括号）
    assert _normalize_sku_name("有机菜心_1787140420") == "有机菜心"
    assert _normalize_sku_name("本地新鲜菜心_1787140411") == "本地新鲜菜心"
    assert _normalize_sku_name("6包裝 維他朱古奶") == "維他朱古奶"
    # S11：10司馬斤 不剥
    assert "司馬斤" in _normalize_sku_name("本地新鲜菜心 10司馬斤") or \
        _normalize_sku_name("本地新鲜菜心 10司馬斤") == "本地新鲜菜心 10司馬斤"


# -------------------------------------------------------------
# F-主数据无碰撞（tmp 级碰撞门）
# -------------------------------------------------------------
def test_master_data_no_collision_after_normalize():
    os.environ["DB_PATH"] = os.path.join(_TMP, "suffix_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(__import__("app.db", fromlist=["db"]))
    import app.db as db
    names = ["維他檸檬茶500毫升罐", "維他朱古奶", "大天鵝鴨五花肉 (G80g*24) x2",
             "ANIKAR A雞粉 (5kg*2)", "黑椒汁(500g*6)", "白蘿蔔絲(625ml*12)",
             "冰糖碎(10斤*5)", "本地新鲜菜心 10司馬斤"]
    ids = set()
    for n in names:
        sid, err = db.create_sku(n, base_unit="個")
        assert err is None, (n, err)
        ids.add(sid)
    keys = [_normalize_sku_name(s.name) for s in db.list_skus(tenant_id="default")]
    assert len(keys) == len(set(keys)), "主数据归一键必须零碰撞：%s" % sorted(keys)
