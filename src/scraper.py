"""SUUMO 賃貸物件スクレイパー（関東向け）"""

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CURRENT_YEAR = 2026

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://suumo.jp/",
}

PREFECTURE_TA = {
    "東京都": "13",
    "神奈川県": "14",
    "埼玉県": "11",
    "千葉県": "12",
    "茨城県": "08",
    "栃木県": "09",
    "群馬県": "10",
}


@dataclass
class StationInfo:
    line: str
    name: str
    walk_minutes: int


@dataclass
class Property:
    id: str
    name: str
    address: str
    prefecture: str
    stations: List[StationInfo]
    rent: int           # 円
    admin_fee: int      # 管理費（円）
    deposit: int        # 敷金（円）
    key_money: int      # 礼金（円）
    floor_plan: str     # 間取り
    area: float         # 専有面積（m²）
    floor: int
    total_floors: int
    built_year: int
    structure: str
    url: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    score: Optional[float] = None
    score_details: Optional[dict] = None
    commute_minutes: Optional[int] = None
    hazard_level: Optional[str] = None
    market_price_m2: Optional[float] = None  # 周辺相場（円/m²）
    bargain_score: Optional[float] = None    # 掘り出し物スコア（0-100）
    bargain_details: Optional[dict] = None   # 掘り出し物スコア内訳

    @property
    def total_monthly(self) -> int:
        return self.rent + self.admin_fee

    @property
    def age_years(self) -> int:
        return max(0, CURRENT_YEAR - self.built_year) if self.built_year > 0 else 0

    @property
    def rent_per_m2(self) -> float:
        return self.rent / self.area if self.area > 0 else 0

    @property
    def nearest_station(self) -> Optional[StationInfo]:
        return min(self.stations, key=lambda s: s.walk_minutes) if self.stations else None


