"""住所 → 緯度経度変換（国土地理院 API / 無料・キー不要）"""

import logging
import time
from functools import lru_cache
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"


@lru_cache(maxsize=1024)
def geocode(address: str) -> Optional[Tuple[float, float]]:
    """住所文字列を (lat, lon) に変換する。失敗時は None を返す。"""
    if not address:
        return None
    try:
        resp = requests.get(
            GSI_URL,
            params={"q": address},
            timeout=10,
            headers={"User-Agent": "property-finder/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        # 最初の候補を使用
        coords = data[0]["geometry"]["coordinates"]
        lon, lat = float(coords[0]), float(coords[1])
        return lat, lon
    except Exception as e:
        logger.debug(f"ジオコード失敗 ({address}): {e}")
        return None
    finally:
        time.sleep(0.3)  # 国土地理院への礼儀


def geocode_batch(addresses: list, progress_callback=None) -> list:
    """住所リストをまとめてジオコードする。戻り値は (lat, lon) または None のリスト。"""
    results = []
    for i, addr in enumerate(addresses):
        results.append(geocode(addr))
        if progress_callback and i % 5 == 0:
            progress_callback(f"住所変換: {i+1}/{len(addresses)}")
    return results
