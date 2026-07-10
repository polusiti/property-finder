"""BIT（不動産競売物件情報サイト）スクレイパー

https://www.bit.sikkou.jp/ — 裁判所が運営する競売物件情報。
・情報封鎖ゼロ：物件詳細・写真・評価書が全て公開
・評価額の60〜80%で落札できるケースあり
・仲介業者なし＝手数料ゼロ

関東の主要地方裁判所をカバーする。
"""

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BIT_BASE = "https://www.bit.sikkou.jp"
BIT_SEARCH = "https://www.bit.sikkou.jp/app/sas/mks/010/MKS010S00.action"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Referer": BIT_BASE,
}

# 関東の地方裁判所コード
COURTS_KANTO = {
    "東京地裁": "010101",
    "横浜地裁": "010301",
    "さいたま地裁": "011101",
    "千葉地裁": "011201",
    "水戸地裁": "010801",
    "宇都宮地裁": "010901",
    "前橋地裁": "011001",
}

# 物件種別コード
PROPERTY_TYPES = {
    "マンション等": "B",
    "土地": "C",
    "一戸建て等": "D",
}


@dataclass
class AuctionProperty:
    id: str
    case_number: str       # 事件番号
    property_type: str     # 種別
    address: str
    court: str             # 裁判所名
    min_bid: int           # 最低売却価額（円）
    assessed_value: int    # 評価額（円）
    area: float            # 専有面積（m²）
    floor_plan: str
    built_year: int
    url: str
    bid_start: str
    bid_end: str
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def discount_rate(self) -> float:
        """評価額に対する割引率（大きいほど割安）"""
        if self.assessed_value <= 0:
            return 0.0
        return 1.0 - (self.min_bid / self.assessed_value)

    @property
    def discount_pct(self) -> str:
        return f"{self.discount_rate * 100:.0f}% OFF"

    @property
    def estimated_monthly_cost(self) -> int:
        """購入した場合の月額換算（ローン35年・金利1.5%想定）"""
        if self.min_bid <= 0:
            return 0
        monthly_rate = 0.015 / 12
        n = 35 * 12
        if monthly_rate == 0:
            return self.min_bid // n
        payment = self.min_bid * monthly_rate * (1 + monthly_rate) ** n
        payment /= (1 + monthly_rate) ** n - 1
        return int(payment)


