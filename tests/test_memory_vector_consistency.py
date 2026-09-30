# -*- coding: utf-8 -*-
"""记忆表 ↔ 向量库一致性巡检工具的回归测试（计划 P0-T3 巡检入口 / Stream B）。

覆盖两类真实出现过的层级脱钩（gap 05 §H2），并逐条给出可复算断言：
1. **DB 有行、向量缺失** —— 历史基线（P0-T3 清退期，2026-09-22）是 9 行全缺向量
   （当时两库整体清退、记忆尚未生效）；
2. **向量有 memory_id、DB 无行** —— 历史基线（H2 审计时点，2026-09-22 前）是
   12 个 mid / 24 条孤儿向量；
3. **向量无 memory_id** —— 历史基线 155 条，属第三类问题（无法按 mid 回溯删除），
   它**不算孤儿**（口径必须在测试里钉死，否则巡检会把 155 当成 155 个孤儿虚报）。

本模块一律**不把 live 的当前数字写成断言** —— 上面三处「基线」是历史快照，不是当前
live 状态：记忆生效后生产向量库 `demo/.rag_chroma` **必须存在**，其行数 / 向量数随
记忆增删而变化（曾把「两库已清退、9 行全缺向量」当现状，是已被设计变更合法推翻的
旧前提，见 `test_check_creates_no_store_dir_and_leaves_live_untouched` 的 why）。
要断言 live，只能用「跑前跑后逐项不变」的指纹口径，不能用「必须是某个固定数字」。

反证（防止「工具恒报一致」这种恒真用例）：
- `test_counterproof_injected_orphan_vector_makes_check_fail` 在同一个临时库上先断言
  干净态无孤儿，再手工注入一条孤儿向量，断言巡检**翻成不一致**并列出该 mid。
  若工具退化成恒返空清单，本条失败。
- `test_consistent_case_reports_intersection` 是反向的判别式：两侧对齐时必须报
  intersection 命中且两个问题清单为空（证明工具不是靠"永远报不一致"蒙过上面两条）。

隔离：本模块只用自己的临时 DB（真实 schema，经 `db._make_engine()` 建表）与临时
向量目录；向量库用 chromadb 客户端直接写入（不走 rag.py），且巡检侧只读
（sqlite3 mode=ro）。

两条契约（本波把「钉旧契约」改成「守真不变量」）
------------------------------------------------
1. 巡检这个只读动作**不得写入任何向量库**：既不创建传入目录，也不碰已存在的 live
   向量库 —— 见 `test_check_creates_no_store_dir_and_leaves_live_untouched`（对 live
   向量库取「文件相对路径集合 + 总字节数 + `chroma.sqlite3` 的 md5」指纹，前后逐项比对）。
2. RAG 缺省路径**只有一份实现** `app/services/rag_paths.py`：`rag.PERSIST_DIR` 与巡检
   工具的缺省解析必须同源 —— 见 `test_path_defaults_mirror_authoritative_sources`。
两条契约各配一条**自校准**用例（指纹能抓到变化 / 回退旧口径后同源等式必须不成立），
防止它们退化成「恒真的装饰」。
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

# 隔离环境：本模块自己的临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_vector_consistency_")
os.environ["DB_PATH"] = os.path.join(_TMP, "base.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.memory_consistency as mc  # noqa: E402

_LIVE_RAG_DIRS = (
    os.path.join(_DEMO_DIR, ".rag_chroma"),
    os.path.join(_REPO_ROOT, ".rag_chroma"),
)


# -------------------------------------------------------------
# 夹具构造：真实 schema 的临时库 + 临时向量库
# -------------------------------------------------------------
def _seed_db(tmp_path, rows):
    """建一个带真实 vendor_memory schema 的临时库并插入记忆行。

    rows = [(memory_id, vendor, tenant_id)]；memory_id 传 "" 表示该行没有 mid。
    用 db._make_engine() 建表而不是手抄 DDL —— 巡检 SQL 必须对着**真实**表结构跑，
    手抄 DDL 会让「查询与真实 schema 不匹配」这类缺陷逃过测试。
    """
    path = os.path.join(str(tmp_path), "consistency.db")
    db.DB_PATH = path
    db._make_engine()
    conn = sqlite3.connect(path)
    try:
        conn.executemany(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status) "
            "VALUES (?, ?, ?, '', '', 'active')",
            [(mid, vendor, tid) for mid, vendor, tid in rows])
        conn.commit()
    finally:
        conn.close()
    return path


def _write_vectors(store_dir, entries, collection="vendor_memory"):
    """用 chromadb 客户端直接写临时向量库：entries = [(document, memory_id 或 None)]。

    刻意不经过 rag.py（本任务要求巡检工具与测试都不依赖它的内部函数），也刻意传
    `embedding_function=None` + 显式向量 —— 否则 chromadb 会去加载默认的 ONNX
    嵌入模型（联网/下载），测试就不再是离线的。
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(store_dir))
    col = client.get_or_create_collection(collection, embedding_function=None)
    metas = []
    for _doc, mid in entries:
        meta = {"vendor": "一致性探針供應商"}
        if mid is not None:
            meta["memory_id"] = mid
        metas.append(meta)
    col.add(
        ids=["vec-%d" % i for i in range(len(entries))],
        embeddings=[[0.1 + 0.01 * i] * 8 for i in range(len(entries))],
        documents=[doc for doc, _mid in entries],
        metadatas=metas,
    )
    client.clear_system_cache()


