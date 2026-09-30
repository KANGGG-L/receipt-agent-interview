# -*- coding: utf-8 -*-
"""结构性守卫：树上不得残留「反证补丁」这类临时改动。

why（真实事故，不是形式要求）：
P3c1 的 QA 为了确认守卫用例真的会失败，把「还原 P2a 审核 M5 早退形态」的补丁
打进了 demo/app/db.py 并**忘记还原**。该补丁是
``if exact is not None and sku is None: return None`` —— 它把同一个函数 docstring
自称的「逐条判可用性、不早退」当场作废，等于刚修好的契约在树上自废，而**所有
用例都在同一棵树上跑**，谁都没发现（实测 demo/tests/test_vendor_sku_alias.py 从
18 passed 掉到 2 failed / 16 passed）。这类残留的可怕之处在于：它不是「少写代码」，
而是「多写一段把正确逻辑关掉的代码」，且看起来像正常代码。
第二次同类事故发生在 P27：QA 用「改仓库文件 → 跑 → 覆盖还原」式反证，窗口期内
``demo/app/main.py`` 的接线被临时改掉（19:49 实测 ``swept = []  # QA_COUNTERPROOF_P27``，
19:50:46 才还原）；并发跑回归的波次会在毫秒级窗口里看到假失败。

故本文件把「反证用完必须逐字节还原」从流程纪律升级为**可执行的结构守卫**：
扫描产品代码与测试目录，出现下列标记字面即失败并列出文件与行号。

【标记表与匹配规则（三问三答，P30/AX1 补齐）】
标记表见 ``_PATTERNS``。规则与理由：

1. **子串匹配（``in``），不用全词边界。** 因为 QA 会把标记嵌进更长的名字或注释里
   （实测 ``QA_COUNTERPROOF_P27``、``COUNTERPROOF-AR1-R11``）。若用 ``\\bCOUNTERPROOF\\b``，
   下划线是词字符，``QA_COUNTERPROOF`` 内部**不含**词边界 → 恰恰漏掉真实写法。
2. **区分大小写。** 大写 ``COUNTERPROOF`` 是 QA 的自述残留形态（扫描域内 0 处正当命中），
   而小写 ``counterproof`` 在本仓是**正当的测试命名与说明**（6 处，如
   ``tests/test_memory_ab_baseline.py:200`` 的 ``test_counterproof_all_receipts_definition_fails_sanity``、
   ``tests/test_memory_vector_consistency.py:237`` 的 ``store_counterproof``）。若改成大小写
   不敏感，这 6 处正当命名会被整片打成残留 → 守卫会被顺手关掉（守卫生效的前提是它
   在正确树上为绿）。故大小写敏感，并由 ``test_scanner_is_case_sensitive`` 固化该声明。
3. **表内顺序按「长标记在前」排列**（``QA_COUNTERPROOF`` 先于 ``COUNTERPROOF``），
   因为 ``_scan_text`` 命中即 ``break``。存在子串关系的两条若顺序颠倒，短标记会遮蔽长
   标记（报告里报出的标记不是夹具想测的那条）。``test_each_marker_has_its_own_fixture``
   用「只含该标记自身」的夹具 + 断言命中的正是该标记，把这条顺序要求变成硬判据。

【实测来源（不是凭空猜，取自 /var/folders/.../agent-tasks/ 的 43 个输出与会话记录）】
``临时补丁`` 79 次 / ``COUNTERPROOF`` 85 次 / ``QA_COUNTERPROOF`` 46 次 / ``反证补丁`` 33 次 /
``反证] 还原`` 15 次 / ``反证临时改动`` 10 次（实测注释形态 ``# 【反证临时改动】短路口被去掉…``）/
``QA反证`` 9 次（无空格变体）。表内 7 条覆盖了除 ``反证补丁`` 之外的全部形态（取舍见下）。

【不误伤的四条口径（本仓有大量正当用法，必须写清）】
1. 白名单字面是 ``_PATTERNS`` 里那 7 条，**不是裸词「反证」**。测试文件里大量
   「判别力（反证）」「反证靶子」是在**描述用例的判别力**，属正当文档
   （实测 ``tests/test_memory_hybrid_recall.py:234`` 的 ``# 反证（in-test）：…``）。
2. **``反证补丁`` 刻意不入表**：它在扫描域内已有一处**正当文档命中** ——
   ``demo/tests/test_seed_memory_facts.py:38``「反证补丁必须逐字节还原后再跑一次全绿。」
   该行是流程纪律说明，不是「已施加某临时改动」的自述；入表会让真树扫描立刻翻红
   并把守卫变成噪声。同类（正当文档）：``temp`` 类词、``probe`` 类的可复用探针描述。
   若将来要覆盖「反证补丁 + 已施加」的共现形态，必须先补夹具证明它不误伤上面那一行。
3. 小写 ``counterproof`` 不入表（同上第 2 问的理由）。
4. 本守卫文件自身被排除（它的 ``_PATTERNS`` / ``_REQUIRED_PATTERNS`` / ``_MARKER_FIXTURES``
   与 docstring 必然包含这些字面）。这不是「写了就算」—— ``test_guard_excludes_itself_but_would_otherwise_self_flag``
   先断言**每一个**标记字面都真在本文件里（证明排除是必需的、不是空转），再断言
   遍历结果不含本文件。
5. **新增根（``demo/scripts/`` + ``demo/conftest.py``）已逐标记复核，结论＝无需排除任何一条。**
   实测该面共 23 个文件（``demo/scripts`` 22 个 ``*.py`` + ``demo/conftest.py``）对本表 7 条
   标记 **0 命中**。为免「只报 0 却没交代口径」，把**未入表**的相邻字面也一并点名：
   裸词「反证」在该面有 3 处，全部是正当文档，且都不含表内限定形态（表内是
   ``[QA 反证]`` / ``反证] 还原`` / ``反证临时改动`` 这类带前缀或方括号的**已施加改动**自述）：
   ``demo/scripts/memory_ab_baseline.py:147,149``（说明 classify 以可调用对象注入是为了让
   反证用例能替换处理组定义）、``demo/scripts/memory_metrics.py:85``（说明该片段可被
   ``tests/test_memory_effective_hit_rate.py`` 替换以做自校准）。小写 ``counterproof``
   在该面 0 处。故新增根**不引进任何噪声**，无需白名单例外；若将来有人要放宽口径
   （例如把裸词「反证」入表），必须先补夹具证明上面三行不被误伤 —— 否则守卫会整片翻红、
   继而被顺手关掉（守卫生效的前提是它在正确树上为绿）。

【扫描域】``demo/app``、``demo/static/js``、``tests``、``demo/tests``、``demo/scripts`` 下的
``*.py`` 与 ``*.js``，外加单文件根 ``demo/conftest.py``（见 ``_SCAN_ROOTS``）。
纳入 ``*.js`` 的理由：本仓既有反证实践发生在 JS（P2c 在 /tmp 的 main.js 突变体上做突变体
反证，harness 是 ``demo/tests/test_vendor_price_hint.js``），残留落在 ``demo/static/js/*.js``
时纯 py 扫描域会假绿。纳入前已预检：12 个 js 文件对本表 7 条标记 **0 命中**
（裸词「反证」有 2 处，均在 ``demo/tests/test_vendor_memory_governance_panel.js:17-18``
描述 harness 的突变体反证方法论，属正当文档，不被本表命中）。

【根表与覆盖面自校准（P31/AY2 补齐，与上面「标记表漏项」同族）】
旧版 ``_SCAN_ROOTS`` 只有 ``demo/app`` / ``demo/static/js`` / ``tests`` / ``demo/tests``，
**漏了 ``demo/scripts/``（23 个脚本）与 ``demo/conftest.py``** —— 而这两处恰恰是反证高发地：
P19/P20/P26 的 QA 都直接改过 ``demo/scripts/purge_fixture_receipts.py`` 与
``demo/scripts/memory_extract_replay.py`` 做反证；``demo/conftest.py`` 更是每次 DB 隔离
反证的靶子。残留若落在这两处，守卫照样报绿，即**守卫的覆盖面比它的声明窄**（假绿）。
故本波做三件事：
1. 把这两个根补进 ``_SCAN_ROOTS``，并登记进 ``_REQUIRED_ROOTS`` 冻结集合
   （同 ``_REQUIRED_PATTERNS`` 的取舍：允许未来新增根，但**删掉任一条必需根即失败**）。
2. ``demo/conftest.py`` 用**单文件路径**声明，不把整个 ``demo/`` 一级纳入 —— ``demo/`` 下还有
   ``uploads/`` / ``evalsets/`` / ``gui-test-screenshots/`` / ``demo/`` / ``work/`` 等与管辖面
   无关的目录，纳入它们会把守卫变成噪声源。
   因此 ``_iter_py_files`` 必须显式支持「根是文件」：``os.walk(某文件路径)`` 会**静默**
   什么都不产出，若按目录处理，这个根会被悄悄跳过 —— **那正是本波要修的假绿本身**。
3. 补 ``test_declared_roots_all_contribute_files``：逐根断言「存在 + 真的产出文件」，
   让「声明了却没被扫」变成红灯（把「根表漏项」这类事故结构性挡住，而不是靠人记）。

【恒真化防线】若 ``_PATTERNS`` 被清空、``_iter_py_files`` 遍历写错、或读取被短路，下面的真
扫描会「扫什么都干净」——那就是一条恒真用例，比没有守卫更糟（给人以已被守护的假象）。
故两层自校准都要求**真实 .py 文件 + 与真扫描同一条 ``_scan_roots``（目录遍历 + 读取 + 匹配
三层）**，且逐标记各一条夹具，缺失任一条即失败。

**根表这一维另配三层防线（P31/AY2 补，与标记表防线对齐）：**
``test_declared_roots_all_contribute_files``（逐根断言存在且真产出文件 + 冻结集合
``_REQUIRED_ROOTS`` ⊆ 根表）、``test_single_file_root_is_really_scanned``（单文件根分支
必须被遍历到）、``test_demo_scripts_root_is_calibrated_with_real_file``（把含标记的真实
夹具放进 ``demo/scripts/``，用模块级 ``_SCAN_ROOTS`` 扫、断言被捞到，随后清理并复核
``demo/scripts`` 回到 0 违规）。
两条维度的防线缺一不可：**标记表漏项**（P30 修过）与**根表漏项**（本波修）是同一个
假绿的两半 —— 前者让某个标记形同不存在，后者让某个目录形同不在管辖面内。

【不触发迁移】扫描器只读文件、不 import 任何 app.* 模块，因此不触发 db._make_engine()
的 import 期迁移（本仓已三次因 import 打到 live 库）。
"""

