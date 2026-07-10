"""国土交通省 不動産情報ライブラリ API クライアント

APIキーは https://www.reinfolib.mlit.go.jp/api/request/ から無料申請。
キーがない場合はフォールバック値を返す。
"""

import logging
import time
from functools import lru_cache
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"

# 市区町村コード（関東主要エリア）
CITY_CODES = {
    # 東京都特別区
    "千代田区": "13101", "中央区": "13102", "港区": "13103",
    "新宿区": "13104", "文京区": "13105", "台東区": "13106",
    "墨田区": "13107", "江東区": "13108", "品川区": "13109",
    "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
    "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
    "豊島区": "13116", "北区": "13117", "荒川区": "13118",
    "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
    "葛飾区": "13122", "江戸川区": "13123",
    # 東京都市部
    "八王子市": "13201", "立川市": "13202", "武蔵野市": "13203",
    "三鷹市": "13204", "府中市": "13206", "調布市": "13208",
    "町田市": "13209",
    # 神奈川県
    "横浜市": "14100", "川崎市": "14130", "相模原市": "14150",
    "横須賀市": "14201", "平塚市": "14203", "鎌倉市": "14204",
    "藤沢市": "14205", "小田原市": "14206",
    # 埼玉県
    "さいたま市": "11100", "川越市": "11201", "熊谷市": "11202",
    "川口市": "11203", "所沢市": "11206", "越谷市": "11220",
    # 千葉県
    "千葉市": "12100", "市川市": "12203", "船橋市": "12204",
    "松戸市": "12207", "柏市": "12217", "浦安市": "12227",
}

# ハザードレベル変換
HAZARD_LEVEL_MAP = {
    "0": "なし",
    "1": "0.5m未満",
    "2": "0.5〜3m",
    "3": "3〜5m",
    "4": "5m以上",
}


class ReinfilibClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.has_key = bool(api_key and api_key.strip())
        if not self.has_key:
            logger.info("reinfolib APIキーなし → フォールバックモードで動作")

    def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        if not self.has_key:
            return None
        try:
            resp = requests.get(
                f"{BASE_URL}/{endpoint}",
                params=params,
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"reinfolib API 失敗 ({endpoint}): {e}")
            return None
        finally:
            time.sleep(0.5)

    def get_transaction_price_stats(self, address: str) -> Optional[dict]:
        """住所から市区町村を特定し、直近2年の取引価格の中央値（円/m²）を返す。"""
        city_code = self._resolve_city_code(address)
        if not city_code:
            return None

        raw = self._get(
            "XIT001",
            {
                "year": "2024",
                "area": city_code,
                "priceClassification": "02",  # 中古マンション等
                "language": "ja",
            },
        )
        if not raw or "data" not in raw:
            return None

        prices_per_m2 = []
        for item in raw["data"]:
            try:
                price = float(item.get("TradePrice", 0))
                area = float(item.get("Area", 0))
                if price > 0 and area > 0:
                    prices_per_m2.append(price / area)
            except (ValueError, TypeError):
                continue

        if not prices_per_m2:
            return None

        prices_per_m2.sort()
        median = prices_per_m2[len(prices_per_m2) // 2]
        return {
            "median_price_m2": median,
            "sample_count": len(prices_per_m2),
            "city_code": city_code,
        }

    def get_hazard_level(self, lat: float, lon: float) -> str:
        """指定座標の洪水浸水想定レベルを返す。取得不可の場合は 'unknown'。"""
        if not self.has_key or lat is None or lon is None:
            return "unknown"

        raw = self._get(
            "XPT001",
            {
                "lat": str(lat),
                "lon": str(lon),
                "item": "flood",
            },
        )
        if not raw:
            return "unknown"

        try:
            level = str(raw.get("floodDepth", "0"))
            return HAZARD_LEVEL_MAP.get(level, "unknown")
        except Exception:
            return "unknown"

    def _resolve_city_code(self, address: str) -> Optional[str]:
        for city, code in CITY_CODES.items():
            if city in address:
                return code
        return None