class BITScraper:
    def __init__(self, delay_range=(3.0, 6.0)):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay_range = delay_range

    def search_kanto(
        self,
        property_types: Optional[List[str]] = None,
        max_pages: int = 3,
        progress_callback=None,
    ) -> List[AuctionProperty]:
        """関東全域の競売物件を取得する。"""
        if property_types is None:
            property_types = ["マンション等", "一戸建て等"]

        all_props = []
        for court_name, court_code in COURTS_KANTO.items():
            for pt_name in property_types:
                pt_code = PROPERTY_TYPES.get(pt_name, "B")
                if progress_callback:
                    progress_callback(f"{court_name} / {pt_name} を検索中...")
                props = self._search_court(
                    court_code, court_name, pt_code, pt_name, max_pages
                )
                all_props.extend(props)
                logger.info(f"{court_name}/{pt_name}: {len(props)} 件")
                time.sleep(random.uniform(*self.delay_range))

        return all_props

    def _search_court(
        self, court_code: str, court_name: str, pt_code: str, pt_name: str, max_pages: int
    ) -> List[AuctionProperty]:
        props = []
        for page in range(1, max_pages + 1):
            params = {
                "saibanshoCd": court_code,
                "shurui": pt_code,
                "page": str(page),
            }
            try:
                resp = self.session.get(BIT_SEARCH, params=params, timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                page_props = self._parse_list(soup, court_name, pt_name)
                if not page_props:
                    break
                props.extend(page_props)
                if not self._has_next(soup):
                    break
                time.sleep(random.uniform(*self.delay_range))
            except Exception as e:
                logger.warning(f"BIT取得失敗 ({court_name} p{page}): {e}")
                break
        return props

    def _parse_list(self, soup: BeautifulSoup, court_name: str, pt_name: str) -> List[AuctionProperty]:
        props = []

        # BITのリスト行を探す（テーブル形式）
        rows = soup.select("table.bukken-list tr, tr.bukken-row, .listTable tr")
        if not rows:
            # フォールバック：汎用テーブル行
            rows = soup.select("table tr")[1:]  # ヘッダーを除く

        for row in rows:
            try:
                prop = self._parse_row(row, court_name, pt_name)
                if prop:
                    props.append(prop)
            except Exception as e:
                logger.debug(f"BIT行パース失敗: {e}")

        return props

    def _parse_row(self, row: BeautifulSoup, court_name: str, pt_name: str) -> Optional[AuctionProperty]:
        cells = row.select("td")
        if len(cells) < 5:
            return None

        texts = [c.get_text(strip=True) for c in cells]

        # 事件番号
        case_number = texts[0] if texts else ""
        if not re.search(r"\d{4}.*\d+", case_number):
            return None  # ヘッダー行などをスキップ

        # リンク
        link = row.select_one("a[href*='MKS']")
        url = urljoin(BIT_BASE, link["href"]) if link and link.get("href") else ""

        # 住所（セル位置はサイト構造に依存）
        address = self._find_address(texts)
        if not address:
            return None

        # 価格系
        min_bid = self._parse_price_from_texts(texts, ["最低", "売却", "入札"])
        assessed = self._parse_price_from_texts(texts, ["評価", "鑑定"])

        # 面積
        area = self._parse_area_from_texts(texts)

        # 築年
        built_year = self._parse_year_from_texts(texts)

        # 入札期間
        bid_start, bid_end = self._parse_bid_period(texts)

        prop_id = hashlib.md5(f"{case_number}{court_name}".encode()).hexdigest()[:12]

        return AuctionProperty(
            id=prop_id,
            case_number=case_number,
            property_type=pt_name,
            address=address,
            court=court_name,
            min_bid=min_bid,
            assessed_value=assessed,
            area=area,
            floor_plan="",
            built_year=built_year,
            url=url or BIT_SEARCH,
            bid_start=bid_start,
            bid_end=bid_end,
        )

    def _has_next(self, soup: BeautifulSoup) -> bool:
        for a in soup.select("a"):
            if "次へ" in a.get_text() or "次ページ" in a.get_text():
                return True
        return False

    # --- パースヘルパー ---

    def _find_address(self, texts: list) -> str:
        for t in texts:
            if re.search(r"[都道府県市区町村]", t) and len(t) > 5:
                return t
        return ""

    def _parse_price_from_texts(self, texts: list, keywords: list) -> int:
        for t in texts:
            if any(k in t for k in keywords):
                man = re.search(r"([\d,]+)万", t.replace(",", ""))
                if man:
                    return int(man.group(1).replace(",", "")) * 10000
                oku = re.search(r"([\d.]+)億", t)
                if oku:
                    return int(float(oku.group(1)) * 1e8)
                yen = re.search(r"([\d,]+)円", t.replace(",", ""))
                if yen:
                    return int(yen.group(1).replace(",", ""))
        # フォールバック：数字が大きいセルを価格と見なす
        for t in texts:
            nums = re.findall(r"[\d,]+", t.replace(",", ""))
            for n in nums:
                val = int(n.replace(",", ""))
                if 1000000 < val < 500000000:
                    return val
        return 0

    def _parse_area_from_texts(self, texts: list) -> float:
        for t in texts:
            m = re.search(r"([\d.]+)\s*m[²2]", t)
            if m:
                return float(m.group(1))
        return 0.0

    def _parse_year_from_texts(self, texts: list) -> int:
        for t in texts:
            m = re.search(r"(昭和|平成|令和)(\d+)年", t)
            if m:
                era, year = m.group(1), int(m.group(2))
                if era == "昭和":
                    return 1925 + year
                elif era == "平成":
                    return 1988 + year
                elif era == "令和":
                    return 2018 + year
            m2 = re.search(r"(\d{4})年", t)
            if m2 and 1900 < int(m2.group(1)) < 2030:
                return int(m2.group(1))
        return 0

    def _parse_bid_period(self, texts: list) -> tuple:
        dates = []
        for t in texts:
            m = re.search(r"(\d{4}[/\-年]\d{1,2}[/\-月]\d{1,2})", t)
            if m:
                dates.append(m.group(1))
        if len(dates) >= 2:
            return dates[0], dates[1]
        elif len(dates) == 1:
            return dates[0], ""
        return "", ""
