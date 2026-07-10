"""通勤時間計算

Google Maps Transit API が利用可能な場合はそれを使用。
APIキーがない場合は駅徒歩時間ベースの推定値を返す。
"""

import logging
import math
import time
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


class CommuteCalculator:
    def __init__(self, api_key: str = "", destination: str = "渋谷駅"):
        self.api_key = api_key
        self.destination = destination
        self.has_key = bool(api_key and api_key.strip())
        self._gmaps = None

        if self.has_key:
            try:
                import googlemaps
                self._gmaps = googlemaps.Client(key=api_key)
                logger.info("Google Maps API: 有効")
            except ImportError:
                logger.warning("googlemaps パッケージ未インストール → フォールバック")
            except Exception as e:
                logger.warning(f"Google Maps API 初期化失敗: {e}")

    def get_commute_minutes(
        self,
        lat: Optional[float],
        lon: Optional[float],
        station_name: str = "",
        walk_minutes: int = 10,
    ) -> int:
        """通勤時間（分）を返す。取得失敗時は推定値。"""
        if self._gmaps and lat and lon:
            result = self._gmaps_transit(lat, lon)
            if result is not None:
                return result

        # フォールバック：最寄り駅の路線ごとの大まかな所要時間推定
        return self._estimate_commute(station_name, walk_minutes)

    def _gmaps_transit(self, lat: float, lon: float) -> Optional[int]:
        try:
            from datetime import datetime
            import pytz

            now = datetime.now(pytz.timezone("Asia/Tokyo"))
            result = self._gmaps.directions(
                origin=(lat, lon),
                destination=self.destination,
                mode="transit",
                departure_time=now,
                language="ja",
            )
            if result and result[0]["legs"]:
                duration = result[0]["legs"][0]["duration"]["value"]
                time.sleep(0.2)
                return duration // 60
        except Exception as e:
            logger.debug(f"Google Maps 通勤時間取得失敗: {e}")
        return None

    def _estimate_commute(self, station_name: str, walk_minutes: int) -> int:
        """
        駅名から目的地までの大まかな電車所要時間をヒューリスティックで推定。
        実際の路線情報は持たないため、あくまでも参考値。
        """
        # 主要ターミナル駅はほぼ 0〜10 分圏内
        major_hubs = {
            # 渋谷近辺
            "渋谷": 0, "恵比寿": 3, "代官山": 3, "中目黒": 5,
            "六本木": 5, "下北沢": 8, "目黒": 8,
            "新宿": 5, "池袋": 10, "五反田": 10,
            "品川": 8, "東京": 12, "秋葉原": 12,
            "三軒茶屋": 10, "自由が丘": 15,
            "上野": 15, "吉祥寺": 20,
            "横浜": 25, "川崎": 20, "大宮": 30, "千葉": 35,
            "町田": 35, "立川": 40,
            # 大田区・城南エリア（渋谷まで所要時間）
            "大森": 22, "蒲田": 28, "矢口渡": 30, "武蔵新田": 32,
            "下丸子": 30, "鵜の木": 28, "沼部": 26, "多摩川": 22,
            "雪が谷大塚": 25, "御嶽山": 27, "久が原": 28, "千鳥町": 30,
            "池上": 32, "蓮沼": 33, "糀谷": 32, "大鳥居": 30,
            "穴守稲荷": 32, "天空橋": 35, "羽田空港": 38,
            "西馬込": 25, "馬込": 22, "中馬込": 20,
            "旗の台": 18, "荏原中延": 20, "戸越銀座": 18,
            "大井町": 15, "大井": 17,
        }
        for hub, mins in major_hubs.items():
            if hub in station_name:
                return walk_minutes + mins

        # 都内は概ね 20〜35 分、郊外は 35〜60 分と仮定
        suburban_keywords = ["市", "町", "村"]
        if any(k in station_name for k in suburban_keywords):
            return walk_minutes + 45
        return walk_minutes + 28  # デフォルト推定
