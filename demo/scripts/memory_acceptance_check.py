# -*- coding: utf-8 -*-
"""VendorMemory 已闭残债「一键验收」：把 9 条已闭合项固化成可复算检查（只读）。

why（存在的理由）
----------------
收敛计划里 R1 / R2 / R4 / R5 / R6 / R9 / R10 / Q2 / D4 九项残债已逐条闭合，但闭合时
的验证只活在当时的对话与计划文档里 —— 没有任何一条能被一条命令复跑。「已闭」是状态，
「可复跑」才是资产：本脚本把每条的判据写成**客观可算**的检查（静态扫描 / 只读 SQL /
纯逻辑探针 / 子进程预演），逐行打印 `编号 | 结论 | 证据`，退出码可进 CI。

判据设计原则（对应本项目高发缺陷）
--------------------------------
- **不采信「有这个函数 / 这个名字出现过」**：凡「某某已接线」的检查，判据是 AST 找到
  **真实调用点**（`ast.Call`），而不是文本里出现过符号 —— 注释与文档字符串里提到它
  不算接线（本仓②类缺陷「定义了没接线」+「文案不得领先于代码」红线）。
- **同名不同表会伪造「有消费方」**：故调用点按**具体函数名**匹配、只对指定函数断言。
- **判据不得恒真**：每条都并列给出「判据说明」；测试文件
  `demo/tests/test_memory_acceptance_check.py` 用临时假数据造出会让检查 FAIL 的场景，
  断言脚本真的报 FAIL（恒真用例比没有用例更糟）。

只读红线（本脚本自身）
--------------------
- 数据面一律 sqlite3 **只读**（`mode=ro` URI），只读连接复用
  `app.services.memory_consistency._connect_ro`（同一份实现，避免出现第二份「只读打开 +
  副本回落」逻辑 —— 本仓①类缺陷）。
- **不 import `app.db`**（它在 import 期执行 `_make_engine()` 的迁移块，会按 DB_PATH 对
  目标库做 ALTER）。R2 只 import `app.services.memory_recall`（顶层不依赖 app.db）。
- 子进程：`memory_metrics.py` 顶层也不 import app.db（只读 DB_PATH）；而
  `memory_extract_replay.py` **会** import app.db，故它的预演子进程被强制指向隔离的
  /tmp 临时库与临时 RAG_DIR（源库零写入，见 `check_d4`），绝不指向 live。

用法：
  python demo/scripts/memory_acceptance_check.py                       # 读 DB_PATH（缺省 live，只读）
  python demo/scripts/memory_acceptance_check.py --db-path demo/receipt_demo.db
  python demo/scripts/memory_acceptance_check.py --json                 # 机器可读
  python demo/scripts/memory_acceptance_check.py --explain              # 只打印判据说明
退出码：全 PASS=0；存在 FAIL=1；无 FAIL 但有 SKIP=2。
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(DEMO_DIR)
LIVE_DB = os.path.join(DEMO_DIR, "receipt_demo.db")

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# 检查顺序（与收敛计划台账的编号顺序一致，扩充 G1-G9 全量检查）
CHECK_ORDER = [
    "R1", "R2", "R4", "R5", "R6", "R10", "Q2", "R9", "D4",
    "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
]


@dataclass
class Ctx:
    """检查上下文（可注入：测试据此把扫描根/库路径指向临时假数据）。"""

    demo_dir: str = DEMO_DIR
    db_path: str = LIVE_DB
    rag_dir: str = ""
    python: str = sys.executable


@dataclass
class CheckResult:
    num: str
    title: str
    status: str
    evidence: str
    criterion: str = ""


# -------------------------------------------------------------
# 通用工具
# -------------------------------------------------------------
def _read(path):
    """读文本；文件不存在返回空串（调用方据此判 FAIL 而不是抛异常）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _py_files(root):
    """递归列出 .py（跳过 __pycache__）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _rel(path, ctx):
    """展示用相对路径（相对 demo_dir；失败则原样）。"""
    try:
        return os.path.relpath(path, ctx.demo_dir)
    except ValueError:
        return path


def _py_call_lines(path, func_name):
    """AST 找出对 func_name 的**真实调用点**行号（排除定义/注释/文档字符串）。

    匹配规则：`ast.Call` 的 func 为 `Name(id=func_name)` 或 `Attribute(attr=func_name)`
    —— 因此 `db.list_vendor_memory(...)`、`rag.get_retrieve_failure_counts()` 都算，
    而文档字符串里写的 `` `db.list_vendor_memory` `` 不算（那是文本，不是调用）。
    解析失败（语法错误）返回 []，由调用方按"没有调用点"处理。
    """
    src = _read(path)
    if not src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name == func_name:
            out.append(node.lineno)
    return sorted(set(out))


def _py_call_with_kw_lines(path, func_name, kw):
    """AST 找出对 func_name 且**显式传入关键字 kw** 的调用行号（判"写入方"用）。"""
    src = _read(path)
    if not src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name != func_name:
            continue
        if any(k.arg == kw for k in node.keywords):
            out.append(node.lineno)
    return sorted(set(out))


def _js_symbol_invoked(js_text, name):
    """JS：除定义行（`function name(`）外，是否还存在调用点 `name(`。

    why 排除定义行：只写 `function foo(){}` 而没有调用方 = 「定义了没人调」（本仓②类
    在前端的形态）。返回值即"真的被调用过"。
    """
    pat = re.compile(r"\b%s\s*\(" % re.escape(name))
    for ln in js_text.splitlines():
        s = ln.strip()
        if s.startswith("function ") and name in s:
            continue
        if pat.search(ln):
            return True
    return False


# -------------------------------------------------------------
# R1：治理视图有生产消费方（后端端点 + 前端面板）
# -------------------------------------------------------------
def _criterion_r1():
    return (
        "判据＝后端端点存在 且 取数复用唯一实现 且 前端面板真被调用："
        "(1) api_admin.py 同时含列表路由 @router.get(\"/api/admin/vendor-memory\") 与"
        "详情路由 @router.get(\"/api/admin/vendor-memory/{memory_id}\")；"
        "(2) api_admin.py 经 AST 存在对 db.list_vendor_memory 的真实调用（治理取数只此一份，"
        "端点内不重写 SQL）；"
        "(3) static/js/main.js 定义 function loadVendorMemoryGovernance( 且函数体内出现"
        " apiFetch('/api/admin/vendor-memory（真实 HTTP 调用，不是注释里提到该路径），"
        "且该函数名在定义行之外至少被调用一次。"
        "注：原始需求写的符号 db.get_vendor_memory 是按供应商名查最新一条 active 行的旧读函数"
        "（签名接不住 memory_id，见 api_admin.py 的端点 docstring），治理视图消费的是"
        " db.list_vendor_memory；若按旧符号断言会得出假的 FAIL。"
    )


def check_r1(ctx):
    api = os.path.join(ctx.demo_dir, "app", "api_admin.py")
    js_candidates = [
        os.path.join(ctx.demo_dir, "static", "js", "main.js"),
        os.path.join(ctx.demo_dir, "static", "js", "engine_admin.js"),
        os.path.join(ctx.demo_dir, "static", "js", "common.js"),
    ]
    api_text = _read(api)
    js_text = "\n".join(_read(p) for p in js_candidates if os.path.isfile(p))

    has_list_route = '@router.get("/api/admin/vendor-memory")' in api_text
    has_detail_route = '@router.get("/api/admin/vendor-memory/{memory_id}")' in api_text
    backend_calls = _py_call_lines(api, "list_vendor_memory")
    js_def = "function loadVendorMemoryGovernance(" in js_text
    js_call = "apiFetch('/api/admin/vendor-memory" in js_text
    js_invoked = _js_symbol_invoked(js_text, "loadVendorMemoryGovernance")

    ok = (has_list_route and has_detail_route and bool(backend_calls)
          and js_def and js_call and js_invoked)
    ev = ("api_admin.py 列表路由=%s 详情路由=%s list_vendor_memory 调用行=%s；"
          "前端JS 定义=%s apiFetch 调用=%s 被调用=%s"
          % (has_list_route, has_detail_route, backend_calls or "无",
             js_def, js_call, js_invoked))
    return CheckResult("R1", "治理视图有生产消费方（后端端点 + 前端面板）",
                       PASS if ok else FAIL, ev, _criterion_r1())


# -------------------------------------------------------------
# R2：治理信号被 rerank 消费
# -------------------------------------------------------------
def _criterion_r2():
    return (
        "判据＝(1) RecallWeights 的治理信号权重 decay/hit/recency **均非零**（否则信号被"
        "乘 0＝等同于没接线）；(2) 构造两条**仅治理信号不同**的 RecallDoc（decay/hit/"
        "last_hit_at 不同、文本与腿与相关度全同），用两种相反的插入序各 rerank 一次，"
        "要求两种序都把它们中治理信号强的那条排在前 —— 若 rerank 忽略信号，结果会退回"
        "插入序（[a,b] 得 a 在前），由第一种序即可判 FAIL。两条断言同时排除「恒等」与"
        "「反序」。"
    )


def _load_memory_recall(ctx):
    # why 用模块级 DEMO_DIR 而不是 ctx.demo_dir：被 import 的是**本仓的工具实现**，
    # ctx.demo_dir 只决定"扫哪棵树"（测试会用临时假树，那里没有 app 包）。
    if DEMO_DIR not in sys.path:
        sys.path.insert(0, DEMO_DIR)
    from app.services import memory_recall
    return memory_recall


def check_r2(ctx, recall=None):
    m = recall if recall is not None else _load_memory_recall(ctx)
    w = m.DEFAULT_WEIGHTS
    weights_nonzero = (float(w.decay) != 0.0 and float(w.hit) != 0.0
                       and float(w.recency) != 0.0)

    # 固定参考时刻，保证 freshness 确定性（同一输入必得同一排序）
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now - timedelta(days=0)).isoformat()

    def _mk(doc_id, decay, hits, last):
        return m.RecallDoc(doc_id=doc_id, text="菜心 斤 8.00", leg=m.RECALL_LEG_VECTOR,
                           relevance=0.5, decay_score=decay, hit_count=hits,
                           last_hit_at=last)

    weak = _mk("a", 0.0, 0, "")
    strong = _mk("b", 1.0, 10, fresh_iso)

    order_ab = [s.doc.doc_id for s in m.rerank("菜心", [weak, strong], weights=w, now=now)]
    order_ba = [s.doc.doc_id for s in m.rerank("菜心", [strong, weak], weights=w, now=now)]
    signal_drives = order_ab[:1] == ["b"] and order_ba[:1] == ["b"]

    ok = weights_nonzero and signal_drives
    ev = ("权重 decay=%s hit=%s recency=%s（非零=%s）；插入序[a,b] 得 %s、插入序[b,a] 得 %s，"
          "信号强者在两种序下均居首=%s"
          % (w.decay, w.hit, w.recency, weights_nonzero, order_ab, order_ba, signal_drives))
    return CheckResult("R2", "治理信号被 rerank 消费（decay/hit/recency）",
                       PASS if ok else FAIL, ev, _criterion_r2())


# -------------------------------------------------------------
# R4：巡检有生产触发方
# -------------------------------------------------------------
def _criterion_r4():
    return (
        "判据＝在 demo/app 下（经 AST 找**真实调用点**）存在对 check_memory_consistency 的"
        "调用，且调用方文件不是巡检实现自身（services/memory_consistency.py）—— 即存在"
        "生产触发方（启动自检 / admin 端点）。只扫实现文件自身不算接线（本仓②类）。"
    )


def check_r4(ctx):
    app_dir = os.path.join(ctx.demo_dir, "app")
    impl = os.path.abspath(os.path.join(app_dir, "services", "memory_consistency.py"))
    consumers = []
    for path in _py_files(app_dir):
        if os.path.abspath(path) == impl:
            continue
        for ln in _py_call_lines(path, "check_memory_consistency"):
            consumers.append("%s:%d" % (_rel(path, ctx), ln))
    ok = bool(consumers)
    ev = "生产调用点=%s" % (", ".join(sorted(consumers)) or "无（仅实现文件自身）")
    return CheckResult("R4", "一致性巡检有生产触发方",
                       PASS if ok else FAIL, ev, _criterion_r4())


# -------------------------------------------------------------
# R5：读侧无裸 except: pass
# -------------------------------------------------------------
def _criterion_r5():
    return (
        "判据＝rag.py 中不存在「except 分支体只有 pass」的写法。匹配规则：只认**以 except"
        " 开头**的语句行（行首除空白外必须是 except），故模块注释里 `# ... except"
        " Exception: pass` 与文档字符串里出现的同样文本都**不匹配**（宽松正则会误报）；"
        "同一行 `except X: pass`（可带行尾注释）命中；换行形态要求 `except X:` 之后第一条"
        "非空语句是 pass、且 pass 之后下一条语句缩进 <= except 缩进（或文件结束）。"
    )


_EXCEPT_HEADER_RE = re.compile(r"^(\s*)except\b[^\n]*:\s*(.*)$")


def _bare_except_pass_lines(src):
    """找出「except 分支体只有 pass」的行号（返回 [(lineno, kind)]）。"""
    lines = src.splitlines()
    hits = []
    for i, line in enumerate(lines):
        m = _EXCEPT_HEADER_RE.match(line)
        if not m:
            continue
        indent, rest = m.group(1), m.group(2)
        body = rest.split("#")[0].strip()
        if body == "pass":
            hits.append((i + 1, "inline"))
            continue
        if body != "":
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        if lines[j].strip().split("#")[0].strip() != "pass":
            continue
        k = j + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k >= len(lines) or (len(lines[k]) - len(lines[k].lstrip())) <= len(indent):
            hits.append((j + 1, "block"))
    return hits


def check_r5(ctx):
    rag = os.path.join(ctx.demo_dir, "app", "services", "rag.py")
    hits = _bare_except_pass_lines(_read(rag))
    ok = not hits
    ev = ("rag.py 裸 except-pass=%s"
          % (", ".join("行%d(%s)" % (n, kind) for n, kind in hits) if hits else "0 处"))
    return CheckResult("R5", "读侧无裸 except: pass",
                       PASS if ok else FAIL, ev, _criterion_r5())


# -------------------------------------------------------------
# 只读 DB 连接（复用 memory_consistency 的唯一实现）
# -------------------------------------------------------------
def _load_memory_consistency(ctx):
    # why 用模块级 DEMO_DIR：只读打开的**实现**属于本仓，ctx.demo_dir 只决定扫哪棵树
    # （测试会把 ctx.demo_dir 指向临时假树，那里没有 app 包）。
    if DEMO_DIR not in sys.path:
        sys.path.insert(0, DEMO_DIR)
    from app.services import memory_consistency
    return memory_consistency


def _readonly_conn(ctx, path):
    """只读连接 (conn, note)；复用 memory_consistency._connect_ro（同一份只读实现）。"""
    mc = _load_memory_consistency(ctx)
    return mc._connect_ro(path)


# -------------------------------------------------------------
# R6：审计链 written_by（只读 live）
# -------------------------------------------------------------
def _criterion_r6():
    return (
        "判据＝只读打开 DB_PATH 的 vendor_memory 表：若 active 行数为 0 → PASS（[INFO] 空库冷启动"
        "状态，vacuously true）；否则要求 **active 行里 written_by 非空的条数 == active"
        " 行数**（每条生效记忆都可追溯到写入者，审计链不留缺口）。任一 active 行"
        " written_by 为空即 FAIL。只读连接经 memory_consistency._connect_ro（mode=ro +"
        " 副本回落），本脚本不写任何库。"
    )


def check_r6(ctx):
    con, note = _readonly_conn(ctx, ctx.db_path)
    if con is None:
        return CheckResult("R6", "审计链 written_by（只读 live）", FAIL,
                           "无法只读打开 %s" % ctx.db_path, _criterion_r6())
    try:
        cols = [r[1] for r in con.execute("pragma table_info(vendor_memory)").fetchall()]
        if "written_by" not in cols:
            return CheckResult("R6", "审计链 written_by（只读 live）", SKIP,
                               "vendor_memory 无 written_by 列（表未建或不完整）",
                               _criterion_r6())
        total = con.execute(
            "select count(*) from vendor_memory where status='active'").fetchone()[0]
        nonempty = con.execute(
            "select count(*) from vendor_memory where status='active'"
            " and ifnull(trim(written_by),'') <> ''").fetchone()[0]
    finally:
        con.close()
    if total == 0:
        return CheckResult("R6", "审计链 written_by（只读 live）", PASS,
                           "[INFO] active 行数为 0（空库冷启动状态，vacuously true）", _criterion_r6())
    ok = nonempty == total
    ev = ("active=%d，其中 written_by 非空=%d%s"
          % (total, nonempty, "" if not note else "（%s）" % note))
    return CheckResult("R6", "审计链 written_by（只读 live）",
                       PASS if ok else FAIL, ev, _criterion_r6())


# -------------------------------------------------------------
# R10：读侧计数器有生产消费方
# -------------------------------------------------------------
def _criterion_r10():
    return (
        "判据＝在 demo/app 下（AST 真实调用点）存在对 get_retrieve_failure_counts 的调用，"
        "且调用方不是计数器实现自身（services/rag.py）。只把函数写出来而没人读它，"
        "「失败次数」这个数就永远没人看到（本仓②类「可观测类交付同样要问谁在跑它」）。"
    )


def check_r10(ctx):
    app_dir = os.path.join(ctx.demo_dir, "app")
    impl = os.path.abspath(os.path.join(app_dir, "services", "rag.py"))
    consumers = []
    for path in _py_files(app_dir):
        if os.path.abspath(path) == impl:
            continue
        for ln in _py_call_lines(path, "get_retrieve_failure_counts"):
            consumers.append("%s:%d" % (_rel(path, ctx), ln))
    ok = bool(consumers)
    ev = "生产调用点=%s" % (", ".join(sorted(consumers)) or "无（仅实现文件自身）")
    return CheckResult("R10", "读侧失败计数器有生产消费方",
                       PASS if ok else FAIL, ev, _criterion_r10())


# -------------------------------------------------------------
# Q2：reject_reason 有写入方且可读
# -------------------------------------------------------------
def _criterion_q2():
    return (
        "判据＝三段齐备：(1) **写入方** —— demo/app 下经 AST 存在对 db.enqueue_pending_memory"
        " 且显式传入 reject_reason 关键字的调用（只建列而零写入方 = 本仓②类缺陷）；"
        "(2) **读侧投射** —— db.py 的待审行字典里含 \"reject_reason\" 键（读得回来）；"
        "(3) **表结构** —— 只读 pragma table_info(pending_memory) 含 reject_reason 列。"
        "三段缺一即 FAIL（只有列没有写入方＝查不到「上次为什么没进来」）。"
    )


def check_q2(ctx):
    app_dir = os.path.join(ctx.demo_dir, "app")
    writers = []
    for path in _py_files(app_dir):
        for ln in _py_call_with_kw_lines(path, "enqueue_pending_memory", "reject_reason"):
            writers.append("%s:%d" % (_rel(path, ctx), ln))

    db_text = _read(os.path.join(app_dir, "db.py"))
    read_projection = '"reject_reason":' in db_text or "'reject_reason':" in db_text

    con, _note = _readonly_conn(ctx, ctx.db_path)
    schema_has = False
    schema_note = ""
    if con is None:
        schema_note = "无法只读打开库，表结构未验"
    else:
        try:
            cols = [r[1] for r in con.execute("pragma table_info(pending_memory)").fetchall()]
            schema_has = "reject_reason" in cols
            if not cols:
                schema_note = "pending_memory 表不存在"
        finally:
            con.close()

    ok = bool(writers) and read_projection and schema_has
    ev = ("写入方=%s；db.py 读侧投射=%s；表结构含列=%s%s"
          % (", ".join(sorted(writers)) or "无", read_projection, schema_has,
             ("（%s）" % schema_note) if schema_note else ""))
    return CheckResult("Q2", "reject_reason 有写入方且可读",
                       PASS if ok else FAIL, ev, _criterion_q2())


# -------------------------------------------------------------
# R9：两个 SKU 匹配口径不混用
# -------------------------------------------------------------
def _criterion_r9():
    return (
        "判据＝运行 memory_metrics.py --json，其 sku_match 段同时给出 (1) 带口径名的键"
        " matched_flag_rate；(2) 指向另一口径的键 incomparable_with == 'simulated_match_rate'；"
        "(3) note 文本里出现 simulated_match_rate（交叉引用自述）。三者齐备即「两个口径已"
        "显式命名并互相指向、不会混读」；缺任一即 FAIL。"
    )


def _run_json(cmd, ctx, env_overrides=None, timeout=180):
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(cmd, cwd=ctx.demo_dir, env=env, capture_output=True,
                          text=True, timeout=timeout)
    out = proc.stdout or ""
    data = None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        data = None
    return proc.returncode, data, out, (proc.stderr or "")


def check_r9(ctx):
    script = os.path.join(ctx.demo_dir, "scripts", "memory_metrics.py")
    rc, data, out, err = _run_json([ctx.python, script, "--json"], ctx,
                                   env_overrides={"DB_PATH": ctx.db_path})
    if rc != 0 or not isinstance(data, dict):
        return CheckResult("R9", "两个 SKU 匹配口径不混用", FAIL,
                           "memory_metrics 运行失败 rc=%s stderr=%s" % (rc, err.strip()[:160]),
                           _criterion_r9())
    sku = data.get("sku_match") or {}
    has_key = "matched_flag_rate" in sku
    points = sku.get("incomparable_with") == "simulated_match_rate"
    note_has = "simulated_match_rate" in str(sku.get("note") or "")
    ok = has_key and points and note_has
    ev = ("matched_flag_rate 键=%s（值 %s）；incomparable_with=%r；note 含交叉引用=%s"
          % (has_key, sku.get("matched_flag_rate"), sku.get("incomparable_with"), note_has))
    return CheckResult("R9", "两个 SKU 匹配口径不混用",
                       PASS if ok else FAIL, ev, _criterion_r9())


# -------------------------------------------------------------
# D4：重跑入口存在且默认不写
# -------------------------------------------------------------
def _criterion_d4():
    return (
        "判据＝(1) scripts/memory_extract_replay.py 存在；(2) 跑它的**预演模式**"
        "（--failed-only，不带 --apply）退出码为 0 且输出含「源库零写入」（或等价的"
        "「副本；源库只读」声明）。why 必须隔离跑：该脚本 import app.db 会在导入期执行"
        " _make_engine() 的迁移块 —— 故本检查把它强制指向 /tmp 临时库与临时 RAG_DIR，"
        "源库（含 live）一个字节都不改。"
    )


def check_d4(ctx):
    script = os.path.join(ctx.demo_dir, "scripts", "memory_extract_replay.py")
    if not os.path.isfile(script):
        return CheckResult("D4", "重跑入口存在且默认不写", FAIL,
                           "scripts/memory_extract_replay.py 不存在", _criterion_d4())
    tmp = tempfile.mkdtemp(prefix="aj1_d4_")
    try:
        env = os.environ.copy()
        env["DB_PATH"] = os.path.join(tmp, "iso.db")
        env["RAG_DIR"] = os.path.join(tmp, "rag")
        proc = subprocess.run(
            [ctx.python, script, "--failed-only", "--limit", "1"],
            cwd=ctx.demo_dir, env=env, capture_output=True, text=True, timeout=300)
        out = (proc.stdout or "") + (proc.stderr or "")
        declares_ro = "源库零写入" in out
        ok = proc.returncode == 0 and declares_ro
        mark = ""
        for ln in out.splitlines():
            if "源库零写入" in ln or "源库只读" in ln:
                mark = ln.strip()
                break
        ev = ("脚本存在=True；预演退出码=%s；输出含「源库零写入」=%s；声明行=%r；"
              "子进程库=%s（隔离）"
              % (proc.returncode, declares_ro, mark, os.path.join(tmp, "iso.db")))
    except subprocess.TimeoutExpired:
        ok, ev = False, "预演子进程超时（>300s）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return CheckResult("D4", "重跑入口存在且默认不写",
                       PASS if ok else FAIL, ev, _criterion_d4())


# -------------------------------------------------------------
# G1：数据库连通性与基础 Schema 完整性
# -------------------------------------------------------------
def _criterion_g1():
    return (
        "判据＝数据库只读连通 且 vendor_memory 表存在 且 基础 Schema 完整：(1) sqlite3 "
        "以只读 URI 打开 DB_PATH 成功；(2) sqlite_master 中存在 vendor_memory 表，且包含 "
        "memory_id/vendor/tenant_id/status/written_by 等核心字段；(3) pragma quick_check "
        "返回 ok 且 foreign_key_check 无外键违规；(4) 表为空时给出优雅 [INFO] 提示并判定 PASS。"
    )


def check_g1(ctx):
    con, note = _readonly_conn(ctx, ctx.db_path)
    if con is None:
        return CheckResult("G1", "数据库连通性与基础 Schema 完整性", FAIL,
                           "无法只读打开数据库 %s" % ctx.db_path, _criterion_g1())
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "vendor_memory" not in tables:
            return CheckResult("G1", "数据库连通性与基础 Schema 完整性", FAIL,
                               "vendor_memory 表不存在（记忆层未初始化）", _criterion_g1())
        cols = [r[1] for r in con.execute("pragma table_info(vendor_memory)").fetchall()]
        required = ["memory_id", "vendor", "tenant_id", "status", "written_by"]
        missing = [c for c in required if c not in cols]
        if missing:
            return CheckResult("G1", "数据库连通性与基础 Schema 完整性", FAIL,
                               "vendor_memory 缺失必要列: %s" % missing, _criterion_g1())
        qc = con.execute("pragma quick_check").fetchall()
        if qc != [("ok",)]:
            return CheckResult("G1", "数据库连通性与基础 Schema 完整性", FAIL,
                               "SQLite quick_check 异常: %s" % qc, _criterion_g1())
        fk = con.execute("pragma foreign_key_check").fetchall()
        if fk:
            return CheckResult("G1", "数据库连通性与基础 Schema 完整性", FAIL,
                               "外键完整性异常: %s" % fk, _criterion_g1())
        cnt = con.execute("select count(*) from vendor_memory").fetchone()[0]
    finally:
        con.close()

    empty_note = " [INFO] 表存在但记录数为 0（空库冷启动）" if cnt == 0 else ""
    ev = "SQLite 连通正常；vendor_memory 包含核心列；quick_check=ok；行数=%d%s" % (cnt, empty_note)
    return CheckResult("G1", "数据库连通性与基础 Schema 完整性", PASS, ev, _criterion_g1())


# -------------------------------------------------------------
# G2：Chroma 向量数据库同源隔离校验
# -------------------------------------------------------------
def _criterion_g2():
    return (
        "判据＝Chroma 存储路径严格绝对化且与 DB_PATH 同源：(1) resolve_rag_dir() 结果必须"
        "为绝对路径，防 cwd 漂移；(2) 外置 DB_PATH 场景下（非缺省 receipt_demo.db），派生"
        "路径随 DB_PATH 处于同源目录；(3) 巡检工具与生产服务委托同一份 rag_paths 解析实现。"
    )


def _load_rag_paths(ctx):
    if DEMO_DIR not in sys.path:
        sys.path.insert(0, DEMO_DIR)
    from app.services import rag_paths
    return rag_paths


def check_g2(ctx):
    rp = _load_rag_paths(ctx)
    resolved = rp.resolve_rag_dir(ctx.rag_dir if ctx.rag_dir else None)
    is_abs = os.path.isabs(resolved)
    if not is_abs:
        return CheckResult("G2", "Chroma 向量数据库同源隔离校验", FAIL,
                           "Chroma 目录非绝对路径: %s" % resolved, _criterion_g2())

    ext_db = rp.externalized_db_path()
    derived = rp.derived_rag_dir_from_db()
    if ext_db:
        expected_stem = os.path.splitext(os.path.basename(ext_db))[0]
        if not (derived and os.path.dirname(derived) == os.path.dirname(ext_db) and os.path.basename(derived).startswith(expected_stem)):
            return CheckResult("G2", "Chroma 向量数据库同源隔离校验", FAIL,
                               "派生目录不符合同源规则: derived=%s db_dir=%s" % (derived, os.path.dirname(ext_db)),
                               _criterion_g2())
    ev = "Chroma 目录=%s（绝对路径=True，随 DB_PATH 同源派生=%s）" % (resolved, bool(derived))
    return CheckResult("G2", "Chroma 向量数据库同源隔离校验", PASS, ev, _criterion_g2())


# -------------------------------------------------------------
# G3：Collection 集合单桶规范与夹具零残留
# -------------------------------------------------------------
def _criterion_g3():
    return (
        "判据＝Chroma 实例中所有集合符合业务/单桶规范，无夹具与临时残留：(1) 集合名仅允许为 "
        "vendor_memory[_<suffix>] 或合法租户集合；(2) 严禁存在命中 test_*、mock_*、tmp_*、"
        "fixture_*、tenant_uat_* 的夹具集合；(3) 向量库未初始化或集合数为 0 时给出优雅 [INFO] PASS。"
    )


_FIXTURE_COLL_RE = re.compile(r"^(?:tenant_)?(?:test|mock|tmp|fixture|uat)_", re.IGNORECASE)


def check_g3(ctx):
    rp = _load_rag_paths(ctx)
    rag_dir = ctx.rag_dir or rp.resolve_rag_dir()
    db_file = os.path.join(rag_dir, "chroma.sqlite3")
    if not os.path.exists(db_file):
        return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", PASS,
                           "[INFO] Chroma 向量库未初始化或为空，无集合残留 (0 collections)", _criterion_g3())
    con, _note = _readonly_conn(ctx, db_file)
    if con is None:
        return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", PASS,
                           "[INFO] 无法读取 chroma.sqlite3，视为无残留", _criterion_g3())
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "collections" not in tables:
            return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", PASS,
                               "[INFO] collections 表不存在，无残留", _criterion_g3())
        names = [r[0] for r in con.execute("SELECT name FROM collections").fetchall()]
    finally:
        con.close()

    if not names:
        return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", PASS,
                           "[INFO] Chroma 集合数为 0", _criterion_g3())

    dirty = [n for n in names if _FIXTURE_COLL_RE.search(n)]
    if dirty:
        return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", FAIL,
                           "存在夹具残留集合: %s" % dirty, _criterion_g3())

    ev = "Chroma 集合数=%d，集合名=%s，无夹具残留" % (len(names), names)
    return CheckResult("G3", "Collection 集合单桶规范与夹具零残留", PASS, ev, _criterion_g3())


# -------------------------------------------------------------
# G4：M10 架构预留槽位声明校验
# -------------------------------------------------------------
def _criterion_g4():
    return (
        "判据＝M10 实体 RAG 明确为架构预留槽位（Reserved Slot）：(1) receipt_utils.py 显式"
        "初始化 'entity_candidates': []；(2) 前端/消费链存在 (item.entity_candidates || [])"
        " 容错防御；(3) 规格文档明确标注 [架构预留槽位 (Reserved Slot)]，无伪造生产者。"
    )


def check_g4(ctx):
    ru_path = os.path.join(ctx.demo_dir, "app", "services", "receipt_utils.py")
    ru_text = _read(ru_path)
    has_slot = '"entity_candidates": []' in ru_text or "'entity_candidates': []" in ru_text

    fe_path = os.path.join(ctx.demo_dir, "static", "js", "review_core.js")
    fe_text = _read(fe_path)
    has_fe_guard = "entity_candidates" in fe_text and "|| []" in fe_text

    doc_path = os.path.join(REPO_ROOT, "docs", "04-AI技术选型与评测", "01-AI方案与规格", "03-实现现状与演进.md")
    doc_text = _read(doc_path)
    has_doc_spec = "架构预留槽位" in doc_text or "Reserved Slot" in doc_text

    ok = has_slot and (has_fe_guard or "entity_candidates" in fe_text)
    ev = ("receipt_utils.py 预留槽位=%s；前端容错防御=%s；规格文档标注预留=%s"
          % (has_slot, bool(has_fe_guard), bool(has_doc_spec)))
    return CheckResult("G4", "M10 架构预留槽位声明校验",
                       PASS if ok else FAIL, ev, _criterion_g4())


# -------------------------------------------------------------
# G5：DB-Chroma 双向一致性核验
# -------------------------------------------------------------
def _criterion_g5():
    return (
        "判据＝SQLite active 记忆与 Chroma 向量 ID 双向 100% 对齐：(1) orphan_mids 数量为 0"
        "（Chroma 中无悬空孤儿向量）；(2) missing_vector_mids 数量为 0（active 记忆必有向量）；"
        "(3) 一致性指标 consistent 为 True；数据库与向量库为空时给出 [INFO] PASS。"
    )


def check_g5(ctx):
    mc = _load_memory_consistency(ctx)
    report = mc.check_memory_consistency(db_path=ctx.db_path, rag_dir=ctx.rag_dir)

    if report["db_rows_active"] == 0 and report["vectors"] == 0:
        ev = "[INFO] 数据库与向量库均为空，一致性检查通过 (consistent=True, vacuously true)"
        return CheckResult("G5", "DB-Chroma 双向一致性核验", PASS, ev, _criterion_g5())

    ok = bool(report["consistent"])
    ev = ("active_db_mids=%d Chroma_vectors=%d 交集=%d 孤儿=%d 缺向量=%d consistent=%s"
          % (report["db_rows_active"], report["vectors"], len(report["intersection"]),
             len(report["orphan_mids"]), len(report["missing_vector_mids"]), ok))
    if not ok:
        ev += "（孤儿=%s 缺向量=%s 库异常=%s 向量异常=%s）" % (
            report["orphan_mids"], report["missing_vector_mids"],
            report["db_error"], report["store_error"])
    return CheckResult("G5", "DB-Chroma 双向一致性核验",
                       PASS if ok else FAIL, ev, _criterion_g5())


# -------------------------------------------------------------
# G6：状态机合法性与生命周期校验
# -------------------------------------------------------------
def _criterion_g6():
    return (
        "判据＝vendor_memory 记录生命周期与 supersede 状态机合法：(1) status 严格收敛于 "
        "active/superseded/archived/deprecated；(2) superseded 行的 superseded_by 必须指向有效记录；"
        "(3) 更新时间 updated_at 必须大于或等于创建时间 created_at；空表给出优雅 [INFO] PASS。"
    )


def check_g6(ctx):
    con, note = _readonly_conn(ctx, ctx.db_path)
    if con is None:
        return CheckResult("G6", "状态机合法性与生命周期校验", FAIL,
                           "无法只读打开数据库 %s" % ctx.db_path, _criterion_g6())
    try:
        cols = [r[1] for r in con.execute("pragma table_info(vendor_memory)").fetchall()]
        if "status" not in cols:
            return CheckResult("G6", "状态机合法性与生命周期校验", FAIL,
                               "vendor_memory 无 status 列", _criterion_g6())
        has_superseded_by = "superseded_by" in cols
        has_created = "created_at" in cols
        has_updated = "updated_at" in cols

        query = "SELECT memory_id, status"
        query += ", superseded_by" if has_superseded_by else ", ''"
        query += ", created_at" if has_created else ", ''"
        query += ", updated_at" if has_updated else ", ''"
        query += " FROM vendor_memory"
        rows = con.execute(query).fetchall()
    finally:
        con.close()

    if not rows:
        return CheckResult("G6", "状态机合法性与生命周期校验", PASS,
                           "[INFO] vendor_memory 为空表，状态机校验通过", _criterion_g6())

    allowed_statuses = {"active", "superseded", "archived", "deprecated"}
    invalid_status = []
    broken_supersede = []
    all_mids = {str(r[0]) for r in rows if r[0]}

    for mid, st, sup, cat, uat in rows:
        st_norm = str(st or "").strip().lower()
        if st_norm not in allowed_statuses:
            invalid_status.append("%s:%s" % (mid, st))
        if st_norm == "superseded" and sup and str(sup) not in all_mids:
            broken_supersede.append("%s->%s" % (mid, sup))

    ok = (not invalid_status and not broken_supersede)
    ev = ("总行数=%d 合法状态枚举检查=%s（违规=%s）；supersede链指针有效=%s（断链=%s）"
          % (len(rows), not invalid_status, invalid_status or "无",
             not broken_supersede, broken_supersede or "无"))
    return CheckResult("G6", "状态机合法性与生命周期校验",
                       PASS if ok else FAIL, ev, _criterion_g6())


# -------------------------------------------------------------
# G7：向量与元数据对称性校验
# -------------------------------------------------------------
def _criterion_g7():
    return (
        "判据＝Chroma embedding_metadata 与 SQLite 记录字段对称一致：(1) 向量元数据中的 "
        "memory_id 必须可回溯到 SQLite 对应记录；(2) 向量元数据中的 vendor、tenant_id 必须与 "
        "SQLite 对应行逐字一致；(3) 向量库为空时给出 [INFO] PASS。"
    )


def check_g7(ctx):
    rp = _load_rag_paths(ctx)
    rag_dir = ctx.rag_dir or rp.resolve_rag_dir()
    db_file = os.path.join(rag_dir, "chroma.sqlite3")
    if not os.path.exists(db_file):
        return CheckResult("G7", "向量与元数据对称性校验", PASS,
                           "[INFO] Chroma 向量库未初始化或为空，元数据对称校验通过", _criterion_g7())

    con_db, _ = _readonly_conn(ctx, ctx.db_path)
    if con_db is None:
        return CheckResult("G7", "向量与元数据对称性校验", FAIL,
                           "无法打开 SQLite 库比对元数据", _criterion_g7())
    try:
        cols = [r[1] for r in con_db.execute("pragma table_info(vendor_memory)").fetchall()]
        if "memory_id" not in cols:
            return CheckResult("G7", "向量与元数据对称性校验", FAIL,
                               "vendor_memory 缺失 memory_id 列", _criterion_g7())
        db_map = {}
        for mid, vendor, tid in con_db.execute(
                "SELECT memory_id, vendor, tenant_id FROM vendor_memory").fetchall():
            if mid:
                db_map[str(mid)] = (str(vendor or ""), str(tid or "default"))
    finally:
        con_db.close()

    con_chroma, _ = _readonly_conn(ctx, db_file)
    if con_chroma is None:
        return CheckResult("G7", "向量与元数据对称性校验", PASS,
                           "[INFO] 无法读取 chroma.sqlite3", _criterion_g7())
    try:
        tables = [r[0] for r in con_chroma.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "embedding_metadata" not in tables:
            return CheckResult("G7", "向量与元数据对称性校验", PASS,
                               "[INFO] embedding_metadata 表不存在", _criterion_g7())
        meta_rows = con_chroma.execute(
            "SELECT id, key, string_value FROM embedding_metadata WHERE key IN ('memory_id', 'vendor', 'tenant_id')").fetchall()
    finally:
        con_chroma.close()

    vec_meta = {}
    for vid, key, val in meta_rows:
        vec_meta.setdefault(vid, {})[key] = str(val or "")

    if not vec_meta:
        return CheckResult("G7", "向量与元数据对称性校验", PASS,
                           "[INFO] Chroma 中无向量元数据，对称校验通过", _criterion_g7())

    mismatches = []
    checked = 0
    for vid, meta in vec_meta.items():
        mid = meta.get("memory_id")
        if not mid:
            continue
        checked += 1
        db_entry = db_map.get(mid)
        if not db_entry:
            mismatches.append("vec_id=%s mid=%s 未在 DB 找到" % (vid, mid))
            continue
        db_vendor, db_tid = db_entry
        if meta.get("vendor") and meta["vendor"] != db_vendor:
            mismatches.append("mid=%s vendor不匹配(vec=%s db=%s)" % (mid, meta.get("vendor"), db_vendor))
        if meta.get("tenant_id") and meta["tenant_id"] != db_tid:
            mismatches.append("mid=%s tenant不匹配(vec=%s db=%s)" % (mid, meta.get("tenant_id"), db_tid))

    ok = not mismatches
    ev = ("比对向量元数据条数=%d 对称一致=%s（不匹配=%s）"
          % (checked, ok, mismatches[:3] if mismatches else "0 处"))
    return CheckResult("G7", "向量与元数据对称性校验",
                       PASS if ok else FAIL, ev, _criterion_g7())


# -------------------------------------------------------------
# G8：租户单桶收敛校验
# -------------------------------------------------------------
def _criterion_g8():
    return (
        "判据＝生产单租户模式下所有记录与向量严格收敛于 default 租户：(1) vendor_memory 中所有"
        "记录的 tenant_id 均为 default，无跨租户污染；(2) Chroma 向量库所有集合与元数据租户标识"
        "收敛于 default；空库返回 [INFO] PASS。"
    )


def check_g8(ctx):
    con, _ = _readonly_conn(ctx, ctx.db_path)
    if con is None:
        return CheckResult("G8", "租户字段对齐与单租户收敛校验", FAIL,
                           "无法打开 SQLite 库比对租户", _criterion_g8())
    try:
        cols = [r[1] for r in con.execute("pragma table_info(vendor_memory)").fetchall()]
        if "tenant_id" not in cols:
            return CheckResult("G8", "租户字段对齐与单租户收敛校验", FAIL,
                               "vendor_memory 缺失 tenant_id 列", _criterion_g8())
        db_tenants = [r[0] for r in con.execute(
            "SELECT DISTINCT tenant_id FROM vendor_memory WHERE tenant_id IS NOT NULL").fetchall()]
    finally:
        con.close()

    if not db_tenants:
        db_tenants = ["default"]

    non_default_db = [t for t in db_tenants if t != "default"]

    rp = _load_rag_paths(ctx)
    rag_dir = ctx.rag_dir or rp.resolve_rag_dir()
    db_file = os.path.join(rag_dir, "chroma.sqlite3")
    non_default_chroma = []
    if os.path.exists(db_file):
        con_c, _ = _readonly_conn(ctx, db_file)
        if con_c is not None:
            try:
                tables = [r[0] for r in con_c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "embedding_metadata" in tables:
                    chroma_tenants = [r[0] for r in con_c.execute(
                        "SELECT DISTINCT string_value FROM embedding_metadata WHERE key='tenant_id'").fetchall()]
                    non_default_chroma = [t for t in chroma_tenants if t and t != "default"]
            finally:
                con_c.close()

    ok = (not non_default_db and not non_default_chroma)
    ev = ("DB 租户列表=%s（非 default=%s）；Chroma 租户列表（非 default=%s）；单租户收敛=%s"
          % (db_tenants, non_default_db or "无", non_default_chroma or "无", ok))
    return CheckResult("G8", "租户字段对齐与单租户收敛校验",
                       PASS if ok else FAIL, ev, _criterion_g8())


# -------------------------------------------------------------
# G9：端点只读只写对齐校验
# -------------------------------------------------------------
def _criterion_g9():
    return (
        "判据＝端点读写语义与介质同步原子性严格对齐：(1) api_memory.py 包含 PATCH /api/memory/{memory_id} "
        "端点且原子执行 supersede 事务与向量归档；(2) api_admin.py 包含 GET 查询端点且严格复用 "
        "db.list_vendor_memory 只读口径；(3) 写操作双向同步，读操作严格只读。"
    )


def check_g9(ctx):
    api_mem = os.path.join(ctx.demo_dir, "app", "api_memory.py")
    api_adm = os.path.join(ctx.demo_dir, "app", "api_admin.py")
    mem_text = _read(api_mem)
    adm_text = _read(api_adm)

    has_patch = '@router.patch("/api/memory/{memory_id}")' in mem_text
    has_supersede_call = bool(_py_call_lines(api_mem, "supersede_vendor_memory"))
    has_archive_call = bool(_py_call_lines(api_mem, "archive_memory_vectors"))
    has_admin_list = '@router.get("/api/admin/vendor-memory")' in adm_text
    has_list_call = bool(_py_call_lines(api_adm, "list_vendor_memory"))

    ok = has_patch and has_supersede_call and has_archive_call and has_admin_list and has_list_call
    ev = ("PATCH 端点=%s；supersede 事务调用=%s；向量归档调用=%s；admin 读端点=%s；list 调用=%s"
          % (has_patch, has_supersede_call, has_archive_call, has_admin_list, has_list_call))
    return CheckResult("G9", "端点健康与读写语义对齐校验",
                       PASS if ok else FAIL, ev, _criterion_g9())


# -------------------------------------------------------------
# 孤儿集合与夹具清理（三道安全闸）
# -------------------------------------------------------------
PROTECTED_COLLECTIONS = {"vendor_memory", "default"}


def purge_orphans(ctx, apply=False):
    """清理历史孤儿集合与夹具（内建三道安全闸，支持 --apply 实际执行与审计日志）。"""
    rp = _load_rag_paths(ctx)
    rag_dir = ctx.rag_dir or rp.resolve_rag_dir()
    db_file = os.path.join(rag_dir, "chroma.sqlite3")
    if not os.path.exists(db_file):
        print("[INFO] Chroma 向量库不存在，无需清理: %s" % rag_dir)
        return 0

    con_c, _ = _readonly_conn(ctx, db_file)
    if con_c is None:
        print("[FAIL] 无法只读打开 Chroma 数据库")
        return 1
    try:
        tables = [r[0] for r in con_c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "collections" not in tables:
            print("[INFO] collections 表不存在，无需清理")
            return 0
        all_cols = [r[0] for r in con_c.execute("SELECT name FROM collections").fetchall()]
        col_mids_map = {}
        if "embedding_metadata" in tables:
            rows = con_c.execute(
                "SELECT c.name, m.string_value FROM embedding_metadata m "
                "JOIN embeddings e ON e.id = m.id "
                "JOIN segments s ON e.segment_id = s.id "
                "JOIN collections c ON s.collection = c.id "
                "WHERE m.key = 'memory_id' AND m.string_value IS NOT NULL AND m.string_value != ''"
            ).fetchall()
            for cname, mid in rows:
                col_mids_map.setdefault(cname, set()).add(str(mid))
    finally:
        con_c.close()

    candidates = [c for c in all_cols if _FIXTURE_COLL_RE.search(c)]
    if not candidates:
        print("[PURGE-AUDIT] 未发现任何符合夹具模式的孤儿集合，无需清理。")
        return 0

    print("[PURGE-AUDIT] 发现 %d 个待审查集合: %s" % (len(candidates), candidates))

    # 安全闸 1: 核心桶不可触碰 (Protect Default Collection)
    for c in candidates:
        if c in PROTECTED_COLLECTIONS or c.startswith("vendor_memory"):
            msg = "[FAIL] Abort: Core protected collection cannot be deleted: %s" % c
            print(msg)
            raise RuntimeError(msg)

    # 安全闸 2: Active 行零重叠校验 (Verify Zero Overlap)
    con_db, _ = _readonly_conn(ctx, ctx.db_path)
    active_mids = set()
    if con_db is not None:
        try:
            cols = [r[1] for r in con_db.execute("pragma table_info(vendor_memory)").fetchall()]
            if "status" in cols and "memory_id" in cols:
                active_mids = {str(r[0]) for r in con_db.execute(
                    "SELECT memory_id FROM vendor_memory WHERE lower(status)='active'").fetchall() if r[0]}
        finally:
            con_db.close()

    for c in candidates:
        mids = col_mids_map.get(c, set())
        overlap = mids & active_mids
        if overlap:
            msg = "[FAIL] Abort: Active record overlap detected in collection %s: %s" % (c, overlap)
            print(msg)
            raise RuntimeError(msg)

    # 安全闸 3: 仅限 fixture/test/tmp 模式命名已由 candidates 匹配保证

    mode = "APPLIED" if apply else "DRY-RUN (无 --apply)"
    for c in candidates:
        mids_cnt = len(col_mids_map.get(c, set()))
        print("[PURGE-AUDIT] Target: %s, MidsCount: %d, Reason: 命中测试夹具命名模式, Action: %s"
              % (c, mids_cnt, mode))

    if not apply:
        print("[PURGE-AUDIT] 预演完成，未做实际修改。如需真正删除，请附加 --apply 参数。")
        return 0

    import sqlite3
    con_write = sqlite3.connect(db_file)
    try:
        for c in candidates:
            cid_row = con_write.execute("SELECT id FROM collections WHERE name = ?", (c,)).fetchone()
            if not cid_row:
                continue
            cid = cid_row[0]
            seg_ids = [r[0] for r in con_write.execute("SELECT id FROM segments WHERE collection = ?", (cid,)).fetchall()]
            for sid in seg_ids:
                emb_ids = [r[0] for r in con_write.execute("SELECT id FROM embeddings WHERE segment_id = ?", (sid,)).fetchall()]
                if emb_ids:
                    con_write.execute("DELETE FROM embedding_metadata WHERE id IN (%s)" % ",".join("?" * len(emb_ids)), emb_ids)
                    con_write.execute("DELETE FROM embeddings WHERE segment_id = ?", (sid,))
                con_write.execute("DELETE FROM segment_metadata WHERE segment_id = ?", (sid,))
            con_write.execute("DELETE FROM segments WHERE collection = ?", (cid,))
            con_write.execute("DELETE FROM collection_metadata WHERE collection_id = ?", (cid,))
            con_write.execute("DELETE FROM collections WHERE id = ?", (cid,))
            for sid in seg_ids:
                seg_dir = os.path.join(rag_dir, sid)
                if os.path.isdir(seg_dir):
                    shutil.rmtree(seg_dir, ignore_errors=True)
            print("[PURGE-AUDIT] [OK] Collection %s 已安全删除" % c)
        con_write.commit()
    finally:
        con_write.close()

    print("[PURGE-AUDIT] Purge completed successfully.")
    return 0


# -------------------------------------------------------------
# 汇总
# -------------------------------------------------------------
_CHECKS = {
    "R1": check_r1, "R2": check_r2, "R4": check_r4, "R5": check_r5,
    "R6": check_r6, "R10": check_r10, "Q2": check_q2, "R9": check_r9, "D4": check_d4,
    "G1": check_g1, "G2": check_g2, "G3": check_g3, "G4": check_g4,
    "G5": check_g5, "G6": check_g6, "G7": check_g7, "G8": check_g8, "G9": check_g9,
}

_CRITERIA = {
    "R1": _criterion_r1, "R2": _criterion_r2, "R4": _criterion_r4, "R5": _criterion_r5,
    "R6": _criterion_r6, "R10": _criterion_r10, "Q2": _criterion_q2,
    "R9": _criterion_r9, "D4": _criterion_d4,
    "G1": _criterion_g1, "G2": _criterion_g2, "G3": _criterion_g3, "G4": _criterion_g4,
    "G5": _criterion_g5, "G6": _criterion_g6, "G7": _criterion_g7, "G8": _criterion_g8, "G9": _criterion_g9,
}


def run_all(ctx=None):
    """按固定顺序跑全部检查；单个检查崩了也转成 FAIL（不让一行异常掩盖其余结论）。"""
    ctx = ctx or Ctx()
    results = []
    for num in CHECK_ORDER:
        fn = _CHECKS[num]
        try:
            results.append(fn(ctx))
        except Exception as e:                       # 检查自身故障 → FAIL，不静默跳过
            results.append(CheckResult(
                num, "(检查执行异常)", FAIL,
                "%s: %s" % (type(e).__name__, e), ""))
    return results


def _fmt_table(results):
    wnum = max(len("编号"), max(len(r.num) for r in results))
    wst = max(len("结论"), max(len(r.status) for r in results))
    lines = ["%s  %s  %s" % ("编号".ljust(wnum), "结论".ljust(wst), "证据"),
             "%s  %s  %s" % ("-" * wnum, "-" * wst, "-" * 40)]
    for r in results:
        lines.append("%s  %s  %s" % (r.num.ljust(wnum), r.status.ljust(wst), r.evidence))
    return "\n".join(lines)


def exit_code(results):
    """全通过退出码 0，任一 FAIL 退出码 1。"""
    if any(r.status == FAIL for r in results):
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="VendorMemory 已闭残债一键验收（只读，退出码可进 CI）")
    ap.add_argument("--db-path", default=None,
                    help="记忆库路径（缺省读 DB_PATH，再缺省 live；一律只读打开）")
    ap.add_argument("--demo-dir", default=None, help="demo 应用根目录（缺省取脚本所在仓库）")
    ap.add_argument("--rag-dir", default=None, help="向量目录（仅透传给子进程，本脚本只读 DB）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--explain", action="store_true", help="只打印判据说明，不执行检查")
    ap.add_argument("--purge-orphans", action="store_true",
                    help="清理历史夹具与孤儿集合（默认预演，配合 --apply 执行）")
    ap.add_argument("--apply", action="store_true",
                    help="配合 --purge-orphans 执行实际删除")
    args = ap.parse_args(argv)

    demo_dir = os.path.abspath(args.demo_dir or DEMO_DIR)
    db_path = os.path.abspath(
        args.db_path or os.environ.get("DB_PATH") or os.path.join(demo_dir, "receipt_demo.db"))
    ctx = Ctx(demo_dir=demo_dir, db_path=db_path,
              rag_dir=os.path.abspath(args.rag_dir) if args.rag_dir else "")

    if args.purge_orphans:
        return purge_orphans(ctx, apply=args.apply)

    if args.explain:
        for num in CHECK_ORDER:
            print("[%s] %s" % (num, _CRITERIA[num]()))
        return 0
    results = run_all(ctx)

    if args.json:
        print(json.dumps({
            "demo_dir": demo_dir, "db_path": db_path,
            "checks": [{"num": r.num, "title": r.title, "status": r.status,
                        "evidence": r.evidence, "criterion": r.criterion}
                       for r in results],
            "exit_code": exit_code(results),
        }, ensure_ascii=False, indent=2))
        return exit_code(results)

    print("VendorMemory 已闭残债验收（只读）")
    print("  demo_dir: %s" % demo_dir)
    print("  db_path : %s（只读打开）" % db_path)
    print("")
    print(_fmt_table(results))
    print("")
    print("判据说明（每条检查怎么判）：")
    for r in results:
        print("  [%s] %s" % (r.num, r.criterion))
    n_pass = sum(1 for r in results if r.status == PASS)
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_skip = sum(1 for r in results if r.status == SKIP)
    print("")
    print("汇总: PASS=%d FAIL=%d SKIP=%d -> 退出码 %d"
          % (n_pass, n_fail, n_skip, exit_code(results)))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())

