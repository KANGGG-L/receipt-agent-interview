# -*- coding: utf-8 -*-
"""
Playwright Audit Script: Export Center Tables Visual QA & E2E Inspection
"""
import os
import sys
import json
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SCREENSHOT_DIR = os.path.join(ROOT_DIR, "demo", "gui-test-screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def wait_for_preview(page, timeout=15000):
    page.wait_for_function(
        """() => {
            if (!window.exportCenterState) return false;
            if (!window.exportCenterState.reports || window.exportCenterState.reports.length === 0) return false;
            if (window.exportCenterState.isLoading) return false;
            const title = document.getElementById('exportActiveReportTitle');
            if (!title || title.innerText.trim() === '报表实时预览') return false;
            const summary = document.getElementById('exportPreviewSummary');
            if (!summary) return false;
            const text = summary.innerText || '';
            return !text.includes('正在生成实时预览数据');
        }""",
        timeout=timeout
    )
    time.sleep(0.5)

def inspect_table_dom(page, report_id, report_title):
    eval_result = page.evaluate("""() => {
        const container = document.querySelector('#exportPreviewTableWrapper .table-container') || document.querySelector('.export-table-preview-card .table-container');
        const table = document.getElementById('exportPreviewTable');
        const ths = Array.from(document.querySelectorAll('#exportPreviewThead th'));
        const trs = Array.from(document.querySelectorAll('#exportPreviewTbody tr'));

        // 1. Check TH overlapping
        const thBoxes = ths.map(th => {
            const r = th.getBoundingClientRect();
            return { text: th.innerText.trim(), left: r.left, right: r.right, width: r.width, height: r.height };
        });

        const headerOverlaps = [];
        for (let i = 0; i < thBoxes.length - 1; i++) {
            const curr = thBoxes[i];
            const next = thBoxes[i + 1];
            // Overlap if curr.right > next.left with a margin of 0.5px (accounting for subpixel rounding)
            if (curr.right > next.left + 0.5) {
                headerOverlaps.push({
                    col1: curr.text,
                    col2: next.text,
                    currRight: curr.right,
                    nextLeft: next.left,
                    overlapAmount: curr.right - next.left
                });
            }
        }

        // 2. Check TD truncation & ellipsis
        let totalCells = 0;
        let truncatedCells = [];
        trs.forEach((tr, rIdx) => {
            const tds = Array.from(tr.querySelectorAll('td'));
            tds.forEach((td, cIdx) => {
                totalCells++;
                const text = td.innerText.trim();
                const colName = thBoxes[cIdx] ? thBoxes[cIdx].text : `Col ${cIdx}`;
                if (text.includes('...') || text.includes('…')) {
                    truncatedCells.push({
                        row: rIdx,
                        col: colName,
                        text: text
                    });
                }
            });
        });

        // 3. Container & table scroll check
        let containerScroll = {
            containerClientWidth: container ? container.clientWidth : 0,
            containerScrollWidth: container ? container.scrollWidth : 0,
            tableClientWidth: table ? table.clientWidth : 0,
            isOverflowing: container ? container.scrollWidth > container.clientWidth : false,
            tableLayout: table ? window.getComputedStyle(table).tableLayout : '',
            tableMinWidth: table ? window.getComputedStyle(table).minWidth : ''
        };

        // Check if horizontal scroll can actually scroll
        let canScroll = false;
        if (container && container.scrollWidth > container.clientWidth) {
            const initialScroll = container.scrollLeft;
            container.scrollLeft = 50;
            if (container.scrollLeft > 0) {
                canScroll = true;
            }
            container.scrollLeft = 0;
        } else {
            canScroll = true;
        }

        return {
            colCount: ths.length,
            rowCount: trs.length,
            headers: thBoxes.map(b => b.text),
            headerOverlaps: headerOverlaps,
            totalCells: totalCells,
            truncatedCells: truncatedCells,
            containerScroll: containerScroll,
            canScroll: canScroll
        };
    }""")
    return eval_result

def main():
    print("Starting Playwright Export Center Table Audit...")
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        print(f"Navigating to {BASE_URL}...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=15000)

        current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        print(f"Current role: {current_role}")
        if current_role != "owner":
            print("Switching role to 'owner'...")
            with page.expect_navigation(timeout=15000):
                page.select_option("#demoRoleSelect", "owner")
            page.wait_for_selector('.sidebar-btn[data-target="tab-export"]', timeout=15000)

        print("Clicking '数据导出中心' tab...")
        page.click('.sidebar-btn[data-target="tab-export"]')
        wait_for_preview(page)

        targets = [
            {
                "category": "inventory",
                "report_id": "inventory_stocktake",
                "title": "实时库存盘点底单 (现场核对版)",
                "screenshot": "export_audit_stocktake_fixed.png"
            },
            {
                "category": "inventory",
                "report_id": "inventory_valuation",
                "title": "实时库存台账与货值盘点表",
                "screenshot": "export_audit_valuation_fixed.png"
            },
            {
                "category": "dishes",
                "report_id": "dish_bom_recipes",
                "title": "餐品标准 BOM 结构与理论毛利表",
                "screenshot": "export_audit_dish_bom_fixed.png"
            },
            {
                "category": "suppliers",
                "report_id": "receipts_itemized_ledger",
                "title": "采购收据入账全量明细台账 (财务归档)",
                "screenshot": "export_audit_ledger_fixed.png"
            },
            {
                "category": "suppliers",
                "report_id": "supplier_payables_aging",
                "title": "供应商应付账款与账龄对账表",
                "screenshot": "export_audit_aging_fixed.png"
            },
            {
                "category": "department",
                "report_id": "department_cost_summary",
                "title": "各部门/档口物料采购花销汇总",
                "screenshot": "export_audit_dept_summary_fixed.png"
            }
        ]

        for target in targets:
            cat = target["category"]
            rep_id = target["report_id"]
            shot_name = target["screenshot"]
            print(f"\n--- Testing Category: {cat} | Report: {rep_id} ---")

            page.evaluate(f"() => selectExportCategory('{cat}', '{rep_id}')")
            wait_for_preview(page)

            dom_data = inspect_table_dom(page, rep_id, target["title"])
            results[rep_id] = dom_data

            print(f"Report: {target['title']}")
            print(f"Columns ({dom_data['colCount']}): {', '.join(dom_data['headers'])}")
            print(f"Rows: {dom_data['rowCount']}, Total Cells: {dom_data['totalCells']}")
            print(f"Header Overlaps: {len(dom_data['headerOverlaps'])}")
            if dom_data['headerOverlaps']:
                for o in dom_data['headerOverlaps']:
                    print(f"  [!] OVERLAP: '{o['col1']}' collides with '{o['col2']}' by {o['overlapAmount']:.2f}px")
            else:
                print("  [OK] Zero header collision detected!")

            print(f"Truncated cells: {len(dom_data['truncatedCells'])}")
            if dom_data['truncatedCells']:
                for tc in dom_data['truncatedCells'][:5]:
                    print(f"  [!] TRUNCATED: Row {tc['row']}, {tc['col']}: '{tc['text']}'")
            else:
                print("  [OK] Zero text truncation detected!")

            scroll_info = dom_data['containerScroll']
            print(f"Scroll info: Container clientWidth={scroll_info['containerClientWidth']}, scrollWidth={scroll_info['containerScrollWidth']}, tableLayout={scroll_info['tableLayout']}, minWidth={scroll_info['tableMinWidth']}")
            print(f"Scrollable verified: {dom_data['canScroll']}")

            if shot_name:
                shot_path = os.path.join(SCREENSHOT_DIR, shot_name)
                table_card = page.locator(".export-table-preview-card")
                if table_card.count() > 0:
                    table_card.screenshot(path=shot_path)
                else:
                    page.screenshot(path=shot_path)
                print(f"  [SHOT] Saved screenshot: {shot_path}")

        browser.close()

    report_file = os.path.join(SCREENSHOT_DIR, "audit_dom_summary.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nAudit complete! Saved detailed summary to {report_file}")

if __name__ == "__main__":
    main()
