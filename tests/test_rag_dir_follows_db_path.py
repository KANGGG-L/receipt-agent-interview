# -*- coding: utf-8 -*-
"""P0-T1（G1）：向量库落盘目录必须**随外置 DB_PATH 同源派生**，且不得写生产目录。

要修的缺陷（2026-10-20 实测复现，机制见 `app/services/rag_paths.py` 模块 docstring）：
`resolve_rag_dir()` 原先只有「显式参数 > `RAG_DIR` > 缺省 `<demo>/.rag_chroma`」三档，
没有「随 `DB_PATH` 派生」这一档。于是**只外置 DB、不设 RAG_DIR** 的运行（TestClient
用例 / E2E / 迁移脚本）DB 行落临时库、向量落生产目录 —— live `demo/.rag_chroma` 里
2026-10-15 那批 `tenant_uat_a_*`（30 条 / 15 mid，vendor 全是夹具名 `Vendor_A`）与
`tenant_uat_b_*`（10 条 / 5 mid，`Vendor_B`）共 40 条夹具向量即由此而来；巡检因此
`consistent=false` / 孤儿 20。

本文件钉住新优先级与三条既有不变量：
    显式参数 > `CHROMA_PATH` > `RAG_DIR` > 随外置 `DB_PATH` 派生 > 缺省 `<demo>/.rag_chroma`
① 只设 `DB_PATH`（外置临时库）→ 解析为该 DB 的 sibling 目录，且 `demo/.rag_chroma`
   **指纹不变**（含端到端：真的开一次 Chroma 取 store，向量落在 sibling 而非生产目录；
   顺带钉住 T9/D7 之后租户桶的**集合名形态** `tenant_<归一tid>_vendor_memory_<sha1(tid)[:12]>_<identity>`）；
② 未设任何环境变量 → `demo/.rag_chroma`（生产行为零变化）；
③ `CHROMA_PATH` 与 `RAG_DIR` 同时存在 → `CHROMA_PATH` 优先；
④ 空串 / 全空白的 `DB_PATH` / `RAG_DIR` / `CHROMA_PATH` 与未设置等价（每个形态都必须
   **真的被 setenv 过**，见 `test_db_path_relative_name_forms_are_not_externalized`）；
⑤ 从两个不同 cwd import 后解析结果一致（R7 既有不变量，派生档同样 cwd 无关）；
⑥ `memory_consistency` 继续委托同一个 `resolve_rag_dir()`，派生档不得有第二份实现；
⑦ T9（G5 / D7）租户集合名单射化：`a.b` 与 `a_b`、65 个字符之后才分叉的长 tid 都不得
   同名；同名必同 tid（可判范围内）；default 桶基础名不变；
⑧ T9 的 tmp 沙箱端到端探针：按新命名写入 active 行后 `retrieve_context` 与向量检索都
   取得回，且两个归一后同形的租户真的落在两个桶里。

反证（见 plan §7.4）：本文件对「删掉派生分支」的突变体必须翻红（① 与 ⑥ 都会失败）；
对「去掉 sha1 摘要段」的突变体也必须翻红（⑦ 的同名对与 ⑧ 的「桶里只有自己的 mid」翻红）；
用例 ④/⑤ 的自检与「指纹探针有效性」自检另附，防止恒真。

红线
----
- 探针子进程恒把 `DB_PATH` 指向临时库：`import app.services.rag` 会连带 `app.db`，其
  import 期迁移块按 `DB_PATH` 执行 ALTER，绝不允许落回 live。
- 端到端探针在**解析结果等于生产目录时立即拒绝打开 Chroma**（打印 REFUSED），因此本文件
  即使在被反证（派生分支被删）的状态下运行也不会污染 live；此时断言因 REFUSED 而失败。
- 巡检 / 指纹只读：`os.walk` + `hashlib`，不打开 Chroma 客户端。
- T9 端到端探针（⑧）**显式**设 `DB_PATH` 与 `RAG_DIR` 到本轮 tmp（不用 T1 的派生档），
  并在跑前后对 live 向量目录与 live 库（含 `-wal` / `-shm` 侧车）逐项对拍。
"""

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
_LIVE_RAG_DIR = os.path.join(_DEMO_DIR, ".rag_chroma")
_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_KEYS = ("DB_PATH", "RAG_DIR", "CHROMA_PATH")