# -------------------------------------------------------------
# 指纹 + 「唯一实现」测量工具（供上面两条契约使用）
# -------------------------------------------------------------
_CHROMA_DB_FILE = "chroma.sqlite3"


def _dir_fingerprint(path):
    """目录指纹：文件相对路径集合 + 总字节数 + `chroma.sqlite3` 的 md5；不存在返回 None。

    用三元组而不是单个 mtime / size：mtime 会因别的进程的只读打开而抖动（本项目实测过
    「只有时间戳变」的假阳性），而 sqlite 的页写入可能不改变主库文件大小 —— 只有内容
    哈希能同时抓住这两类变化。目录不存在时返回 None，让调用方能断言「仍不存在」。
    """
    if not os.path.isdir(path):
        return None
    files = []
    total = 0
    for dirpath, _dirs, filenames in os.walk(path):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            files.append(os.path.relpath(fp, path))
            total += os.path.getsize(fp)
    sqlite_md5 = None
    db_file = os.path.join(path, _CHROMA_DB_FILE)
    if os.path.isfile(db_file):
        with open(db_file, "rb") as f:
            sqlite_md5 = hashlib.md5(f.read()).hexdigest()
    return {"files": sorted(files), "total_bytes": total,
            "chroma_sqlite3_md5": sqlite_md5}


# 子进程片段：在干净环境里测量两侧的**缺省**解析结果。
_SUBPROCESS_RESOLVE_SNIPPET = (
    "import json, sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "from app.services import rag, rag_paths;"
    "print(json.dumps({'rag': rag.PERSIST_DIR,"
    " 'default': rag_paths.default_rag_dir()}))"
)


def _default_resolution_in_subprocess(tmp_path):
    """在干净环境的子进程里测量 `rag.PERSIST_DIR` 的缺省值。

    why 必须子进程：`rag.PERSIST_DIR` 在 import 期定格，而 conftest 的向量隔离夹具会把
    已加载的 rag 模块的 `PERSIST_DIR` 改写成本轮临时目录 —— 同进程内量到的永远不是
    缺省值。片段必须移除 `RAG_DIR`；`DB_PATH` 指向临时库（`import app.services.rag` 会
    连带 `app.db`，其 import 期迁移块会按 `DB_PATH` 执行 ALTER，绝不能让 DB_PATH 落回 live）。
    """
    env = dict(os.environ)
    env.pop("RAG_DIR", None)
    env["DB_PATH"] = os.path.join(str(tmp_path), "subprocess_resolve.db")
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_RESOLVE_SNIPPET, _DEMO_DIR],
        cwd=_DEMO_DIR, env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, "缺省解析子进程失败: %s" % proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# -------------------------------------------------------------
# 1. DB 有行无向量 → missing_vector_mids
# -------------------------------------------------------------
def test_check_detects_db_row_without_vector(tmp_path):
    db_path = _seed_db(tmp_path, [("mid-db-only", "德利行", "default")])
    rag_dir = tmp_path / "store_empty"
    _write_vectors(rag_dir, [("無關向量", "mid-other")])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))

    assert r["db_rows"] == 1
    assert r["missing_vector_mids"] == ["mid-db-only"], \
        "DB 有行而向量库无该 mid，必须进 missing_vector_mids"
    assert r["orphan_mids"] == ["mid-other"], "反向的孤儿也必须同时被报出"
    assert r["consistent"] is False


