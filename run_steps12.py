"""ステップ1(スクレイピング) + ステップ2(ジオコーディング) だけ実行してキャッシュ保存"""
import json, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from src.scraper import SuumoScraper, Property, StationInfo
from src.geocoder import geocode

CACHE_DIR = Path(__file__).parent / ".run_cache"
CACHE_DIR.mkdir(exist_ok=True)

def props_to_json(props):
    result = []
    for p in props:
        ns = p.nearest_station
        result.append({
            "id": p.id, "name": p.name, "url": p.url, "address": p.address,
            "rent": p.rent, "admin_fee": p.admin_fee,
            "deposit": p.deposit, "key_money": p.key_money,
            "floor_plan": p.floor_plan, "area": p.area,
            "age_years": p.age_years, "floor": p.floor, "structure": p.structure,
            "total_monthly": p.total_monthly,
            "lat": p.lat, "lon": p.lon,
            "station_name": ns.name if ns else None,
            "station_walk": ns.walk_minutes if ns else None,
            "commute_minutes": p.commute_minutes,
            "score": p.score, "score_details": p.score_details,
            "bargain_score": p.bargain_score, "bargain_details": p.bargain_details,
        })
    return result

def json_to_props(data):
    props = []
    for d in data:
        p = Property(
            id=d["id"], name=d["name"], url=d["url"], address=d["address"],
            rent=d["rent"], admin_fee=d["admin_fee"],
            deposit=d["deposit"], key_money=d["key_money"],
            floor_plan=d["floor_plan"], area=d["area"],
            age_years=d["age_years"], floor=d["floor"], structure=d["structure"],
        )
        p.lat = d.get("lat"); p.lon = d.get("lon")
        if d.get("station_name"):
            p.nearest_station = StationInfo(name=d["station_name"], walk_minutes=d["station_walk"] or 15)
        props.append(p)
    return props

# Step 1: スクレイピング
step1_file = CACHE_DIR / "step1_raw.json"
if step1_file.exists():
    print("Step1: キャッシュ使用")
    with open(step1_file, encoding="utf-8") as f:
        props = json_to_props(json.load(f))
else:
    print("Step1: SUUMOスクレイピング開始...")
    scraper = SuumoScraper(delay_range=(2.0, 4.0))
    props = scraper.search(
        prefecture="東京都", max_rent_man=15.0, min_area=20.0,
        max_walk_min=15, max_pages=3, ward_codes=["13111", "13110"],
    )
    print(f"  取得: {len(props)} 件")
    with open(step1_file, "w", encoding="utf-8") as f:
        json.dump(props_to_json(props), f, ensure_ascii=False)
    print("  保存完了")

# Step 2: ジオコーディング
step2_file = CACHE_DIR / "step2_geo.json"
if step2_file.exists():
    print("Step2: キャッシュ使用")
    with open(step2_file, encoding="utf-8") as f:
        props = json_to_props(json.load(f))
else:
    print(f"Step2: ジオコーディング ({len(props)} 件)...")
    for i, p in enumerate(props):
        r = geocode(p.address)
        if r:
            p.lat, p.lon = r
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(props)}")
            # 途中経過もファイルに保存（中断対策）
            with open(step2_file, "w", encoding="utf-8") as f:
                json.dump(props_to_json(props), f, ensure_ascii=False)
    ok = sum(1 for p in props if p.lat)
    print(f"  成功: {ok}/{len(props)} 件")
    with open(step2_file, "w", encoding="utf-8") as f:
        json.dump(props_to_json(props), f, ensure_ascii=False)
    print("  保存完了")

print("Steps 1+2 完了")