# 端到端探针（写成脚本文件执行，而不是 `python -c` 单行 —— 单行里 `if ...: print(...)`
# 用分号拼接会得到非法语法）：在子进程里按「只设 DB_PATH」的形态取一次 store，观察向量
# 真正落在哪。安全阀：解析结果等于生产目录时**不打开** Chroma（否则反证态会把污染真做出
# 来），改印 REFUSED 并由用例判负。
_PROBE_E2E_SRC = '''\
import hashlib, json, os, sys
sys.path.insert(0, sys.argv[1])
from app.services import embeddings, rag, rag_paths

live = os.path.realpath(sys.argv[2])
target = rag.PERSIST_DIR
if os.path.realpath(target) == live:
    print(json.dumps({"status": "REFUSED", "target": target}))
    sys.exit(0)
TID = "uat_a"
st = rag._store(TID)
print(json.dumps({
    "status": "OPENED",
    "target": target,
    "resolved": rag_paths.resolve_rag_dir(),
    "collection": st._collection.name,
    "named_by_function": rag._tenant_collection_name(TID),
    "tid": TID,
    "digest": hashlib.sha1(TID.encode("utf-8")).hexdigest()[:12],
    "identity_suffix": embeddings.requested_identity().collection_suffix(),
    "exists_after": os.path.isdir(target),
    "sqlite_after": os.path.exists(os.path.join(target, "chroma.sqlite3")),
}))
'''

# 轻量探针：只 import rag_paths + memory_consistency（都不装配 ORM），换 cwd 验证同源性。
_PROBE_LIGHT = (
    "import json, sys, os;"
    "sys.path.insert(0, sys.argv[1]);"
    "from app.services import rag_paths;"
    "from app.services import memory_consistency as mc;"
    "print(json.dumps({'rp': rag_paths.resolve_rag_dir(),"
    " 'mc': mc._resolve_rag_dir(), 'cwd': os.getcwd()}))"
)


