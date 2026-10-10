# -*- coding: utf-8 -*-
"""
麦麦多快好省 · 营养需求区间计算

依据：2023 版《中国居民膳食营养素参考摄入量》
参考区间性质：个体能量代谢公式误差可达 ±15%，所有输出均为参考区间，
不是诊断结论，也不构成医疗或健康建议。

钠的特殊逻辑：钠只做「上限告警」，没有「达标」概念，不设下限、不做进度条达标提示。
钙采用 2023 版数值，不使用旧版 50 岁以上 1000mg 的逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 钠：2023 版成年人建议摄入量耐受最高限量 2000 mg/天
SODIUM_LIMIT_MG = 2000.0

# 活动水平 -> 生理活动水平系数 PAL
PAL_LEVELS = {
    "久坐": 1.2,
    "轻度": 1.375,
    "中度": 1.55,
    "高强度": 1.725,
}

PAL_DESC = {
    "久坐": "久坐为主，几乎不运动",
    "轻度": "每周运动 1-3 次",
    "中度": "每周运动 3-5 次",
    "高强度": "每周运动 6-7 次",
}

# 三种饮食模式 -> 评分权重 [蛋白质密度, 热量适配, 钙贡献, 钠惩罚, 价格优势]
DIET_MODES = {
    "均衡": {
        "weights": [1.0, 1.0, 0.8, 1.0, 1.2],
        "desc": "价格与营养平衡，默认推荐",
    },
    "控卡": {
        "weights": [0.6, 2.0, 0.5, 1.2, 0.8],
        "desc": "热量优先，优先贴近个人目标热量区间",
    },
    "增肌": {
        "weights": [2.0, 1.2, 0.8, 0.8, 0.6],
        "desc": "蛋白质优先，同预算下蛋白质密度最高",
    },
}

# 取餐方式优先级（「快」的排序规则，数字越小越优先）
PICKUP_PRIORITY = {
    "自提": 1,       # orderType=1, beType=1 —— 免配送费
    "得来速": 2,     # beType=5 —— 车道取餐，店内等待更短
    "预约时段": 3,
    "外送": 4,       # orderType=2 —— 有地理与配送费信息
}

# 组合约束
MAX_MAIN = 1          # 1 主食
MAX_SIDE = 2          # 0~2 小食
MAX_DRINK = 1         # 0~1 饮料

# 一餐占全天能量目标的参考比例。
# TDEE 是全天值，若直接与单餐比较，所有套餐都会「低于参考区间下界」，
# 热量适配维度将失去区分度，故按常见三餐结构折算单餐区间。
MEAL_RATIO = 1.0 / 3.0
MEAL_RATIO_DESC = "按一天三餐平均分配（单餐约为全天能量目标的 1/3）"


@dataclass
class Range:
    low: float
    high: float

    def as_tuple(self) -> Tuple[float, float]:
        return self.low, self.high

    def contains(self, v: float) -> bool:
        return self.low <= v <= self.high

    def position(self, v: float) -> str:
        """相对区间位置。措辞严格遵守中性表述要求。"""
        if v < self.low:
            return "低于参考区间下界"
        if v > self.high:
            return "高于参考区间上界，建议减少后续摄入"
        return "位于参考区间内"

    def ratio(self, v: float) -> float:
        """相对区间中值的百分比，用于生成「热量 X%」这类对比文案。"""
        mid = (self.low + self.high) / 2.0
        if mid <= 0:
            return 0.0
        return round(v / mid * 100.0, 1)

    def to_dict(self) -> Dict:
        return {"low": round(self.low, 1), "high": round(self.high, 1)}


@dataclass
class NutrientNeeds:
    """用户的营养需求参考区间。"""
    # 画像
    gender: str                 # 男 / 女
    age: int
    height_cm: float
    weight_kg: float
    activity: str               # 久坐 / 轻度 / 中度 / 高强度
    diet_mode: str              # 均衡 / 控卡 / 增肌

    # 派生量
    bmr: float = 0.0
    tdee: float = 0.0
    meal_ratio: float = MEAL_RATIO

    energy_kcal: Optional[Range] = None       # 单餐能量参考区间
    energy_day_kcal: Optional[Range] = None   # 全天能量参考区间
    protein_g: Optional[Range] = None         # 单餐蛋白质参考区间
    protein_day_g: Optional[Range] = None     # 全天蛋白质参考区间
    fat_g: Optional[Range] = None
    carb_g: Optional[Range] = None
    calcium_mg: Optional[Range] = None

    notes: list = field(default_factory=list)

    # ---------------- 计算 ----------------

    def compute(self, meal_ratio: float = MEAL_RATIO) -> "NutrientNeeds":
        """meal_ratio: 单餐占全天能量目标的比例，默认 1/3（一天三餐）。"""
        if meal_ratio <= 0 or meal_ratio > 1:
            raise ValueError("meal_ratio 必须落在 (0, 1] 区间内")
        self.meal_ratio = meal_ratio
        g = (self.gender or "").strip()
        if g not in ("男", "女"):
            raise ValueError("gender 必须为「男」或「女」")
        if self.age < 1 or self.age > 120:
            raise ValueError("age 超出合理范围 1-120")
        if self.height_cm < 50 or self.height_cm > 260:
            raise ValueError("height_cm 超出合理范围 50-260")
        if self.weight_kg < 20 or self.weight_kg > 300:
            raise ValueError("weight_kg 超出合理范围 20-300")
        act = (self.activity or "").strip()
        if act not in PAL_LEVELS:
            raise ValueError(f"activity 必须为：{'/'.join(PAL_LEVELS)}")
        mode = (self.diet_mode or "").strip()
        if mode not in DIET_MODES:
            raise ValueError(f"diet_mode 必须为：{'/'.join(DIET_MODES)}")

        # Mifflin-St Jeor
        if g == "男":
            self.bmr = 10 * self.weight_kg + 6.25 * self.height_cm - 5 * self.age + 5
        else:
            self.bmr = 10 * self.weight_kg + 6.25 * self.height_cm - 5 * self.age - 161

        pal = PAL_LEVELS[act]
        self.tdee = self.bmr * pal

        # 能量区间 = TDEE × 0.9 ~ × 1.1，并按单餐占比折算
        self.energy_day_kcal = Range(self.tdee * 0.9, self.tdee * 1.1)
        self.energy_kcal = Range(self.energy_day_kcal.low * meal_ratio,
                                 self.energy_day_kcal.high * meal_ratio)
        self.notes.append(
            "能量公式个体误差可达 ±15%，以下结果均为参考区间，非精确需求量。"
        )
        self.notes.append(MEAL_RATIO_DESC + "；全天区间 "
                          f"{self.energy_day_kcal.low:.0f}~{self.energy_day_kcal.high:.0f} kcal，"
                          f"单餐区间 {self.energy_kcal.low:.0f}~{self.energy_kcal.high:.0f} kcal。")

        # 蛋白质区间（同样按单餐占比折放）
        lo = self.weight_kg * 0.8
        hi = self.weight_kg * 1.2
        cand = [(lo, hi)]
        if self.age >= 65:
            cand.append((self.weight_kg * 1.2, self.weight_kg * 1.5))
        if act == "高强度":
            cand.append((self.weight_kg * 1.2, self.weight_kg * 1.6))
        # 冲突时取更宽者；再按单餐占比折算
        self.protein_day_g = Range(min(c[0] for c in cand), max(c[1] for c in cand))
        self.protein_g = Range(self.protein_day_g.low * meal_ratio,
                               self.protein_day_g.high * meal_ratio)

        # 脂肪 / 碳水（由能量反推）
        e_low, e_high = self.energy_kcal.as_tuple()
        self.fat_g = Range(e_low * 0.20 / 9.0, e_high * 0.30 / 9.0)
        self.carb_g = Range(e_low * 0.50 / 4.0, e_high * 0.65 / 4.0)

        # 钙：2023 版查表，非公式。单餐按 meal_ratio 折算。
        self.calcium_day_mg = calcium_range(self.age)
        self.calcium_mg = Range(self.calcium_day_mg.low * meal_ratio,
                                self.calcium_day_mg.high * meal_ratio)
        if self.age >= 50:
            self.notes.append(
                "钙参考值按 2023 版执行：成人统一 800mg，50 岁以上已由旧版 1000mg 下调。"
            )

        return self

    # ---------------- 输出 ----------------

    def weights(self) -> list:
        return list(DIET_MODES[self.diet_mode]["weights"])

    def to_dict(self) -> Dict:
        return {
            "profile": {
                "gender": self.gender,
                "age": self.age,
                "height_cm": self.height_cm,
                "weight_kg": self.weight_kg,
                "activity": self.activity,
                "activity_desc": PAL_DESC[self.activity],
                "diet_mode": self.diet_mode,
                "diet_mode_desc": DIET_MODES[self.diet_mode]["desc"],
            },
            "bmr": round(self.bmr, 1),
            "pal": PAL_LEVELS[self.activity],
            "tdee": round(self.tdee, 1),
            "meal_ratio": round(self.meal_ratio, 4),
            "ranges": {
                "energy_kcal": self.energy_kcal.to_dict(),
                "energy_day_kcal": self.energy_day_kcal.to_dict(),
                "protein_g": self.protein_g.to_dict(),
                "protein_day_g": self.protein_day_g.to_dict(),
                "fat_g": self.fat_g.to_dict(),
                "carb_g": self.carb_g.to_dict(),
                "calcium_mg": self.calcium_mg.to_dict(),
                "calcium_day_mg": self.calcium_day_mg.to_dict(),
            },
            "sodium_limit_mg": SODIUM_LIMIT_MG,
            "notes": list(self.notes),
        }

    def summary_text(self) -> str:
        p = self.to_dict()["profile"]
        r = self.to_dict()["ranges"]
        return (
            f"画像：{p['gender']} · {p['age']}岁 · {p['height_cm']:.0f}cm · "
            f"{p['weight_kg']:.1f}kg · {p['activity']}（{p['activity_desc']}）· {p['diet_mode']}模式\n"
            f"BMR {self.bmr:.0f} kcal，TDEE {self.tdee:.0f} kcal\n"
            f"全天能量参考区间 {r['energy_day_kcal']['low']:.0f}~{r['energy_day_kcal']['high']:.0f} kcal"
            f"（{MEAL_RATIO_DESC}）\n"
            f"单餐能量参考区间 {r['energy_kcal']['low']:.0f}~{r['energy_kcal']['high']:.0f} kcal\n"
            f"单餐蛋白质参考区间 {r['protein_g']['low']:.0f}~{r['protein_g']['high']:.0f} g"
            f"（全天 {r['protein_day_g']['low']:.0f}~{r['protein_day_g']['high']:.0f} g）\n"
            f"单餐脂肪参考区间 {r['fat_g']['low']:.0f}~{r['fat_g']['high']:.0f} g\n"
            f"单餐碳水参考区间 {r['carb_g']['low']:.0f}~{r['carb_g']['high']:.0f} g\n"
            f"单餐钙参考区间 {r['calcium_mg']['low']:.0f}~{r['calcium_mg']['high']:.0f} mg"
            f"（全天 {r['calcium_day_mg']['low']:.0f}~{r['calcium_day_mg']['high']:.0f} mg）\n"
            f"钠上限 {SODIUM_LIMIT_MG:.0f} mg/天（仅上限参考，不设下限）"
        )


def calcium_range(age: int) -> Range:
    """钙参考摄入量：2023 版按年龄段查表。"""
    if age <= 11:
        return Range(600, 800)
    if age <= 17:
        return Range(1000, 1200)
    return Range(640, 1000)     # 含孕期哺乳期成人


def sodium_status(total_mg: float) -> Dict:
    """钠只做上限告警。措辞禁用「超标」，改为中性描述。"""
    limit = SODIUM_LIMIT_MG
    pct = round(total_mg / limit * 100.0, 1)
    if total_mg > limit:
        level = "above"
        text = f"钠摄入 {total_mg:.0f} mg，高于 {limit:.0f} mg 参考上限（已摄入 {pct}%）"
    else:
        level = "within"
        text = f"钠摄入 {total_mg:.0f} mg，位于 {limit:.0f} mg 参考上限之内（已摄入 {pct}%）"
    return {
        "total_mg": round(total_mg, 1),
        "limit_mg": limit,
        "percent": pct,
        "level": level,
        "text": text,
        "excess_mg": round(max(0.0, total_mg - limit), 1),
    }


def describe(value: float, rng: Optional[Range], unit: str = "") -> Dict:
    """生成「与用户目标区间的对比」结构。"""
    suffix = f" {unit}" if unit else ""
    if rng is None:
        return {"value": round(value, 1), "position": "无参考区间", "ratio": None}
    return {
        "value": round(value, 1),
        "range": rng.to_dict(),
        "position": rng.position(value),
        "position_short": (
            "区间内" if rng.contains(value)
            else ("低于下界" if value < rng.low else "高于上界")
        ),
        "ratio": rng.ratio(value),
        "text": f"{value:.0f}{suffix}（参考区间 {rng.low:.0f}~{rng.high:.0f}{suffix}，"
                f"{rng.ratio(value)}% · {rng.position(value)}）",
    }


if __name__ == "__main__":
    n = NutrientNeeds(gender="男", age=28, height_cm=175, weight_kg=70,
                      activity="轻度", diet_mode="均衡").compute()
    print(n.summary_text())
    for note in n.notes:
        print("备注：", note)
    print()
    print(sodium_status(1850)["text"])
    print(sodium_status(2600)["text"])
    print(describe(650, n.energy_kcal, "kcal")["text"])
    print(describe(320, n.protein_g, "g")["text"])