# -------------------------------------------------------------
# 2. 向量有 memory_id、DB 无行 → orphan_mids
# -------------------------------------------------------------
def test_check_detects_orphan_vector_without_db_row(tmp_path):
    db_path = _seed_db(tmp_path, [("mid-in-db", "祥興", "default")])
    rag_dir = tmp_path / "store_orphan"
    _write_vectors(rag_dir, [("孤儿向量", "mid-orphan")])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))

    assert r["orphan_mids"] == ["mid-orphan"], \
        "向量带 mid 但 DB 无对应行，必须进 orphan_mids"
    assert r["missing_vector_mids"] == ["mid-in-db"]
    assert r["stores"][0]["distinct_memory_id"] == 1
    assert r["stores"][0]["vectors"] == 1


# -------------------------------------------------------------
# 3. 判别式反向用例：两侧对齐时必须报「一致 + 交集命中」
# -------------------------------------------------------------
def test_consistent_case_reports_intersection(tmp_path):
    """两侧 mid 一一对应时，工具必须报一致且交集非空。

    why：没有这条，「工具恒报不一致」也能通过上面两条 —— 那样巡检就失去了信息量。
    """
    db_path = _seed_db(tmp_path, [("mid-1", "金百加", "default"),
                                  ("mid-2", "金百加", "default")])
    rag_dir = tmp_path / "store_ok"
    _write_vectors(rag_dir, [("notes 文檔", "mid-1"), ("sample 文檔", "mid-1"),
                             ("notes 文檔", "mid-2")])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))

    assert r["intersection"] == ["mid-1", "mid-2"]
    assert r["orphan_mids"] == [] and r["missing_vector_mids"] == []
    assert r["stores"][0]["vectors"] == 3, "向量条数按行计（同一 mid 两条都算）"
    assert r["stores"][0]["distinct_memory_id"] == 2, "distinct 口径按 mid 去重"
    assert r["consistent"] is True


# -------------------------------------------------------------
# 4. 反证：注入一条孤儿向量 → 巡检必须翻红
# -------------------------------------------------------------
def test_counterproof_injected_orphan_vector_makes_check_fail(tmp_path):
    """手工往临时库注入一条孤儿向量 → 巡检由「一致」翻成「不一致」并点名该 mid。

    这就是本工具的判别力证据：若它恒返空清单，第一步（干净态）会通过，
    第二步（注入后仍报一致）会失败。
    """
    db_path = _seed_db(tmp_path, [("mid-ok", "聯豐", "default")])
    rag_dir = tmp_path / "store_counterproof"
    _write_vectors(rag_dir, [("已確認明細", "mid-ok")])

    before = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))
    assert before["consistent"] is True, "注入前必须是干净态，否则反证不成立"
    assert before["orphan_mids"] == []

    # 注入孤儿：直接对同一个临时向量库补一条带 mid 的记录
    import chromadb

    client = chromadb.PersistentClient(path=str(rag_dir))
    col = client.get_or_create_collection("vendor_memory", embedding_function=None)
    col.add(ids=["vec-injected-orphan"], embeddings=[[0.9] * 8],
            documents=["注入的孤儿向量"],
            metadatas=[{"vendor": "注入供應商", "memory_id": "mid-injected"}])
    client.clear_system_cache()

    after = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))
    assert after["orphan_mids"] == ["mid-injected"], \
        "注入孤儿后必须被检出（否则巡检是恒真的装饰品）"
    assert after["consistent"] is False


