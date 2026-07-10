"""物件スコアリングエンジン

5軸（価格割安度・通勤時間・ハザードリスク・物件状態・立地）で
0〜100点の総合スコアを算出する。
"""

import logging
import statistics
from typing import List, Optional

from .scraper import Property

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "price_value": 0.30,
    "commute": 0.25,
    "hazard": 0.20,
    "property_condition": 0.15,
    "location": 0.10,
}

HAZARD_SCORES = {
    "なし": 100,
    "0.5m未満": 80,
    "0.5〜3m": 50,
    "3〜5m": 25,
    "5m以上": 5,
    "unknown": 65,  # 不明はやや低め
}

STRUCTURE_SCORES = {
    "RC": 100, "鉄筋コンクリート": 100,
    "SRC": 95, "ALC": 85,
    "鉄骨造": 75, "軽量鉄骨": 65,
    "木造": 50,
    "": 70,
}


class Scorer:
    def __init__(self, weights: dict = None, max_commute_min: int = 45):
        self.weights = weights or DEFAULT_WEIGHTS
        self.max_commute_min = max_commute_min

    def score_all(self, properties: List[Property]) -> List[Property]:
        """全物件にスコアを付与して返す（スコア降順でソート済み）。"""
        if not properties:
            return []

        # エリア別の相場（円/m²）を自力計算
        area_medians = self._calc_area_medians(properties)

        for prop in properties:
            prop.score, prop.score_details = self._score_one(prop, area_medians)
            # 相場価格をセット（表示用）
            ward = self._extract_ward(prop.address)
            prop.market_price_m2 = area_medians.get(ward)

        return sorted(properties, key=lambda p: p.score or 0, reverse=True)

    def _score_one(self, prop: Property, area_medians: dict) -> tuple:
        details = {}

        # 1. 価格割安度
        details["price_value"] = self._score_price(prop, area_medians)

        # 2. 通勤時間
        details["commute"] = self._score_commute(prop.commute_minutes)

        # 3. ハザードリスク
        details["hazard"] = HAZARD_SCORES.get(prop.hazard_level or "unknown", 65)

        # 4. 物件状態
        details["property_condition"] = self._score_condition(prop)

        # 5. 立地
        details["location"] = self._score_location(prop)

        total = sum(
            details[k] * self.weights.get(k, 0) for k in details
        )
        return round(total, 1), details

    def _score_price(self, prop: Property, area_medians: dict) -> float:
        ward = self._extract_ward(prop.address)
        median_m2 = area_medians.get(ward)

        if median_m2 is None or prop.area <= 0:
            # 相場不明時：家賃の絶対値で簡易評価（安い方が高スコア）
            if prop.rent <= 60000:
                return 90
            elif prop.rent <= 90000:
                return 70
            elif prop.rent <= 130000:
                return 50
            else:
                return 30

        own_m2 = prop.rent_per_m2
        ratio = own_m2 / median_m2  # 1.0 = 相場通り

        # ratio < 1 = 割安、> 1 = 割高
        if ratio <= 0.80:
            return 100
        elif ratio <= 0.90:
            return 85
        elif ratio <= 1.00:
            return 70
        elif ratio <= 1.10:
            return 50
        elif ratio <= 1.20:
            return 30
        else:
            return 10

    def _score_commute(self, minutes: Optional[int]) -> float:
        if minutes is None:
            return 55  # 不明時は中間値

        max_m = self.max_commute_min
        if minutes <= 15:
            return 100
        elif minutes <= 25:
            return 85
        elif minutes <= 35:
            return 65
        elif minutes <= max_m:
            return 40
        else:
            # 上限超過：線形に下げる
            return max(0, 40 - (minutes - max_m) * 2)

    def _score_condition(self, prop: Property) -> float:
        # 築年数（0年=100, 50年=0）
        age = prop.age_years
        age_score = max(0, 100 - age * 2)

        # 構造
        struct_score = STRUCTURE_SCORES.get(prop.structure, 70)

        # 階数（地上3階以上で加点、1階は減点）
        if prop.floor >= 3:
            floor_score = min(100, 70 + prop.floor * 3)
        elif prop.floor == 2:
            floor_score = 65
        else:
            floor_score = 45  # 1階

        return age_score * 0.5 + struct_score * 0.3 + floor_score * 0.2

    def _score_location(self, prop: Property) -> float:
        nearest = prop.nearest_station
        if not nearest:
            return 50

        walk = nearest.walk_minutes
        if walk <= 3:
            walk_score = 100
        elif walk <= 5:
            walk_score = 90
        elif walk <= 8:
            walk_score = 75
        elif walk <= 10:
            walk_score = 60
        elif walk <= 15:
            walk_score = 40
        else:
            walk_score = 20

        return walk_score

    def _calc_area_medians(self, properties: List[Property]) -> dict:
        """ward別の家賃/m² 中央値を計算する。"""
        ward_prices: dict = {}
        for prop in properties:
            if prop.area <= 0 or prop.rent <= 0:
                continue
            ward = self._extract_ward(prop.address)
            ward_prices.setdefault(ward, []).append(prop.rent_per_m2)

        return {
            ward: statistics.median(prices)
            for ward, prices in ward_prices.items()
            if len(prices) >= 3  # サンプル3件未満は信頼性低いため除外
        }

    def _extract_ward(self, address: str) -> str:
        """住所から市区町村部分を抽出する。"""
        import re
        m = re.search(r"([一-鿿]{2,6}[都道府県])([一-鿿]{2,6}[市区町村])", address)
        if m:
            return m.group(2)
        m2 = re.search(r"([一-鿿]{2,6}[市区町村])", address)
        return m2.group(1) if m2 else "不明"