# -------------------------------------------------------------
# 工具
# -------------------------------------------------------------
def _clear_keys(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


def _fingerprint(root):
    """目录指纹：相对路径集合 + 总字节数 + chroma.sqlite3 的 md5（全部只读）。"""
    rel = []
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            rel.append(os.path.relpath(p, root).replace(os.sep, "/"))
            total += os.path.getsize(p)
    db_file = os.path.join(root, "chroma.sqlite3")
    sqlite_md5 = None
    if os.path.exists(db_file):
        h = hashlib.md5()
        with open(db_file, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        sqlite_md5 = h.hexdigest()
    return {"files": sorted(rel), "total_bytes": total, "sqlite_md5": sqlite_md5}


def _run_probe(code, env, cwd=_DEMO_DIR):
    proc = subprocess.run([sys.executable, "-c", code, _DEMO_DIR],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, "探针子进程失败: %s" % proc.stderr[-2500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _md5_of(path):
    """文件内容 md5（只读）；不存在返回 None。live 库是 WAL 形态，侧车另计。"""
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_script_probe(src, env, tmp_path, cwd=_DEMO_DIR, live=_LIVE_RAG_DIR):
    """把多行探针写成 tmp 脚本再执行（`python -c` 单行容不下带缩进的复合语句）。"""
    script = tmp_path / "rag_probe.py"
    script.write_text(src, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script), _DEMO_DIR, live],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, "端到端探针子进程失败: %s" % proc.stderr[-2500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _probe_env(tmp_db):
    """端到端探针环境：DB_PATH 恒指向临时库（红线），RAG_DIR / CHROMA_PATH 一律移除。"""
    env = dict(os.environ)
    for k in _KEYS:
        env.pop(k, None)
    env["DB_PATH"] = tmp_db
    env["AUTH_ENABLED"] = "0"
    return env


# -------------------------------------------------------------
# 自检：指纹探针与「拒绝打开」安全阀都必须有效（否则 ① 是恒真判据）
# -------------------------------------------------------------
def test_fingerprint_probe_detects_changes(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    (d / "chroma.sqlite3").write_text("v1", encoding="utf-8")
    before = _fingerprint(str(d))
    assert before["sqlite_md5"] and before["files"] == ["chroma.sqlite3"]

    (d / "chroma.sqlite3").write_text("v2-longer", encoding="utf-8")
    assert _fingerprint(str(d))["sqlite_md5"] != before["sqlite_md5"], \
        "指纹探针测不出内容变化 → 用例①的『指纹不变』将是恒真判据"

    (d / "extra.bin").write_bytes(b"x" * 10)
    after = _fingerprint(str(d))
    assert after["files"] != before["files"] or after["total_bytes"] != before["total_bytes"], \
        "指纹探针测不出文件增减 → 用例①的『指纹不变』将是恒真判据"
    # 目录不存在时的形态也要可用（用例①会先取 live 指纹，live 必然存在）
    assert _fingerprint(str(tmp_path / "nonexistent"))["files"] == []


def test_live_baseline_fingerprint_is_available():
    """前提复核：live 向量目录真实存在且非空，否则「指纹不变」毫无意义。"""
    fp = _fingerprint(_LIVE_RAG_DIR)
    assert fp["files"], "live 向量目录为空/不存在，指纹判据失效"
    assert fp["sqlite_md5"], "live chroma.sqlite3 缺失，指纹判据失效"


# -------------------------------------------------------------
# ① 只设 DB_PATH（外置临时库）→ 该 DB 的 sibling 目录
# -------------------------------------------------------------
def test_only_db_path_set_derives_sibling_dir(tmp_path, monkeypatch):
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    db = str(tmp_path / "e2e_like.db")
    monkeypatch.setenv("DB_PATH", db)

    got = rag_paths.resolve_rag_dir()
    assert got == os.path.abspath(str(tmp_path / "e2e_like.rag_chroma")), \
        "只设 DB_PATH 时必须派生 <DB 同目录>/<DB 名去扩展名>.rag_chroma，实得 %r" % got
    assert os.path.realpath(got) != os.path.realpath(_LIVE_RAG_DIR), \
        "派生结果仍是生产目录 → G1 污染通道未堵"


@pytest.mark.parametrize("db_name", ["uat031.db", "a.b.db", "no_ext"])
def test_derivation_strips_only_the_last_extension(tmp_path, monkeypatch, db_name):
    """去扩展名只去最后一段（`a.b.db` → `a.b.rag_chroma`）；无扩展名也要能派生。"""
    _clear_keys(monkeypatch)
    db = str(tmp_path / db_name)
    monkeypatch.setenv("DB_PATH", db)
    from app.services import rag_paths
    stem = os.path.splitext(db_name)[0]
    assert rag_paths.resolve_rag_dir() == \
        os.path.abspath(str(tmp_path / (stem + ".rag_chroma")))


def test_db_path_relative_name_forms_are_not_externalized(tmp_path, monkeypatch):
    """`receipt_demo.db` / `./receipt_demo.db` / 空白 / 空串 四种写法都视同未声明
    （口径同 db.py:61-66）。

    `demo/.env` 里生效的正是 `DB_PATH=./receipt_demo.db`，dotenv 会把它注入进程环境；
    若把这种写法当「外置」，生产进程会派生出 `receipt_demo.rag_chroma` 这个新库 ——
    等于把「零变化」这条不变量换成一次静默搬家。

    why 每个形态都**无条件 setenv**（上一轮审核的 mustFix）：旧写法是
    `if form: monkeypatch.setenv(...)`，于是 `""` 那一轮根本没改环境变量，实际是把上一轮
    的 `"   "` 又测了一遍 —— 用例自称覆盖「空串」而真没覆盖过（空串与「未设置」在
    `os.environ` 里是两种状态，必须显式 `setenv(key, "")` 才测得到前者）。这里同时用
    `os.environ["DB_PATH"] == form` 把「环境确实被设成该形态」钉住，防止这个循环体再被
    写成条件赋值而悄悄退化成重复迭代。
    """
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    default = rag_paths.default_rag_dir()
    for form in ("receipt_demo.db", "./receipt_demo.db", "   ", ""):
        monkeypatch.setenv("DB_PATH", form)
        assert os.environ["DB_PATH"] == form, \
            "DB_PATH 没真的被设成 %r（循环体退化成条件赋值时空串那轮会重复测上一形态）" % form
        assert rag_paths.resolve_rag_dir() == default, \
            "DB_PATH=%r 不得触发派生（应回落缺省）" % form
        assert rag_paths.externalized_db_path() == "", \
            "DB_PATH=%r 不得算外置" % form

    # 真正「未设置」的形态也要单独测一次（与空串不等价于它，二者都得回落缺省）
    monkeypatch.delenv("DB_PATH", raising=False)
    assert rag_paths.resolve_rag_dir() == default


def test_db_path_pointing_at_live_db_keeps_production_rag_dir(monkeypatch):
    """显式把 DB_PATH 写成 live 库的绝对路径 **不算外置**。

    §9 的巡检复现命令 `DB_PATH=$PWD/receipt_demo.db` 依赖这一条：DB 就是 live 库时向量
    必须仍指 `demo/.rag_chroma`，否则巡检会去读一个不存在的 `receipt_demo.rag_chroma`、
    报出「store 不存在 / 全部缺向量」的假结论。
    """
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DB_PATH", _LIVE_DB)
    assert rag_paths.resolve_rag_dir() == rag_paths.default_rag_dir() == _LIVE_RAG_DIR

    monkeypatch.setenv("DB_PATH", os.path.join(_DEMO_DIR, ".", "receipt_demo.db"))
    assert rag_paths.resolve_rag_dir() == _LIVE_RAG_DIR, "同一路径的非规范写法也要认出来"


# -------------------------------------------------------------
# ① 端到端：真开一次 store，向量落在 sibling，生产目录指纹逐项不变
# -------------------------------------------------------------
def test_store_creation_lands_beside_db_and_live_fingerprint_unchanged(tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db = str(db_dir / "e2e_like.db")
    before = _fingerprint(_LIVE_RAG_DIR)

    out = _run_script_probe(_PROBE_E2E_SRC, _probe_env(db), tmp_path)

    assert out["status"] == "OPENED", (
        "探针把向量目录解析成生产目录后主动拒绝打开 Chroma —— 即「只外置 DB 不设 RAG_DIR」"
        "仍会污染 live（G1 未修）。解析值=%r" % out["target"])
    expected = os.path.abspath(str(db_dir / "e2e_like.rag_chroma"))
    assert out["target"] == expected, "PERSIST_DIR 必须是外置 DB 的 sibling，实得 %r" % out["target"]
    assert out["resolved"] == expected
    assert out["exists_after"] and out["sqlite_after"], \
        "取 store 后向量库应落在 sibling 目录里（chroma.sqlite3 已生成）"
    # T9（D7）后租户桶名带 sha1(tid)[:12] 消歧摘要，且位置在 identity 后缀**之前**
    # （`memory_consistency._collection_tenant` 靠非贪婪正则停在第一个 `_vendor_memory`
    # 反推租户，摘要放进 tid 段会让租户视角巡检再也匹配不到该租户的集合，把整批 active
    # 行误报成缺向量）。期望值由**本用例自己按 hashlib.sha1 算出**，不调
    # `_tenant_collection_name` —— 拿命名函数的产物去比命名函数就是恒真判据；
    # 回滚命名实现（去掉摘要段）时本断言必须翻红。
    _PROBE_TID = "uat_a"
    expected_digest = hashlib.sha1(_PROBE_TID.encode("utf-8")).hexdigest()[:12]
    assert out["digest"] == expected_digest, \
        "探针自算的 sha1(tid)[:12] 与用例独立算出的不等：%r vs %r" % (
            out["digest"], expected_digest)
    expected_coll = "tenant_%s_vendor_memory_%s_%s" % (
        _PROBE_TID, expected_digest, out["identity_suffix"])
    assert out["collection"] == expected_coll, (
        "租户集合名必须是 tenant_<归一tid>_vendor_memory_<sha1(tid)[:12]>_<identity 后缀>："
        "实得 %r 期望 %r（名字取自真 Chroma 打开的集合，故本断言同时确认走的是真实写入通道）"
        % (out["collection"], expected_coll))
    assert out["collection"] == out["named_by_function"], \
        "`_store` 真打开的集合名必须与命名函数一致：%r vs %r" % (
            out["collection"], out["named_by_function"])

    after = _fingerprint(_LIVE_RAG_DIR)
    assert after == before, \
        "生产向量目录被改动（G1 复发）：before=%r after=%r" % (before, after)


# -------------------------------------------------------------
# ② 未设任何环境变量 → demo/.rag_chroma
# -------------------------------------------------------------
def test_no_env_resolves_to_production_default(monkeypatch):
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    assert rag_paths.resolve_rag_dir() == os.path.join(_DEMO_DIR, ".rag_chroma")
    assert rag_paths.resolve_rag_dir() == rag_paths.default_rag_dir()
    assert rag_paths.externalized_db_path() == "", "未设 DB_PATH 不得算外置"


# -------------------------------------------------------------
# ③ CHROMA_PATH 优先于 RAG_DIR（且两者都优先于派生）
# -------------------------------------------------------------
def test_chroma_path_beats_rag_dir(tmp_path, monkeypatch):
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    chroma = str(tmp_path / "via_chroma_path")
    rag = str(tmp_path / "via_rag_dir")
    monkeypatch.setenv("CHROMA_PATH", chroma)
    monkeypatch.setenv("RAG_DIR", rag)
    assert rag_paths.resolve_rag_dir() == os.path.abspath(chroma), \
        "CHROMA_PATH 与 RAG_DIR 同时存在时 CHROMA_PATH 必须优先"


def test_env_overrides_beat_db_derivation(tmp_path, monkeypatch):
    """不变量 (b)：RAG_DIR 显式覆盖仍优先于派生分支（测试隔离依赖它）。"""
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ext.db"))
    rag = str(tmp_path / "isolated")
    monkeypatch.setenv("RAG_DIR", rag)
    assert rag_paths.resolve_rag_dir() == os.path.abspath(rag)

    chroma = str(tmp_path / "chroma")
    monkeypatch.setenv("CHROMA_PATH", chroma)
    assert rag_paths.resolve_rag_dir() == os.path.abspath(chroma)


def test_explicit_argument_beats_all_env(tmp_path, monkeypatch):
    from app.services import rag_paths
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ext.db"))
    monkeypatch.setenv("RAG_DIR", str(tmp_path / "rag"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    assert rag_paths.resolve_rag_dir(str(tmp_path / "explicit")) == \
        os.path.abspath(str(tmp_path / "explicit"))


# -------------------------------------------------------------
# ④ 空串 / 全空白与未设置等价
# -------------------------------------------------------------
@pytest.mark.parametrize("key", _KEYS)
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_env_value_equals_unset(tmp_path, monkeypatch, key, blank):
    """每个键的空值都必须与「未设置」逐项等价（不得把空串当路径、也不得当 cwd）。"""
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    reference = rag_paths.resolve_rag_dir()
    monkeypatch.setenv(key, blank)
    assert rag_paths.resolve_rag_dir() == reference, \
        "%s=%r 与未设置不等价：%r vs %r" % (key, blank, rag_paths.resolve_rag_dir(), reference)


def test_blank_db_path_does_not_derive_cwd_relative(tmp_path, monkeypatch):
    """空 DB_PATH 不得派生出相对（cwd）路径 —— 那是 H3 的老形态。"""
    from app.services import rag_paths
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DB_PATH", "")
    got = rag_paths.resolve_rag_dir()
    assert got == rag_paths.default_rag_dir()
    assert os.path.isabs(got) and got != os.path.abspath(".rag_chroma")


# -------------------------------------------------------------
# ⑤ 两个不同 cwd import → 解析一致（R7 不变量在派生档同样成立）
# -------------------------------------------------------------
@pytest.mark.parametrize("shape", ["no_env", "db_externalized", "chroma_path", "rag_dir"])
def test_resolution_is_cwd_independent(tmp_path, shape):
    db = str(tmp_path / "cwd_probe.db")
    env_overrides = {}
    if shape == "no_env":
        pass
    elif shape == "db_externalized":
        env_overrides["DB_PATH"] = db
    elif shape == "chroma_path":
        env_overrides["CHROMA_PATH"] = str(tmp_path / "chroma")
    elif shape == "rag_dir":
        env_overrides["RAG_DIR"] = str(tmp_path / "rag")

    results = []
    for cwd in (_REPO_ROOT, _DEMO_DIR):
        env = dict(os.environ)
        for k in _KEYS:
            env.pop(k, None)
        env.update(env_overrides)
        out = _run_probe(_PROBE_LIGHT, env, cwd=cwd)
        assert os.path.realpath(out["cwd"]) == os.path.realpath(cwd), \
            "子进程没真的换 cwd（两个『不同 cwd』是假的）"
        results.append(out)

    rp = [r["rp"] for r in results]
    mc = [r["mc"] for r in results]
    assert len(set(rp)) == 1, "rag_paths 解析随 cwd 变化：%r" % rp
    assert rp == mc, "巡检与 rag_paths 结论不一致（第二份实现？）：%r vs %r" % (rp, mc)
    assert os.path.isabs(rp[0])
    if shape == "no_env":
        assert rp[0] == _LIVE_RAG_DIR
    elif shape == "db_externalized":
        assert rp[0] == os.path.abspath(str(tmp_path / "cwd_probe.rag_chroma"))


# -------------------------------------------------------------
# ⑥ 同一份实现：派生档只在 rag_paths 里有一份
# -------------------------------------------------------------
def test_memory_consistency_delegates_derivation_too(tmp_path, monkeypatch):
    """不变量 (c)：巡检必须跟着派生，而不是自己再算一份（P6·X2 D1 同源要求）。"""
    from app.services import rag_paths
    from app.services import memory_consistency as mc
    _clear_keys(monkeypatch)
    db = str(tmp_path / "audit.db")
    monkeypatch.setenv("DB_PATH", db)
    expected = os.path.abspath(str(tmp_path / "audit.rag_chroma"))

    assert mc._resolve_rag_dir() == expected
    # 唯一实现被替换后巡检必须随之改变：证明是委托、不是「两份恰好相等」
    monkeypatch.setattr(rag_paths, "resolve_rag_dir", lambda *a, **k: "/tmp/__delegated__")
    assert mc._resolve_rag_dir() == "/tmp/__delegated__", \
        "巡检复刻了第二份解析口径（改了唯一实现它不跟着改）"


def test_memory_consistency_has_no_second_resolver():
    """巡检源码不得再自己读向量目录的环境变量或自己拼缺省目录。

    判据选「是否自己读键 / 自己拼目录」而不是「源码里有没有 .rag_chroma 字样」：
    docstring 为说明历史必然提到该目录名，按字样扫会误报（本仓 P6·X2 的 D1 形态是
    **行为**复刻，不是文字复刻）。
    """
    src = open(os.path.join(_DEMO_DIR, "app", "services", "memory_consistency.py"),
               encoding="utf-8").read()
    assert "rag_paths.resolve_rag_dir(" in src, "巡检必须调用唯一实现"
    for forbidden in ('environ.get("RAG_DIR")', 'environ.get("CHROMA_PATH")',
                      'environ["RAG_DIR"]', 'environ["CHROMA_PATH"]',
                      'os.path.join(app_dir()', '".rag_chroma"'):
        assert forbidden not in src, \
            "巡检里出现了第二份向量目录口径: %r" % forbidden
    assert "_DEFAULT_RAG_DIR = rag_paths.default_rag_dir()" in src, \
        "模块级缺省常量必须取自唯一实现"


# -------------------------------------------------------------
# ⑦ T9（G5 / D7）：租户 collection 名必须单射
# -------------------------------------------------------------
# 规格：`tenant_<归一 tid>_vendor_memory_<sha1(tid)[:12]>_<identity 后缀>`。
# 归一 = 非 `[a-zA-Z0-9_-]` 替换为 `_`，可读段截断 64 —— 两者都**只影响可读性**，
# 消歧由摘要段负责。修复前（L2/G5）`a.b` 与 `a_b` 归一后同名、第 65 个字符之后的差异
# 被截断抹平，两个租户的物理隔离面并成一个桶。
_TID_DIGEST_LEN = 12
_TID_READABLE_MAXLEN = 64
_TID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _tid_digest(tid):
    """按规格独立算出的消歧摘要（不调被测函数 —— 那是恒真判据）。"""
    return hashlib.sha1(str(tid).encode("utf-8")).hexdigest()[:_TID_DIGEST_LEN]


def _sanitized_readable(tid):
    return _TID_UNSAFE.sub("_", str(tid))[:_TID_READABLE_MAXLEN]


def _expected_tenant_collection(tid, identity_suffix):
    return "tenant_%s_vendor_memory_%s_%s" % (
        _sanitized_readable(tid), _tid_digest(tid), identity_suffix)


def _legacy_tenant_collection_name(tid, identity_suffix):
    """修复前的旧命名口径：`tenant_<归一+截断 tid>_vendor_memory_<identity>`（G5 本体）。

    why 用例里要带一份旧口径：**先证明所选的 tid 对子在旧口径下确实同名**，再要求新口径
    把它们分开 —— 否则「两个名字不同」可能只是因为这两个 tid 本来就没碰撞（判据空转）。
    """
    return "tenant_%s_vendor_memory_%s" % (
        _sanitized_readable(tid), identity_suffix)


def test_tenant_collection_name_is_injective():
    """`a.b` 与 `a_b`、以及 64 字符之后才分叉的长 tid，都必须得到不同名。

    判别力：去掉摘要段（回到「归一 + 截断」）→ 每组的「新名字不同」断言立刻相等 → 红；
    把摘要挪进 tid 段（`tenant_<tid>_<摘要>_vendor_memory_<identity>`）→ 本用例仍绿，
    但 `tests/test_memory_vector_consistency.py` 的「消费方往返 + 租户视角巡检」用例翻红
    （`memory_consistency._collection_tenant` 的反推会把摘要当成 tid 的一部分）。
    """
    from app.services import embeddings, rag

    suffix = embeddings.requested_identity().collection_suffix()
    pairs = [
        ("a.b", "a_b"),                       # 归一后同形（点 → 下划线）
        ("a/b", "a_b"),                       # 归一后同形（斜杠 → 下划线）
        ("a b", "a.b"),                       # 归一后同形（空格与点都落到同一个字符）
        ("x" * 70, "x" * 69 + "y"),           # 前 64 字符完全相同
        ("x" * 80 + ".tail1", "x" * 80 + ".tail2"),   # 差异全在截断窗口之外
        ("租戶.甲", "租戶_甲"),                # 非 ASCII 归一
    ]
    for left, right in pairs:
        assert _legacy_tenant_collection_name(left, suffix) == \
            _legacy_tenant_collection_name(right, suffix), (
            "前提不成立：这对 tid 在**旧**口径下本就不同名（%r / %r），拿它证明单射化"
            "没有判别力" % (left, right))
        name_l = rag._tenant_collection_name(left)
        name_r = rag._tenant_collection_name(right)
        assert name_l != name_r, "T9 单射要求被破坏：%r 与 %r 得到同一个集合名 %r" % (
            left, right, name_l)
        assert name_l == _expected_tenant_collection(left, suffix), \
            "%r 的集合名不符合规格：实得 %r 期望 %r" % (
                left, name_l, _expected_tenant_collection(left, suffix))
        assert name_r == _expected_tenant_collection(right, suffix), \
            "%r 的集合名不符合规格：实得 %r 期望 %r" % (
                right, name_r, _expected_tenant_collection(right, suffix))


def test_same_collection_name_implies_same_tid():
    """同名必同 tid（可判范围内）：一批互不相同的 tid 必须得到两两不同的名字。

    why 只做「两两不同」而不做「名字反推 tid」：摘要不可逆，归一与截断又是有损的，
    所以可判范围就是「一组已知 tid 的像是否两两相异」—— 这也正是 G5 的缺陷面
    （两个不同租户被并进同一个物理桶）。`""` / 全空白 / `default` 折成同一个桶是
    **刻意的既有归一**（与 db 侧、`_store` 侧同口径），故从语料里先做同一折叠。
    """
    from app.services import rag

    corpus = ["t1", "t2", "a.b", "a_b", "a-b", "a/b", "A.b", "租戶.甲", "租戶_甲", "租戶甲",
              "x" * 64, "x" * 64 + "y", "x" * 65, "x" * 65 + "y",
              "tenant_t1_vendor_memory", "t1_vendor_memory_t2", "", "  ", "default"]
    tenant_tids = sorted({t for t in (str(x).strip() or "default" for x in corpus)
                          if t != "default"})
    by_name = {}
    for tid in tenant_tids:
        by_name.setdefault(rag._tenant_collection_name(tid), []).append(tid)
    collided = {name: tids for name, tids in by_name.items() if len(tids) > 1}
    assert not collided, "不同 tid 并进了同一个桶（G5 本体）：%r" % (collided,)

    # 每个租户桶名都必须带摘要段（防「靠截断消歧」回流），且可读段不超过上限
    shape = re.compile(
        r"^tenant_(?P<readable>[A-Za-z0-9_-]{1,%d})_vendor_memory_"
        r"(?P<digest>[0-9a-f]{%d})_(?P<identity>[a-z0-9_]+)$"
        % (_TID_READABLE_MAXLEN, _TID_DIGEST_LEN))
    for tid in tenant_tids:
        m = shape.match(rag._tenant_collection_name(tid))
        assert m, "租户桶名形制不符（tid=%r）：%r" % (tid, rag._tenant_collection_name(tid))
        assert m.group("digest") == _tid_digest(tid), \
            "摘要段必须是 sha1(tid)[:12]（tid=%r）：%r" % (tid, m.group("digest"))
        assert m.group("readable") == _sanitized_readable(tid), \
            "可读段必须是归一+截断后的 tid（tid=%r）：%r" % (tid, m.group("readable"))


def test_default_bucket_base_name_is_unchanged():
    """default / 空租户的桶基础名必须仍是 `vendor_memory_<identity>`（不掺摘要段）。

    why：live `demo/.rag_chroma` 现存 12 条向量全在这个桶里（T2 收口终态），D7 只要求
    改**租户**桶名；给默认桶加摘要会让这批向量无谓失联。
    """
    from app.services import embeddings, rag

    suffix = embeddings.requested_identity().collection_suffix()
    for tid in (None, "", "   ", "default"):
        assert rag._tenant_collection_name(tid) == "vendor_memory_%s" % suffix, \
            "默认桶名被改动（tid=%r）：%r" % (tid, rag._tenant_collection_name(tid))
    assert _tid_digest("default") not in rag._tenant_collection_name("default"), \
        "默认桶不得掺租户摘要段（会让现存 12 条 live 向量失联）"


# -------------------------------------------------------------
# ⑧ T9 端到端探针（tmp 沙箱）：新命名下 active 行可检索、两个租户真分开
# -------------------------------------------------------------
# 子进程脚本：`t9.a` 与 `t9_a` 归一后同为 `t9_a`（旧口径必然并桶），各自写一条 active
# 记忆后要求：① 两个集合名不同且等于规格；② 每个桶里**只有**自己那条 mid（物理隔离，
# 旧口径下这里会同时看到两条 → 翻红）；③ 相似度检索与 `retrieve_context` 都能取回自己
# 的内容；④ 他商在本桶检索为空（DB 侧 scope 门的既有行为，一并留证）。
_PROBE_T9_SRC = '''\
import hashlib, json, os, re, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[3])          # 仓库根：ingest_memory 会惰性 import ai_registry
from app import db
from app.services import embeddings, rag

LIVE = os.path.realpath(sys.argv[2])
target = rag.PERSIST_DIR
if os.path.realpath(target) == LIVE:
    print(json.dumps({"status": "REFUSED", "target": target}))
    sys.exit(0)

suffix = embeddings.requested_identity().collection_suffix()
db._make_engine()
CASES = (("t9.a", "T9單射 probe 甲商", "t9markjiu"),
         ("t9_a", "T9單射 probe 乙商", "t9markyi"))
out = {"status": "OK", "target": target, "suffix": suffix, "cases": []}
for tid, vendor, marker in CASES:
    rag.ingest_memory(vendor, "白糖 兩包", notes="版式：" + marker, tenant_id=tid)
    rows = [r for r in db.list_vendor_memory(vendor, tenant_id=tid)
            if str(r.get("status")) == "active"]
    if len(rows) != 1:
        print(json.dumps({"status": "DB_ROW_COUNT", "tid": tid, "rows": len(rows)}))
        sys.exit(0)
    mid = str(rows[0]["memory_id"])
    store = rag._store(tid)
    got = store.get(where={"memory_id": mid})
    owned = sorted({str((m or {}).get("memory_id"))
                    for m in (store.get().get("metadatas") or [])})
    sim = [d.page_content for d in store.similarity_search("白糖 兩包 版式", k=5)]
    ctx = rag.retrieve_context(vendor, tenant_id=tid)
    other = [v for t, v, _m in CASES if t != tid][0]
    ctx_foreign = rag.retrieve_context(other, tenant_id=tid)
    readable = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:64]
    digest = hashlib.sha1(tid.encode("utf-8")).hexdigest()[:12]
    out["cases"].append({
        "tid": tid, "vendor": vendor, "marker": marker, "mid": mid,
        "collection": store._collection.name,
        "expected": "tenant_%s_vendor_memory_%s_%s" % (readable, digest, suffix),
        "vectors_for_mid": len((got or {}).get("ids") or []),
        "mids_in_bucket": owned,
        "sim_hits": sim,
        "ctx": ctx,
        "ctx_foreign": ctx_foreign,
    })
out["failure_counts"] = rag.get_retrieve_failure_counts()
print(json.dumps(out, ensure_ascii=False))
'''


def test_t9_new_names_end_to_end_retrievable_in_tmp_store(tmp_path):
    """新命名下写入 active 行 → 检索取回；且归一后同形的两个租户真的分桶。

    红线：只跑在 tmp 沙箱（`DB_PATH` + `RAG_DIR` 都显式指临时目录），探针在解析结果
    等于生产目录时直接拒绝打开 Chroma；跑前后对 live 向量目录与 live 库（含 WAL 侧车）
    取指纹逐项比对。

    判别力（逐条对应回滚）：
    - 回滚 `_tenant_collection_name` 去掉摘要段 → 两个 tid 并成同一个桶，
      `names[0] != names[1]` 与 `mids_in_bucket == [mid]` 双双翻红；
    - 摘要挪进 tid 段 → 本用例仍绿，但 `tests/test_memory_vector_consistency.py`
      的消费方往返 / 租户视角巡检翻红；
    - 桶名格式与规格不符 → `collection == expected` 翻红（expected 由子进程独立算出）。
    """
    before_rag = _fingerprint(_LIVE_RAG_DIR)
    before_db = [_md5_of(_LIVE_DB + s) for s in ("", "-wal", "-shm")]

    env = dict(os.environ)
    for k in _KEYS:
        env.pop(k, None)
    env["DB_PATH"] = str(tmp_path / "t9_e2e.db")
    env["RAG_DIR"] = str(tmp_path / "t9_e2e_rag")
    env["RAG_EMBEDDING_PROVIDER"] = "hash"      # 离线档，绝不出网
    env["AUTH_ENABLED"] = "0"

    script = tmp_path / "t9_probe.py"
    script.write_text(_PROBE_T9_SRC, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script), _DEMO_DIR, _LIVE_RAG_DIR, _REPO_ROOT],
                          cwd=_DEMO_DIR, env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, "T9 端到端探针失败: %s" % proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["status"] == "OK", "探针未走完写入/检索（status=%r）：%s" % (
        out["status"], proc.stderr[-1500:])
    assert os.path.realpath(out["target"]) == os.path.abspath(str(tmp_path / "t9_e2e_rag")), \
        "探针的向量落点不是本轮 tmp 目录：%r" % out["target"]
    assert len(out["cases"]) == 2, out

    names = [c["collection"] for c in out["cases"]]
    assert names[0] != names[1], \
        "`t9.a` 与 `t9_a` 归一后同为 `t9_a`，必须有不同桶（并桶＝G5 本体）：%r" % names
    for c in out["cases"]:
        assert c["collection"] == c["expected"], \
            "集合名与规格不符（tid=%r）：实得 %r 期望 %r" % (
                c["tid"], c["collection"], c["expected"])
        assert c["mids_in_bucket"] == [c["mid"]], (
            "租户桶必须只装自己的 memory_id（tid=%r 实得 %r）—— 旧命名口径下这两个 tid"
            "会并成一个桶、这里会同时看到两条 mid" % (c["tid"], c["mids_in_bucket"]))
        assert c["vectors_for_mid"] >= 1, \
            "按 mid 在新桶里取不到向量（tid=%r）" % c["tid"]
        assert any(c["marker"] in text for text in c["sim_hits"]), \
            "新命名下相似度检索取不回自己写入的向量（tid=%r）：%r" % (c["tid"], c["sim_hits"])
        assert c["marker"] in c["ctx"], \
            "retrieve_context 在新命名下取不回该 active 行（tid=%r）：%r" % (
                c["tid"], c["ctx"])
        assert c["ctx_foreign"] == "", \
            "他租户供应商在本桶检索应当为空（scope 门失效？）：%r" % c["ctx_foreign"]
    assert out["failure_counts"] == {}, \
        "检索路径出现静默降级计数：%r" % (out["failure_counts"],)

    assert _fingerprint(_LIVE_RAG_DIR) == before_rag, "T9 探针改动了 live 向量目录"
    assert [_md5_of(_LIVE_DB + s) for s in ("", "-wal", "-shm")] == before_db, \
        "T9 探针改动了 live 库或其 WAL 侧车"
