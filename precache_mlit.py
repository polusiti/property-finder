"""MLITデータを事前キャッシュするだけのスクリプト"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from src.mlit_api import MlitPriceModel

mlit = MlitPriceModel(api_key="c7dc344fd0ce4723ae86f228441e22ef")
stats = mlit.load(city_code="13111", years=["2022", "2023", "2024"])
print(f"完了: {mlit.summary()}")
print(f"地区別 m2単価 上位5:")
for d, s in sorted(stats.items(), key=lambda x: x[1]['median'], reverse=True)[:5]:
    print(f"  {d}: {s['median']//10000}万円/m2 ({s['count']}件)")