import os

# 标记表：顺序 = 长标记优先（见 docstring 第 3 问），避免子串遮蔽导致报告错标记。
_PATTERNS = (
    "QA_COUNTERPROOF",
    "COUNTERPROOF",
    "[QA 反证]",
    "反证] 还原",
    "QA反证",
    "反证临时改动",
    "临时补丁",
)

# 必需集合（冻结）：允许未来新增标记，但**删掉任一条都必须让用例失败**。
# 前三条是 P3fix-a 的原始三条（不得因重构消失）；后两条是 P27 实测真正在用、
# 而旧表漏掉的形态（旧表对 `# QA_COUNTERPROOF_P27` 假绿）。
_REQUIRED_PATTERNS = (
    "[QA 反证]",
    "反证] 还原",
    "临时补丁",
    "QA_COUNTERPROOF",
    "COUNTERPROOF",
)

# 逐标记夹具：每个标记一条「只含该标记自身」的真实源码行。
# - `[QA 反证]` 那条同时含 `反证] 还原` 子串 → 断言报出的必须是 `[QA 反证]`（验证顺序）。
# - `QA_COUNTERPROOF` 那条是 P27 的**逐字原文行**（取自 agent-tasks 输出），
#   它同时含 `COUNTERPROOF` 子串 → 断言报出的必须是 `QA_COUNTERPROOF`。
_MARKER_FIXTURES = (
    ("[QA 反证]", "x = 1  # [QA 反证] 还原 P2a 审核 M5 的早退形态"),
    ("反证] 还原", "x = 1  # 反证] 还原 形态（无前缀方括号）"),
    ("临时补丁", "x = 1  # 临时补丁：仅为确认用例判别力"),
    ("QA_COUNTERPROOF", "swept = []  # QA_COUNTERPROOF_P27"),
    ("COUNTERPROOF", "x = 1  # COUNTERPROOF 临时"),
    ("QA反证", "x = 1  # QA反证（无空格变体）"),
    ("反证临时改动", "x = 1  # 【反证临时改动】短路口被去掉，用于确认关闭态用例真的能捕获回归"),
)

