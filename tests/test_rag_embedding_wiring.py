# -*- coding: utf-8 -*-
"""O1 回归：rag 真正接上 embeddings 三档 + collection 名带 identity（修 gap M7）。

背景（为什么需要这个文件）：`app/services/embeddings.py` 早已实现 hash / siliconflow /
dashscope 三档与 `EmbeddingIdentity`，但 `rag.py` 里**还留着一份重复的
`_CharNGramEmbeddings`**，且 `_embedding_provider()` 根本没调 `get_embeddings()` ——
于是 `RAG_EMBEDDING_PROVIDER` 仍是假开关（运维按文档改了键，实际还在跑 256 维哈希，
且不会留下任何日志），同时同一套哈希算法有两份实现（本仓高发①类缺陷）。此外集合名
不带 identity，切档会把 1024 维与 256 维写进同一个 collection。

本文件钉住的契约：

1. `rag.py` 不再有第二份嵌入实现（AST + 源码双向判据），`_embedding_provider()` 返回
   的就是 `embeddings.get_embeddings()` 的产物（类型与 identity 都是 embeddings 模块的）；
2. 缺省档（环境变量未设）行为与接线前**逐值一致**：独立复刻旧算法逐值比对，且全程
   零网络（socket 层自校准陷阱，避免「0 连接」其实是陷阱没生效）；
3. `_tenant_collection_name()` 拼上 `embedding.identity.collection_suffix()`（含全局桶），
   三档后缀互不相同，切档即换集合名；
4. **名字与实际生效档一致**：`_store()` 传的是探测/回落**之后**那个对象的 identity，
   所以探测失败回落到 hash 时，集合名也必须带 hash 后缀（否则 256 维会写进名为
   1024 维的集合）；
5. 命名约定的**消费方**（`memory_consistency._collection_tenant`）同步认得带后缀的
   集合名 —— 只改产生方不改消费方，租户视角巡检会把整批 active 行误报成缺向量；
6. Q1：**Chroma 已缓存时不得重解析档位、不得再发可用性探测**。旧实现把
   `_embedding_provider()` 写在缓存判断之前，远端档下每次取记忆都多一次外呼
   （实测连续三次 `_store` 时 `requests.post` 累计 1→2→3）。

隔离：DB 与 RAG 目录由 tests/conftest.py 的 autouse 夹具指向 tmp，本模块产生的向量
只落在临时目录。对**两个 live 向量目录**的期望不同，故分别守住（见第 5 组用例）：

- `<demo>/.rag_chroma`：记忆已生效 → **必须存在**；要守的不变量是「本用例不得对它
  产生任何写入」（跑前跑后取指纹逐项比对）；
- `<repo>/.rag_chroma`：P8a-R7 后缺省解析收敛到 `<demo>/.rag_chroma` → **仍不应存在**。

两条判据各配一条**自校准**用例，防止它们退化成恒真的装饰。why 自校准**一律在
`tmp_path` 上做**（在临时目录里搭一个与 live 存储同结构的副本 / 建一个临时目录），
而不是真的去写 live：写 live 会（a）字面违反「不得写入 live 向量库」的红线，（b）用例
被 kill 或在 cleanup 前抛错就会留下 live 垃圾，（c）周期性改写 live 目录 mtime，破坏
本仓用 mtime 做的 live 变更归因（P29 实测旧形态把 `demo/.rag_chroma` 的 mtime 从
21:41:52 改到 22:00:48）。判别力不靠「在哪个目录造变化」—— 校准跑的是与 live **完全
相同的那条断言函数**；live 路径本身另由 `test_live_store_path_constants_point_at_real_live_dirs`
的静态断言钉住，防「校准搬到 tmp 之后判据其实指向了一个随便的目录」这种新的假绿。

远端档一律用假的 HTTP 响应对象，不联网、不读真实凭据。

反证：把 `_tenant_collection_name` 的 identity 后缀去掉（或让 `_store` 用请求档而非
实际生效档拼名）→ 第 3/4/5 组用例必须失败；把 `_store` 里的档位解析移回缓存判断之前
（Q1 修复回滚）→ 第 6 组用例必须失败；把第 5 组的指纹比对改回
`assert not os.path.exists(<live dir>)`（P0-T3 的旧口径）→ 该用例在
`<demo>/.rag_chroma` 已存在时会失败（live 上无效、只在裸机上是恒真装饰）；把
`_assert_store_not_written` 的比对短路成恒真（或让 `_dir_fingerprint` 返回常量）→
两条自校准用例必须失败。
"""

