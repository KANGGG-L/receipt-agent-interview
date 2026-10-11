# -*- coding: utf-8 -*-
"""把 FastAPI 应用里所有路由摊平成 APIRoute 列表。

FastAPI 0.143 起 include_router 不再把子路由逐条展开到 app.routes，而是放一个
_IncludedRouter 外壳；直接遍历 app.routes 会漏掉全部业务端点。
"""


def iter_routes(app_or_router):
    for r in app_or_router.routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            for sub in iter_routes(inner):
                if prefix and getattr(sub, "path", None) is not None:
                    yield _Prefixed(sub, prefix)
                else:
                    yield sub
        else:
            yield r


class _Prefixed:
    """带前缀的路由视图：只读 path，其余属性透传。"""

    def __init__(self, route, prefix):
        self._route = route
        self.path = prefix + route.path

    def __getattr__(self, name):
        return getattr(self._route, name)