class SuumoScraper:
    BASE_URL = "https://suumo.jp"
    SEARCH_URL = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"

    def __init__(self, delay_range=(2.5, 5.0)):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay_range = delay_range

    def search(
        self,
        prefecture: str,
        max_rent_man: float = 12.0,
        min_area: float = 20.0,
        max_walk_min: int = 15,
        max_age_years: int = 9999999,
        max_pages: int = 3,
        ward_codes: List[str] = None,
        progress_callback=None,
    ) -> List[Property]:
        ta = PREFECTURE_TA.get(prefecture, "13")

        # SUUMO の et: 離散値のみ有効 (3/5/7/10/15)
        VALID_ET = [3, 5, 7, 10, 15]
        et_val = min((e for e in VALID_ET if e >= max_walk_min), default=15)

        # SUUMO の mb: 離散値のみ有効 (15/20/25/30/35/40/45/50/55/60/65/70/80/90/100)
        VALID_MB = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100]
        mb_val = min((m for m in VALID_MB if m >= int(min_area)), default=15)

        params = {
            "ar": "030",
            "bs": "040",
            "ta": ta,
            "cb": "0.0",
            "ct": f"{max_rent_man:.1f}",
            "et": str(et_val),
            "mb": str(mb_val),
            "mt": "9999999",
            "pc": "30",
        }
        # 築年数フィルタ: 実際の有効値のみ設定（9999など無効値はSUUMOがエラーを返す）
        if max_age_years < 50:
            params["cn"] = str(max_age_years)
        # 区・市コード指定（例: 大田区=13111）単区の場合はそのまま、複数は後処理
        ward_codes = ward_codes or []

        results = []
        for page in range(1, max_pages + 1):
            params["page"] = str(page)
            base = urlencode(params)
            # 区コードは sc=xxx 形式で追記（複数の場合は繰り返す）
            if ward_codes:
                sc_part = "&".join(f"sc={c}" for c in ward_codes)
                url = f"{self.SEARCH_URL}?{base}&{sc_part}"
            else:
                url = f"{self.SEARCH_URL}?{base}"
            logger.info(f"スクレイピング: {prefecture} p{page}")

            if progress_callback:
                progress_callback(f"{prefecture} {page}ページ目を取得中...")

            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"リクエスト失敗 ({prefecture} p{page}): {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            items = self._parse_page(soup, prefecture)

            if not items:
                logger.info(f"  → 取得なし（終了）")
                break

            results.extend(items)
            logger.info(f"  → {len(items)} 件取得（累計: {len(results)} 件）")

            if not self._has_next_page(soup):
                break

            time.sleep(random.uniform(*self.delay_range))

        return results

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        for a in soup.select(".pagination-parts a, .paginationWrapper a"):
            if "次へ" in a.get_text() or ">" in a.get_text():
                return True
        return False

    def _parse_page(self, soup: BeautifulSoup, prefecture: str) -> List[Property]:
        props = []
        for item in soup.select("div.cassetteitem"):
            try:
                props.extend(self._parse_item(item, prefecture))
            except Exception as e:
                logger.debug(f"物件パース失敗: {e}")
        return props

    def _parse_item(self, item: BeautifulSoup, prefecture: str) -> List[Property]:
        name = self._text(item, ".cassetteitem_content-title") or "不明"
        address = self._text(item, ".cassetteitem_detail-col1") or ""
        stations = self._parse_stations(item)
        built_year, structure = self._parse_building_info(item)

        props = []
        # tbodyがある場合もない場合も対応
        rows = item.select("table.cassetteitem_other tbody tr")
        if not rows:
            rows = item.select("table.cassetteitem_other tr")
        for row in rows:
            prop = self._parse_room(row, name, address, prefecture, stations, built_year, structure)
            if prop:
                props.append(prop)
        return props

    def _parse_stations(self, item: BeautifulSoup) -> List[StationInfo]:
        stations = []
        col2 = item.select_one(".cassetteitem_detail-col2")
        if not col2:
            return stations
        for div in col2.select("div"):
            s = self._parse_station_text(div.get_text(strip=True))
            if s:
                stations.append(s)
        return stations

    def _parse_station_text(self, text: str) -> Optional[StationInfo]:
        walk_m = re.search(r"(?:歩|徒歩)\s*(\d+)\s*分", text)
        if not walk_m:
            return None
        walk = int(walk_m.group(1))

        station_m = re.search(r"([^\s/　]+駅)", text)
        station = station_m.group(1) if station_m else "不明駅"

        line_m = re.match(r"(.+?)[\s/　]", text)
        line = line_m.group(1).strip() if line_m else ""

        return StationInfo(line=line, name=station, walk_minutes=walk)

    def _parse_building_info(self, item: BeautifulSoup):
        col3 = item.select_one(".cassetteitem_detail-col3")
        text = col3.get_text(strip=True) if col3 else ""

        built_year = 0
        if "新築" in text:
            built_year = CURRENT_YEAR
        else:
            age_m = re.search(r"築(\d+)年", text)
            if age_m:
                built_year = CURRENT_YEAR - int(age_m.group(1))

        struct_m = re.search(r"(木造|軽量鉄骨|鉄骨造|RC|鉄筋コンクリート|SRC|ALC)", text)
        structure = struct_m.group(1) if struct_m else ""

        return built_year, structure

    def _parse_room(
        self, row, name, address, prefecture, stations, built_year, structure
    ) -> Optional[Property]:
        try:
            cells = row.select("td")
            if len(cells) < 6:
                return None

            # td[2] = 階数（例: "2階"）
            floor_text = cells[2].get_text(strip=True) if len(cells) > 2 else "1階"
            floor = self._parse_floor_num(floor_text)
            total_m = re.search(r"/\s*(\d+)階建", floor_text)
            total_floors = int(total_m.group(1)) if total_m else floor

            # td[3] = 賃料 / 管理費
            rent = self._parse_yen(
                self._text(row, ".cassetteitem_price--rent") or
                self._text(row, ".cassetteitem_other-emphasis") or "0"
            )
            admin = self._parse_yen(
                self._text(row, ".cassetteitem_price--administration") or "0"
            )

            # td[4] = 敷金 / 礼金
            deposit = self._parse_yen(
                self._text(row, ".cassetteitem_price--deposit") or "0"
            )
            key_m = self._parse_yen(
                self._text(row, ".cassetteitem_price--gratuity") or "0"
            )

            # td[5] = 間取り / 面積
            floor_plan = self._text(row, ".cassetteitem_madori") or ""
            area = self._parse_area(
                self._text(row, ".cassetteitem_menseki") or "0"
            )

            # 詳細リンク
            link = (
                row.select_one("a.js-cassette_link_href") or
                row.select_one("a.cassetteitem_other-linktext") or
                row.select_one("a[href*='/chintai/']")
            )
            url = urljoin(self.BASE_URL, link["href"]) if link and link.get("href") else ""

            if rent == 0 or not url:
                return None

            prop_id = hashlib.md5(url.encode()).hexdigest()[:12]

            return Property(
                id=prop_id,
                name=name,
                address=address,
                prefecture=prefecture,
                stations=stations,
                rent=rent,
                admin_fee=admin,
                deposit=deposit,
                key_money=key_m,
                floor_plan=floor_plan,
                area=area,
                floor=floor,
                total_floors=total_floors,
                built_year=built_year,
                structure=structure,
                url=url,
            )
        except Exception as e:
            logger.debug(f"部屋行パース失敗: {e}")
            return None

    # --- helpers ---

    def _text(self, el, selector: str) -> str:
        found = el.select_one(selector)
        return found.get_text(strip=True) if found else ""

    def _parse_floor_num(self, text: str) -> int:
        m = re.search(r"(-?\d+)階", text)
        return int(m.group(1)) if m else 1

    def _parse_yen(self, text: str) -> int:
        text = re.sub(r"[,\s]", "", text)
        man = re.search(r"([\d.]+)万", text)
        if man:
            return int(float(man.group(1)) * 10000)
        yen = re.search(r"(\d+)円", text)
        if yen:
            return int(yen.group(1))
        return 0

    def _parse_area(self, text: str) -> float:
        m = re.search(r"([\d.]+)", text)
        return float(m.group(1)) if m else 0.0
