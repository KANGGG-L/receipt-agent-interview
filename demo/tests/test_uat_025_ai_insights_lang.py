# -*- coding: utf-8 -*-
"""UAT-025 DEV：繁体洞察请求须传当前语言（静态接线 + API review_locale）。

MANUAL_UI（真实 Hant 触发网络捕获）留给独立 QA/UAT。
真实模型输出不在本卡范围内。
"""
from pathlib import Path

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None

ROOT = Path(__file__).resolve().parents[2]
INV_JS = ROOT / "demo/static/js/inventory_board.js"
API_ADMIN = ROOT / "demo/app/api_admin.py"


def test_load_ai_lean_insights_appends_lang_from_i18n():
    src = INV_JS.read_text(encoding="utf-8")
    assert "function loadAiLeanInsights" in src
    # Must read current UI locale
    assert "I18N.getLocale" in src or "window.I18N.getLocale" in src
    # Must build /api/ai-insights?lang=...
    assert "/api/ai-insights?lang=" in src
    assert "encodeURIComponent(lang)" in src
    # Must not leave a bare no-lang call as the only path for owner
    # (empty-lang fallback URL without query is OK when locale unavailable)
    assert "apiFetch(insightsUrl)" in src or "apiFetch('/api/ai-insights?'" in src
    # Regression: bare apiFetch('/api/ai-insights') without lang construction is the bug
    bare = "apiFetch('/api/ai-insights')"
    # After fix, bare call should not be the owner path; allow only inside comments
    for line in src.splitlines():
        if bare in line and not line.strip().startswith("//"):
            # empty-lang ternary arm may still mention the path as string literal
            if "insightsUrl" in line or "?lang=" in line:
                continue
            if line.strip() in ("apiFetch('/api/ai-insights')", "apiFetch('/api/ai-insights');"):
                msg = "bare /api/ai-insights fetch without lang still present: " + line.strip()
                if pytest is not None:
                    pytest.fail(msg)
                raise AssertionError(msg)


def test_api_admin_ai_insights_records_review_locale_contract():
    src = API_ADMIN.read_text(encoding="utf-8")
    assert 'def ai_insights(request: Request, lang: str = ""):' in src
    assert 'data["review_locale"] = normalize_locale(lang) or ""' in src
    assert "UAT-025" in src
    assert "weekly_insights" in src
    # Must not call the review model from this endpoint
    assert "run_review" not in src[src.index("def ai_insights") : src.index("def ai_insights") + 800]


def test_hant_and_hans_lang_map_to_review_locale(monkeypatch):
    """Supportive API self-check; skip if app deps unavailable in this env."""
    try:
        from fastapi.testclient import TestClient
        from app.chains import review_chain
        from app.main import app
        from _b2_helpers import hdr, new_tenant
    except Exception as exc:  # pragma: no cover - env without runtime deps
        if pytest is not None:
            pytest.skip("app runtime deps unavailable: %s" % exc)
        raise AssertionError("app runtime deps unavailable: %s" % exc)

    def _boom(*args, **kwargs):
        raise AssertionError("insights must not call review model")

    monkeypatch.setattr(review_chain, "run_review", _boom)
    client = TestClient(app)

    r_hant = client.get("/api/ai-insights", headers=hdr(new_tenant()), params={"lang": "zh-Hant-HK"})
    assert r_hant.status_code == 200, r_hant.text
    assert r_hant.json()["data"]["review_locale"] == "zh-Hant-HK"

    r_alias = client.get("/api/ai-insights", headers=hdr(new_tenant()), params={"lang": "zh-HK"})
    assert r_alias.status_code == 200
    assert r_alias.json()["data"]["review_locale"] == "zh-Hant-HK"

    r_hans = client.get("/api/ai-insights", headers=hdr(new_tenant()), params={"lang": "zh-Hans"})
    assert r_hans.status_code == 200
    assert r_hans.json()["data"]["review_locale"] == "zh-Hans"

    r_empty = client.get("/api/ai-insights", headers=hdr(new_tenant()))
    assert r_empty.status_code == 200
    assert r_empty.json()["data"]["review_locale"] == ""
