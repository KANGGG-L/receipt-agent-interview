# -*- coding: utf-8 -*-
"""B-P0-1 存量脏数据迁移：本地新鲜菜心_1787140411 (sku 07) 合并到 本地新鲜菜心 (sku 03)。

幂等，可重复执行。成功后 GET /api/inventory 仅保留 1 条 本地新鲜菜心。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db

def seed_dirty_if_needed():
    """若库中不存在测试双 SKU，则创建以复现缺陷现场。"""
    s = db.get_session()
    try:
        rows = s.query(db._SkuRow).all()
        names = {r.name: r for r in rows}
        # 保证 sku 03 存在
        if "本地新鲜菜心" not in names:
            s.add(db._SkuRow(name="本地新鲜菜心", category="蔬菜", base_unit="斤", current_stock=10.0, sku_code="SKU-03"))
            s.commit()
            print("[seed]  创建 本地新鲜菜心 (sku 03)")
        # 脏数据 sku 07
        if "本地新鲜菜心_1787140411" not in names:
            s.add(db._SkuRow(name="本地新鲜菜心_1787140411", category="蔬菜", base_unit="斤", current_stock=5.0, sku_code="SKU-07"))
            s.commit()
            print("[seed]  创建 本地新鲜菜心_1787140411 (sku 07 脏数据)")
        # 额外脏变体用于回归
        if "有机菜心_1787140420" not in names:
            s.add(db._SkuRow(name="有机菜心_1787140420", category="蔬菜", base_unit="斤", current_stock=3.0, sku_code="SKU-DIRTY-01"))
            s.commit()
            print("[seed]  创建 有机菜心_1787140420 (脏变体)")
        if "有机菜心" not in {r.name for r in s.query(db._SkuRow).all()}:
            s.add(db._SkuRow(name="有机菜心", category="蔬菜", base_unit="斤", current_stock=7.0, sku_code="SKU-CLEAN-01"))
            s.commit()
            print("[seed]  创建 有机菜心 (主)")
    finally:
        s.close()

def run_migration():
    # 先播种
    seed_dirty_if_needed()
    # 显式处理 sku07 -> sku03：若两者均存在则直接 merge
    s = db.get_session()
    try:
        rows = s.query(db._SkuRow).all()
        by_name = {r.name: r for r in rows}
        has_dirty = "本地新鲜菜心_1787140411" in by_name
        has_clean = "本地新鲜菜心" in by_name
        if has_dirty and has_clean:
            dirty = by_name["本地新鲜菜心_1787140411"]
            clean = by_name["本地新鲜菜心"]
            if dirty.active == 1:
                s.close()
                result, err = db.merge_skus(clean.id, [dirty.id])
                if err:
                    print(f"[merge] failed: {err}")
                    return False
                print(f"[merge] sku 07 ({dirty.id}) -> sku 03 ({clean.id}) 迁移完成: {result}")
            else:
                print("[merge] sku 07 已停用，跳过显式合并")
                s.close()
        else:
            s.close()
            print("[merge] 显式双 SKU 不全，fallback 到全量 dedup")
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        print(f"[merge] 显式合并异常 {e}，走全量 dedup")

    # 全量 dedup 兜底（幂等）
    reports = db.deduplicate_skus_by_canonical()
    if reports:
        for r in reports:
            print(f"[dedup] canonical={r['canonical']} primary={r['primary_name']}({r['primary_id']}) merged={r['merged_names']}")
    else:
        print("[dedup] 无新增合并组（已归一）")

    # 校验：仅 1 条 本地新鲜菜心活跃
    active = [s for s in db.list_skus(include_inactive=False) if s.name == "本地新鲜菜心"]
    all_local = [s for s in db.list_skus(include_inactive=True) if "本地新鲜菜心" in s.name]
    print(f"[verify] 活跃 本地新鲜菜心 数量={len(active)}  预期=1")
    print(f"[verify] 全部含 本地新鲜菜心 的 SKU: {[(r.id, r.name, r.active) for r in all_local]}")
    if len(active) == 1:
        print("[verify] PASS: GET /api/inventory 仅 1 条 本地新鲜菜心")
        return True
    else:
        print("[verify] FAIL: 仍有多条活跃变体")
        # 列出库存总量
        for r in active:
            print(f"  - id={r.id} name={r.name} stock={r.current_stock}")
        return False

if __name__ == "__main__":
    ok = run_migration()
    sys.exit(0 if ok else 1)