# -------------------------------------------------------------
# 5. 无 memory_id 的向量：计入 vectors_without_memory_id，不算孤儿
# -------------------------------------------------------------
def test_vectors_without_memory_id_are_counted_but_not_orphans(tmp_path):
    """历史基线里 155 条向量没有 memory_id —— 那是「不可回溯」，不是「孤儿」。

    why（数字为何是历史的）：155 是 P0-T3 清退期的一次性 live 读数，当前 live 向量库
    已随记忆生效重建（无 mid 向量数 ≠ 155）。这里引它只为说明**口径**：无 mid 的向量
    无法与 DB 比对，既不进 orphan_mids（没有 mid 可比），也不进 missing_vector_mids
    （它没有归属的 DB 行），只能单独计数 —— 口径必须在测试里钉死，否则巡检会把
    它们当成孤儿虚报。本条断言全部作用在 fixture 自建的临时向量库上，与 live 无关。
    """
    db_path = _seed_db(tmp_path, [("mid-a", "大生", "default")])
    rag_dir = tmp_path / "store_no_mid"
    _write_vectors(rag_dir, [("帶 mid", "mid-a"), ("無 mid", None), ("無 mid 2", None)])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))

    store = r["stores"][0]
    assert store["vectors"] == 3
    assert store["distinct_memory_id"] == 1
    assert store["vectors_without_memory_id"] == 2
    assert r["orphan_mids"] == []
    assert r["missing_vector_mids"] == []
    assert r["consistent"] is True, "无 mid 向量属 counts 类问题，不改变一致判定"


def test_db_row_without_memory_id_is_counted(tmp_path):
    """DB 侧同理：无 mid 的行无法参与比对，单独计数，不混进 missing_vector_mids。"""
    db_path = _seed_db(tmp_path, [("", "無 mid 行", "default"), ("mid-b", "無 mid 行", "default")])
    rag_dir = tmp_path / "store_mixed"
    _write_vectors(rag_dir, [("文檔", "mid-b")])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))

    assert r["db_rows"] == 2
    assert r["db_rows_without_memory_id"] == 1
    assert r["missing_vector_mids"] == []
    assert r["orphan_mids"] == []
    assert r["consistent"] is True


# -------------------------------------------------------------
# 6. 租户口径：None = 全租户；给值 = DB 与 collection 双侧收窄
# -------------------------------------------------------------
def test_tenant_scope_narrows_both_sides(tmp_path):
    """tenant_id 给值时 DB 与向量双侧都收窄；缺省 None 时不得静默回退成 default。

    why：H1 的根因就是「读侧不传租户 → 恒落 default」，巡检若同样默认 default，
    非默认租户的孤儿/缺向量永远不会被报出来。
    """
    db_path = _seed_db(tmp_path, [("mid-default", "德利行", "default"),
                                  ("mid-t2", "祥興", "t2")])
    rag_dir = tmp_path / "store_tenants"
    _write_vectors(rag_dir, [("default 桶孤兒", "mid-orphan-default")],
                   collection="vendor_memory")
    _write_vectors(rag_dir, [("t2 桶", "mid-t2")],
                   collection="tenant_t2_vendor_memory")

    full = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))
    assert full["tenant_scope"] == "all"
    assert full["db_rows"] == 2
    assert sorted(full["orphan_mids"]) == ["mid-orphan-default"]
    assert full["missing_vector_mids"] == ["mid-default"]
    assert {s["collection"] for s in full["stores"]} == {
        "vendor_memory", "tenant_t2_vendor_memory"}

    t2 = mc.check_memory_consistency(tenant_id="t2", db_path=db_path, rag_dir=str(rag_dir))
    assert t2["tenant_scope"] == "t2"
    assert t2["db_rows"] == 1
    assert [s["collection"] for s in t2["stores"]] == ["tenant_t2_vendor_memory"]
    assert t2["orphan_mids"] == [] and t2["missing_vector_mids"] == []
    assert t2["consistent"] is True, "t2 视角只应看到 t2 的库与行"

    dflt = mc.check_memory_consistency(tenant_id="default", db_path=db_path,
                                       rag_dir=str(rag_dir))
    assert dflt["db_rows"] == 1
    assert dflt["orphan_mids"] == ["mid-orphan-default"]
    assert dflt["missing_vector_mids"] == ["mid-default"]


