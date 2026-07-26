"""未ジオコード物件をPhoton APIで再ジオコーディングするスクリプト"""
import json, time, re, requests, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
CACHE_DIR = Path(__file__).parent / ".run_cache"

WARD_MAP = {"大田区": "Ota-ku", "目黒区": "Meguro-ku"}
TOWN_MAP = {
    "東蒲田": "Higashi-Kamata", "西蒲田": "Nishi-Kamata", "南蒲田": "Minami-Kamata",
    "蒲田": "Kamata", "大森北": "Omori-kita", "大森南": "Omori-minami",
    "大森東": "Omori-higashi", "大森西": "Omori-nishi", "大森": "Omori",
    "山王": "Sanno", "池上": "Ikegami", "雪が谷大塚": "Yukigaya-Otsuka",
    "御嶽山": "Mitakeyama", "久が原": "Kugahara", "千鳥": "Chidori",
    "下丸子": "Shimo-Maruko", "上池台": "Kami-Ikeda", "北千束": "Kita-Senzoku",
    "南千束": "Minami-Senzoku", "長原": "Nagahara", "洗足池": "Senzokuike",
    "石川台": "Ishikawadai", "鵜の木": "Unoki", "沼部": "Numabe",
    "多摩川": "Tamagawa", "田園調布": "Denenchofu", "矢口": "Yaguchi",
    "蓮沼": "Hasunuma", "東糀谷": "Higashi-Kojiya", "糀谷": "Kojiya",
    "羽田": "Haneda", "大鳥居": "Otorii", "萩中": "Haginaka",
    "南馬込": "Minami-Magome", "北馬込": "Kita-Magome", "中馬込": "Naka-Magome",
    "東馬込": "Higashi-Magome", "西馬込": "Nishi-Magome", "馬込": "Magome",
    "旗の台": "Hatanodai", "荏原": "Ebara", "戸越": "Togoshi",
    "仲池上": "Naka-Ikegami", "東矢口": "Higashi-Yaguchi", "中央": "Chuo",
    "中目黒": "Nakameguro", "上目黒": "Kamimeguro", "下目黒": "Shimomeguro",
    "目黒本町": "Meguro-Honcho", "青葉台": "Aobadai", "駒場": "Komaba",
    "大橋": "Ohashi", "東山": "Higashiyama", "祐天寺": "Yutenji",
    "中央町": "Chuocho", "碑文谷": "Himonoya", "原町": "Haramachi",
    "洗足": "Senzoku", "緑が丘": "Midorigaoka", "自由が丘": "Jiyugaoka",
    "柿の木坂": "Kakinokizaka", "八雲": "Yakumo", "平町": "Tairacho",
    "大岡山": "Ookayama", "鷹番": "Takaban", "五本木": "Gohongi",
    "三田": "Mita", "目黒": "Meguro",
}


def translate(address: str):
    ward_en = next((en for jp, en in WARD_MAP.items() if jp in address), None)
    if not ward_en:
        return None
    town_en = next((en for jp, en in TOWN_MAP.items() if jp in address), None)
    num_m = re.search(r"(\d+)", address[address.index("区") + 1:] if "区" in address else address)
    num = num_m.group(1) if num_m else ""
    if town_en:
        return f"{town_en} {num}, {ward_en}, Tokyo".strip()
    return f"{ward_en}, Tokyo"


def photon_geocode(query: str):
    r = requests.get(
        "https://photon.komoot.io/api/",
        params={"q": query, "limit": 1, "bbox": "139.55,35.50,139.85,35.72"},
        headers={"User-Agent": "property-finder/1.0"},
        timeout=10,
    )
    feats = r.json().get("features", [])
    if feats:
        c = feats[0]["geometry"]["coordinates"]
        return float(c[1]), float(c[0])
    return None


def main():
    with open(CACHE_DIR / "step2_geo.json", encoding="utf-8") as f:
        data = json.load(f)

    no_geo = [i for i, d in enumerate(data) if not d.get("lat")]
    logging.info(f"未ジオコード: {len(no_geo)}/{len(data)}件")

    ok = 0
    for count, idx in enumerate(no_geo):
        d = data[idx]
        q = translate(d["address"])
        if not q:
            continue
        try:
            result = photon_geocode(q)
            if result:
                data[idx]["lat"], data[idx]["lon"] = result
                ok += 1
            time.sleep(0.4)
        except Exception as e:
            logging.warning(f"失敗: {d['address'][:20]} - {e}")

        if (count + 1) % 100 == 0:
            logging.info(f"進捗: {count+1}/{len(no_geo)} 成功={ok}")
            with open(CACHE_DIR / "step2_geo.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

    with open(CACHE_DIR / "step2_geo.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logging.info(f"完了: {ok}/{len(no_geo)}件 ジオコード成功")


if __name__ == "__main__":
    main()
