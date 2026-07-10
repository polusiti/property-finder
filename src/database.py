"""SQLite による物件データ永続化"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .scraper import Property, StationInfo

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "properties.db"

CREATE_PROPERTIES_SQL = """
CREATE TABLE IF NOT EXISTS properties (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    address         TEXT,
    prefecture      TEXT,
    stations_json   TEXT,
    rent            INTEGER,
    admin_fee       INTEGER,
    deposit         INTEGER,
    key_money       INTEGER,
    floor_plan      TEXT,
    area            REAL,
    floor           INTEGER,
    total_floors    INTEGER,
    built_year      INTEGER,
    structure       TEXT,
    url             TEXT,
    lat             REAL,
    lon             REAL,
    score           REAL,
    score_details   TEXT,
    commute_minutes INTEGER,
    hazard_level    TEXT,
    market_price_m2 REAL,
    scraped_at      TEXT,
    is_available    INTEGER DEFAULT 1,
    first_seen      TEXT,
    days_on_market  INTEGER,
    price_history   TEXT,
    negotiation_label TEXT,
    bargain_score   REAL,
    bargain_details TEXT
);
"""

CREATE_REVIEWS_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    building_name   TEXT NOT NULL,
    building_address TEXT,
    noise_score     INTEGER CHECK(noise_score BETWEEN 1 AND 5),
    sunlight_score  INTEGER CHECK(sunlight_score BETWEEN 1 AND 5),
    management_score INTEGER CHECK(management_score BETWEEN 1 AND 5),
    neighbor_score  INTEGER CHECK(neighbor_score BETWEEN 1 AND 5),
    actual_commute_min INTEGER,
    pros            TEXT,
    cons            TEXT,
    lived_period    TEXT,
    submitted_at    TEXT NOT NULL
);
"""

CREATE_AUCTION_SQL = """
CREATE TABLE IF NOT EXISTS auction_properties (
    id              TEXT PRIMARY KEY,
    case_number     TEXT,
    property_type   TEXT,
    address         TEXT,
    court           TEXT,
    min_bid         INTEGER,
    assessed_value  INTEGER,
    area            REAL,
    floor_plan      TEXT,
    built_year      INTEGER,
    url             TEXT,
    bid_start       TEXT,
    bid_end         TEXT,
    lat             REAL,
    lon             REAL,
    fetched_at      TEXT
);
"""


@dataclass
class Review:
    id: Optional[int]
    building_name: str
    building_address: str
    noise_score: int
    sunlight_score: int
    management_score: int
    neighbor_score: int
    actual_commute_min: Optional[int]
    pros: str
    cons: str
    lived_period: str
    submitted_at: str

    @property
    def avg_score(self) -> float:
        scores = [self.noise_score, self.sunlight_score,
                  self.management_score, self.neighbor_score]
        return sum(scores) / len(scores)


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.execute(CREATE_PROPERTIES_SQL)
        con.execute(CREATE_REVIEWS_SQL)
        con.execute(CREATE_AUCTION_SQL)
        # 既存テーブルへのカラム追加（マイグレーション）
        _add_column_if_missing(con, "properties", "first_seen", "TEXT")
        _add_column_if_missing(con, "properties", "days_on_market", "INTEGER")
        _add_column_if_missing(con, "properties", "price_history", "TEXT")
        _add_column_if_missing(con, "properties", "negotiation_label", "TEXT")
        _add_column_if_missing(con, "properties", "bargain_score", "REAL")
        _add_column_if_missing(con, "properties", "bargain_details", "TEXT")


def _add_column_if_missing(con, table: str, column: str, col_type: str):
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ─── 賃貸物件 ───

def upsert_properties(properties: List[Property]):
    init_db()
    now = datetime.now().isoformat()
    with _conn() as con:
        for p in properties:
            stations_json = json.dumps(
                [{"line": s.line, "name": s.name, "walk_minutes": s.walk_minutes}
                 for s in p.stations],
                ensure_ascii=False,
            )
            con.execute(
                """
                INSERT INTO properties
                    (id, name, address, prefecture, stations_json,
                     rent, admin_fee, deposit, key_money, floor_plan,
                     area, floor, total_floors, built_year, structure,
                     url, lat, lon, score, score_details, commute_minutes,
                     hazard_level, market_price_m2, scraped_at, is_available,
                     first_seen, days_on_market, price_history, negotiation_label,
                     bargain_score, bargain_details)
                VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    score             = excluded.score,
                    score_details     = excluded.score_details,
                    commute_minutes   = excluded.commute_minutes,
                    hazard_level      = excluded.hazard_level,
                    market_price_m2   = excluded.market_price_m2,
                    lat               = excluded.lat,
                    lon               = excluded.lon,
                    scraped_at        = excluded.scraped_at,
                    is_available      = 1,
                    days_on_market    = COALESCE(excluded.days_on_market, days_on_market),
                    first_seen        = COALESCE(first_seen, excluded.first_seen),
                    price_history     = COALESCE(excluded.price_history, price_history),
                    negotiation_label = COALESCE(excluded.negotiation_label, negotiation_label),
                    bargain_score     = excluded.bargain_score,
                    bargain_details   = excluded.bargain_details
                """,
                (
                    p.id, p.name, p.address, p.prefecture, stations_json,
                    p.rent, p.admin_fee, p.deposit, p.key_money, p.floor_plan,
                    p.area, p.floor, p.total_floors, p.built_year, p.structure,
                    p.url, p.lat, p.lon, p.score,
                    json.dumps(p.score_details or {}, ensure_ascii=False),
                    p.commute_minutes, p.hazard_level, p.market_price_m2, now,
                    getattr(p, "first_seen", None),
                    getattr(p, "days_on_market", None),
                    json.dumps(getattr(p, "price_history", []), ensure_ascii=False),
                    getattr(p, "negotiation_label", None),
                    getattr(p, "bargain_score", None),
                    json.dumps(getattr(p, "bargain_details", None) or {}, ensure_ascii=False),
                ),
            )