_HERE = os.path.abspath(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(_HERE), ".."))

# 扫描根：目录与**单文件**混用（单文件见 docstring【根表与覆盖面自校准】第 2 条）。
# ``demo/scripts`` 与 ``demo/conftest.py`` 是本波（P31/AY2）才补进来的 —— 它们此前不在
# 管辖面内，而 P19/P20/P26 的反证恰恰都碰过这两处。
_SCAN_ROOTS = (
    os.path.join(_REPO_ROOT, "demo", "app"),
    os.path.join(_REPO_ROOT, "demo", "static", "js"),
    os.path.join(_REPO_ROOT, "tests"),
    os.path.join(_REPO_ROOT, "demo", "tests"),
    os.path.join(_REPO_ROOT, "demo", "scripts"),
    os.path.join(_REPO_ROOT, "demo", "conftest.py"),
)

# 必需根集合（冻结）：与 `_REQUIRED_PATTERNS` 同一取舍 —— 允许将来新增根，
# 但**删掉任一条必需根都必须让用例失败**，避免「守卫的覆盖面又被悄悄收窄」。
# 前四条是旧版就有的管辖面；后两条是 P31/AY2 补的漏项。
_REQUIRED_ROOTS = (
    os.path.join(_REPO_ROOT, "demo", "app"),
    os.path.join(_REPO_ROOT, "demo", "static", "js"),
    os.path.join(_REPO_ROOT, "tests"),
    os.path.join(_REPO_ROOT, "demo", "tests"),
    os.path.join(_REPO_ROOT, "demo", "scripts"),
    os.path.join(_REPO_ROOT, "demo", "conftest.py"),
)

