# -*- coding: utf-8 -*-
"""首轮注入统计口径（demo/scripts/memory_hint_stats.py）的判别式用例。

why 需要这组用例：口径必须**可判定**且**不许恒真** —— 一个「任何输入都返回 0」或
「任何输入都返回 100%」的分类器看起来同样"干净"，却能让人把两种相反的事都说圆。
本文件用**成对相反**的输入把判据钉住：

  - 同一前缀 `[prior:hint]` 之下，正文等于裸 hint 名 → 记 `bare`（未注入记忆）；
    正文有额外内容 → 记 `memory`（注入成功）。两条必须得到相反的分类。
  - `[prior:retry]` / 无前缀（首轮补检索）都**不得**计入「首轮 hint 通道注入」。
  - 分母为 0 时比率必须是 `None`（不许用 0% 冒充「测了但一次没中」）。
  - hint 覆盖率的两个分母（带键事件 / 全部事件）必须各自正确：键缺失 ≠ 无 hint。

隔离：本文件不 import 任何 app.*；DB_PATH / RAG_DIR 仍指向临时目录（对本文件并非必需，
但与同目录其它用例同款，避免将来顺手 import 时踩到 live 库）。
"""

import os
import sqlite3
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mem_hint_stats_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_y3.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import memory_hint_stats as hs  # noqa: E402


def _mk_db(path, receipts, events):
    """receipts = [(rag_context_json, supplier_name)]；events = [properties 原文]。"""
    con = sqlite3.connect(path)
    try:
        con.execute("create table receipts(rag_context_json text, supplier_name text)")
        con.execute("create table user_event(event_type text, properties text)")
        for payload, name in receipts:
            con.execute("insert into receipts(rag_context_json, supplier_name) values(?,?)",
                        (payload, name))
        for props in events:
            con.execute("insert into user_event(event_type, properties) values('upload', ?)",
                        (props,))
        con.commit()
    finally:
        con.close()
    return path


# 检索载荷的两种真实形态（都来自 live 实测）：带 [供应商名] 头行的新版、直接以版式开头的旧版
PAYLOAD_NEW = "[prior:hint]\n[德利行]\n版式：ncr_handwritten\n1. 菜心 2斤 @5 = 10"
PAYLOAD_OLD = ("[prior:hint]\n版式：printed_delivery_note\n"
               "供应商 德利行 Tak Lee Hong 反馈纠偏：呢行品名認錯咗\n- 菜心 1.0斤 @8.5 = 8.5")
BARE = "[prior:hint]\n德利行"


# -------------------------------------------------------------
# 1. 成对相反：注入成功 vs 只注入裸 hint 名
# -------------------------------------------------------------
@pytest.mark.parametrize("payload", [PAYLOAD_NEW, PAYLOAD_OLD])
def test_memory_payload_counted_as_injected(tmp_path, payload):
    db = _mk_db(str(tmp_path / "a.db"), [(payload, "德利行")], [])
    fr = hs.collect(db)["first_round_injection"]
    assert (fr["hint_channel_receipts"], fr["injected_receipts"]) == (1, 1)
    assert fr["bare_hint_only_receipts"] == 0
    assert fr["injected_rate_pct_of_hint_channel"] == 100.0


def test_bare_hint_name_is_not_counted_as_injected(tmp_path):
    """hint 非空但该供应商无记忆 → 写进去的是裸 hint 名，不得算作首轮注入记忆。"""
    db = _mk_db(str(tmp_path / "b.db"), [(BARE, "德利行")], [])
    fr = hs.collect(db)["first_round_injection"]
    assert (fr["hint_channel_receipts"], fr["injected_receipts"]) == (1, 0)
    assert fr["bare_hint_only_receipts"] == 1
    assert fr["injected_rate_pct_of_hint_channel"] == 0.0


def test_both_shapes_side_by_side(tmp_path):
    """同一库里两种形态并存 → 分类互斥且各自计数（防止「只认一种」的恒真判据）。"""
    db = _mk_db(str(tmp_path / "c.db"), [(PAYLOAD_NEW, "德利行"), (BARE, "德利行")], [])
    fr = hs.collect(db)["first_round_injection"]
    assert fr["hint_channel_receipts"] == 2
    assert fr["injected_receipts"] == 1
    assert fr["bare_hint_only_receipts"] == 1
    assert fr["injected_rate_pct_of_hint_channel"] == 50.0


# -------------------------------------------------------------
# 2. 非首轮通道不得混入
# -------------------------------------------------------------
def test_retry_and_backfill_are_not_first_round(tmp_path):
    db = _mk_db(str(tmp_path / "d.db"), [
        ("[prior:retry]\n[德利行]\n版式：x", "德利行"),
        ("[德利行]\n版式：x", "德利行"),          # 无前缀 = 首轮补检索
        ("[prior:parse]\n[德利行]\n版式：x", "德利行"),
    ], [])
    fr = hs.collect(db)["first_round_injection"]
    assert fr["hint_channel_receipts"] == 0
    assert fr["injected_rate_pct_of_hint_channel"] is None
    assert fr["retry_channel_receipts"] == 1
    assert fr["parse_channel_receipts"] == 1
    assert fr["backfill_receipts"] == 1


def test_empty_payload_excluded(tmp_path):
    db = _mk_db(str(tmp_path / "e.db"), [("", "德利行"), ("{}", "德利行")], [])
    fr = hs.collect(db)["first_round_injection"]
    assert fr["hint_channel_receipts"] == 0
    assert fr["injected_rate_pct_of_hint_channel"] is None


# -------------------------------------------------------------
# 3. hint 覆盖率的两个分母
# -------------------------------------------------------------
def test_hint_coverage_two_denominators(tmp_path):
    events = ['{"has_vendor_hint": true}', '{"has_vendor_hint": false}', "{}", None]
    db = _mk_db(str(tmp_path / "f.db"), [], events)
    hc = hs.collect(db)["hint_coverage"]
    assert hc["upload_events_total"] == 4
    assert hc["upload_events_with_hint_key"] == 2
    assert hc["hint_true"] == 1 and hc["hint_false"] == 1
    assert hc["coverage_pct_of_keyed_events"] == 50.0
    assert hc["coverage_pct_of_all_events"] == 25.0


def test_coverage_is_none_when_no_events(tmp_path):
    db = _mk_db(str(tmp_path / "g.db"), [], [])
    hc = hs.collect(db)["hint_coverage"]
    assert hc["coverage_pct_of_keyed_events"] is None
    assert hc["coverage_pct_of_all_events"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