def load_properties(min_score: float = 0.0, prefectures: Optional[List[str]] = None) -> List[Property]:
    init_db()
    with _conn() as con:
        where = ["is_available = 1", "score >= ?"]
        params: list = [min_score]
        if prefectures:
            placeholders = ",".join("?" * len(prefectures))
            where.append(f"prefecture IN ({placeholders})")
            params.extend(prefectures)
        rows = con.execute(
            f"SELECT * FROM properties WHERE {' AND '.join(where)} ORDER BY score DESC",
            params,
        ).fetchall()
    return [_row_to_property(r) for r in rows]


def count_properties() -> int:
    init_db()
    with _conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM properties WHERE is_available=1"
        ).fetchone()[0]


# ─── 競売物件 ───

def upsert_auction_properties(props: list):
    init_db()
    now = datetime.now().isoformat()
    with _conn() as con:
        for p in props:
            con.execute(
                """
                INSERT INTO auction_properties
                    (id, case_number, property_type, address, court,
                     min_bid, assessed_value, area, floor_plan, built_year,
                     url, bid_start, bid_end, lat, lon, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    min_bid      = excluded.min_bid,
                    bid_start    = excluded.bid_start,
                    bid_end      = excluded.bid_end,
                    fetched_at   = excluded.fetched_at
                """,
                (
                    p.id, p.case_number, p.property_type, p.address, p.court,
                    p.min_bid, p.assessed_value, p.area, p.floor_plan, p.built_year,
                    p.url, p.bid_start, p.bid_end, p.lat, p.lon, now,
                ),
            )


def load_auction_properties() -> list:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM auction_properties ORDER BY fetched_at DESC"
        ).fetchall()
    from .bit_scraper import AuctionProperty
    results = []
    for r in rows:
        try:
            results.append(AuctionProperty(
                id=r["id"], case_number=r["case_number"],
                property_type=r["property_type"], address=r["address"],
                court=r["court"], min_bid=r["min_bid"] or 0,
                assessed_value=r["assessed_value"] or 0,
                area=r["area"] or 0.0, floor_plan=r["floor_plan"] or "",
                built_year=r["built_year"] or 0, url=r["url"] or "",
                bid_start=r["bid_start"] or "", bid_end=r["bid_end"] or "",
                lat=r["lat"], lon=r["lon"],
            ))
        except Exception:
            continue
    return results


# ─── 居住者レビュー ───

def save_review(review: Review):
    init_db()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO reviews
                (building_name, building_address, noise_score, sunlight_score,
                 management_score, neighbor_score, actual_commute_min,
                 pros, cons, lived_period, submitted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review.building_name, review.building_address,
                review.noise_score, review.sunlight_score,
                review.management_score, review.neighbor_score,
                review.actual_commute_min, review.pros, review.cons,
                review.lived_period, review.submitted_at,
            ),
        )


def get_reviews(building_name: str = "", building_address: str = "") -> List[Review]:
    init_db()
    with _conn() as con:
        if building_name:
            rows = con.execute(
                "SELECT * FROM reviews WHERE building_name LIKE ? ORDER BY submitted_at DESC",
                (f"%{building_name}%",),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM reviews ORDER BY submitted_at DESC LIMIT 100"
            ).fetchall()
    return [_row_to_review(r) for r in rows]


def count_reviews() -> int:
    init_db()
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]


# ─── 内部変換 ───

def _row_to_property(row: sqlite3.Row) -> Property:
    stations_raw = json.loads(row["stations_json"] or "[]")
    stations = [StationInfo(**s) for s in stations_raw]
    score_details = json.loads(row["score_details"] or "{}")
    p = Property(
        id=row["id"], name=row["name"], address=row["address"],
        prefecture=row["prefecture"], stations=stations,
        rent=row["rent"], admin_fee=row["admin_fee"],
        deposit=row["deposit"], key_money=row["key_money"],
        floor_plan=row["floor_plan"], area=row["area"],
        floor=row["floor"], total_floors=row["total_floors"],
        built_year=row["built_year"], structure=row["structure"],
        url=row["url"], lat=row["lat"], lon=row["lon"],
        score=row["score"], score_details=score_details,
        commute_minutes=row["commute_minutes"],
        hazard_level=row["hazard_level"],
        market_price_m2=row["market_price_m2"],
    )
    p.first_seen        = row["first_seen"]
    p.days_on_market    = row["days_on_market"]
    p.negotiation_label = row["negotiation_label"]
    p.bargain_score     = row["bargain_score"]
    p.bargain_details   = json.loads(row["bargain_details"] or "{}")
    return p


def _row_to_review(row: sqlite3.Row) -> Review:
    return Review(
        id=row["id"],
        building_name=row["building_name"],
        building_address=row["building_address"] or "",
        noise_score=row["noise_score"] or 3,
        sunlight_score=row["sunlight_score"] or 3,
        management_score=row["management_score"] or 3,
        neighbor_score=row["neighbor_score"] or 3,
        actual_commute_min=row["actual_commute_min"],
        pros=row["pros"] or "",
        cons=row["cons"] or "",
        lived_period=row["lived_period"] or "",
        submitted_at=row["submitted_at"],
    )