# -------------------------------------------------------------
# 7. 零写入：不创建传入目录；对已存在的 live 向量库不得产生任何写入
# -------------------------------------------------------------
def test_check_creates_no_store_dir_and_leaves_live_untouched(tmp_path):
    """巡检不得创建传入目录；也不得往**已存在的 live 向量库**写入任何字节。

    why（旧判据为何过时）：本用例原先断言「live 向量库目录不存在」—— 那是 P0-T3
    （live 向量库被整体清退、记忆尚未生效）时的正确口径。**该前提已被设计变更合法
    推翻**：现在记忆真的生效了，生产向量库 `demo/.rag_chroma` **必须存在**（含 active
    行的向量，巡检报 consistent）。「目录不存在」只是当时的手段，要守的**目的**是
    「巡检这个只读动作不得写入任何向量库」，故改为对已存在的 live 向量库取**指纹**
    （文件相对路径集合 + 总字节数 + `chroma.sqlite3` 的 md5），跑完巡检后重取并逐项
    比对；目录本就不存在时则断言仍不存在。

    判别力：巡检全程以 sqlite3 `mode=ro` 读 Chroma 持久化表（`_connect_ro`，只读打开
    受阻时退回复制到临时目录再读，原库一字节不改）。一旦它退化回
    `PersistentClient(path=...)` 打开目录（会执行内部 migration、写 acquire_write /
    maintenance_log），或开始往传入目录落盘，指纹立刻不同 —— 指纹本身能抓到「新增
    文件」与「只改内容不改大小」两类变化，由
    `test_dir_fingerprint_detects_store_change` 自校准。
    """
    missing_store = tmp_path / "does_not_exist"
    db_path = _seed_db(tmp_path, [("mid-x", "大生", "default")])

    live_store = os.path.join(_DEMO_DIR, ".rag_chroma")
    live_before = _dir_fingerprint(live_store)

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(missing_store))

    assert r["store_present"] is False
    assert r["vectors"] == 0 and r["stores"] == []
    assert not missing_store.exists(), "巡检不得创建向量库目录"
    assert r["missing_vector_mids"] == ["mid-x"], "库不存在等价于全部缺向量"

    live_after = _dir_fingerprint(live_store)
    if live_before is None:
        assert live_after is None, "巡检不得把 live 向量库创建出来"
    else:
        assert live_after == live_before, (
            "巡检不得对已存在的 live 向量库产生任何写入：文件相对路径集合 / 总字节数 / "
            "chroma.sqlite3 的 md5 必须逐项相同\nbefore=%r\nafter =%r"
            % (live_before, live_after))

    # 附带守卫：缺省口径收敛到 <demo>/.rag_chroma 之后，任何从仓库根运行、且未显式设
    # `RAG_DIR` 的代码都不该再写进根目录那个 store（这正是 R7「同一 memory_id 的向量
    # 落进另一个 store」的症状）。
    repo_store = os.path.join(_REPO_ROOT, ".rag_chroma")
    assert repo_store in {os.path.abspath(d) for d in _LIVE_RAG_DIRS}
    assert not os.path.exists(repo_store), \
        "<repo>/.rag_chroma 不应存在（P8a-R7 后缺省已收敛到 <demo>/.rag_chroma）"


