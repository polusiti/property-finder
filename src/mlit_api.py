"""
国土交通省 不動産情報ライブラリ API クライアント

売買成約価格から地区別m2単価を算出し、賃貸の適正家賃を推計する。
東京の表面利回り4%換算: 月額適正家賃 ≈ 売買価格/m2 × 面積 × 0.04 / 12
"""

import json
import logging
import statistics
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
CACHE_DIR = Path(__file__).parent.parent / ".mlit_cache"

# 東京都大田区 表面利回り (gross yield)
# 実データ: 大田区は3.5〜5.0% → 4.0%で推計
DEFAULT_GROSS_YIELD = 0.040

# 面積帯別の利回り補正（小さいほど利回り高め）
AREA_YIELD_ADJUST = [
    (25, +0.005),   # ~25m2: +0.5pt (小型は利回り高め)
    (40, +0.002),   # ~40m2: +0.2pt
    (60,  0.000),   # ~60m2: 標準
    (float('inf'), -0.002),  # 60m2〜: -0.2pt (大型は利回り低め)
]


class MlitPriceModel:
    """
    国土交通省API から取得した実売買価格をベースに
    地区別m2単価モデルを構築し、賃貸適正家賃を推計する。
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._headers = {"Ocp-Apim-Subscription-Key": api_key}
        self._district_stats: Dict[str, dict] = {}  # district -> stats
        self._loaded = False

    # ──────────────────────────────────────────────
    # データ取得・モデル構築
    # ──────────────────────────────────────────────

    def load(self, city_code: str = "13111",
             years: List[str] = None,
             force_refresh: bool = False) -> dict:
        """
        APIから売買取引データを取得して地区別価格モデルを構築する。
        キャッシュが存在すればそれを使用（force_refresh=Trueで強制再取得）。

        Returns: {district: {count, median_price_per_m2, p25, p75}}
        """
        if years is None:
            years = ["2023", "2024"]

        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / f"mlit_{city_code}_{'_'.join(years)}.json"

        if not force_refresh and cache_file.exists():
            logger.info("MLITキャッシュから読み込み: %s", cache_file)
            with open(cache_file, "r", encoding="utf-8") as f:
                self._district_stats = json.load(f)
            self._loaded = True
            return self._district_stats

        logger.info("MLIT APIからデータ取得中 (city=%s, years=%s)...", city_code, years)
        raw: List[dict] = []
        for year in years:
            for quarter in ["1", "2", "3", "4"]:
                try:
                    items = self._fetch_quarter(city_code, year, quarter)
                    raw.extend(items)
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning("MLIT取得失敗 %sQ%s: %s", year, quarter, e)

        logger.info("MLIT 取得合計: %d件 マンション", len(raw))
        self._district_stats = self._build_stats(raw)

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(self._district_stats, f, ensure_ascii=False, indent=2)

        self._loaded = True
        return self._district_stats

    def _fetch_quarter(self, city_code: str, year: str, quarter: str) -> List[dict]:
        r = requests.get(
            f"{API_BASE}/XIT001",
            headers=self._headers,
            params={"response_format": "json", "year": year,
                    "quarter": quarter, "area": "13", "city": city_code},
            timeout=20,
        )
        r.encoding = "utf-8"
        r.raise_for_status()
        data = r.json()
        return [
            x for x in data.get("data", [])
            if x.get("Type") == "中古マンション等"
            and x.get("TradePrice")
            and x.get("Area")
        ]

    def _build_stats(self, records: List[dict]) -> dict:
        """地区名 → m2単価リストの統計を計算する。"""
        from collections import defaultdict
        district_prices: Dict[str, List[float]] = defaultdict(list)

        for rec in records:
            try:
                area = int(rec.get("Area", 0) or 0)
                price = int(rec.get("TradePrice", 0) or 0)
                district = rec.get("DistrictName", "").strip()
                if area > 10 and price > 0 and district:
                    district_prices[district].append(price / area)
            except (ValueError, TypeError):
                continue

        stats = {}
        for district, prices in district_prices.items():
            if len(prices) < 3:
                continue
            stats[district] = {
                "count": len(prices),
                "median": round(statistics.median(prices)),
                "p25": round(statistics.quantiles(prices, n=4)[0]),
                "p75": round(statistics.quantiles(prices, n=4)[2]),
                "mean": round(statistics.mean(prices)),
            }
        return stats

    # ──────────────────────────────────────────────
    # 適正家賃推計
    # ──────────────────────────────────────────────

    def lookup_district(self, address: str) -> Optional[dict]:
        """住所文字列から地区統計を引く（部分一致）。"""
        if not self._loaded or not address:
            return None
        for district, stats in self._district_stats.items():
            if district and district in address:
                return stats
        return None

    def predict_fair_rent(self, prop, yield_rate: float = None) -> Optional[float]:
        """
        売買価格ベースで適正月額家賃を推計する。

        適正家賃 = 売買価格/m2 × 面積 × 利回り / 12
        面積帯に応じて利回りを微調整。
        """
        if not self._loaded:
            return None

        stats = self.lookup_district(prop.address)
        if not stats:
            return None

        price_per_m2 = stats["median"]
        area = prop.area

        # 面積帯別利回り補正
        base_yield = yield_rate if yield_rate is not None else DEFAULT_GROSS_YIELD
        for threshold, adj in AREA_YIELD_ADJUST:
            if area <= threshold:
                base_yield += adj
                break

        estimated_asset_value = price_per_m2 * area
        monthly_rent = estimated_asset_value * base_yield / 12
        return round(monthly_rent)

    def value_gap_pct(self, prop) -> Optional[float]:
        """
        売買価格ベース適正家賃と実際の家賃の乖離率。
        正 = 実際家賃が割安、負 = 割高。
        """
        fair = self.predict_fair_rent(prop)
        if not fair or fair <= 0 or not prop.rent:
            return None
        return (fair - prop.rent) / fair * 100

    def district_rank(self, address: str) -> Optional[str]:
        """地区の価格帯ランクを返す（S/A/B/C）。"""
        stats = self.lookup_district(address)
        if not stats:
            return None
        m = stats["median"]
        if m >= 1_000_000:
            return "S"
        if m >= 850_000:
            return "A"
        if m >= 700_000:
            return "B"
        return "C"

    def summary(self) -> str:
        n = len(self._district_stats)
        total = sum(s["count"] for s in self._district_stats.values())
        return f"地区数={n}, 総取引件数={total}"