import ast
import hashlib
import os
import socket
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.services.embeddings as emb  # noqa: E402
import app.services.rag as rag  # noqa: E402

_RAG_SOURCE_PATH = os.path.join(_DEMO_DIR, "app", "services", "rag.py")

# 两个 live 向量目录的期望**不同**，不得用同一条断言（详见第 5 组用例注释）：
# - <demo>/.rag_chroma：记忆已生效 → 必须存在；守的是「本用例不得写入它」；
# - <repo>/.rag_chroma：P8a-R7 后缺省解析收敛到 <demo>/.rag_chroma → 仍不应存在。
_LIVE_DEMO_STORE = os.path.join(_DEMO_DIR, ".rag_chroma")
_LIVE_REPO_STORE = os.path.join(_REPO_ROOT, ".rag_chroma")
_CHROMA_DB_FILE = "chroma.sqlite3"
# 自校准（i）在 tmp_path 的**同结构副本**里写的临时杂文件：前缀独特，便于残留扫描时
# 一眼认出（注意：它落在 tmp 副本里，不再落 live —— live 路径本模块全程只读）。
_SELFCHECK_JUNK_NAME = ".aw1_selfcheck_junk.tmp"
# 副本的子目录名模仿 chroma 的「每 collection 一个 uuid 目录」布局，使自校准能覆盖
# os.walk 递归那一层（文件相对路径集合的判据本体）。
_SELFCHECK_STORE_SUBDIR = "c08cbdbf-0000-0000-0000-0000000000ff"