def test_path_defaults_mirror_authoritative_sources(monkeypatch, tmp_path):
    """RAG 缺省路径**只有一份实现**：`rag.PERSIST_DIR` 与巡检工具的缺省解析必须同源。

    why（新契约）：P8a·AK1 起缺省解析的**唯一实现**是 `app/services/rag_paths.py`
    （应用根基准的绝对路径，与 cwd 无关）。本用例断言的是**值同源**，不再把巡检工具的
    内部函数名 `mc._resolve_rag_dir` 当作契约面（它只是内部委托，随时可能被收敛掉）；
    「巡检工具自己复刻一份口径」这类回流由下面的替换探针钉住。DB 缺省仍在本工具复刻
    一份，继续与 `db._default_db_path` 对拍。

    why 用子进程测 `rag.PERSIST_DIR`：它在 **import 期**由 `rag_paths.resolve_rag_dir()`
    定格，而 conftest 的向量隔离夹具会把已加载的 rag 模块的 `PERSIST_DIR` 改写成临时
    目录 —— 同进程内量到的永远不是缺省值（同一手法见
    tests/test_rag_persist_dir_app_root.py 的探针）。
    """
    monkeypatch.delenv("RAG_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)

    from app.services import rag_paths

    # (1) rag.PERSIST_DIR 的缺省 == 唯一实现的缺省（子进程里 RAG_DIR 已移除）
    out = _default_resolution_in_subprocess(tmp_path)
    assert out["rag"] == out["default"] == rag_paths.default_rag_dir(), \
        "rag.PERSIST_DIR 的缺省必须直接来自唯一实现 rag_paths.default_rag_dir()"
    assert os.path.isabs(out["rag"]), "缺省必须是绝对路径（否则仍依赖 cwd）"

    # (2) 巡检工具的缺省解析也等于同一值 —— 走公开报告字段 rag_dir，不引用内部函数名
    db_path = _seed_db(tmp_path, [("mid-path", "大生", "default")])
    report = mc.check_memory_consistency(db_path=db_path)
    assert report["rag_dir"] == out["rag"] == rag_paths.default_rag_dir(), \
        "巡检工具的缺省解析必须与 rag.PERSIST_DIR 同源（同一份唯一实现）"

    # (3) 委托关系的硬判据：替换唯一实现后，巡检报出的缺省必须随之改变 —— 若巡检自己
    # 复刻了一份解析逻辑，这里不会跟着变（这正是要防的「同一口径两份实现」回流）。
    monkeypatch.setattr(rag_paths, "resolve_rag_dir",
                        lambda *a, **k: "/tmp/__delegated__")
    assert mc.check_memory_consistency(db_path=db_path)["rag_dir"] == "/tmp/__delegated__", \
        "巡检工具必须委托 rag_paths 的唯一实现，而不是自己复刻口径"

    # (4) 源码级硬判据：rag.py 的 PERSIST_DIR 必须由唯一实现赋值（防有人写回旧口径）。
    # 「生产代码里不得再出现 cwd 相对缺省字面量」由 AST 级守卫覆盖（用 grep 会误报
    # rag.py 里说明历史的那条注释）：tests/test_rag_persist_dir_app_root.py 的
    # test_no_third_copy_of_cwd_relative_rag_default。
    rag_src = open(os.path.join(_DEMO_DIR, "app", "services", "rag.py"),
                   encoding="utf-8").read()
    assert "PERSIST_DIR = rag_paths.resolve_rag_dir()" in rag_src, \
        "rag.py 的 PERSIST_DIR 必须由唯一实现 rag_paths.resolve_rag_dir() 赋值"
    mc_src = open(mc.__file__, encoding="utf-8").read()
    assert "rag_paths.resolve_rag_dir(rag_dir)" in mc_src, \
        "巡检工具必须委托唯一实现，而不是自己复刻口径"

    # (5) DB：仍是复刻口径，与 db.py 对拍
    assert mc._resolve_db_path() == os.path.abspath(db._default_db_path)
    # db.py 把这两个等价写法视作「没显式声明」，巡检必须同一口径
    for equiv in ("./receipt_demo.db", "receipt_demo.db"):
        assert mc._resolve_db_path(equiv) == os.path.abspath(db._default_db_path)


def test_dir_fingerprint_detects_store_change(tmp_path):
    """自校准：指纹必须能抓到目录内容的变化。

    why：若指纹退化成常量（或只看容易抖动的 mtime），上面那条「巡检前后指纹相同」
    就是恒真装饰 —— 本仓出现过「回归用例写成恒真」的事故。这里在临时目录上做两次
    真实变化，要求指纹都必须变化；撤销后必须回到原值。
    """
    store = tmp_path / "fake_chroma"
    (store / "seg-1").mkdir(parents=True)
    (store / _CHROMA_DB_FILE).write_bytes(b"sqlite-bytes")
    (store / "seg-1" / "data.bin").write_bytes(b"abc")
    base = _dir_fingerprint(store)
    assert base is not None

    # 变化一：多一个杂文件 —— 路径集合必须变
    (store / "junk.tmp").write_bytes(b"x")
    assert _dir_fingerprint(store)["files"] != base["files"], \
        "指纹未抓到新增文件 —— 它无法充当「无写入」的判据"
    (store / "junk.tmp").unlink()
    assert _dir_fingerprint(store) == base, "撤销变化后指纹必须回到原值"

    # 变化二：只改 chroma.sqlite3 的内容 —— 路径集合与总字节都不变，只有内容哈希能抓到
    (store / _CHROMA_DB_FILE).write_bytes(b"sqlite-bytez")
    assert _dir_fingerprint(store)["chroma_sqlite3_md5"] != base["chroma_sqlite3_md5"], \
        "指纹未抓到库文件内容变化 —— 巡检的 migration 写库会逃过判据"

    # 目录不存在时返回 None（调用方据此断言「仍不存在」）
    assert _dir_fingerprint(tmp_path / "nope") is None


def test_legacy_default_would_break_same_source_assertion(monkeypatch, tmp_path):
    """自校准：巡检的缺省解析若回退到**旧口径**，「两处同源」等式必须不再成立。

    why：旧口径是 cwd 相对的 `.rag_chroma` 字面量（P6·X2 的 D1 形态：同一口径两份实现）。
    这里用 monkeypatch 在测试内模拟那次回退（**不真改产品文件** ——
    `memory_consistency.py` 属于别的波的范围），并 chdir 到临时目录，保证旧口径解析出的
    路径必然不同于「应用根基准」的缺省：若两者恰好相等，上面那条同源等式对巡检侧的
    行为就没有判别力。
    """
    monkeypatch.delenv("RAG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    from app.services import rag_paths

    def _legacy_resolve_rag_dir(rag_dir=None):
        """P8a-R7 之前的旧口径：显式参数 > RAG_DIR > cwd 相对的 ./.rag_chroma。"""
        env = str(os.environ.get("RAG_DIR") or "").strip()
        if env:
            return os.path.abspath(env)
        return os.path.abspath("./.rag_chroma")

    monkeypatch.setattr(mc, "_resolve_rag_dir", _legacy_resolve_rag_dir)

    legacy_value = mc._resolve_rag_dir()
    assert legacy_value == os.path.join(str(tmp_path), ".rag_chroma"), \
        "旧口径必须解析到 cwd 下的 .rag_chroma（否则本自校准的前提不成立）"
    assert legacy_value != rag_paths.default_rag_dir(), \
        "回退到旧口径后「两处同源」等式必须不再成立 —— 否则该等式没有判别力"


# -------------------------------------------------------------
# 8. CLI：python -m app.services.memory_consistency --json
# -------------------------------------------------------------
def test_cli_json_output_and_strict_exit_code(tmp_path):
    """CLI 入口必须可跑：--json 输出可解析报告，--strict 在不一致时退出码 2。

    why：这是「巡检入口」的接线证据 —— 只写函数不接入口，就是本项目高发的
    「定义了没接线」。
    """
    db_path = _seed_db(tmp_path, [("mid-cli", "聯記", "default")])
    rag_dir = tmp_path / "store_cli"
    _write_vectors(rag_dir, [("孤兒", "mid-cli-orphan")])

    env = dict(os.environ)
    env["DB_PATH"] = db_path
    env["RAG_DIR"] = str(rag_dir)

    proc = subprocess.run(
        [sys.executable, "-m", "app.services.memory_consistency", "--json"],
        cwd=_DEMO_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, "缺省（非 strict）巡检不应以非零退出: %s" % proc.stderr
    report = json.loads(proc.stdout)
    assert report["rag_dir"] == os.path.abspath(str(rag_dir)), \
        "CLI 必须按 RAG_DIR 解析向量目录"
    assert report["orphan_mids"] == ["mid-cli-orphan"]
    assert report["missing_vector_mids"] == ["mid-cli"]
    assert report["consistent"] is False

    strict = subprocess.run(
        [sys.executable, "-m", "app.services.memory_consistency", "--strict"],
        cwd=_DEMO_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert strict.returncode == 2, \
        "不一致 + --strict 必须以退出码 2 结束（供定时对账告警用）: %s" % strict.stderr


# -------------------------------------------------------------
# 6. 归档行不需要向量（否则巡检永远报不一致）
# -------------------------------------------------------------
def test_archived_rows_are_not_counted_as_missing(tmp_path):
    """归档/superseded 行不参与「是否需要向量」判定。

    检索侧只注入 status='active'（db.list_vendor_memory 默认过滤），归档行本就
    不该有向量。若把它们算进 missing，live 上 9 条 T3 之前的遗留行会让巡检永远
    报不一致，把真问题淹没（B0 审核 mustFix 1 的根因）。
    """
    db_path = _seed_db(tmp_path, [("mid-live", "德利行", "default"),
                                  ("mid-archived", "旧商户", "default")])
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE vendor_memory SET status='archived' "
                     "WHERE memory_id='mid-archived'")
        conn.commit()
    finally:
        conn.close()

    rag_dir = tmp_path / "store_only_active"
    _write_vectors(rag_dir, [("已注入", "mid-live")])

    r = mc.check_memory_consistency(db_path=db_path, rag_dir=str(rag_dir))
    assert r["missing_vector_mids"] == [], "归档行缺向量不得计入 missing"
    assert r["db_rows"] == 2, r["db_rows"]
    assert r["db_rows_active"] == 1 and r["db_rows_inactive"] == 1, r
    assert r["consistent"] is True
