"""Wayback Machine (archive.org) 連携

SUUMOの物件URLを archive.org CDX API で照合し、
・初回掲載日（いつから出ているか）
・掲載日数（長いほど交渉力が高い）
・家賃の変遷（過去に値下げがあったか）
を取得する。

業者しか持っていない「掲載期間」情報を無料で得る手段。
"""

import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CDX_API = "http://web.archive.org/cdx/search/cdx"
WAYBACK_BASE = "http://web.archive.org/web"

NEGOTIATION_THRESHOLDS = {
    60: ("🔴 強（60日超）", "家賃5〜10%値下げ交渉可。フリーレント3ヶ月も視野に。"),
    30: ("🟠 中（30〜60日）", "管理費無料・礼金ゼロ交渉が通りやすい。"),
    14: ("🟡 やや有（14〜30日）", "フリーレント1ヶ月程度は交渉余地あり。"),
    0:  ("🟢 弱（14日未満）", "出たばかり。相場通りが基本。"),
}


def get_listing_age(suumo_url: str) -> dict:
    """
    Wayback Machine でSUUMO物件URLの掲載履歴を調べる。

    Returns:
        {
            "first_seen": datetime | None,
            "days_on_market": int | None,
            "snapshot_count": int,
            "negotiation_label": str,
            "negotiation_tip": str,
        }
    """
    result = {
        "first_seen": None,
        "days_on_market": None,
        "snapshot_count": 0,
        "negotiation_label": "🔍 不明",
        "negotiation_tip": "Wayback Machine に記録なし",
    }

    try:
        # CDX API: 最古のスナップショット1件を取得
        params = {
            "url": suumo_url,
            "output": "json",
            "fl": "timestamp,statuscode",
            "filter": "statuscode:200",
            "limit": "1",
            "from": "20220101",
            "fastLatest": "true",
        }
        resp = requests.get(CDX_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if len(data) < 2:
            return result

        first_ts = data[1][0]
        first_seen = datetime.strptime(first_ts, "%Y%m%d%H%M%S")
        days = (datetime.now() - first_seen).days

        result["first_seen"] = first_seen
        result["days_on_market"] = days

        # スナップショット総数を取得（掲載頻度の指標）
        count_resp = requests.get(
            CDX_API,
            params={**params, "limit": "200", "fl": "timestamp"},
            timeout=15,
        )
        count_data = count_resp.json()
        result["snapshot_count"] = max(0, len(count_data) - 1)

        # 交渉力ラベルを決定
        for threshold, (label, tip) in sorted(
            NEGOTIATION_THRESHOLDS.items(), reverse=True
        ):
            if days >= threshold:
                result["negotiation_label"] = label
                result["negotiation_tip"] = tip
                break

        time.sleep(1.0)

    except Exception as e:
        logger.debug(f"Wayback Machine 照合失敗 ({suumo_url}): {e}")

    return result


def get_price_history(suumo_url: str, max_snapshots: int = 5) -> list:
    """
    過去のスナップショットから家賃の変遷を取得する。
    処理が重いため、掲載日数が長い物件にだけ使用すること。

    Returns:
        [(datetime, rent_yen), ...]  時系列順
    """
    history = []

    try:
        # スナップショットのタイムスタンプ一覧を取得
        params = {
            "url": suumo_url,
            "output": "json",
            "fl": "timestamp",
            "filter": "statuscode:200",
            "limit": str(max_snapshots * 3),
            "from": "20220101",
        }
        resp = requests.get(CDX_API, params=params, timeout=15)
        data = resp.json()
        timestamps = [row[0] for row in data[1:]]

        if not timestamps:
            return history

        # 均等に間引く（max_snapshots件）
        step = max(1, len(timestamps) // max_snapshots)
        sampled = timestamps[::step][:max_snapshots]

        for ts in sampled:
            archived_url = f"{WAYBACK_BASE}/{ts}/{suumo_url}"
            try:
                snap = requests.get(archived_url, timeout=20)
                rent = _extract_rent_from_html(snap.text)
                if rent:
                    dt = datetime.strptime(ts, "%Y%m%d%H%M%S")
                    history.append((dt, rent))
                time.sleep(2.0)
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"価格履歴取得失敗: {e}")

    return sorted(history, key=lambda x: x[0])


def _extract_rent_from_html(html: str) -> Optional[int]:
    """SUUMOアーカイブページから家賃を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")

    # 賃料要素を複数パターンで探す
    for selector in [
        ".property_view_main-emphasis",
        ".detailbox-property--emphasis",
        "[class*='rent']",
        "[class*='chintai']",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True)
            man = re.search(r"([\d.]+)万", text)
            if man:
                return int(float(man.group(1)) * 10000)

    return None


def batch_check(urls: list, progress_callback=None) -> dict:
    """
    URLリストをまとめてWayback Machine に照合する。
    Returns: {url: result_dict}
    """
    results = {}
    for i, url in enumerate(urls):
        results[url] = get_listing_age(url)
        if progress_callback:
            progress_callback(f"掲載履歴を確認中: {i+1}/{len(urls)}")
    return results