# 会影响档位解析的全部环境变量：逐条清空，保证用例不依赖（也不污染）本机 .env。
# 注意 conftest 已把 RAG_EMBEDDING_PROVIDER 锁成 hash，这里再清空是为了让「环境变量
# 未设」这一档也被真实覆盖（缺省档是 hash，两者结果相同）。
_PROVIDER_ENV_KEYS = (
    "RAG_EMBEDDING_PROVIDER",
    "SILICONFLOW_BASE_URL", "SILICONFLOW_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env_and_store_cache(monkeypatch):
    """清空档位相关环境变量 + 复位进程内 collection 缓存（逐用例独立）。"""
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(rag, "_vs", None)
    monkeypatch.setattr(rag, "_vs_tenant", {})


def _rag_source():
    with open(_RAG_SOURCE_PATH, encoding="utf-8") as fh:
        return fh.read()


def _legacy_char_ngram(text, dim=256):
    """接线前 `rag._CharNGramEmbeddings._embed` 的独立复刻（不从被测模块取）。

    why 独立复刻：用被测实现证明被测实现是循环论证。复刻的内容就是「缺省档零行为
    变化」的判据本体 —— 同一输入必须得到逐值相同的向量。
    """
    import hashlib
    import unicodedata

    norm = unicodedata.normalize("NFKC", str(text or "")).casefold().replace(" ", "")
    vec = [0.0] * dim
    for n in (2, 3):
        if len(norm) < n:
            bucket = int(hashlib.sha256(norm.encode()).hexdigest(), 16) % dim
            vec[bucket] += 1.0
            continue
        for i in range(len(norm) - n + 1):
            gram = norm[i:i + n]
            bucket = int(hashlib.sha256(gram.encode()).hexdigest(), 16) % dim
            vec[bucket] += 1.0
    factor = sum(c * c for c in vec) ** 0.5
    if factor > 0:
        vec = [c / factor for c in vec]
    return vec


class _FakeResp:
    """最小 HTTP 响应替身（只实现被 embeddings 读取的三个属性）。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("响应体不是 JSON")
        return self._payload


class _CapturingChroma:
    """Chroma 替身：记录构造参数，返回自身（供 `_store()` 装配断言用，不落盘）。"""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _CapturingChroma.instances.append(self)


# ------------------------------------------------- 1. 只剩一份嵌入实现

def test_rag_no_longer_defines_its_own_embeddings():
    """rag.py 不得再有第二份嵌入实现（①类缺陷：同一根因多份实现只改一份）。

    判据取 AST（代码事实）而不只做字符串匹配：注释里提一句历史是正常的，但**代码里**
    不得再有该名字、不得再 import hashlib / 调用 sha256（哈希算法的实现痕迹）。两条
    一起给，字符串判据负责「grep 可见的干净」，AST 判据负责「注释改回去也躲不过」。
    """
    src = _rag_source()
    assert "_CharNGramEmbeddings" not in src, "rag.py 仍留有重复的嵌入实现"
    assert "hashlib" not in src, "rag.py 仍留着 char-ngram 哈希的实现痕迹（hashlib）"
    assert not hasattr(rag, "_CharNGramEmbeddings"), "模块上仍挂着重复的嵌入实现"

    tree = ast.parse(src)
    subclasses = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(getattr(base, "id", getattr(base, "attr", "")) == "Embeddings"
                for base in node.bases)
    ]
    assert subclasses == [], "rag.py 不应再定义 Embeddings 子类：%s" % subclasses

    defined = [
        getattr(node, "name", "") for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    ]
    assert "_CharNGramEmbeddings" not in defined
    assert not [n for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                and any(alias.name == "hashlib" for alias in n.names)], \
        "rag.py 不应再 import hashlib（哈希实现已收敛到 embeddings 模块）"


def test_embedding_provider_returns_embeddings_module_object():
    """`_embedding_provider()` 必须返回 embeddings 模块的对象（接线点，唯一实现）。"""
    obj = rag._embedding_provider()
    assert type(obj) is emb.HashEmbeddings, \
        "缺省档应返回 embeddings.HashEmbeddings，实际 %r" % (type(obj),)
    assert obj.identity.label == "hash:char-ngram-hash:256"
    assert len(obj.embed_query("德利行 本地牛腩 2斤")) == emb.HASH_DIM


# ------------------------------------------------- 2. 缺省档：零行为变化 + 零网络

def test_default_tier_vectors_match_legacy_values():
    """③ 缺省档（环境变量未设）与接线前逐值一致：同输入同输出。"""
    provider = rag._embedding_provider()
    assert provider.identity.provider == emb.PROVIDER_HASH

    texts = ["", "A", "德利行", "德利行 本地牛腩 2斤", "ANIKAR A雞粉 x2 @HK$88.00",
             "7-ELEVEN HK (1542)", "供应商 测试供应商 的已确认进货明细：\n"]
    for text in texts:
        assert provider.embed_query(text) == _legacy_char_ngram(text), \
            "缺省档向量与接线前不一致：%r" % (text,)

    docs = ["供应商甲 白糖", "供应商乙 白糖"]
    assert provider.embed_documents(docs) == [_legacy_char_ngram(t) for t in docs]


def test_default_tier_opens_no_socket_and_no_request(monkeypatch):
    """缺省档全程离线：socket 层陷阱（先自校准）+ requests.post 计数。"""
    attempts = []

    def _trap_connect(self, address):
        attempts.append(address)
        raise AssertionError("缺省档不得建立网络连接（address=%r）" % (address,))

    monkeypatch.setattr(socket.socket, "connect", _trap_connect)
    probe = socket.socket()
    try:
        with pytest.raises(AssertionError):
            probe.connect(("127.0.0.1", 9))
    finally:
        probe.close()
    assert attempts, "陷阱自校准失败：connect 未被拦截，0 连接断言不可信"
    attempts.clear()

    post_calls = []
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: post_calls.append(a))

    provider = rag._embedding_provider()          # 取嵌入对象
    provider.embed_query("德利行 白糖")            # 真算一次向量
    rag._tenant_collection_name("t1")            # 取集合名（不得为了命名做探测）

    assert attempts == [], "缺省档发起了网络连接：%r" % (attempts,)
    assert post_calls == [], "缺省档发起了 HTTP 请求"


# ------------------------------------------------- 3. collection 名带 identity（②）

def test_tenant_collection_name_carries_identity_suffix():
    """② 租户集合名 = tenant_<tid>_vendor_memory_<identity 后缀>。"""
    suffix = emb.HashEmbeddings().identity.collection_suffix()
    name = rag._tenant_collection_name("t1")
    assert name == "tenant_t1_vendor_memory_%s" % suffix
    assert name.endswith("_" + suffix)
    assert name != "tenant_t1_vendor_memory", "缺 identity 后缀会让切档混写同一集合"
    assert rag._tenant_collection_name("t1") != rag._tenant_collection_name("t2")

    # 安全清洗与截断（沿用原行为）
    assert rag._tenant_collection_name("a.b").startswith("tenant_a_b_vendor_memory_")
    long_tid = "x" * 80
    assert rag._tenant_collection_name(long_tid).startswith(
        "tenant_%s_vendor_memory_" % ("x" * 64))


def test_default_bucket_collection_name_also_carries_identity_suffix():
    """全局桶同样带后缀：默认租户切档时也不得混写（hash 256 与 bge-m3 1024）。"""
    suffix = emb.HashEmbeddings().identity.collection_suffix()
    for tid in (None, "", "default", "  "):
        name = rag._tenant_collection_name(tid)
        assert name == "vendor_memory_%s" % suffix, "全局桶名必须带 identity：%r" % (name,)


# ------------------------------------------------- 4. 切档 → 集合名跟着变（④）

def test_three_tier_suffixes_are_pairwise_distinct(monkeypatch):
    """④ 三档 identity 后缀互不相同，且集合名随档位变化（不联网、不需凭据）。"""
    hash_id = emb.HashEmbeddings().identity
    sf_id = emb.requested_identity("siliconflow")
    ds_id = emb.requested_identity("dashscope")
    suffixes = [hash_id.collection_suffix(), sf_id.collection_suffix(),
                ds_id.collection_suffix()]
    assert len(set(suffixes)) == 3, suffixes
    for suf in suffixes:
        assert suf and all(c.isalnum() or c in "_-" for c in suf)

    default_name = rag._tenant_collection_name("t1")           # env 未设 → hash
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "siliconflow")
    sf_name = rag._tenant_collection_name("t1")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    ds_name = rag._tenant_collection_name("t1")

    assert default_name.endswith("_" + hash_id.collection_suffix())
    assert sf_name.endswith("_" + sf_id.collection_suffix())
    assert ds_name.endswith("_" + ds_id.collection_suffix())
    assert len({default_name, sf_name, ds_name}) == 3
    assert "siliconflow" in sf_name and "1024" in sf_name


def test_store_names_and_builds_with_effective_tier_identity(monkeypatch, caplog):
    """④ 名字必须跟**实际生效档**走：探测失败回落到 hash 时不得叫 siliconflow。

    这是本波次最关键的错配判据：若 `_store()` 用「请求档」拼名、用「回落档」算向量，
    256 维向量就会写进名为 `..._siliconflow_..._1024` 的集合。
    """
    import logging

    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=500, text="upstream boom"))

    _CapturingChroma.instances = []
    monkeypatch.setattr(rag, "Chroma", _CapturingChroma)

    with caplog.at_level(logging.WARNING):
        rag._store("t9")

    assert _CapturingChroma.instances, "Chroma 未被构造，用例前提不成立"
    kwargs = _CapturingChroma.instances[0].kwargs
    effective = kwargs["embedding_function"].identity
    # 前提：探测确实失败了（否则本用例退化成「等于请求档」的假绿）
    assert effective.provider == emb.PROVIDER_HASH, "探测应失败回落到 hash"
    assert "实际回落档位=hash" in caplog.text
    assert kwargs["collection_name"] == rag._tenant_collection_name(
        "t9", effective), "集合名必须由实际生效档的 identity 拼出"
    assert kwargs["collection_name"].endswith("_" + effective.collection_suffix())
    assert "siliconflow" not in kwargs["collection_name"], \
        "回落档的向量不得写进以请求档命名的集合"


# ------------------------------------------------- 5. 真 Chroma：落地名与隔离

def _dir_fingerprint(path):
    """目录指纹：文件的相对路径集合 + 总字节数 + `chroma.sqlite3` 的 md5；不存在返回 None。

    why 用三元组而不是单个 mtime / 大小：mtime 会因别的进程的只读打开而抖动（本项目实测过
    「只有时间戳变」的假阳性）；而 sqlite 的页写入可能不改变主库文件大小 —— 只有内容哈希能
    同时抓住「新增文件」与「只改内容不改大小」两类变化。不存在时返回 None，让调用方能断言
    「仍不存在」。
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
        with open(db_file, "rb") as fh:
            sqlite_md5 = hashlib.md5(fh.read()).hexdigest()
    return {"files": sorted(files), "total_bytes": total,
            "chroma_sqlite3_md5": sqlite_md5}


def _assert_store_not_written(path, before, label):
    """断言本用例没有对 `path` 指向的向量库目录产生任何写入（before 为跑前指纹）。

    why 带 path 参数：live 侧（`_LIVE_DEMO_STORE`）与自校准侧（`tmp_path` 下的同结构
    副本）必须跑**同一条**断言。path 参数化正是「校准搬去 tmp、判据仍然有效」的实现
    手段 —— 校准若另写一份判据，就只证明了那份拷贝能干活。

    before 为 None 时同样判失败：那说明前置契约（记忆已生效 → 该目录必须存在）不成立，
    既拦住「目录被误清退」，也避免把断言降级成恒真的空操作。
    """
    assert before is not None, (
        "%s 必须存在。若这里为 None，先查是不是本机没有启用记忆 / 生产向量库被清退；"
        "**不要把它改回 P0-T3 的旧口径「断言不存在」**（为何旧判据过时见本组用例注释）"
        % label)
    after = _dir_fingerprint(path)
    assert after == before, (
        "本用例不得对 %s 产生任何写入：文件相对路径集合 / 总字节数 / chroma.sqlite3 的 "
        "md5 必须逐项相同\nbefore=%r\nafter =%r" % (label, before, after))


def _assert_demo_store_not_written(before):
    """live demo 向量库的「零写入」判据（`_LIVE_DEMO_STORE` 的薄包装）。

    why 只把路径留给包装：包装负责「对准哪个路径」，判别力由被测的
    `_assert_store_not_written` 提供，自校准因此能打在同一个函数上。
    """
    _assert_store_not_written(
        _LIVE_DEMO_STORE, before, "已存在的 live demo 向量库 <demo>/.rag_chroma")


def _assert_store_absent(path, label):
    """断言 `path` 这个向量库目录仍不存在（带 path 参数，理由同 `_assert_store_not_written`）。"""
    assert _dir_fingerprint(path) is None, (
        "%s 不应存在（P8a-R7 后缺省解析收敛到 <demo>/.rag_chroma）；它一旦出现，"
        "说明仍有代码按 cwd 相对口径把向量写进第二个 store：%s" % (label, path))


def _assert_repo_store_absent():
    """断言仓库根那个向量目录仍不存在（`_LIVE_REPO_STORE` 的薄包装）。"""
    _assert_store_absent(_LIVE_REPO_STORE, "<repo>/.rag_chroma")


def test_real_store_creates_identity_suffixed_collections():
    """真 Chroma 上核对：租户桶与全局桶的 collection 名都带 identity 后缀。

    同时守住「本用例不得对 live 向量库产生任何写入」这个**真不变量**（见下方注释）。
    """
    demo_before = _dir_fingerprint(_LIVE_DEMO_STORE)

    tenant_name = rag._store("wire_t1")._collection.name
    global_name = rag._store(None)._collection.name
    suffix = rag._embedding_provider().identity.collection_suffix()

    assert tenant_name == "tenant_wire_t1_vendor_memory_%s" % suffix
    assert global_name == "vendor_memory_%s" % suffix
    assert tenant_name != global_name

    # why 旧判据（`assert not os.path.exists(live_dir)`）已过时：它断言「live 向量库目录
    # 不存在」—— 那是 P0-T3（live 向量库被整体清退、记忆尚未生效）时的正确口径。**该前提
    # 已被设计变更合法推翻**：现在记忆真的生效了，生产向量库 <demo>/.rag_chroma **必须存在**
    # （里面有 active 行的向量）。「目录不存在」只是当时的手段，要守的**目的**是「本用例这个
    # 写向量的动作不得落到 live 上」，故 demo 侧改为对已存在的目录取指纹、跑完再逐项比对。
    #
    # 两个目录期望**不同**，不得用同一条断言：demo 侧必须存在且不得被写入；repo 侧仍必须
    # 不存在（P8a-R7 后缺省解析收敛到 <demo>/.rag_chroma）。两条判据各有自校准用例，见本文件
    # `test_selfcheck_*`。
    _assert_demo_store_not_written(demo_before)
    _assert_repo_store_absent()


def _make_store_like(root):
    """在 `root` 下搭一个与 live 向量库**同结构**的副本（chroma.sqlite3 + 一个 uuid 目录）。

    why 要求同结构：指纹的三元组里，「文件相对路径集合」依赖 os.walk 的递归、
    「chroma.sqlite3 的 md5」依赖读取子文件 —— 在一个空目录上学到的东西证明不了它在真实
    布局上有效。副本完全由本函数自造，**不含任何 live 数据**（只放占位字节）。
    """
    root.mkdir(parents=True, exist_ok=True)
    sub = root / _SELFCHECK_STORE_SUBDIR
    sub.mkdir(exist_ok=True)
    (sub / "data_level0.bin").write_bytes(b"placeholder-vector-index\n")
    (sub / "header.bin").write_bytes(b"placeholder-header\n")
    (root / _CHROMA_DB_FILE).write_bytes(b"SQLite format 3\x00placeholder\n")
    return root


def test_selfcheck_store_fingerprint_detects_write(tmp_path):
    """自校准（i）：指纹必须真能抓到「往向量库目录写文件 / 等长改写 sqlite」。

    why 在 tmp_path 的副本上做，而不是真的写 live：本用例原先真的往 `<demo>/.rag_chroma`
    写了一个临时杂文件再删（P29 实测把该目录 mtime 从 21:41:52 改到 22:00:48）。三个后果：
    ① 字面违反「不得写入 live 向量库」的红线；② 用例被 kill 或在 cleanup 前抛错会在 live
    留下垃圾；③ 本仓用目录 mtime 做 live 变更归因，它会周期性改写 live 目录 mtime、可能把
    别人的授权写入误判成「本窗口被写」。判别力不依赖「在哪个目录造变化」—— 这里调的是与
    live 完全相同的 `_assert_store_not_written`，只是 path 换成临时副本；live 路径的绑定由
    `test_live_store_path_constants_point_at_real_live_dirs` 的静态断言负责。

    两态各校准一次（分别覆盖指纹的两个判据面）：
    - 子目录里新增文件 → 文件相对路径集合必须变（覆盖 os.walk 递归那一层）；
    - 等长改写 chroma.sqlite3 → 只比总字节数会漏，md5 必须抓到。
    每次变化都要求那条生产断言**失败**，撤销后要求指纹逐字节回到原值。
    """
    store = str(_make_store_like(tmp_path / "rag_store"))
    before = _dir_fingerprint(store)
    assert before is not None, "自校准前提不成立：同结构副本必须建成"

    junk = os.path.join(store, _SELFCHECK_STORE_SUBDIR, _SELFCHECK_JUNK_NAME)
    assert not os.path.exists(junk), "自校准残留：上一轮没清理干净 %s" % junk
    try:
        with open(junk, "w", encoding="utf-8") as fh:
            fh.write("self-calibration\n")
        assert _dir_fingerprint(store) != before, \
            "指纹未抓到向量库子目录里的新增文件 —— 它无法充当「无写入」的判据"
        with pytest.raises(AssertionError) as caught:
            _assert_store_not_written(store, before, "临时副本")
        assert _SELFCHECK_JUNK_NAME in str(caught.value), \
            "自校准必须因「多了一个文件」而失败，实际报错：%s" % (caught.value,)
    finally:
        if os.path.exists(junk):
            os.remove(junk)
    assert _dir_fingerprint(store) == before, \
        "自校准撤销新增文件后指纹必须逐字节回到原值"

    db_file = os.path.join(store, _CHROMA_DB_FILE)
    with open(db_file, "rb") as fh:
        original = fh.read()
    flipped = original[:-1] + bytes([original[-1] ^ 0x01])
    assert len(flipped) == len(original), "等长改写的前提不成立"
    with open(db_file, "wb") as fh:
        fh.write(flipped)
    assert _dir_fingerprint(store) != before, \
        "指纹未抓到 chroma.sqlite3 的等长改写（只比总字节数会漏）"
    with pytest.raises(AssertionError):
        _assert_store_not_written(store, before, "临时副本")
    with open(db_file, "wb") as fh:
        fh.write(original)
    assert _dir_fingerprint(store) == before, \
        "自校准撤销等长改写后指纹必须逐字节回到原值"


def test_selfcheck_store_absence_assertion_detects_new_dir(tmp_path):
    """自校准（ii）：在 tmp_path 建一个目录 → 「该 store 不存在」判据必须失败。

    why 不在 `<repo>/.rag_chroma` 建目录：那会改写仓库根的目录 mtime；更实际的危害是
    用例一旦被 kill 就会在仓库根留下这个空目录，而该用例的前提断言要求它不存在 ——
    一次中断会让这条用例从此永久失败。校准打在 `_assert_store_absent` 本体上，路径换成
    tmp_path 下的临时目录；`_LIVE_REPO_STORE` 的绑定同样由静态断言用例负责。
    """
    missing = str(tmp_path / "rag_store_absent")
    assert _dir_fingerprint(missing) is None, "自校准前提不成立：临时目录不应已存在"
    try:
        os.makedirs(missing)
        with pytest.raises(AssertionError):
            _assert_store_absent(missing, "临时目录")
    finally:
        if os.path.isdir(missing):
            os.rmdir(missing)

    assert _dir_fingerprint(missing) is None, "自校准清理后临时目录必须恢复为不存在"


def test_live_store_path_constants_point_at_real_live_dirs(monkeypatch):
    """静态断言：两条 live 判据对准的确实是仓库里那两个真实 live 路径。

    why 这是自校准搬到 tmp_path 之后**必须**补的另一半：自校准证明「断言函数有判别力」，
    但若断言里的路径常量写错了（或有人把 `_assert_demo_store_not_written` 指向别处），
    校准仍会全绿、而 live 侧实际无人看守 —— 那是一种新的假绿。两步一起钉：
    ① 两个常量 realpath 等于由 `_REPO_ROOT` 推出的真实路径；
    ② 用探针记录 `_dir_fingerprint` 收到的路径，确认生产断言真的对着这两个常量取指纹
    （只断言常量相等，证明不了「实际使用点」用的是它）。
    """
    assert os.path.realpath(_LIVE_DEMO_STORE) == os.path.realpath(
        os.path.join(_REPO_ROOT, "demo", ".rag_chroma")), \
        "demo 侧判据没有对准真实的 live 向量库路径"
    assert os.path.realpath(_LIVE_REPO_STORE) == os.path.realpath(
        os.path.join(_REPO_ROOT, ".rag_chroma")), \
        "repo 侧判据没有对准真实的 live 向量库路径"

    seen = []
    real_fingerprint = _dir_fingerprint

    def _spy(path):
        seen.append(os.path.realpath(path))
        return real_fingerprint(path)

    monkeypatch.setitem(globals(), "_dir_fingerprint", _spy)

    with pytest.raises(AssertionError):
        # 传一个与现状必然不同的假指纹 → 断言必失败，探针由此记录它实际取的路径。
        _assert_demo_store_not_written({"files": (), "total_bytes": -1,
                                        "chroma_sqlite3_md5": None})
    assert os.path.realpath(_LIVE_DEMO_STORE) in seen, \
        "demo 侧生产断言没有对着 `_LIVE_DEMO_STORE` 取指纹（路径常量与实际使用点脱钩）"

    seen.clear()
    try:
        _assert_repo_store_absent()
    except AssertionError:
        pass  # 本机若恰好存在第二个 store，判据失败是它的本职；探针记录不受影响
    assert os.path.realpath(_LIVE_REPO_STORE) in seen, \
        "repo 侧生产断言没有对着 `_LIVE_REPO_STORE` 取指纹（路径常量与实际使用点脱钩）"


# ------------------------------------------------- 6. 命名约定消费方同步（①类守卫）

def test_naming_consumer_recognises_suffixed_and_legacy_names():
    """`memory_consistency._collection_tenant` 必须认得带后缀的新名（与旧名并存）。

    why：只改产生方不改消费方，租户视角巡检会把整批 active 行误报成「缺向量」——
    这正是本仓①类缺陷（同一约定多份实现只改一份）。
    """
    from app.services import memory_consistency as mc

    assert mc._collection_tenant(rag._tenant_collection_name("t2")) == "t2"
    assert mc._collection_tenant(rag._tenant_collection_name(None)) == "default"
    assert mc._collection_tenant(rag._tenant_collection_name("t2", emb.requested_identity(
        "siliconflow"))) == "t2"
    # 历史无后缀名仍要认（存量库 + 巡检测试夹具都用这种形态）
    assert mc._collection_tenant("tenant_t2_vendor_memory") == "t2"
    assert mc._collection_tenant("vendor_memory") == "default"
    # 与约定无关的名字仍归 unknown（不得被误判成某个租户）
    assert mc._collection_tenant("something_else") == "unknown"


# ------------------------------------------------- 7. Q1：缓存命中不得重解析档位（不得重发探测）

def _counting_post(calls, dim=emb.SILICONFLOW_DIM):
    """构造「探测必然通过」的远端档假响应，并逐次记录外呼（本组用例的计数通道）。"""
    def _post(url, *args, **kwargs):
        calls.append(url)
        return _FakeResp(status_code=200, payload={
            "data": [{"index": 0, "embedding": [0.01] * dim}]})
    return _post


def _remote_tier_env(monkeypatch):
    """把档位切成 siliconflow 并给齐成对凭据（base+key），避免配置错误干扰判据。"""
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-fake-for-test")


def test_second_store_same_tenant_makes_no_embedding_request(monkeypatch):
    """Q1 判据：同一进程内第二次 `_store(同一 tenant)` 不得再触发任何 embedding 外呼。

    计数通道自校准（本仓既有纪律）：先断言「首次调用确实产生了一次外呼且被本通道记到」，
    否则「第二次增量为 0」既可能是真无外呼、也可能是通道失效（陷阱没生效的假绿）。
    """
    attempted = []
    _remote_tier_env(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", _counting_post(attempted))

    rag._store("cache_t1")
    assert len(attempted) == 1, \
        "自校准失败：首次 _store（缓存未命中）应产生 1 次探测外呼，实际 %d" % len(attempted)

    rag._store("cache_t1")
    rag._store("cache_t1")
    assert len(attempted) == 1, \
        "缓存命中仍重解析档位并外呼（旧行为每次 +1）：累计 %d 次 %r" % (
            len(attempted), attempted)

    # 全局桶（tenant_id=None）走的是另一条缓存，判据相同
    rag._store(None)
    assert len(attempted) == 2, "全局桶首次 _store 也应解析一次档位（自校准）"
    rag._store(None)
    assert len(attempted) == 2, "全局桶缓存命中仍重解析档位"


def test_cached_store_skips_tier_resolution_and_keeps_o1_invariant(monkeypatch):
    """Q1 机制面：缓存命中连档位解析都不做，且未命中时只解析一次（O1 不变量）。

    why 另立一条而不只看外呼数：hash 档「解析了但恰好不外呼」，只数外呼区分不出来。
    这里直接数 `rag._embedding_provider` 的调用次数；同时钉住 O1 的核心不变量 ——
    缓存未命中时嵌入对象与集合名出自**同一次**解析（探测通过 → 集合名带该档后缀）。
    """
    real_provider = rag._embedding_provider
    resolved = []

    def _spy_provider():
        obj = real_provider()
        resolved.append(obj.identity.label)
        return obj

    monkeypatch.setattr(rag, "_embedding_provider", _spy_provider)
    _remote_tier_env(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", _counting_post([]))

    store = rag._store("resolve_t1")
    assert resolved == ["siliconflow:BAAI/bge-m3:1024"], \
        "缓存未命中应恰好解析一次档位且探测通过，实际 %r" % (resolved,)

    rag._store("resolve_t1")
    rag._store("resolve_t1")
    assert len(resolved) == 1, "缓存命中不得再解析档位，实际共 %d 次" % len(resolved)

    # O1 不变量：集合名由**这一次**解析出的生效档（siliconflow）拼出
    effective = emb.requested_identity("siliconflow")
    assert store._collection.name == rag._tenant_collection_name("resolve_t1", effective), \
        "集合名与生效档不一致：%r" % store._collection.name
    assert store._collection.name.endswith("_" + effective.collection_suffix())
