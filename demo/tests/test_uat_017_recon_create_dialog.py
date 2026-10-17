# -*- coding: utf-8 -*-
"""UAT-017 specialty: reconciliation create modal dialog semantics (static + source wiring).

Dev-only gate. Independent QA / UAT must re-verify with real browser AXTree / Tab / Esc.
Does not rewrite other modals (negative boundary).
"""
from __future__ import annotations

import os
import re

import pytest

DEMO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TPL = os.path.join(DEMO, "templates", "index.html")
JS = os.path.join(DEMO, "static", "js", "finance_ops.js")
REVIEW_MODAL = os.path.join(DEMO, "static", "js", "review_modal.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _recon_create_block(html: str) -> str:
    m = re.search(
        r'<div id="reconCreateModal"[^>]*>.*?</div>\s*</div>\s*</div>',
        html,
        flags=re.S,
    )
    assert m, "reconCreateModal block not found"
    return m.group(0)


def test_recon_create_modal_has_dialog_semantics_in_html():
    html = _read(TPL)
    block = _recon_create_block(html)
    assert 'role="dialog"' in block
    assert 'aria-modal="true"' in block
    assert 'aria-labelledby="reconCreateTitle"' in block
    assert 'aria-describedby="reconSupplierHint"' in block
    assert 'id="reconCreateTitle"' in block
    assert 'id="btnCancelReconCreate"' in block
    assert 'id="btnSubmitReconCreate"' in block
    assert 'aria-label="关闭"' in block
    # labels associated
    assert 'for="reconPeriodType"' in block
    assert 'for="reconStart"' in block
    assert 'for="reconEnd"' in block


def test_open_recon_modal_uses_accessible_modal_path():
    src = _read(JS)
    m = re.search(r"\nfunction openReconModal\([\s\S]*?\n\}", src)
    assert m, "openReconModal not found"
    body = m.group(0)
    assert "openAccessibleModal" in body
    assert "classList.remove('hide')" not in body
    assert "#reconPeriodType" in body
    # cancel / submit still go through closeModalById (keeps Esc focus restore path)
    assert "closeModalById('reconCreateModal')" in src
    assert "submitReconCreate" in src


def test_accessible_modal_helpers_still_present():
    src = _read(REVIEW_MODAL)
    assert "function openAccessibleModal" in src
    assert "function closeAccessibleModal" in src
    assert "function getModalFocusableElements" in src
    # Esc/Tab global listener lives in admin_observation.js; ensure closeAccessibleModal
    # restores trigger focus (UAT-017 AC: Esc closes and returns focus).
    assert "item.trigger.focus" in src


def test_uat017_scope_does_not_rewrite_unrelated_modals():
    """Negative boundary: only recon create modal is in UAT-017 product scope."""
    html = _read(TPL)
    # payment modal may still lack static role — must not be "fixed" by this card
    pay = re.search(r'<div id="paymentModal"[^>]*>', html)
    assert pay, "paymentModal missing"
    tag = pay.group(0)
    # Allow either state; assert we did not accidentally add UAT-017 title ids elsewhere
    assert "reconCreateTitle" not in tag
    resolve = re.search(r'<div id="reconResolveModal"[^>]*>', html)
    assert resolve
    # Resolve modal is out of UAT-017 scope — must not gain reconCreateTitle
    assert "reconCreateTitle" not in resolve.group(0)
