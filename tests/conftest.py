# -*- coding: utf-8 -*-
"""tests/ 顶层夹具：离线可跑的环境锁定。

BUG-04（step12 记录）：裸跑 pytest 时，模块级 load_dotenv() 会把本机的
AUTH_ENABLED=1 注入进程，导致约 196 个权限相关用例失败。此处在**任何测试模块
导入之前**（conftest 先于测试模块加载）把鉴权锁定为关闭，保证离线全绿。

dotenv 的 load_dotenv() 默认 override=False，因此这里预设的环境变量不会被
后续 .env 覆盖。若某用例需要验证鉴权开启的行为，请在用例内显式 monkeypatch
app.auth 的判定函数，而不是放开这里的全局开关。
"""

import os
import sys

# 鉴权关闭，必须在 import app.* 之前
os.environ["AUTH_ENABLED"] = "0"

# demo 应用根目录（tests 与 demo/tests 都能 import app.*）
_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

# 仓库根目录（tests 直接 import ai_registry.*）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