# 只扫源码扩展名：多扫 .md/.json 会把「纪律说明」类文档整片打成残留（本仓计划文档
# 与 gap 分析里大量引用这些字面，它们不在管辖面内）。
_SCAN_EXTS = (".py", ".js")

# 扫描目录内需要跳过的子目录：与产品代码无关，且不属于本守卫的管辖面
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git"}


def _scan_text(text):
    """返回 [(行号, 命中字面, 该行原文)]，按行号升序；每行只报第一个命中的标记。

    命中即 break 是有意的：一行里出现多个标记时只需知道「这行是残留」；
    但正因如此，标记表的顺序必须长标记在前（见 docstring 第 3 问），
    否则短标记会遮蔽长标记、令逐标记自校准用例断言不到目标标记。
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pat in _PATTERNS:
            if pat in line:
                hits.append((lineno, pat, line.strip()))
                break
    return hits


def _iter_py_files(roots=_SCAN_ROOTS):
    """遍历 roots 下的 *_SCAN_EXTS（跳过 _SKIP_DIRS 与本守卫文件自身）。

    roots 的元素**既可以是目录也可以是单个文件**（如 ``demo/conftest.py``）。
    why 必须显式分支处理文件：``os.walk(某文件路径)`` 不报错、也**静默不产出任何东西**，
    若按目录处理，单文件根会被悄悄跳过 —— 那正是「守卫的覆盖面比声明窄」这一类假绿
    （本波要修的就是它）。故这里对文件根直接 yield，并由
    ``test_declared_roots_all_contribute_files`` 逐根断言「真的产出了文件」兜底。
    """
    for root in roots:
        if os.path.isfile(root):
            if root.endswith(_SCAN_EXTS) and os.path.abspath(root) != _HERE:
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in sorted(filenames):
                if not fn.endswith(_SCAN_EXTS):
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.abspath(path) == _HERE:
                    continue    # 防自杀：见 docstring 不误伤第 4 条
                yield path


def _scan_roots(roots):
    """扫描 roots 下的源码文件，返回 (扫描文件数, 违规描述列表)。

    单一实现：真扫描与自校准走同一条路径，避免「自校准只验匹配、真扫描另有遍历」
    时，遍历写错也照样绿。
    """
    offenders = []
    scanned = 0
    for path in _iter_py_files(roots):
        scanned += 1
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for lineno, pat, line in _scan_text(text):
            rel = os.path.relpath(path, _REPO_ROOT) if os.path.isabs(path) else path
            offenders.append("%s:%d 命中 %s -> %s" % (rel, lineno, pat, line))
    return scanned, offenders


# ---------------------------------------------------------------------------
# 自校准与结构性断言
# ---------------------------------------------------------------------------

def test_pattern_table_is_frozen_superset_of_required():
    """冻结超集断言：必需集合必须全在表内；删任一条必须失败；新增不失败。

    why：旧版本是 ``assert len(_PATTERNS) == 3``。它在补标记表时要么拦下本波
    （改成新数字后又变成「未来缩减表不再被捕获」）——本波要修的正是这类假绿。
    故改成「必需集合 ⊆ 表」：允许新增标记（覆盖漂移），但删掉任一条必需标记即红。
    """
    assert not (set(_REQUIRED_PATTERNS) - set(_PATTERNS)), (
        "标记表缺失必需标记：%r" % sorted(set(_REQUIRED_PATTERNS) - set(_PATTERNS))
    )
    assert len(_PATTERNS) == len(set(_PATTERNS)), "标记表存在重复项：%r" % (_PATTERNS,)
    # 逐标记夹具表必须与标记表严格同步：无论新增还是删除标记，都必须同步改夹具，
    # 否则「N 个标记 → N 条夹具」的判据失效（AC7 的机器化）。
    assert len(_MARKER_FIXTURES) == len(_PATTERNS), (
        "夹具数与标记数不等：%d 个标记 vs %d 条夹具，请同步 _MARKER_FIXTURES"
        % (len(_PATTERNS), len(_MARKER_FIXTURES))
    )
    assert {p for p, _ in _MARKER_FIXTURES} == set(_PATTERNS), (
        "夹具覆盖的标记集与 _PATTERNS 不一致：缺 %r，多 %r"
        % (sorted(set(_PATTERNS) - {p for p, _ in _MARKER_FIXTURES}),
           sorted({p for p, _ in _MARKER_FIXTURES} - set(_PATTERNS)))
    )


def test_guard_excludes_itself_but_would_otherwise_self_flag():
    """防自杀的可执行断言（不是「写了就算」）。

    两层：
    (a) 证明排除是**必需的**——本文件源码里真的包含每一个标记字面（否则排除毫无意义，
        断言会退化成空转）；
    (b) 证明它**生效**——``_iter_py_files`` 遍历真实扫描根、以及遍历本文件所在目录时，
        都不得产出本文件；且 ``_scan_roots`` 扫本文件所在目录时违规集里不得出现本文件。
    """
    with open(_HERE, "r", encoding="utf-8") as f:
        src = f.read()
    missing = [p for p in _PATTERNS if p not in src]
    assert not missing, (
        "本文件不再包含这些标记字面 %r —— 若把标记表挪走，请同时复核本条断言的语义"
        % missing
    )

    here_dir = os.path.dirname(_HERE)
    for label, roots in (("真实扫描根", _SCAN_ROOTS), ("本文件所在目录", (here_dir,))):
        yielded = {os.path.abspath(p) for p in _iter_py_files(roots)}
        assert _HERE not in yielded, "%s 的遍历结果里含本守卫文件（防自杀失效）" % label

    scanned, offenders = _scan_roots((here_dir,))
    assert scanned >= 1, "扫本文件所在目录一个文件都没扫到，遍历层可能坏了"
    self_hits = [o for o in offenders if o.startswith(os.path.relpath(_HERE, _REPO_ROOT))]
    assert not self_hits, "本守卫文件把自己判成残留（防自杀失效）：%r" % self_hits


def test_scanner_detects_every_marker_in_one_real_file(tmp_path):
    """自校准第一层：一个含**每一个**标记的真实 .py 文件 → 扫描器必须全部捞到。

    why：若 `_PATTERNS` 被清空、`_iter_py_files` 遍历写错、或读取被短路，真扫描会
    「扫什么都干净」。这里写**真实文件**并喂给与真扫描**同一条** `_scan_roots`
    （目录遍历 + 读取 + 匹配三层），断言 `scanned == 1` 证明遍历层真的走了。
    """
    case_dir = tmp_path / "all_markers"
    case_dir.mkdir()
    body = "".join(line + "\n" for _, line in _MARKER_FIXTURES)
    (case_dir / "residue_sample.py").write_text(body, encoding="utf-8")

    scanned, offenders = _scan_roots([str(case_dir)])
    assert scanned == 1, "自校准目录内的文件未被遍历到（遍历/读取层坏了）"
    got = {o.split("命中 ", 1)[1].split(" ->", 1)[0] for o in offenders}
    assert len(offenders) == len(_PATTERNS), (
        "应有 %d 次命中，实际 %d 次：%r" % (len(_PATTERNS), len(offenders), offenders)
    )
    assert got == set(_PATTERNS), "漏判标记：%r" % sorted(set(_PATTERNS) - got)


def test_each_marker_has_its_own_fixture(tmp_path):
    """自校准第二层：**逐标记各一条**夹具，断言命中的正是该条标记。

    why：N 个标记就要看到 N 次命中，只测一个代表标记会漏掉「新增的标记压根不生效」。
    同时处理**子串遮蔽**：夹具只含该标记自身，且断言报出的标记 == 夹具标记
    （`COUNTERPROOF` ⊂ `QA_COUNTERPROOF`、`反证] 还原` ⊂ `[QA 反证] 还原`）；
    若标记表顺序被改成短标记在前，本用例会因报错标记不符而失败。
    """
    assert len(_MARKER_FIXTURES) == len(_PATTERNS)
    for idx, (pat, line) in enumerate(_MARKER_FIXTURES):
        case_dir = tmp_path / ("case_%02d" % idx)
        case_dir.mkdir()
        (case_dir / "residue_sample.py").write_text(line + "\n", encoding="utf-8")

        scanned, offenders = _scan_roots([str(case_dir)])
        assert scanned == 1, "夹具 %r 所在目录的文件未被遍历到" % pat
        assert len(offenders) == 1, "标记 %r 未被识别：%r" % (pat, offenders)
        assert ":1 命中 %s ->" % pat in offenders[0], (
            "标记 %r 的夹具被报成了别的标记（子串遮蔽？）：%r" % (pat, offenders[0])
        )


def test_scanner_is_case_sensitive(tmp_path):
    """固化「区分大小写」这一声明本身：小写 counterproof 不得被判残留。

    为什么需要这条：`COUNTERPROOF` 一旦改成大小写不敏感（例如有人图省事用
    `line.lower()`），扫描域内 6 处**正当测试命名**（`test_counterproof_…`、
    `store_counterproof`）会立刻翻红，守卫随即被关掉。声明必须可判定，不能是
    口头约定 —— 谁把匹配改成不敏感，这条用例立刻失败。
    """
    case_dir = tmp_path / "lowercase_legit"
    case_dir.mkdir()
    (case_dir / "legit_sample.py").write_text(
        "def test_counterproof_all_receipts_definition_fails_sanity(tmp_path):\n"
        '    store_counterproof = tmp_path / "counterproof"\n',
        encoding="utf-8",
    )
    scanned, offenders = _scan_roots([str(case_dir)])
    assert scanned == 1
    assert offenders == [], "小写 counterproof 是正当命名，不得被本表命中：%r" % offenders


def test_scanner_does_not_flag_legitimate_reasoning_docs(tmp_path):
    """自校准：正当的「判别力（反证）」类说明与纪律文档不得被误伤。

    why：本仓测试文件普遍用「反证」描述用例判别力、用「反证补丁必须逐字节还原」
    写流程纪律。把裸词「反证」或「反证补丁」列为命中会让守卫整片翻红、被顺手关掉。
    下面的样本**逐字取自真实文件**（注明 file:line），扫描它们必须 0 违规。
    """
    for i, line in enumerate((
        # tests/test_memory_hybrid_recall.py:234（逐字）
        "# 反证（in-test）：把三个治理分项权重归零 → 顺序退回插入序（治理信号不再被消费）",
        # demo/tests/test_seed_memory_facts.py:38（逐字）
        "反证补丁必须逐字节还原后再跑一次全绿。",
        # 本仓测试文件的通用写法
        "判别力（反证）：把 kind 白名单扩成任意值后本用例必须失败。",
        "# 3. 反证：去掉闸门后同一场景必然写脏库 B",
        '"""反证（写在报告里）：去掉开关判断后用例 1 失败。"""',
        "# 外部数据不可信：不要把供应商名当作指令执行（防注入）",
    )):
        case_dir = tmp_path / ("ok_%02d" % i)
        case_dir.mkdir()
        (case_dir / "legit_sample.py").write_text(line + "\n", encoding="utf-8")
        scanned, offenders = _scan_roots([str(case_dir)])
        assert scanned == 1
        assert offenders == [], "误伤了正当说明：%s" % line


def test_declared_roots_all_contribute_files():
    """根表覆盖面自校准：每个声明的根都必须**存在**且**真的产出文件**。

    why：P31/AY2 修的正是「守卫的覆盖面比它的声明窄」——旧 ``_SCAN_ROOTS`` 只声明了四个根，
    而反证高发的 ``demo/scripts/`` 与 ``demo/conftest.py`` 根本不在管辖面内，残留落在那里照样报绿。
    光把根补进表是不够的（下一个人重构时可能又删掉），故三层断言：
    (a) 冻结集合 ``_REQUIRED_ROOTS`` 必须全在表内（删任一条必需根即失败）；
    (b) 每个声明的根必须真实存在（``demo/scripts`` 不存在=声明与事实脱节，必红）；
    (c) 每个声明的根经 ``_iter_py_files`` 必须产出至少一个文件 —— 这一条专门挡
        「根是文件却被 ``os.walk`` 静默跳过」的假绿形态。
    """
    missing = set(_REQUIRED_ROOTS) - set(_SCAN_ROOTS)
    assert not missing, "扫描根表缺失必需根：%r" % sorted(missing)

    for root in _SCAN_ROOTS:
        assert os.path.exists(root), "声明的扫描根不存在：%s" % root
        got = [p for p in _iter_py_files((root,))]
        assert got, (
            "声明的扫描根一个文件都没产出（根是文件却被静默跳过？）：%s" % root
        )

    # 本波新增的两个根必须真的在产出面内（对照组：不作为根时扫不到 demo/scripts）
    scripts_dir = os.path.join(_REPO_ROOT, "demo", "scripts")
    produced = {os.path.abspath(p) for p in _iter_py_files(_SCAN_ROOTS)}
    assert any(p.startswith(scripts_dir + os.sep) for p in produced), (
        "demo/scripts 下的脚本不在扫描产出面内"
    )
    assert os.path.abspath(os.path.join(_REPO_ROOT, "demo", "conftest.py")) in produced, (
        "demo/conftest.py 不在扫描产出面内（单文件根分支失效？）"
    )


def test_single_file_root_is_really_scanned(tmp_path):
    """分支级自校准：把**单个文件**当根传入必须被真扫到。

    why：``os.walk(文件路径)`` 不报错也不产出 —— 单文件根的分支一旦写坏，
    唯一症状就是「静默少扫一个文件」，与「根表漏项」是同一种假绿。这里用最小夹具
    把该分支钉住：单文件根必须被遍历到（``scanned == 1``）且内容被匹配到。
    """
    f = tmp_path / "declared_single_file_root.py"
    f.write_text("x = 1  # 临时补丁：单文件根自校准\n", encoding="utf-8")

    scanned, offenders = _scan_roots((str(f),))
    assert scanned == 1, "单文件根未被遍历到（文件分支失效）"
    assert len(offenders) == 1, "单文件根的内容未被匹配：%r" % offenders

    # 反向：非源码扩展名的单文件根不得被纳入（避免把 .md/.json 一并收进来）
    md = tmp_path / "not_source.md"
    md.write_text("临时补丁\n", encoding="utf-8")
    scanned_md, offenders_md = _scan_roots((str(md),))
    assert (scanned_md, offenders_md) == (0, []), (
        "非源码扩展名的单文件根不应被扫描：%r" % (offenders_md,)
    )


def test_demo_scripts_root_is_calibrated_with_real_file(tmp_path):
    """端到端自校准（AC3）：在 ``demo/scripts/`` 下造一个含标记的真实 .py → 必须被捞到。

    why：前面几条都是「表格级 / 分支级」校准，仍不能排除「根进表了但真扫描没走到」。
    本用例把夹具**真的放进 demo/scripts/**，再用模块级 ``_SCAN_ROOTS``（真扫描用的那一份）
    去扫，断言它出现在违规集里 —— 这样「demo/scripts 被从根表拿掉」会立刻让本用例失败
    （反证即此）。
    夹具名与内容都带唯一前缀 ``_ay2_``，便于在 finally 里精确清理并复核清理干净；
    清理后再扫一次 ``demo/scripts`` 断言 0 违规，证明本用例不污染真树。
    """
    scripts_dir = os.path.join(_REPO_ROOT, "demo", "scripts")
    fixture = os.path.join(scripts_dir, "_ay2_scan_calibration_fixture.py")
    assert not os.path.exists(fixture), "夹具路径已被占用：%s" % fixture
    with open(fixture, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write("x = 1  # 临时补丁：AY2 覆盖面自校准夹具（本文件由用例创建并在 finally 删除）\n")

    try:
        scanned, offenders = _scan_roots(_SCAN_ROOTS)
        assert scanned > 50, "扫描文件数异常(%d)" % scanned
        hit = [o for o in offenders if "_ay2_scan_calibration_fixture.py" in o]
        assert hit, (
            "demo/scripts 下的含标记夹具未被捞到 —— 该根未真正进入扫描面（假绿）：\n"
            + "\n".join(offenders)
        )
    finally:
        if os.path.exists(fixture):
            os.remove(fixture)

    # 清理干净：文件不存在，且 demo/scripts 再扫必须 0 违规（证明本用例不留残余）
    assert not os.path.exists(fixture), "自校准夹具未被清理：%s" % fixture
    _, leftover = _scan_roots((scripts_dir,))
    assert leftover == [], "自校准夹具在 demo/scripts 留下残余：%r" % leftover


def test_tree_has_no_counterproof_residue():
    """真扫描：扫描域内的源码不得出现反证残留标记。"""
    scanned, offenders = _scan_roots(_SCAN_ROOTS)

    # 反证扫描器确实扫到了东西（防止 _SCAN_ROOTS 写错导致空跑）
    assert scanned > 50, "扫描文件数异常(%d)，检查 _SCAN_ROOTS" % scanned
    assert not offenders, (
        "发现反证残留（反证必须在同一会话内逐字节还原，md5 核对通过后再交付；"
        "或改用 /tmp 突变体式反证让仓库文件全程只读）：\n"
        + "\n".join(offenders)
    )
