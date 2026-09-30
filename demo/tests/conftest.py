# -*- coding: utf-8 -*-
"""demo/tests/ 局部夹具：真实浏览器用例的本地服务自举（D25 环境债治本）。

放在这里而不是 demo/conftest.py 的原因：该夹具只服务「需要活体 15010 服务」的真实
浏览器用例，按需启用（`@pytest.mark.usefixtures("playwright_live_server")`），刻意
不做 autouse —— 否则纯离线用例也会被平白拉起一个服务、拖慢并引入端口竞争。

具体实现（只读拷贝 live 库为 DB_PATH、健康探测、复用必须可判定归属、精确回收）见同目录
`_playwright_server.py`；本文件只负责把它暴露成一个会话级夹具。所有浏览器用例一律经
此夹具自举，不再各写一份 `ensure_server()`，从而保证服务**必然**带 DNS 锁定（见
`_playwright_serve.py`），消除「文件名顺序决定谁先起服务」导致的顺序相关假失败。
"""

import os
import sys

import pytest

# 保证 `import _playwright_server` 在本目录被 pytest 以非包方式收集时仍可用
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from _playwright_server import ensure_server  # noqa: E402


@pytest.fixture(scope="session")
def playwright_live_server():
    """会话级：无服务时以 live 库只读副本为 DB_PATH 启动，已有服务则复用。

    会话级的作用域让同一轮 pytest 里所有需要它的用例共享同一个服务实例，避免每个
    用例反复起停造成端口抖动；结束时只回收「本进程自己启动的那个」进程与其临时副本
    目录，绝不动别人已经在跑的服务（`ensure_server()` 返回 None 即此类情形）。
    """
    handle = ensure_server()
    try:
        yield handle
    finally:
        if handle is not None:
            handle.stop()
