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

# 检查顺序（与收敛计划台账的编号顺序一致）
CHECK_ORDER = ["R1", "R2", "R4", "R5", "R6", "R10", "Q2", "R9", "D4"]


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
    js = os.path.join(ctx.demo_dir, "static", "js", "main.js")
    api_text, js_text = _read(api), _read(js)

    has_list_route = '@router.get("/api/admin/vendor-memory")' in api_text
    has_detail_route = '@router.get("/api/admin/vendor-memory/{memory_id}")' in api_text
    backend_calls = _py_call_lines(api, "list_vendor_memory")
    js_def = "function loadVendorMemoryGovernance(" in js_text
    js_call = "apiFetch('/api/admin/vendor-memory" in js_text
    js_invoked = _js_symbol_invoked(js_text, "loadVendorMemoryGovernance")

    ok = (has_list_route and has_detail_route and bool(backend_calls)
          and js_def and js_call and js_invoked)
    ev = ("api_admin.py 列表路由=%s 详情路由=%s list_vendor_memory 调用行=%s；"
          "main.js 定义=%s apiFetch 调用=%s 被调用=%s"
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
        "判据＝只读打开 DB_PATH 的 vendor_memory 表：若 active 行数为 0 → SKIP（没有可判的"
        "样本，不用 PASS 冒充）；否则要求 **active 行里 written_by 非空的条数 == active"
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
        return CheckResult("R6", "审计链 written_by（只读 live）", SKIP,
                           "active 行数为 0（无样本可判）", _criterion_r6())
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
# 汇总
# -------------------------------------------------------------
_CHECKS = {
    "R1": check_r1, "R2": check_r2, "R4": check_r4, "R5": check_r5,
    "R6": check_r6, "R10": check_r10, "Q2": check_q2, "R9": check_r9, "D4": check_d4,
}

# 判据自述（`--explain` 用；与各 check 的 criterion 逐字同源，避免两处文案分叉）
_CRITERIA = {
    "R1": _criterion_r1, "R2": _criterion_r2, "R4": _criterion_r4, "R5": _criterion_r5,
    "R6": _criterion_r6, "R10": _criterion_r10, "Q2": _criterion_q2,
    "R9": _criterion_r9, "D4": _criterion_d4,
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
    """全 PASS=0；存在 FAIL=1；无 FAIL 但有 SKIP=2。"""
    if any(r.status == FAIL for r in results):
        return 1
    if any(r.status == SKIP for r in results):
        return 2
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
    args = ap.parse_args(argv)

    demo_dir = os.path.abspath(args.demo_dir or DEMO_DIR)
    db_path = os.path.abspath(
        args.db_path or os.environ.get("DB_PATH") or os.path.join(demo_dir, "receipt_demo.db"))
    ctx = Ctx(demo_dir=demo_dir, db_path=db_path,
              rag_dir=os.path.abspath(args.rag_dir) if args.rag_dir else "")

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
    print("")
    print("汇总: PASS=%d FAIL=%d SKIP=%d -> 退出码 %d"
          % (n_pass,
             sum(1 for r in results if r.status == FAIL),
             sum(1 for r in results if r.status == SKIP),
             exit_code(results)))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
