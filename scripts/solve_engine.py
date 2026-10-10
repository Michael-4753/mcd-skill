# -*- coding: utf-8 -*-
"""
麦麦多快好省 · 多方案求解引擎（核心）

职责：
  1. 组合枚举：在「1 主食 + 0~2 小食 + 0~1 饮料」约束下生成候选组合
  2. 五维评分：蛋白质密度 / 热量适配 / 钙贡献 / 钠惩罚 / 价格优势
  3. 三方案输出：极致省钱(A) / 均衡最优(B) / 营养优先(C)
  4. 约束过滤：预算、忌口、钠尽量 ≤ 2000mg、不重复餐品

重要约定：
  - 未经 calculate-price 实算确认的方案，verified=False，
    必须标注「未经实价验证」，不得以「最省」结论呈现给用户。
  - 本引擎不做医疗/健康判断，所有数值均为参考区间对比。

命令行用法：
  python solve_engine.py --demo
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from nutrition_db import NutritionDB, get_db
from nutrition_needs import (
    DIET_MODES, MAX_DRINK, MAX_MAIN, MAX_SIDE, SODIUM_LIMIT_MG,
    NutrientNeeds, describe, sodium_status,
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    """来自 query-meals 的餐品，价格单位为「元」（已由调用方从分转换）。"""
    productCode: str
    productName: str
    price: float
    category: str = "其他"
    nutrition: Optional[Dict] = None      # 匹配到的营养条目
    match_note: str = ""                  # 匹配失败原因

    @property
    def has_nutrition(self) -> bool:
        return bool(self.nutrition and self.nutrition.get("has_data"))

    def nutrition_text(self) -> Optional[str]:
        """nutritionDescription 原样透出（可能是能量换算说明），不得清洗。"""
        if not self.nutrition:
            return None
        d = self.nutrition.get("nutritionDescription")
        return d


@dataclass
class Combo:
    """一个候选套餐组合。"""
    items: List[MenuItem]

    def __post_init__(self):
        self.gross = round(sum(i.price for i in self.items), 2)
        n = self.nutrition_total()
        self.kcal = n["kcal"]
        self.protein = n["protein"]
        self.fat = n["fat"]
        self.carb = n["carb"]
        self.sodium = n["sodium"]
        self.calcium = n["calcium"]
        self.full_nutrition = n["complete"]

    def nutrition_total(self) -> Dict:
        acc = dict(kcal=0.0, protein=0.0, fat=0.0, carb=0.0,
                   sodium=0.0, calcium=0.0)
        complete = True
        for it in self.items:
            if not it.has_nutrition:
                complete = False
                continue
            nn = it.nutrition
            acc["kcal"] += float(nn["energyKcal"] or 0)
            acc["protein"] += float(nn["protein"] or 0)
            acc["fat"] += float(nn["fat"] or 0)
            acc["carb"] += float(nn["carbohydrate"] or 0)
            acc["sodium"] += float(nn["sodium"] or 0)
            acc["calcium"] += float(nn["calcium"] or 0)
        return {k: round(v, 1) for k, v in acc.items()} | {"complete": complete}

    def names(self) -> str:
        return " + ".join(i.productName for i in self.items)

    def to_payload(self) -> Dict:
        """转换为 calculate-price 的 items 参数（不含券，券由上层附加）。"""
        return [{"productCode": it.productCode, "quantity": 1} for it in self.items]


# ---------------------------------------------------------------------------
# 求解引擎
# ---------------------------------------------------------------------------


class SolveEngine:
    def __init__(self, needs: NutrientNeeds, budget: float,
                 db: Optional[NutritionDB] = None,
                 excludes: Optional[Sequence[str]] = None,
                 max_price_for_score: Optional[float] = None):
        self.needs = needs
        self.budget = float(budget)
        self.db = db or get_db()
        # 忌口/替换：名称关键词黑名单
        self.excludes = [e.strip() for e in (excludes or []) if e and e.strip()]
        # 价格优势归一化的基准：默认取预算
        self.price_ref = float(max_price_for_score or budget)

    # ---------- 候选池 ----------

    def build_pool(self, menu: List[MenuItem]) -> Dict[str, List[MenuItem]]:
        """按分类切分并做营养匹配；营养缺失项不参与评分。"""
        mains, sides, drinks = [], [], []
        no_nutrition: List[MenuItem] = []
        for it in menu:
            if any(x in it.productName for x in self.excludes):
                continue
            if it.nutrition is None:
                it.nutrition = self.db.match(it.productName)
                if it.nutrition is None:
                    it.match_note = "营养参考数据中无对应条目，不参与营养评分"
            cat = self.db.match(it.productName)["category"] if it.nutrition \
                else _guess_category(it.productName)
            it.category = cat
            if cat == "主食":
                mains.append(it)
            elif cat == "小食":
                sides.append(it)
            elif cat == "饮料":
                drinks.append(it)
            if not it.has_nutrition:
                no_nutrition.append(it)
        return {"主食": mains, "小食": sides, "饮料": drinks,
                "_no_nutrition": no_nutrition}

    # ---------- 枚举 ----------

    def enumerate_combos(self, pool: Dict[str, List[MenuItem]],
                         limit: int = 4000) -> List[Combo]:
        mains = [m for m in pool["主食"] if m.has_nutrition][:12]
        sides = [s for s in pool["小食"] if s.has_nutrition][:12]
        drinks = [d for d in pool["饮料"] if d.has_nutrition][:10]
        if not mains:
            return []

        combos: List[Combo] = []
        for m in mains:
            # 0~2 小食：C(n,0)+C(n,1)+C(n,2)
            side_sets = [()]
            side_sets += [(s,) for s in sides]
            side_sets += list(itertools.combinations(sides, 2))
            # 0~1 饮料
            drink_sets = [()] + [(d,) for d in drinks]
            for ss in side_sets:
                for ds in drink_sets:
                    items = [m] + list(ss) + list(ds)
                    # 同一餐品不重复
                    if len({i.productCode for i in items}) != len(items):
                        continue
                    c = Combo(items)
                    if c.gross > self.budget + 1e-9:
                        continue
                    combos.append(c)
                    if len(combos) >= limit:
                        return combos
        return combos

    # ---------- 评分 ----------

    def score(self, c: Combo) -> Dict:
        """五维加权评分。返回各维度原始值便于向用户解释。"""
        w = self.needs.weights()          # [蛋白密度, 热量适配, 钙, 钠惩罚, 价格]
        e = self.needs.energy_kcal
        p = self.needs.protein_g
        ca = self.needs.calcium_mg

        # w1 蛋白质密度：g / 100kcal，越高越好。
        # 归一化基准取 6.0 g/100kcal —— 常见快餐主食区间约 3~6，
        # 高蛋白选项（鸡胸/牛肉/奶类）可超过 6，因此该基准能拉开区分度。
        per100 = (c.protein / c.kcal * 100.0) if c.kcal > 0 else 0.0
        s_protein = min(1.0, per100 / 6.0)

        # w2 热量适配度：落在单餐区间内为 1，偏离按「区间宽度」归一化惩罚。
        # 用区间宽度而非中值作分母，可让小幅超界也获得可观的扣分，
        # 否则高蛋白高热量组合在控卡模式下仍可能靠其他维度胜出。
        if e.contains(c.kcal):
            s_energy = 1.0
        else:
            width = (e.high - e.low) or max(1.0, e.high * 0.2)
            over = (c.kcal - e.high) / width if c.kcal > e.high \
                else (e.low - c.kcal) / width
            s_energy = max(0.0, 1.0 - over) ** 1.5

        # w3 钙贡献：相对参考区间上界的达成度
        s_calcium = min(1.0, c.calcium / ca.high) if ca.high > 0 else 0.0

        # w4 钠惩罚：仅对超出 2000mg 部分加权，区间内不惩罚
        excess = max(0.0, c.sodium - SODIUM_LIMIT_MG)
        s_sodium_penalty = excess / SODIUM_LIMIT_MG      # 直接以负权计入

        # w5 价格优势：实付越低越高
        s_price = 1.0 if self.price_ref <= 0 else max(
            0.0, 1.0 - c.gross / self.price_ref)

        total = (w[0] * s_protein
                 + w[1] * s_energy
                 + w[2] * s_calcium
                 - w[3] * s_sodium_penalty
                 + w[4] * s_price)
        return {
            "total": round(total, 4),
            "protein_density_per100kcal": round(per100, 2),
            "protein_score": round(s_protein, 3),
            "energy_score": round(s_energy, 3),
            "calcium_score": round(s_calcium, 3),
            "sodium_penalty": round(s_sodium_penalty, 3),
            "price_score": round(s_price, 3),
            "weights": w,
        }

    # ---------- 三方案 ----------

    def pick_three(self, combos: List[Combo],
                   priced: Optional[Dict[str, float]] = None
                   ) -> Dict[str, Optional[Combo]]:
        """挑出 A 极致省钱 / B 均衡最优 / C 营养优先。

        三方案强制互不相同：若候选因评分趋同而选中同一组合，
        会依次按「次优价格 / 次优总分」回退，保证用户拿到 3 个真正不同的选择。

        priced: {combo_key: 实付金额}，来自 calculate-price。
                 未实算时传 None —— 此时 A 方案标记 verified=False。
        """
        priced = priced or {}

        def key(c: Combo) -> str:
            return "|".join(sorted(i.productCode for i in c.items))

        def paid(c: Combo) -> Optional[float]:
            return priced.get(key(c))

        scored = [(c, self.score(c)) for c in combos]
        if not scored:
            return {"A": None, "B": None, "C": None}

        # A 极致省钱：实付最低；无实算数据时退化为标价最低
        by_price = sorted(scored, key=lambda p: (
            paid(p[0]) if paid(p[0]) is not None else p[0].gross))
        a = by_price[0]

        # C 营养优先：总分最高，同分时取蛋白密度更高者
        by_score = sorted(scored, key=lambda p: (
            -p[1]["total"], -p[1]["protein_density_per100kcal"]))
        c_best = by_score[0]

        # B 均衡最优：总分前 30% 中，价格优势与总分综合最优
        top_n = max(1, int(len(by_score) * 0.3))
        b = max(by_score[:top_n], key=lambda p: (p[1]["total"] + p[1]["price_score"]))

        # 互异化：按优先级把重复项替换为序列中的下一个未选项
        chosen: Dict[str, Optional[Combo]] = {"A": a[0], "B": b[0], "C": c_best[0]}
        pools = {"A": by_price, "B": by_score, "C": by_score}
        order = ["A", "B", "C"]
        for tag in order:
            used = {key(chosen[t]) for t in order if chosen.get(t) is not None}
            if key(chosen[tag]) not in used:
                continue
            for cand, _ in pools[tag]:
                if key(cand) not in used:
                    chosen[tag] = cand
                    break

        # 写回价格验证状态与评分
        sc_map = {key(c): s for c, s in scored}
        out: Dict[str, Optional[Combo]] = {}
        for tag in order:
            combo = chosen[tag]
            if combo is None:
                out[tag] = None
                continue
            combo._price_paid = paid(combo)
            combo._price_verified = paid(combo) is not None
            combo._score = sc_map.get(key(combo), {})
            out[tag] = combo
        return out

    # ---------- 渲染 ----------

    def render(self, tag: str, c: Optional[Combo], store: Dict,
               coupons: Optional[List[Dict]] = None) -> Dict:
        """把方案渲染成结构化输出，供上层转述给用户。"""
        titles = {
            "A": "极致省钱",
            "B": "均衡最优",
            "C": "营养优先",
        }
        hint = {
            "A": "券叠到最低，可用积分则抵，牺牲部分营养密度",
            "B": "价格与营养平衡（默认推荐）",
            "C": "同样预算下蛋白质密度最高",
        }
        if c is None:
            return None

        verified = getattr(c, "_price_verified", False)
        paid_val = getattr(c, "_price_paid", None)
        net = paid_val if paid_val is not None else c.gross
        saved = round(c.gross - net, 2)

        n = self.needs
        nutrition = {
            "kcal": c.kcal, "protein": c.protein, "fat": c.fat,
            "carb": c.carb, "sodium": c.sodium, "calcium": c.calcium,
        }
        return {
            "tag": tag,
            "title": titles[tag],
            "hint": hint[tag],
            "items": [
                {"name": i.productName, "productCode": i.productCode,
                 "price": i.price, "quantity": 1,
                 "nutritionDescription": i.nutrition_text()}
                for i in c.items
            ],
            "price": {
                "gross": c.gross,
                "paid": net,
                "saved": saved,
                "verified": verified,
                "note": ("已通过 calculate-price 实算确认" if verified
                         else "未经 calculate-price 实算验证，仅为标价参考"),
            },
            "coupons": coupons or [],
            "nutrition_total": nutrition,
            "nutrition_complete": c.full_nutrition,
            "compare": {
                "energy": describe(c.kcal, n.energy_kcal, "kcal"),
                "protein": describe(c.protein, n.protein_g, "g"),
                "fat": describe(c.fat, n.fat_g, "g"),
                "carb": describe(c.carb, n.carb_g, "g"),
                "calcium": describe(c.calcium, n.calcium_mg, "mg"),
                "sodium": sodium_status(c.sodium),
            },
            "score": getattr(c, "_score", {}),
            "pickup": store,
            "price_payload": c.to_payload(),
            "combo_key": "|".join(sorted(i.productCode for i in c.items)),
        }


def _guess_category(name: str) -> str:
    from nutrition_db import categorize
    return categorize(name)


# ---------------------------------------------------------------------------
# 追问清单（输出方案后必须反问，禁止单轮结束）
# ---------------------------------------------------------------------------

FOLLOWUP_QUESTIONS = [
    "这个价位接受吗？",
    "想加小食或换饮料吗？",
    "需不需要换得来速或自提来省配送费？",
    "是否有忌口或过敏？",
]


# ---------------------------------------------------------------------------
# Demo（使用本地营养库 + 模拟菜单，仅用于自测，不产出对外结论）
# ---------------------------------------------------------------------------

DEMO_MENU = [
    ("P001", "猪柳麦满分", 22.0), ("P002", "原味板烧鸡腿麦满分", 21.5),
    ("P003", "巨无霸", 28.0), ("P004", "双层吉士汉堡", 26.0),
    ("P005", "板烧鸡腿堡", 19.0), ("P006", "吉士汉堡包", 17.0),
    ("S001", "中薯条", 10.0), ("S002", "麦乐鸡5块", 19.0),
    ("S003", "麦辣鸡翅-2块", 14.0), ("S004", "香芋派", 9.5),
    ("S005", "圆筒冰淇淋", 6.0), ("S006", "大杯玉米杯", 9.0),
    ("D001", "可乐中杯", 8.0), ("D002", "无糖可口可乐中杯", 8.0),
    ("D003", "纯牛奶（盒装）", 9.5), ("D004", "热牛奶小杯", 11.0),
]


def run_demo():
    needs = NutrientNeeds(gender="男", age=28, height_cm=175, weight_kg=70,
                          activity="轻度", diet_mode="均衡").compute()
    db = get_db()
    menu = []
    for code, name, price in DEMO_MENU:
        m = MenuItem(productCode=code, productName=name, price=price)
        m.nutrition = db.match(name)
        if m.nutrition is None:
            m.match_note = "营养参考数据中无对应条目"
        menu.append(m)

    eng = SolveEngine(needs, budget=45.0, db=db)
    pool = eng.build_pool(menu)
    combos = eng.enumerate_combos(pool)
    three = eng.pick_three(combos)

    store = {"mode": "自提", "orderType": 1, "beType": 1,
             "storeCode": "DEMO", "storeName": "示例门店（自测数据）",
             "pickup_priority": 1}
    for tag in ("A", "B", "C"):
        out = eng.render(tag, three[tag], store)
        if not out:
            print(f"[{tag}] 无可行方案")
            continue
        print(f"\n===== 方案 {out['tag']} ｜ {out['title']} =====")
        print(f"餐品：{ ' + '.join(i['name'] for i in out['items']) }")
        print(f"标价合计 {out['price']['gross']} 元 / 实付 {out['price']['paid']} 元 "
              f"/ 节省 {out['price']['saved']} 元 [{out['price']['note']}]")
        nt = out["nutrition_total"]
        print(f"营养合计：{nt['kcal']} kcal、蛋白 {nt['protein']}g、脂肪 {nt['fat']}g、"
              f"碳水 {nt['carb']}g、钠 {nt['sodium']}mg、钙 {nt['calcium']}mg")
        print("热量对比：" + out["compare"]["energy"]["text"])
        print("蛋白对比：" + out["compare"]["protein"]["text"])
        print("钠提示：" + out["compare"]["sodium"]["text"])
        print("评分：" + json.dumps(out["score"], ensure_ascii=False))
    print("\n输出后必须反问：")
    for q in FOLLOWUP_QUESTIONS:
        print("  - " + q)
    print(f"\n候选组合数：{len(combos)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    run_demo()