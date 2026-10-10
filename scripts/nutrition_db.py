# -*- coding: utf-8 -*-
"""
麦麦多快好省 · 营养参考数据基线

数据来源：MCP 工具 list-nutrition-foods 实拉取（160 条），落盘为本地缓存。
用途：MCP 不可用或超时的降级兜底；正常路径仍以 MCP 实时返回为准。

重要数据质量约定（必须原样保留，不得清洗掉）：
  1. 大量条目 nutritionDescription 为 null —— 这是正常状态，不是缺失。
  2. 部分条目 nutritionDescription 填的是「能量换算说明」而非描述文案，例如
     「纯悦」-> 「能量约清水1杯」
     「芝士安格斯厚牛堡」-> 「能量约鲈鱼1条」
     「柠檬拉明顿」-> 「能量约猕猴桃2个」
     这类字段必须原样透出给用户，不得因为"格式异常"而丢弃或改写。
  3. 名称中含英文/引号/括号，如 "yeyeyeye奶冻款"、'「苹板」支撑Pro'，
     匹配时需做归一化（去空格、全角转半角、忽略大小写与引号差异）。

字段顺序（与 MCP 返回一致）：
productName, nutritionDescription, energyKj, energyKcal,
protein, fat, carbohydrate, sodium, calcium
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 原始数据（逐字来自 list-nutrition-foods 返回，未做任何数值修正）
# ---------------------------------------------------------------------------
RAW = """猪柳麦满分,null,1288,308,16,16,24,781,213
猪柳蛋麦满分,null,1618,387,23,21,25,846,243
烟肉蛋麦满分,null,1089,260,16,10,24,483,238
火腿扒麦满分,null,1092,261,14,12,24,608,131
中薯条,null,1210,289,4,12,38,165,18
大薯条,null,1587,379,6,16,50,216,23
小薯条,null,877,210,3,9,28,120,13
迷你薯条,null,444,106,2,4,14,61,6
圆筒冰淇淋,null,391,93,2,3,14,36,77
麦乐鸡5块,null,890,213,12,12,13,422,9
大杯玉米杯,null,365,87,4,1,12,2,10
小杯玉米杯,null,223,53,2,1,7,1,6
麦辣鸡翅-2块,null,937,224,13,15,9,537,13
朱古力新地,null,1178,282,5,9,44,126,182
草莓新地,null,1027,245,4,6,43,84,158
奥利奥麦旋风,null,1115,266,5,9,40,116,185
草莓麦旋风,null,1234,295,6,9,47,122,186
香芋派,null,971,232,2,12,28,159,7
菠萝派,null,925,221,2,11,27,147,5
大脆鸡扒麦满分,null,1510,361,16,17,35,804,130
原味板烧鸡腿麦满分,null,1029,246,15,9,25,611,133
双层原味板烧鸡腿麦满分,null,1486,355,23,17,27,984,138
吉士汉堡包,null,1231,294,16,12,30,673,144
双层吉士汉堡,null,1796,429,27,22,31,1017,232
麦香鸡,null,1546,369,15,17,39,731,73
麦香鱼,null,1359,325,16,13,35,556,99
麦辣鸡腿汉堡,null,2029,485,24,24,42,1208,154
板烧鸡腿堡,null,1638,391,23,17,35,1041,93
巨无霸,null,2146,513,27,26,42,961,171
培根蔬萃双层牛堡,null,1850,442,24,23,34,728,73
可乐中杯,null,616,147,0,0,36,0,10
可乐大杯,null,936,224,0,0,55,0,15
可乐小杯,null,446,107,0,0,26,0,7
雪碧中杯,null,440,105,0,0,26,29,0
雪碧大杯,null,669,160,0,0,39,45,0
雪碧小杯,null,319,76,0,0,19,21,0
无糖可口可乐中杯,null,0,0,0,0,1,35,0
无糖可口可乐大杯,null,0,0,0,0,1,53,0
无糖可口可乐小杯,null,0,0,0,0,0,25,0
大杯鲜萃咖啡,null,54,13,1,0,0,3,8
小杯鲜萃咖啡,null,63,15,1,0,3,2,6
锡兰红茶,null,8,2,0,0,0,0,5
热朱古力,null,525,125,2,2,22,107,36
纯牛奶（盒装）,null,541,129,7,7,10,73,231
麦旋酷阳光橙,null,955,228,1,2,50,77,52
浓缩咖啡,null,54,13,1,0,2,0,7
那么大鸡排,null,1612,385,24,21,24,996,13
阳光柠檬红茶中杯,null,488,117,0,0,29,17,0
阳光柠檬红茶大杯,null,743,178,0,0,43,25,0
阳光柠檬红茶小杯,null,354,85,0,0,21,12,0
脆薯饼,null,612,146,1,9,14,311,7
脆香油条,null,844,202,5,12,19,230,18
100%苹果汁,null,368,88,0,0,21,0,0
汉堡包,null,1039,248,13,8,29,492,61
麦乐鸡4块,null,712,170,10,10,11,337,7
纯牛奶（盒装）,null,541,129,7,7,10,73,231
纯悦,能量约清水1杯,0,0,0,0,0,0,0
苹果片,null,133,32,0,0,7,0,18
不素之霸双层牛堡,null,2062,493,28,27,34,1012,75
双层深海鳕鱼堡,null,2029,485,28,21,46,917,147
儿童鱼排堡,null,1126,269,15,6,36,442,58
培根安格斯厚牛堡,null,2959,707,34,44,43,1037,161
芝士安格斯厚牛堡,能量约鲈鱼1条,2914,696,32,44,43,897,150
yeyeyeye奶冻款,null,932,223,1,4,43,37,64
皮蛋鸡肉粥,null,558,133,6,3,20,611,20
雪菜脆笋鸡肉粥,null,504,120,4,2,22,552,14
猪柳炒双蛋堡,null,1827,437,25,24,29,705,116
yeyeyeye爆珠款,null,889,212,1,3,42,122,124
柠檬拉明顿,能量约猕猴桃2个,506,121,2,7,12,35,17
芝士双层安格斯厚牛堡,null,4195,1003,58,65,46,1373,251
培根双层安格斯厚牛堡,null,4128,987,55,64,46,1191,226
火腿扒早安营养卷,null,1857,444,16,24,39,1065,108
麦麦脆汁鸡-琵琶腿,null,1372,328,21,20,16,905,24
德式图林根香肠,null,366,87,5,6,2,293,5
图林根香肠早安营养卷,null,1779,425,14,23,38,977,68
麦麦脆汁鸡-带骨里脊,null,1389,332,22,18,16,812,25
【美汁源】“黄金橙橙”,null,691,165,0,0,41,53,0
优品豆浆大杯,null,844,202,8,4,33,43,21
优品豆浆小杯,null,633,151,6,3,25,32,15
浓浓黑巧雪冰中杯,null,1501,359,8,12,51,224,207
浓浓黑巧雪冰大杯,null,1857,444,11,14,65,258,251
特浓奶香雪冰中杯,null,1124,269,6,11,36,180,188
特浓奶香雪冰大杯,null,1291,309,7,12,42,192,223
浓浓抹茶雪冰中杯,null,1208,289,7,11,39,181,220
浓浓抹茶雪冰大杯,null,1418,339,8,12,48,194,270
热牛奶中杯,null,738,176,10,8,15,101,319
热牛奶大杯,null,1006,240,13,11,21,137,435
热牛奶小杯,null,617,147,8,7,13,84,267
冰牛奶中杯,null,939,224,13,11,19,128,406
冰牛奶大杯,null,1127,269,15,13,23,154,487
冰牛奶小杯,null,617,147,8,7,13,84,267
冰奶铁中杯,null,618,148,8,7,13,77,251
冰奶铁大杯,null,678,162,9,7,15,85,275
冰奶铁小杯,null,358,86,5,4,8,44,144
热奶铁中杯,null,779,186,11,8,17,99,320
热奶铁大杯,null,1040,249,14,11,22,134,431
热奶铁小杯,null,559,134,8,6,12,72,231
冰燕麦奶铁中杯,null,554,132,3,5,17,87,270
冰燕麦奶铁大杯,null,608,145,4,5,19,96,296
冰燕麦奶铁小杯,null,322,77,2,3,10,50,155
热燕麦奶铁中杯,null,697,167,4,6,22,112,345
热燕麦奶铁大杯,null,929,222,5,8,29,152,464
热燕麦奶铁小杯,null,500,120,3,5,16,81,248
冰美式中杯,null,54,13,1,0,2,0,7
冰美式大杯,null,60,14,1,0,2,0,8
冰美式小杯,null,41,10,1,0,1,0,6
热美式中杯,null,54,13,1,0,2,0,7
热美式大杯,null,60,14,1,0,2,0,8
热美式小杯,null,41,10,1,0,1,0,6
冰焦糖玛奇朵,null,618,148,8,7,13,77,251
热焦糖玛奇朵,null,698,167,9,7,15,88,284
卡布奇诺中杯,null,698,167,9,7,15,88,285
卡布奇诺大杯,null,932,223,13,10,20,120,385
冰浓浓燕麦黑巧中杯,null,500,120,2,5,16,87,263
冰浓浓燕麦黑巧大杯,null,547,131,3,5,17,95,388
冰燕麦奶中杯,null,833,199,4,8,26,145,438
冰燕麦奶大杯,null,1000,239,5,9,32,174,525
冰燕麦奶小杯,null,547,131,3,5,17,95,288
热燕麦奶中杯,null,655,157,3,6,21,114,344
热燕麦奶大杯,null,893,213,4,8,28,156,469
热燕麦奶小杯,null,547,131,3,5,17,95,288
冰浓浓抹茶牛奶中杯,null,821,196,8,6,25,80,275
冰浓浓抹茶牛奶大杯,null,1004,240,9,7,33,89,314
热浓浓抹茶牛奶中杯,null,996,238,11,8,29,104,351
热浓浓抹茶牛奶大杯,null,1393,333,14,12,41,142,482
冰浓浓黑巧中杯,null,563,135,8,6,12,77,244
冰浓浓黑巧大杯,null,617,147,8,7,13,84,267
热浓浓黑巧中杯,null,738,176,10,8,15,101,319
热浓浓黑巧大杯,null,1006,240,13,11,21,137,435
川宁伯爵红茶,null,7,2,0,0,0,0,0
抹茶阿芙佳朵,null,613,147,3,4,23,50,136
咖啡阿芙佳朵,null,583,139,4,4,21,49,111
热浓浓燕麦黑巧中杯,null,655,157,3,6,21,114,344
热浓浓燕麦黑巧大杯,null,893,213,4,8,28,156,469
草莓拉明顿,null,507,121,2,8,11,34,17
原味板烧鸡腿炒双蛋堡,null,1754,419,27,21,30,715,116
双层猪柳蛋麦满分,null,2148,513,29,32,26,1210,249
双层火腿扒麦满分,null,1611,385,21,22,26,979,134
脆脆薯条,null,1168,279,4,14,32,540,13
麦麦趣鸡球,null,1113,266,17,13,19,789,13
酥酥多笋卷,null,1431,342,14,15,36,1044,41
薄皮焦香V翅,null,804,192,17,11,6,594,15
蜜汁BBQ风味薄皮脆汁鸡-带骨里脊,null,637,152,20,5,6,635,28
蜜汁BBQ风味薄皮脆汁鸡-琵琶腿,null,724,173,18,9,5,547,15
果然多肉,null,2001,478,36,17,44,1343,190
“苹板”支撑Pro,null,1993,476,25,18,50,1077,117
专“薯鱼”你,null,1936,463,18,17,57,652,123
绝代双翅,null,1964,469,32,27,23,1167,34
牛气满满,null,2073,495,26,24,42,764,79
蜜汁快乐,null,1081,258,22,9,20,721,34
迷你朱古力新地,null,586,140,3,4,22,63,90
迷你草莓新地,null,511,122,2,3,21,42,78
鲜蔬蛋沙拉叠叠卷,null,1114,266,11,14,24,518,62
鸡肉蛋沙拉叠叠卷,null,1621,387,17,21,31,842,67
中杯怡泉+C,null,461,110,0,0,27,0,0
大杯怡泉+C,null,701,168,0,0,41,0,0
蒜了吧黄油风味薄皮脆汁鸡-琵琶腿,null,688,164,19,6,6,867,9
蒜了吧黄油风味薄皮脆汁鸡-带骨里脊,null,526,126,19,2,4,750,59
柠萌风味薄皮焦香V翅,null,855,204,18,11,6,768,22"""

# 分类映射：用于「1 主食 + 0~2 小食 + 0~1 饮料」的组合约束
CATEGORY_RULES = [
    # (类别, 关键词)
    ("主食", ["麦满分", "汉堡", "堡", "卷", "粥"]),
    ("小食", ["薯条", "麦乐鸡", "鸡翅", "脆汁鸡", "鸡排", "派", "新地",
              "麦旋风", "雪冰", "薯饼", "油条", "趣鸡球", "笋卷", "脆薯饼",
              "圆筒", "玉米杯", "奶冻", "V翅", "BBQ"]),
    ("饮料", ["可乐", "雪碧", "红茶", "咖啡", "牛奶", "豆浆", "奶茶", "奶铁",
              "美式", "玛奇朵", "卡布奇诺", "阿芙佳朵", "怡泉", "果汁", "纯悦",
              "美汁源", "拉明顿", "麦旋酷"]),
]

# 饮料中的低钠优选项（用于「控卡」「均衡」模式加权，不做硬性推荐）
LOW_SODIUM_DRINKS = [
    "无糖可口可乐", "纯牛奶（盒装）", "优品豆浆", "热牛奶",
    "冰牛奶", "锡兰红茶", "川宁伯爵红茶", "大杯鲜萃咖啡", "浓缩咖啡",
    "冰美式", "热美式", "100%苹果汁",
]


def _num(s: str) -> Optional[float]:
    """宽松数字解析：容忍空串、null、'12.5'、'12.5mg' 等形态。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.lower() == "null" or s in {"-", "—"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def normalize(name: str) -> str:
    """名称归一化，用于 MCP 菜单名与营养库名之间的模糊匹配。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[「」『』\"'“”‘’()（）\[\]【】·]", "", s)
    return s.lower()


def categorize(name: str) -> str:
    """按关键词把餐品归入 主食 / 小食 / 饮料 / 其他。"""
    n = normalize(name)
    for cat, keys in CATEGORY_RULES:
        for k in keys:
            if normalize(k) in n:
                return cat
    return "其他"


def _to_float(x) -> float:
    """字段级强制数值化；脏值一律按 0 处理并在 has_data=False 时整体排除评分。"""
    v = _num(x)
    return 0.0 if v is None else v


def parse_nutrition(raw: Optional[str] = None) -> List[Dict]:
    """把 MCP list-nutrition-foods 的 data 字符串解析为结构化列表。

    raw 为 None 时使用本地 RAW 基线。
    """
    src = raw if raw is not None else RAW
    out: List[Dict] = []
    seen = set()
    for line in src.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or ":" in line:
            continue
        parts = line.split(",")
        if len(parts) < 9:
            continue
        name = parts[0].strip()
        if not name:
            continue
        key = normalize(name)
        if key in seen:      # 基线中「纯牛奶（盒装）」「小杯玉米杯」有重复，去重保留首条
            continue
        seen.add(key)
        desc = parts[1].strip()
        kcal = _num(parts[3])
        out.append({
            "productName": name,
            # null 原样保留；「能量换算说明」类内容原样透出，不做清洗
            "nutritionDescription": None if desc.lower() == "null" else desc,
            "energyKj": _to_float(parts[2]),
            "energyKcal": _to_float(parts[3]),
            "protein": _to_float(parts[4]),
            "fat": _to_float(parts[5]),
            "carbohydrate": _to_float(parts[6]),
            "sodium": _to_float(parts[7]),
            "calcium": _to_float(parts[8]),
            # has_data=False 的条目不参与营养评分，单独列出说明原因
            "has_data": kcal is not None and _num(parts[3]) is not None,
            "category": categorize(name),
            "_norm": key,
        })
    return out


class NutritionDB:
    """营养库：支持按名称精确/模糊匹配，供求解引擎使用。"""

    def __init__(self, items: Optional[List[Dict]] = None):
        self.items: List[Dict] = items if items is not None else parse_nutrition()
        self._index: Dict[str, Dict] = {it["_norm"]: it for it in self.items}
        self._by_cat: Dict[str, List[Dict]] = {}
        for it in self.items:
            self._by_cat.setdefault(it["category"], []).append(it)

    def __len__(self) -> int:
        return len(self.items)

    def all(self) -> List[Dict]:
        return self.items

    def by_category(self, cat: str) -> List[Dict]:
        return list(self._by_cat.get(cat, []))

    def match(self, menu_name: str) -> Optional[Dict]:
        """把 MCP 菜单里的商品名匹配到营养条目。

        策略：归一化精确 -> 归一化包含 -> 去「小/中/大杯」等规格词后再匹配。
        匹配不到返回 None，该餐品按「营养数据缺失」处理，不参与评分。
        """
        n = normalize(menu_name)
        if not n:
            return None
        if n in self._index:
            return self._index[n]
        # 长名优先：菜单名常包含规格后缀
        for it in self.items:
            if it["_norm"] and it["_norm"] in n:
                return it
        stripped = re.sub(r"(小|中|大|超)?(杯|块|份|个|只|包)", "", n)
        if stripped and stripped != n:
            for it in self.items:
                if it["_norm"] and (it["_norm"] in stripped or stripped in it["_norm"]):
                    return it
        return None

    def unmatched_reason(self, menu_name: str) -> str:
        return f"「{menu_name}」在营养参考数据中无对应条目"


_CACHE: Optional[NutritionDB] = None


def get_db() -> NutritionDB:
    global _CACHE
    if _CACHE is None:
        _CACHE = NutritionDB()
    return _CACHE


if __name__ == "__main__":
    db = get_db()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "data", "nutrition_foods.json")
    with open(os.path.abspath(path), "w", encoding="utf-8") as f:
        json.dump(db.all(), f, ensure_ascii=False, indent=1)
    cats = {}
    for it in db.all():
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    desc = [it for it in db.all() if it["nutritionDescription"]]
    print(f"条目数: {len(db)}")
    print(f"分类分布: {cats}")
    print(f"含 nutritionDescription 文本的条目数: {len(desc)}")
    for it in desc:
        print(f"  - {it['productName']} -> {it['nutritionDescription']}")