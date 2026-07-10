"""
Ota Ward Property Radar — Streamlit app
Run: streamlit run ota_app.py
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.WARNING)

st.set_page_config(
    page_title="Ota Ward Property Radar",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Reset Streamlit defaults to 2000s minimal */
@import url('https://fonts.googleapis.com/css2?family=Courier+Prime&display=swap');

html, body, [class*="css"] {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
}

/* Kill the default padding */
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 1rem;
    max-width: 1100px;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #e8e8e8;
    border-right: 1px solid #aaaaaa;
}
section[data-testid="stSidebar"] * {
    font-size: 12px !important;
}
section[data-testid="stSidebar"] h1 {
    font-size: 13px !important;
    font-weight: bold;
    border-bottom: 1px solid #999;
    padding-bottom: 4px;
    margin-bottom: 8px;
    color: #222;
    text-transform: uppercase;
    letter-spacing: 1px;
}

/* Main title */
h1 {
    font-size: 22px !important;
    font-weight: bold;
    color: #1a1a6e;
    border-bottom: 2px solid #1a1a6e;
    padding-bottom: 6px;
    letter-spacing: 0.5px;
}

/* Subheaders */
h2, h3 {
    font-size: 14px !important;
    font-weight: bold;
    color: #1a1a6e;
    border-bottom: 1px solid #cccccc;
    padding-bottom: 3px;
    margin-top: 12px;
}

/* Metric boxes */
div[data-testid="metric-container"] {
    background-color: #f5f5f5;
    border: 1px solid #cccccc;
    border-radius: 0px;
    padding: 8px 12px;
}
div[data-testid="metric-container"] label {
    font-size: 11px !important;
    color: #555555;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 20px !important;
    font-family: "Courier New", Courier, monospace;
    color: #1a1a6e;
    font-weight: bold;
}

/* Tabs */
button[data-baseweb="tab"] {
    font-size: 12px !important;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #444;
    border-radius: 0 !important;
    padding: 6px 14px !important;
    background: #eeeeee !important;
    border: 1px solid #cccccc !important;
    margin-right: 2px;
}
button[data-baseweb="tab"][aria-selected="true"] {
    background: #1a1a6e !important;
    color: white !important;
    border-color: #1a1a6e !important;
}

/* Expander */
details summary {
    font-size: 12px !important;
    font-weight: bold;
    background: #f0f0f0;
    border: 1px solid #cccccc;
    padding: 6px 10px;
}

/* Buttons */
.stButton button {
    border-radius: 0 !important;
    font-size: 12px !important;
    border: 1px solid #999 !important;
    background: #e0e0e0 !important;
    color: #222 !important;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.stButton button:hover {
    background: #1a1a6e !important;
    color: white !important;
    border-color: #1a1a6e !important;
}

/* DataFrames */
.stDataFrame {
    border: 1px solid #cccccc;
    font-size: 12px;
}

/* Caption */
.stCaption {
    font-size: 11px !important;
    color: #777;
}

/* Divider */
hr {
    border: none;
    border-top: 1px solid #cccccc;
    margin: 12px 0;
}

/* Progress bar */
.stProgress > div > div {
    background-color: #1a1a6e !important;
    height: 6px !important;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
MLIT_API_KEY = st.secrets.get("MLIT_API_KEY", "c7dc344fd0ce4723ae86f228441e22ef")
WARD_CODE    = "13111"
DESTINATION  = "Shibuya"
CACHE_DIR    = ROOT / ".run_cache"
MAX_COMMUTE  = 60


@st.cache_resource(show_spinner="Loading MLIT transaction data...")
def get_mlit_model():
    from src.mlit_api import MlitPriceModel
    m = MlitPriceModel(api_key=MLIT_API_KEY)
    m.load(city_code=WARD_CODE, years=["2022","2023","2024"])
    return m


@st.cache_data(show_spinner="Scoring properties...", ttl=600)
def load_and_score(_mlit, cache_stamp: str) -> pd.DataFrame:
    from src.scraper import Property, StationInfo, CURRENT_YEAR
    from src.scorer import Scorer
    from src.bargain_engine import BargainEngine
    from src.optimizer import PropertyOptimizer

    for fname in ["step3_commute", "step2_geo", "step1_raw"]:
        path = CACHE_DIR / f"{fname}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            break
    else:
        return pd.DataFrame()

    props = []
    for d in raw:
        built_year = d["built_year"] if "built_year" in d else (
            CURRENT_YEAR - (d.get("age_years") or 0) if d.get("age_years") else 0)
        if "stations" in d and d["stations"]:
            stations = [StationInfo(line=s.get("line",""), name=s["name"],
                                    walk_minutes=s["walk_minutes"]) for s in d["stations"]]
        elif d.get("station_name"):
            stations = [StationInfo(line="", name=d["station_name"],
                                    walk_minutes=d.get("station_walk") or 15)]
        else:
            stations = []
        p = Property(
            id=d["id"], name=d["name"], url=d.get("url",""),
            address=d["address"], prefecture=d.get("prefecture","Tokyo"),
            stations=stations, rent=d["rent"], admin_fee=d["admin_fee"],
            deposit=d["deposit"], key_money=d["key_money"],
            floor_plan=d["floor_plan"], area=d["area"],
            built_year=built_year, floor=d["floor"],
            total_floors=d.get("total_floors", 0), structure=d["structure"],
        )
        p.lat = d.get("lat"); p.lon = d.get("lon")
        p.commute_minutes = d.get("commute_minutes")
        props.append(p)

    scored = Scorer(max_commute_min=MAX_COMMUTE).score_all(props)
    BargainEngine().fit_and_score(scored)
    opt = PropertyOptimizer()
    opt.fit_segment_models(scored)
    for p in scored:
        p.value_gap_pct = opt.value_gap_pct(p)
    pareto_ids = {pp.id for pp in opt.pareto_frontier(scored)}
    for p in scored:
        p.mlit_gap_pct   = _mlit.value_gap_pct(p)
        p.district_rank  = _mlit.district_rank(p.address)
        p.mlit_fair_rent = _mlit.predict_fair_rent(p)

    rows = []
    for p in scored:
        ns = p.nearest_station
        t5 = PropertyOptimizer.calc_tco(p, years=5)
        t2 = PropertyOptimizer.calc_tco(p, years=2)
        bd = p.bargain_details or {}
        mg = p.mlit_gap_pct
        rows.append({
            "id":             p.id,
            "Name":           p.name,
            "Address":        p.address,
            "URL":            p.url,
            "Rent":           p.rent,
            "Admin Fee":      p.admin_fee,
            "Monthly Total":  p.total_monthly,
            "Layout":         p.floor_plan,
            "Area":           p.area,
            "Age":            p.age_years,
            "Structure":      p.structure or "N/A",
            "lat":            p.lat,
            "lon":            p.lon,
            "Station":        ns.name if ns else "N/A",
            "Walk Min":       ns.walk_minutes if ns else 15,
            "Commute Min":    p.commute_minutes or 99,
            "Livability":     round(p.score or 0, 1),
            "Bargain":        round(p.bargain_score or 0, 1),
            "SUUMO Gap %":    round(p.value_gap_pct or 0, 1),
            "Tx Gap %":       round(mg, 1) if mg is not None else None,
            "Fair Rent":      p.mlit_fair_rent,
            "District Rank":  p.district_rank or "?",
            "5yr TCO":        t5["tco"],
            "2yr TCO":        t2["tco"],
            "Pareto":         p.id in pareto_ids,
            "price_gap_pt":   round(bd.get("value_gap", 0)),
            "urgency_pt":     round(bd.get("urgency", 0)),
            "competition_pt": round(bd.get("competition", 0)),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("**FILTERS**")

    max_rent    = st.slider("Max Rent (10k JPY)", 5, 20, 15)
    max_commute = st.slider("Max Commute (min)", 20, 60, 50)
    max_walk    = st.slider("Max Walk (min)", 3, 20, 15)
    layouts     = st.multiselect("Layout",
        ["Studio","1K","1DK","1LDK","2K","2DK","2LDK","3K+"],
        default=[], placeholder="All")
    ranks       = st.multiselect("District Rank",
        ["S","A","B","C"], default=[], placeholder="All")
    pareto_only = st.checkbox("Pareto-optimal only", False)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Refresh", use_container_width=True,
                     help="Re-scrape SUUMO (2-3 min)"):
            import subprocess
            with st.spinner("Scraping..."):
                r = subprocess.run(
                    [sys.executable, "-u", str(ROOT / "run_steps12.py")],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
            if r.returncode == 0:
                st.success("Done"); load_and_score.clear(); st.rerun()
            else:
                st.error("Failed"); st.text(r.stdout[-400:] + r.stderr[-400:])
    with c2:
        if st.button("Rescore", use_container_width=True):
            load_and_score.clear(); st.rerun()

    st.divider()
    st.markdown(
        '<span style="font-size:10px;color:#888">Data: SUUMO / MLIT API 2022-2024</span>',
        unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
st.markdown("## Ota Ward Property Radar")
st.markdown(
    '<span style="font-size:11px;color:#555">'
    'Commute base: Shibuya &nbsp;|&nbsp; '
    'Benchmark: MLIT 6,025 actual transactions (2022-2024)'
    '</span>',
    unsafe_allow_html=True)
st.markdown("---")

mlit = get_mlit_model()
stamp = str(sorted(p.stat().st_mtime for p in CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else [])
df_all = load_and_score(mlit, stamp)

if df_all.empty:
    st.warning("No cache found. Click 'Refresh' in the sidebar to scrape SUUMO.")
    st.stop()

# Apply filters
df = df_all.copy()
df = df[df["Rent"] <= max_rent * 10000]
df = df[df["Commute Min"] <= max_commute]
df = df[df["Walk Min"] <= max_walk]
layout_map = {"Studio": "ワンルーム", "1K": "1K", "1DK": "1DK",
              "1LDK": "1LDK", "2K": "2K", "2DK": "2DK", "2LDK": "2LDK"}
if layouts:
    jp = [layout_map.get(l, l) for l in layouts]
    df = df[df["Layout"].isin(jp)]
if ranks:
    df = df[df["District Rank"].isin(ranks)]
if pareto_only:
    df = df[df["Pareto"] == True]

# ── KPI row ──
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Properties", f"{len(df)}", f"of {len(df_all)} total")
k2.metric(">10% Underpriced", f"{(df['Tx Gap %'].fillna(0)>10).sum()}")
best = df["Tx Gap %"].max()
k3.metric("Best Gap", f"+{best:.1f}%" if pd.notna(best) else "N/A")
k4.metric("5yr TCO Min", f"{df['5yr TCO'].min()//10000:.0f}万")
avg_c = df["Commute Min"].replace(99, pd.NA).mean()
k5.metric("Avg Commute", f"{avg_c:.0f} min" if pd.notna(avg_c) else "N/A")

st.markdown("---")

# ── Tabs ──
tab_map, tab_top, tab_tx, tab_station, tab_pareto = st.tabs([
    "MAP", "TOP 10", "TX PRICE GAP", "STATION EFFICIENCY", "PARETO FRONTIER"
])

# ── MAP ──
with tab_map:
    df_m = df[df["lat"].notna() & df["lon"].notna()].copy()
    if df_m.empty:
        st.info("No geocoded properties in current filter.")
    else:
        def gap_color(g):
            if g is None or pd.isna(g): return [160, 160, 160, 200]
            if g >= 20:  return [20,  140, 60,  240]
            if g >= 5:   return [100, 180, 100, 220]
            if g >= -5:  return [200, 160, 40,  210]
            return [180, 50, 50, 200]

        df_m["_c"] = df_m["Tx Gap %"].apply(gap_color)
        df_m["_r"] = df_m["Livability"].apply(lambda s: 60 + s * 2.2)
        df_m["_gap_str"] = df_m["Tx Gap %"].apply(
            lambda x: f"{x:+.1f}%" if pd.notna(x) else "N/A")

        deck = pdk.Deck(
            layers=[pdk.Layer("ScatterplotLayer",
                data=df_m,
                get_position=["lon","lat"],
                get_fill_color="_c",
                get_radius="_r",
                radius_min_pixels=4,
                radius_max_pixels=20,
                pickable=True,
            )],
            initial_view_state=pdk.ViewState(
                latitude=df_m["lat"].mean(),
                longitude=df_m["lon"].mean(),
                zoom=12, pitch=0),
            tooltip={
                "html": (
                    "<b style='font-family:Arial'>{Name}</b><br/>"
                    "{Rent} JPY / {Layout} / {Area}sqm / {Age}yr old<br/>"
                    "Station: {Station} {Walk Min}min walk | Shibuya {Commute Min}min<br/>"
                    "Tx Gap: <b>{_gap_str}</b> | Livability: {Livability}pt"
                ),
                "style": {
                    "backgroundColor": "#1a1a6e",
                    "color": "white",
                    "fontSize": "12px",
                    "fontFamily": "Arial",
                    "padding": "8px",
                }
            },
            map_style="mapbox://styles/mapbox/light-v9",
        )
        st.pydeck_chart(deck, use_container_width=True, height=500)
        st.markdown(
            '<span style="font-size:11px;color:#555">'
            'Green = underpriced &nbsp;|&nbsp; Yellow = fair value &nbsp;|&nbsp; '
            'Red = overpriced &nbsp;|&nbsp; Circle size = livability score'
            '</span>',
            unsafe_allow_html=True)

# ── TOP 10 ──
with tab_top:
    st.markdown("### Top 10 Properties — Combined Score")
    st.markdown('<span style="font-size:11px;color:#777">Weighted: livability 40% + bargain 30% + SUUMO gap 20% + Tx gap 25%</span>', unsafe_allow_html=True)

    def total(r):
        return (r["Livability"]*0.4 + r["Bargain"]*0.3
                + max(0, r["SUUMO Gap %"])*0.2
                + max(0, r["Tx Gap %"] or 0)*0.25)

    df_top = df.copy()
    df_top["_total"] = df_top.apply(total, axis=1)
    df_top = df_top.nlargest(10, "_total").reset_index(drop=True)

    for i, r in df_top.iterrows():
        gap_s = f"{r['Tx Gap %']:+.1f}%" if pd.notna(r["Tx Gap %"]) else "N/A"
        rent_man = r["Rent"]//10000
        label = f"#{i+1}  {r['Name']}  —  {rent_man}万/mo  {r['Layout']}  Shibuya {r['Commute Min']}min  Tx {gap_s}"
        with st.expander(label, expanded=(i == 0)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Monthly", f"{r['Monthly Total']//10000}万")
            c2.metric("5yr TCO", f"{r['5yr TCO']//10000}万")
            c3.metric("Tx Gap", gap_s)
            c4.metric("Livability", f"{r['Livability']}pt")

            left, right = st.columns(2)
            with left:
                st.markdown(f"**Address:** {r['Address']}")
                st.markdown(f"**Station:** {r['Station']} {r['Walk Min']}min walk")
                st.markdown(f"**Size:** {r['Area']}sqm  Age: {r['Age']}yr  {r['Structure']}")
                rank_clr = {"S":"#c8960c","A":"#888","B":"#7a4f1e","C":"#555"}.get(r["District Rank"],"#555")
                st.markdown(
                    f'District Rank: <b style="background:{rank_clr};color:white;'
                    f'padding:1px 7px;font-size:12px">{r["District Rank"]}</b>'
                    + (f'&nbsp;&nbsp;Fair Rent: <b>{r["Fair Rent"]//10000:.0f}万/mo</b>'
                       if pd.notna(r["Fair Rent"]) else ""),
                    unsafe_allow_html=True)
            with right:
                st.markdown("**Bargain Score Breakdown**")
                st.progress(r["price_gap_pt"]/100,
                            text=f"Price gap:   {r['price_gap_pt']:.0f}pt")
                st.progress(r["urgency_pt"]/100,
                            text=f"Urgency:     {r['urgency_pt']:.0f}pt")
                st.progress(r["competition_pt"]/100,
                            text=f"Low compete: {r['competition_pt']:.0f}pt")
            st.markdown(f'[View on SUUMO]({r["URL"]})')

# ── TX PRICE GAP ──
with tab_tx:
    st.markdown("### Transaction Price Gap Ranking")
    st.markdown('<span style="font-size:11px;color:#777">Fair rent estimated from MLIT 6,025 actual transactions (2022-2024) at 4% gross yield. Positive = underpriced.</span>', unsafe_allow_html=True)

    df_tx = df[df["Tx Gap %"].notna()].nlargest(20, "Tx Gap %")

    disp = df_tx[["Name","Rent","Fair Rent","Tx Gap %","District Rank",
                  "Layout","Area","Age","Station","Commute Min"]].copy()
    disp["Rent"]     = disp["Rent"].apply(lambda x: f"{x//10000}万")
    disp["Fair Rent"]= disp["Fair Rent"].apply(lambda x: f"{x//10000}万" if pd.notna(x) else "N/A")
    disp["Tx Gap %"] = disp["Tx Gap %"].apply(lambda x: f"{x:+.1f}%")
    disp["Area"]     = disp["Area"].apply(lambda x: f"{x}sqm")
    disp["Commute Min"] = disp["Commute Min"].apply(lambda x: f"{x}min")
    st.dataframe(disp, use_container_width=True, hide_index=True)

    try:
        import plotly.express as px
        df_sc = df[df["Tx Gap %"].notna()].copy()
        fig = px.scatter(
            df_sc, x="Commute Min", y="Tx Gap %",
            size="Area", color="Livability",
            hover_name="Name",
            hover_data={"Rent":True,"Layout":True,"District Rank":True,"Age":True},
            color_continuous_scale="RdYlGn",
            labels={"Commute Min":"Commute (min)", "Tx Gap %":"Tx Price Gap (%)"},
            title="Commute vs Transaction Price Gap",
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#999",
                      annotation_text="fair value")
        fig.add_hline(y=10, line_dash="dash", line_color="#336633",
                      annotation_text="+10% underpriced")
        fig.update_layout(
            height=400,
            font_family="Arial",
            font_size=12,
            plot_bgcolor="#fafafa",
            paper_bgcolor="#ffffff",
            margin=dict(t=40, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.info("Install plotly for chart: pip install plotly")

# ── STATION EFFICIENCY ──
with tab_station:
    st.markdown("### Station Efficiency Ranking")
    st.markdown('<span style="font-size:11px;color:#777">Efficiency = median rent x commute / median area. Lower is better.</span>', unsafe_allow_html=True)

    sts = (df.groupby("Station")
           .agg(Count=("id","count"),
                MedianRent=("Rent","median"),
                MedianArea=("Area","median"),
                MedianCommute=("Commute Min","median"))
           .reset_index())
    sts = sts[sts["Count"] >= 2].copy()
    sts["Efficiency"] = (sts["MedianRent"] * sts["MedianCommute"] / sts["MedianArea"]).round(0)
    sts = sts.sort_values("Efficiency").reset_index(drop=True)

    try:
        import plotly.express as px
        fig2 = px.bar(
            sts.head(15), x="Station", y="Efficiency",
            color="MedianCommute", color_continuous_scale="Blues",
            text="Count",
            labels={"Efficiency":"Efficiency Score (lower=better)",
                    "MedianCommute":"Median Commute (min)"},
            title="Station Efficiency — Top 15",
        )
        fig2.update_layout(
            height=360, font_family="Arial", font_size=12,
            plot_bgcolor="#fafafa", paper_bgcolor="#ffffff",
            margin=dict(t=40, b=60),
        )
        st.plotly_chart(fig2, use_container_width=True)
    except ImportError:
        pass

    disp2 = sts.copy()
    disp2["MedianRent"]    = disp2["MedianRent"].apply(lambda x: f"{x//10000:.0f}万")
    disp2["MedianArea"]    = disp2["MedianArea"].apply(lambda x: f"{x:.1f}sqm")
    disp2["MedianCommute"] = disp2["MedianCommute"].apply(lambda x: f"{x:.0f}min")
    disp2["Efficiency"]    = disp2["Efficiency"].apply(lambda x: f"{x:.0f}")
    disp2 = disp2.rename(columns={
        "Count":"#", "MedianRent":"Rent", "MedianArea":"Area",
        "MedianCommute":"Commute", "Efficiency":"Score"})
    st.dataframe(disp2, use_container_width=True, hide_index=True)

# ── PARETO FRONTIER ──
with tab_pareto:
    st.markdown("### Pareto Frontier")
    st.markdown('<span style="font-size:11px;color:#777">Properties not dominated by any other on all four axes: rent, commute, livability, bargain score.</span>', unsafe_allow_html=True)

    df_p = df[df["Pareto"] == True].sort_values("Livability", ascending=False)

    if df_p.empty:
        st.info("No Pareto-optimal properties in current filter. Relax constraints.")
    else:
        st.markdown(f"**{len(df_p)} properties** on the current Pareto frontier.")

        disp3 = df_p[["Name","Rent","Layout","Area","Age","Station","Walk Min",
                       "Commute Min","Livability","Bargain","SUUMO Gap %",
                       "Tx Gap %","District Rank","5yr TCO"]].copy()
        disp3["Rent"]      = disp3["Rent"].apply(lambda x: f"{x//10000}万")
        disp3["5yr TCO"]   = disp3["5yr TCO"].apply(lambda x: f"{x//10000}万")
        disp3["SUUMO Gap %"] = disp3["SUUMO Gap %"].apply(lambda x: f"{x:+.1f}%")
        disp3["Tx Gap %"]  = disp3["Tx Gap %"].apply(
            lambda x: f"{x:+.1f}%" if pd.notna(x) else "N/A")
        disp3["Area"]      = disp3["Area"].apply(lambda x: f"{x}sqm")
        st.dataframe(disp3, use_container_width=True, hide_index=True)

        try:
            import plotly.express as px
            fig3 = px.scatter(
                df_p, x="Commute Min", y="Rent",
                size="Area", color="Tx Gap %",
                hover_name="Name",
                hover_data={"Layout":True,"Age":True,"Livability":True},
                color_continuous_scale="RdYlGn",
                title="Pareto Frontier — Commute vs Rent",
                labels={"Commute Min":"Commute (min)", "Rent":"Rent (JPY)"},
            )
            fig3.update_layout(
                height=380, font_family="Arial", font_size=12,
                plot_bgcolor="#fafafa", paper_bgcolor="#ffffff",
                margin=dict(t=40, b=40),
            )
            st.plotly_chart(fig3, use_container_width=True)
        except ImportError:
            pass

# ─────────────────────────────────────────────
st.markdown("---")
st.markdown(
    '<span style="font-size:10px;color:#999">'
    'Data sources: SUUMO (listings) / Ministry of Land, Infrastructure, Transport and Tourism '
    '— Real Estate Information Library API (transaction prices). '
    'Listings change daily. Verify on SUUMO before contacting.'
    '</span>',
    unsafe_allow_html=True)
