"""
CBL Load Form Dashboard — Streamlit App
Run: streamlit run cbl_dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
import math
import json
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CBL Route Optimizer",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# CUSTOM CSS
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Main background */
    .main { background-color: #f8f9fc; }

    /* Metric cards */
    div[data-testid="metric-container"] {
        background: #ffffff;
        border: 1px solid #e8ecf0;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    div[data-testid="metric-container"] label {
        font-size: 12px !important;
        color: #6b7280 !important;
        font-weight: 500 !important;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        font-size: 26px !important;
        font-weight: 700 !important;
        color: #1a1a2e !important;
    }

    /* Section headers */
    .section-header {
        font-size: 16px;
        font-weight: 600;
        color: #1a1a2e;
        margin: 24px 0 12px 0;
        padding-bottom: 8px;
        border-bottom: 2px solid #e8ecf0;
    }

    /* Sidebar — white theme */
    section[data-testid="stSidebar"] {
        background: #ffffff;
        border-right: 1px solid #e8ecf0;
    }
    section[data-testid="stSidebar"] * {
        color: #1a1a2e !important;
    }
    section[data-testid="stSidebar"] .stSelectbox label,
    section[data-testid="stSidebar"] .stMultiselect label {
        color: #6b7280 !important;
        font-size: 12px !important;
    }
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #1a1a2e !important;
        font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] small {
        color: #9ca3af !important;
    }

    /* Table */
    .dataframe { font-size: 13px !important; }

    /* Status badge */
    .badge-settled {
        background: #d1fae5; color: #065f46;
        padding: 3px 10px; border-radius: 20px;
        font-size: 11px; font-weight: 600;
    }
    .badge-pending {
        background: #fef3c7; color: #92400e;
        padding: 3px 10px; border-radius: 20px;
        font-size: 11px; font-weight: 600;
    }

    /* Top bar — white */
    .top-bar {
        background: #ffffff;
        border: 1px solid #e8ecf0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        color: #1a1a2e; padding: 20px 28px; border-radius: 14px;
        margin-bottom: 24px;
        display: flex; justify-content: space-between; align-items: center;
    }
    .top-bar h1 { font-size: 22px; font-weight: 700; margin: 0; color: #1a1a2e; }
    .top-bar p  { font-size: 13px; color: #6b7280; margin: 4px 0 0; }

    /* Info note */
    .osrm-note {
        background: #eff6ff; border: 1px solid #bfdbfe;
        border-radius: 8px; padding: 10px 14px;
        font-size: 12px; color: #1d4ed8; margin-bottom: 12px;
    }

</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


def nearest_neighbor_distance(shops_with_coords, warehouse_lat, warehouse_lon):
    """
    Estimate total route distance using nearest-neighbor heuristic (Haversine).
    Returns total_km.
    """
    if not shops_with_coords:
        return 0.0
    pts = [(warehouse_lat, warehouse_lon)] + [(s["Lat"], s["Lon"]) for s in shops_with_coords]
    n = len(pts)
    visited = [False] * n
    order = [0]; visited[0] = True
    unvisited = list(range(1, n))
    while unvisited:
        cur  = order[-1]
        near = min(unvisited, key=lambda j: haversine_km(*pts[cur], *pts[j]))
        order.append(near); visited[near] = True; unvisited.remove(near)
    order.append(0)
    total = sum(haversine_km(*pts[order[i]], *pts[order[i+1]]) for i in range(len(order)-1))
    return round(total, 2)


# ─────────────────────────────────────────────────────────────
# OSRM ROUTE OPTIMIZER FUNCTIONS
# ─────────────────────────────────────────────────────────────

OSRM_BASE_URL = "http://router.project-osrm.org"

DEFAULT_WAREHOUSE = {
    "name": "Warehouse (Start/End)",
    "lat":  24.8542977,
    "lon":  67.1534304,
}


def get_osrm_table(coords, retries=3):
    """coords: list of (lat, lon). Returns (dist_km_matrix, dur_min_matrix) or (None, None)."""
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coord_str}?annotations=distance,duration"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "Ok":
                dist = [[v / 1000.0 if v is not None else 1e9 for v in row] for row in data["distances"]]
                dur  = [[v / 60.0  if v is not None else 1e9 for v in row] for row in data["durations"]]
                return dist, dur
        except Exception:
            time.sleep(2)
    return None, None


def get_osrm_route_geometry(ordered_stops, retries=3):
    """Returns list of [lat, lon] pairs (road geometry) or []."""
    def _lat(s): return s.get("lat", s.get("Lat"))
    def _lon(s): return s.get("lon", s.get("Lon"))
    coord_str = ";".join(f"{_lon(s)},{_lat(s)}" for s in ordered_stops)
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coord_str}?overview=full&geometries=geojson&steps=false"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "Ok":
                coords = data["routes"][0]["geometry"]["coordinates"]
                return [[c[1], c[0]] for c in coords]
        except Exception:
            time.sleep(2)
    return []


def haversine_fallback(coords):
    """Build a straight-line distance matrix when OSRM is unavailable."""
    n = len(coords)
    def hav(a, b):
        R = 6371
        dlat = math.radians(b[0] - a[0]); dlon = math.radians(b[1] - a[1])
        x = (math.sin(dlat/2)**2
             + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
             * math.sin(dlon/2)**2)
        return R * 2 * math.asin(math.sqrt(x))
    dist = [[hav(coords[i], coords[j]) for j in range(n)] for i in range(n)]
    dur  = [[dist[i][j] / 40 * 60 for j in range(n)] for i in range(n)]
    return dist, dur


def run_osrm_optimize(shops, warehouse):
    """Run nearest-neighbor optimization. Returns (rows, total_km, total_min, route_idx, method)."""
    all_coords = [(warehouse["lat"], warehouse["lon"])] + [(s["Lat"], s["Lon"]) for s in shops]
    n = len(all_coords)
    dist_matrix, dur_matrix = get_osrm_table(all_coords)
    method = "OSRM Road Distance"
    if dist_matrix is None:
        dist_matrix, dur_matrix = haversine_fallback(all_coords)
        method = "Haversine Straight-line (OSRM unavailable)"

    visited = [False] * n
    route_idx = [0]; visited[0] = True
    unvisited = list(range(1, n))
    while unvisited:
        cur = route_idx[-1]
        nearest = min(unvisited, key=lambda j: dist_matrix[cur][j])
        route_idx.append(nearest); visited[nearest] = True; unvisited.remove(nearest)
    route_idx.append(0)

    rows = []
    cum_km = cum_min = 0.0
    for step, (f, t) in enumerate(zip(route_idx[:-1], route_idx[1:])):
        leg_km = dist_matrix[f][t]; leg_min = dur_matrix[f][t]
        cum_km += leg_km; cum_min += leg_min
        from_name = warehouse["name"] if f == 0 else shops[f - 1]["ShopName"]
        to_name   = warehouse["name"] if t == 0 else shops[t - 1]["ShopName"]
        to_code   = "WAREHOUSE"       if t == 0 else shops[t - 1]["ShopCode"]
        to_val    = ""                if t == 0 else shops[t - 1]["OrderValue"]
        rows.append({
            "Stop #": step + 1, "From": from_name, "To (Shop)": to_name,
            "ShopCode": to_code, "OrderValue (Rs)": to_val,
            "Leg km": round(leg_km, 3), "Leg min": round(leg_min, 1),
            "Cumulative km": round(cum_km, 3), "Cumulative min": round(cum_min, 1),
        })
    return rows, cum_km, cum_min, route_idx, method


def build_gmaps_chunks(ordered_shops, warehouse, chunk_size=9):
    def _lat(s): return s.get("lat", s.get("Lat"))
    def _lon(s): return s.get("lon", s.get("Lon"))
    def _name(s): return s.get("ShopName", s.get("name", "Warehouse"))
    all_stops = [warehouse] + ordered_shops + [warehouse]
    chunks, i, num = [], 0, 1
    while i < len(all_stops) - 1:
        end = min(i + chunk_size + 1, len(all_stops) - 1)
        seg = all_stops[i:end + 1]
        orig = f"{_lat(seg[0])},{_lon(seg[0])}"; dest = f"{_lat(seg[-1])},{_lon(seg[-1])}"
        wps = "|".join(f"{_lat(s)},{_lon(s)}" for s in seg[1:-1])
        url = (f"https://www.google.com/maps/dir/?api=1&origin={orig}&destination={dest}&travelmode=driving"
               + (f"&waypoints={wps}" if wps else ""))
        chunks.append({"num": num, "from": _name(seg[0]), "to": _name(seg[-1]),
                       "stops": f"Stops {i}-{end}", "url": url})
        i = end; num += 1
    return chunks


def generate_route_map_html(ordered_shops, warehouse, road_geometry,
                            total_km, total_min, load_form="Route", deliveryman=""):
    """Generate self-contained Leaflet HTML map string."""
    stops_js = [{"idx": 0, "label": "W", "name": warehouse["name"],
                 "code": "WAREHOUSE", "lat": warehouse["lat"],
                 "lon": warehouse["lon"], "val": 0, "type": "warehouse", "stop": 0}]
    for i, s in enumerate(ordered_shops, 1):
        stops_js.append({
            "idx": i, "label": str(i), "name": s["ShopName"], "code": s["ShopCode"],
            "lat": s["Lat"], "lon": s["Lon"], "val": s["OrderValue"],
            "type": "high" if s["OrderValue"] > 1000 else "shop", "stop": i,
        })

    if road_geometry:
        road_json = json.dumps(road_geometry); road_note = "OSRM actual road geometry"
    else:
        straight = ([[warehouse["lat"], warehouse["lon"]]]
                    + [[s["Lat"], s["Lon"]] for s in ordered_shops]
                    + [[warehouse["lat"], warehouse["lon"]]])
        road_json = json.dumps(straight); road_note = "straight-line fallback"

    center_lat = sum(s["Lat"] for s in ordered_shops) / len(ordered_shops) if ordered_shops else warehouse["lat"]
    center_lon = sum(s["Lon"] for s in ordered_shops) / len(ordered_shops) if ordered_shops else warehouse["lon"]
    gen_time = datetime.now().strftime("%d %b %Y  %H:%M")

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>*{{margin:0;padding:0}}body{{font-family:sans-serif}}#map{{width:100%;height:600px}}</style>
</head><body>
<div style="background:#1a1a2e;color:#fff;padding:12px 18px;font-size:13px">
  <b>📦 {load_form}</b> · {deliveryman} · {len(ordered_shops)} shops · {total_km:.1f} km · {total_min:.0f} min · {road_note}
</div>
<div id="map"></div>
<script>
const STOPS={json.dumps(stops_js, ensure_ascii=False)};
const ROAD={road_json};
const map=L.map('map').setView([{center_lat},{center_lon}],14);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19}}).addTo(map);
if(ROAD.length>1)L.polyline(ROAD,{{color:'#534AB7',weight:4,opacity:.85,dashArray:'9,4'}}).addTo(map);
function mkIcon(l,t){{const c={{warehouse:'#1D9E75',shop:'#378ADD',high:'#E24B4A'}}[t]||'#888';
return L.divIcon({{className:'',html:`<div style="width:28px;height:28px;border-radius:50%;background:${{c}};
border:2px solid #fff;display:flex;align-items:center;justify-content:center;font-size:${{l.length>1?8:10}}px;
font-weight:700;color:#fff;box-shadow:0 2px 5px rgba(0,0,0,.3)">${{l}}</div>`,iconSize:[28,28],iconAnchor:[14,14]}});}}
STOPS.forEach(s=>{{const m=L.marker([s.lat,s.lon],{{icon:mkIcon(s.label,s.type)}}).addTo(map);
m.bindPopup(`<b>${{s.name}}</b><br>${{s.code}}${{s.val>0?'<br>Rs '+s.val.toLocaleString():''}}`)}});
const bounds=STOPS.map(s=>[s.lat,s.lon]);if(bounds.length)map.fitBounds(bounds,{{padding:[30,30]}});
</script></body></html>"""


@st.cache_data(show_spinner=False)
def load_data(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Load & clean the Load Form Excel file."""
    df = pd.read_excel(BytesIO(file_bytes), header=0, skiprows=[1])
    df.columns = [str(c).strip() for c in df.columns]

    # Rename to safe internal names
    rename = {
        "S/No":               "SNo",
        "Distributor":        "Distributor",
        "Order Booker":       "OrderBooker",
        "Deliveryman":        "Deliveryman",
        "Load Form #":        "LoadForm",
        "Load Form Status":   "Status",
        "Invoice #":          "Invoice",
        "Store Code":         "StoreCode",
        "Store Name":         "StoreName",
        "Locality Name":      "Locality",
        "Sub Locality Name":  "SubLocality",
        "Channel Type Name":  "ChannelType",
        "Channel Name":       "Channel",
        "Sub Channel Name":   "SubChannel",
        "PJP #":              "PJP",
        "SKU Code":           "SKUCode",
        "SKU Name":           "SKUName",
        "Issued":             "Issued",
        "Return":             "Return",
        "Sales":              "Sales",
        "Discount":           "Discount",
        "Return Amount":      "ReturnAmt",
        "Net Sales":          "NetSales",
        "Date":               "Date",
        "Lat":                "Lat",
        "Lon":                "Lon",
        "Latitude":           "Lat",
        "Longitude":          "Lon",
        "latitude":           "Lat",
        "longitude":          "Lon",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # Types
    df["Date"]     = pd.to_datetime(df["Date"], errors="coerce")
    df["NetSales"] = pd.to_numeric(df["NetSales"], errors="coerce").fillna(0)
    df["Issued"]   = pd.to_numeric(df["Issued"],   errors="coerce").fillna(0)
    df["Return"]   = pd.to_numeric(df["Return"],   errors="coerce").fillna(0)
    df["Sales"]    = pd.to_numeric(df["Sales"],    errors="coerce").fillna(0)
    df["Discount"] = pd.to_numeric(df["Discount"], errors="coerce").fillna(0)

    # Extract DM code from Deliveryman string
    df["DMCode"] = df["Deliveryman"].str.extract(r'\[(.*?)\]')

    return df


def fmt_rs(v):
    if v >= 1_000_000:
        return f"Rs {v/1_000_000:.2f}M"
    elif v >= 1_000:
        return f"Rs {v/1_000:.1f}K"
    return f"Rs {v:.0f}"


def build_loadform_summary(df: pd.DataFrame) -> pd.DataFrame:
    grp = df.groupby(["LoadForm", "Deliveryman", "Date", "Status"]).agg(
        Drops     = ("StoreCode", "nunique"),
        Invoices  = ("Invoice",   "nunique"),
        SKUs      = ("SKUCode",   "nunique"),
        NetSales  = ("NetSales",  "sum"),
        Issued    = ("Issued",    "sum"),
        Returns   = ("Return",    "sum"),
        SalesQty  = ("Sales",     "sum"),
        Discount  = ("Discount",  "sum"),
    ).reset_index()
    grp["Date"]       = grp["Date"].dt.strftime("%d %b %Y")
    grp["NetSales"]   = grp["NetSales"].round(2)
    grp["ReturnRate"] = ((grp["Returns"] / grp["Issued"].replace(0, float("nan"))) * 100).round(1).fillna(0)
    return grp.sort_values(["Date", "LoadForm"])


def build_date_summary(df: pd.DataFrame) -> pd.DataFrame:
    grp = df.groupby("Date").agg(
        LoadForms = ("LoadForm",  "nunique"),
        Drops     = ("StoreCode", "nunique"),
        Invoices  = ("Invoice",   "nunique"),
        NetSales  = ("NetSales",  "sum"),
        Issued    = ("Issued",    "sum"),
        Returns   = ("Return",    "sum"),
    ).reset_index()
    grp["NetSales"] = grp["NetSales"].round(2)
    grp["AvgSalesPerDrop"] = (grp["NetSales"] / grp["Drops"].replace(0, float("nan"))).round(0)
    return grp.sort_values("Date")


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📦 CBL Dashboard")
    st.markdown("---")

    uploaded = st.file_uploader(
        "Upload Load Form Excel",
        type=["xlsx", "xls"],
        help="Excel file — header row 1, data starts row 3",
    )

    st.markdown("---")
    st.markdown("### Filters")

    if uploaded:
        raw = load_data(uploaded.read(), uploaded.name)

        dates_avail = sorted(raw["Date"].dropna().dt.date.unique())
        sel_dates = st.multiselect(
            "Date(s)",
            options=dates_avail,
            default=dates_avail,
            format_func=lambda d: d.strftime("%d %b %Y"),
        )

        dms_avail = sorted(raw["Deliveryman"].dropna().unique())
        sel_dms = st.multiselect(
            "Deliveryman",
            options=dms_avail,
            default=dms_avail,
        )

        lfs_avail = sorted(raw["LoadForm"].dropna().unique())
        sel_lfs = st.multiselect(
            "Load Form #",
            options=lfs_avail,
            default=lfs_avail,
        )

        # Apply filters
        df = raw[
            raw["Date"].dt.date.isin(sel_dates) &
            raw["Deliveryman"].isin(sel_dms) &
            raw["LoadForm"].isin(sel_lfs)
        ].copy()

        st.markdown("---")
        st.caption(f"📊 {len(df):,} rows loaded")
        st.caption(f"📅 {raw['Date'].min().strftime('%d %b')} – {raw['Date'].max().strftime('%d %b %Y')}")

    else:
        df = None

    st.markdown("---")
    st.markdown(
        "<small style='opacity:.5'>CBL Route Analytics v1.0</small>",
        unsafe_allow_html=True
    )


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

st.markdown("""
<div class="top-bar">
  <div>
    <h1>📦 CBL Route Optimizer</h1>
  </div>
</div>
""", unsafe_allow_html=True)

if df is None or len(df) == 0:
    st.info("👈 Please upload a **Load Form Excel** file from the sidebar to get started.")

    with st.expander("📋 Expected file format"):
        st.markdown("""
        | Column | Description |
        |--------|-------------|
        | S/No | Serial number |
        | Distributor | Distributor name |
        | Order Booker | OB name |
        | **Deliveryman** | DM name + code |
        | **Load Form #** | LF ID |
        | Load Form Status | Settled / Pending |
        | Invoice # | Invoice ID |
        | **Store Code** | Unique shop ID |
        | Store Name | Shop name |
        | Locality Name | Area |
        | SKU Code / Name | Product |
        | Issued / Return / Sales | Cartons |
        | **Net Sales** | Revenue (Rs) |
        | **Date** | Delivery date |
        | **Lat** | Lat|
        | **:ong** | Long|


        > Row 1 = headers, Row 2 = sub-headers (auto-skipped), Row 3 onwards = data
        """)
    st.stop()


# ─────────────────────────────────────────────────────────────
# TAB LAYOUT
# ─────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Overview",
    "📋 Load Form Summary",
    "📅 Date-wise Analysis",
    "🔍 Deep Dive",
    "🗺️ Route Optimizer",
])


# ══════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════
with tab1:
    total_net   = df["NetSales"].sum()
    total_drops = df["StoreCode"].nunique()
    total_lfs   = df["LoadForm"].nunique()
    total_inv   = df["Invoice"].nunique()
    total_skus  = df["SKUCode"].nunique()
    total_ret   = df["Return"].sum()
    total_iss   = df["Issued"].sum()
    ret_rate    = (total_ret / total_iss * 100) if total_iss > 0 else 0
    avg_drop    = total_net / total_drops if total_drops > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Total Net Sales",    fmt_rs(total_net))
    c2.metric("🏪 Unique Drops",       f"{total_drops:,}")
    c3.metric("📋 Load Forms",         f"{total_lfs:,}")
    c4.metric("🧾 Invoices",           f"{total_inv:,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("📦 SKUs Sold",          f"{total_skus:,}")
    c6.metric("📤 Issued (Cartons)",   f"{total_iss:,.1f}")
    c7.metric("📥 Return Rate",        f"{ret_rate:.1f}%")
    c8.metric("💵 Avg Sale/Drop",      fmt_rs(avg_drop))

    st.markdown('<div class="section-header">Daily Net Sales Trend</div>', unsafe_allow_html=True)
    date_df = build_date_summary(df)
    fig_trend = px.bar(
        date_df,
        x=date_df["Date"].dt.strftime("%d %b"),
        y="NetSales",
        color="Drops",
        color_continuous_scale="Blues",
        labels={"x": "Date", "NetSales": "Net Sales (Rs)", "Drops": "Drops"},
        text=date_df["NetSales"].apply(lambda v: fmt_rs(v)),
    )
    fig_trend.update_traces(textposition="outside", textfont_size=11)
    fig_trend.update_layout(
        height=360, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=20, b=20, l=10, r=10),
        coloraxis_showscale=False,
        xaxis_title="", yaxis_title="Net Sales (Rs)",
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
    )
    st.plotly_chart(fig_trend, use_container_width=True)

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown('<div class="section-header">Sales by Deliveryman</div>', unsafe_allow_html=True)
        dm_df = df.groupby("Deliveryman").agg(
            NetSales=("NetSales", "sum"),
            Drops=("StoreCode", "nunique"),
            LoadForms=("LoadForm", "nunique"),
        ).reset_index().sort_values("NetSales", ascending=True)
        dm_df["DMShort"] = dm_df["Deliveryman"].str.split(" DM ").str[0]

        fig_dm = px.bar(
            dm_df, x="NetSales", y="DMShort", orientation="h",
            color="Drops", color_continuous_scale="Teal",
            labels={"NetSales": "Net Sales (Rs)", "DMShort": ""},
            text=dm_df["NetSales"].apply(fmt_rs),
        )
        fig_dm.update_traces(textposition="outside", textfont_size=10)
        fig_dm.update_layout(
            height=max(300, len(dm_df) * 32),
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=10, b=10, l=10, r=10),
            coloraxis_showscale=False,
            yaxis=dict(tickfont_size=11),
        )
        st.plotly_chart(fig_dm, use_container_width=True)

    with col_right:
        st.markdown('<div class="section-header">Sales by Channel</div>', unsafe_allow_html=True)
        ch_df = df.groupby("Channel")["NetSales"].sum().reset_index().sort_values("NetSales", ascending=False).head(10)
        fig_ch = px.pie(
            ch_df, values="NetSales", names="Channel",
            hole=0.45, color_discrete_sequence=px.colors.qualitative.Safe,
        )
        fig_ch.update_traces(textinfo="percent+label", textfont_size=11)
        fig_ch.update_layout(
            height=380, showlegend=False,
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_ch, use_container_width=True)


# ══════════════════════════════════════════════════════════════
# TAB 2 — LOAD FORM SUMMARY
# ══════════════════════════════════════════════════════════════
with tab2:
    lf_sum = build_loadform_summary(df)

    st.markdown('<div class="section-header">Load Form — Summary Table</div>', unsafe_allow_html=True)
    st.caption(f"Showing {len(lf_sum):,} load forms")

    # Search box
    search = st.text_input("🔍 Search (LF#, Deliveryman, Date…)", placeholder="e.g. D0573LF12525 or Mohid")
    if search:
        mask = lf_sum.apply(lambda r: r.astype(str).str.contains(search, case=False).any(), axis=1)
        lf_display = lf_sum[mask]
    else:
        lf_display = lf_sum

    # Colour-code high return rates
    def highlight_return(val):
        if isinstance(val, (int, float)):
            if val > 10:  return "background-color:#fee2e2; color:#991b1b"
            if val > 5:   return "background-color:#fef3c7; color:#92400e"
        return ""

    styled = (
        lf_display.rename(columns={
            "LoadForm": "Load Form #", "Deliveryman": "DM",
            "Date": "Date", "Status": "Status",
            "Drops": "Drops", "Invoices": "Invoices",
            "SKUs": "SKUs", "NetSales": "Net Sales (Rs)",
            "Issued": "Issued", "Returns": "Returns",
            "SalesQty": "Sales", "Discount": "Discount (Rs)",
            "ReturnRate": "Return %",
        })
        .style
        .map(highlight_return, subset=["Return %"])
        .format({
            "Net Sales (Rs)": "{:,.0f}",
            "Discount (Rs)":  "{:,.0f}",
            "Issued":         "{:.2f}",
            "Returns":        "{:.2f}",
            "Sales":          "{:.2f}",
            "Return %":       "{:.1f}%",
        })
    )
    st.dataframe(styled, use_container_width=True, height=500)

    # Download button
    buf = BytesIO()
    lf_display.to_excel(buf, index=False)
    st.download_button(
        "⬇️  Download Load Form Summary (.xlsx)",
        data=buf.getvalue(),
        file_name="loadform_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown('<div class="section-header">Top 10 Load Forms by Net Sales</div>', unsafe_allow_html=True)
    top10 = lf_sum.nlargest(10, "NetSales")
    fig_top = px.bar(
        top10, x="LoadForm", y="NetSales",
        color="Deliveryman", text=top10["NetSales"].apply(fmt_rs),
        labels={"LoadForm": "Load Form #", "NetSales": "Net Sales (Rs)"},
        color_discrete_sequence=px.colors.qualitative.Plotly,
    )
    fig_top.update_traces(textposition="outside")
    fig_top.update_layout(
        height=380, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=10, b=10, l=10, r=10),
        xaxis_tickangle=-30, showlegend=True,
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
    )
    st.plotly_chart(fig_top, use_container_width=True)


# ══════════════════════════════════════════════════════════════
# TAB 3 — DATE-WISE ANALYSIS
# ══════════════════════════════════════════════════════════════
with tab3:
    date_sum = build_date_summary(df)
    date_sum["DateStr"] = date_sum["Date"].dt.strftime("%d %b %Y")

    st.markdown('<div class="section-header">Date-wise KPI Summary</div>', unsafe_allow_html=True)

    styled_date = (
        date_sum[["DateStr","LoadForms","Drops","Invoices","NetSales","Issued","Returns","AvgSalesPerDrop"]]
        .rename(columns={
            "DateStr": "Date", "LoadForms": "Load Forms",
            "NetSales": "Net Sales (Rs)", "AvgSalesPerDrop": "Avg/Drop (Rs)",
        })
        .style
        .format({
            "Net Sales (Rs)": "{:,.0f}",
            "Avg/Drop (Rs)":  "{:,.0f}",
            "Issued":         "{:.1f}",
            "Returns":        "{:.2f}",
        })
        .background_gradient(subset=["Net Sales (Rs)"], cmap="Blues")
        .background_gradient(subset=["Drops"],          cmap="Greens")
    )
    st.dataframe(styled_date, use_container_width=True)

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown('<div class="section-header">Drops per Day</div>', unsafe_allow_html=True)
        fig_drops = px.area(
            date_sum, x="DateStr", y="Drops",
            markers=True, color_discrete_sequence=["#0ea5e9"],
            labels={"DateStr": "", "Drops": "Unique Drops"},
        )
        fig_drops.update_layout(
            height=300, plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=10, b=10, l=10, r=10),
            yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
        )
        st.plotly_chart(fig_drops, use_container_width=True)

    with col_b:
        st.markdown('<div class="section-header">Load Forms per Day</div>', unsafe_allow_html=True)
        fig_lf = px.bar(
            date_sum, x="DateStr", y="LoadForms",
            color="LoadForms", color_continuous_scale="Purples",
            text="LoadForms",
            labels={"DateStr": "", "LoadForms": "Load Forms"},
        )
        fig_lf.update_traces(textposition="outside")
        fig_lf.update_layout(
            height=300, plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=10, b=10, l=10, r=10),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig_lf, use_container_width=True)

    st.markdown('<div class="section-header">Deliveryman Performance — Date Heatmap</div>', unsafe_allow_html=True)
    pivot = df.groupby(["Deliveryman", df["Date"].dt.strftime("%d %b")])["NetSales"].sum().unstack(fill_value=0)
    pivot_display = pivot.copy()
    pivot_display.index = pivot_display.index.str.split(" DM ").str[0]

    fig_heat = go.Figure(data=go.Heatmap(
        z=pivot_display.values,
        x=pivot_display.columns.tolist(),
        y=pivot_display.index.tolist(),
        colorscale="Blues",
        text=[[fmt_rs(v) for v in row] for row in pivot_display.values],
        texttemplate="%{text}",
        textfont_size=10,
        hoverongaps=False,
    ))
    fig_heat.update_layout(
        height=max(300, len(pivot_display) * 38),
        margin=dict(t=10, b=30, l=120, r=10),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis_title="", yaxis_title="",
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    # Download
    buf2 = BytesIO()
    date_sum.drop(columns=["DateStr"]).to_excel(buf2, index=False)
    st.download_button(
        "⬇️  Download Date-wise Summary (.xlsx)",
        data=buf2.getvalue(),
        file_name="datewise_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ══════════════════════════════════════════════════════════════
# TAB 4 — DEEP DIVE
# ══════════════════════════════════════════════════════════════
with tab4:
    col_dd1, col_dd2 = st.columns(2)

    with col_dd1:
        st.markdown('<div class="section-header">Top SKUs by Net Sales</div>', unsafe_allow_html=True)
        n_skus = st.slider("Show top N SKUs", 5, 30, 15, key="sku_n")
        sku_df = (
            df.groupby("SKUName")["NetSales"].sum()
              .nlargest(n_skus).reset_index()
              .sort_values("NetSales", ascending=True)
        )
        fig_sku = px.bar(
            sku_df, x="NetSales", y="SKUName", orientation="h",
            color="NetSales", color_continuous_scale="Teal",
            text=sku_df["NetSales"].apply(fmt_rs),
            labels={"NetSales": "Net Sales (Rs)", "SKUName": ""},
        )
        fig_sku.update_traces(textposition="outside", textfont_size=10)
        fig_sku.update_layout(
            height=max(350, n_skus * 26),
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=10, b=10, l=10, r=80),
            coloraxis_showscale=False,
            yaxis=dict(tickfont_size=10),
        )
        st.plotly_chart(fig_sku, use_container_width=True)

    with col_dd2:
        st.markdown('<div class="section-header">Top Stores by Net Sales</div>', unsafe_allow_html=True)
        n_stores = st.slider("Show top N Stores", 5, 30, 15, key="store_n")
        store_df = (
            df.groupby(["StoreName", "Locality"])["NetSales"].sum()
              .nlargest(n_stores).reset_index()
              .sort_values("NetSales", ascending=True)
        )
        store_df["Label"] = store_df["StoreName"].str[:25] + " (" + store_df["Locality"].str[:10] + ")"
        fig_store = px.bar(
            store_df, x="NetSales", y="Label", orientation="h",
            color="NetSales", color_continuous_scale="Oranges",
            text=store_df["NetSales"].apply(fmt_rs),
            labels={"NetSales": "Net Sales (Rs)", "Label": ""},
        )
        fig_store.update_traces(textposition="outside", textfont_size=10)
        fig_store.update_layout(
            height=max(350, n_stores * 26),
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=10, b=10, l=10, r=80),
            coloraxis_showscale=False,
            yaxis=dict(tickfont_size=10),
        )
        st.plotly_chart(fig_store, use_container_width=True)

    st.markdown('<div class="section-header">Route Distance Estimator (Nearest Neighbor — Haversine)</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="osrm-note">
    ℹ️ Distance is estimated using <b>Nearest Neighbor + Haversine straight-line</b> formula.
    For actual road distances run the <code>route_optimizer_osrm.py</code> script which uses the OSRM routing engine.
    </div>
    """, unsafe_allow_html=True)

    # Per-LF distance estimate using store lat/lon if available
    # Since this file has no lat/lon, show per-LF drop count as proxy
    lf_dist_df = df.groupby(["LoadForm","Deliveryman","Date"]).agg(
        Drops     = ("StoreCode", "nunique"),
        NetSales  = ("NetSales",  "sum"),
        Localities= ("Locality",  "nunique"),
    ).reset_index()
    lf_dist_df["Est_Km_Proxy"] = (lf_dist_df["Drops"] * 0.45).round(1)
    lf_dist_df["Date"] = lf_dist_df["Date"].dt.strftime("%d %b %Y")
    lf_dist_df["DMShort"] = lf_dist_df["Deliveryman"].str.split(" DM ").str[0]

    fig_dist = px.scatter(
        lf_dist_df,
        x="Drops", y="NetSales",
        size="Est_Km_Proxy", color="DMShort",
        hover_data={"LoadForm": True, "Date": True, "Est_Km_Proxy": True},
        labels={"Drops": "Drops", "NetSales": "Net Sales (Rs)",
                "Est_Km_Proxy": "Est. Km (proxy)", "DMShort": "DM"},
        color_discrete_sequence=px.colors.qualitative.Plotly,
    )
    fig_dist.update_layout(
        height=420, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=10, b=10, l=10, r=10),
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
        xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
    )
    st.plotly_chart(fig_dist, use_container_width=True)
    st.caption("Bubble size = estimated km proxy (drops × 0.45 km average stop gap). Upload data with Lat/Lon columns for exact OSRM distances.")

    st.markdown('<div class="section-header">Locality-wise Drops</div>', unsafe_allow_html=True)
    loc_df = df.groupby(["Locality","SubLocality"]).agg(
        Drops=("StoreCode","nunique"),
        NetSales=("NetSales","sum"),
        LoadForms=("LoadForm","nunique"),
    ).reset_index().sort_values("NetSales", ascending=False).head(20)

    fig_loc = px.treemap(
        loc_df, path=["Locality","SubLocality"],
        values="NetSales", color="Drops",
        color_continuous_scale="Blues",
        custom_data=["Drops","LoadForms"],
    )
    fig_loc.update_traces(
        texttemplate="<b>%{label}</b><br>%{value:,.0f} Rs",
        hovertemplate="<b>%{label}</b><br>Net Sales: Rs %{value:,.0f}<br>Drops: %{customdata[0]}<extra></extra>",
    )
    fig_loc.update_layout(
        height=440, margin=dict(t=10, b=10, l=10, r=10),
    )
    st.plotly_chart(fig_loc, use_container_width=True)

    # Raw data explorer
    with st.expander("🔎 Raw Data Explorer"):
        cols_show = ["LoadForm","Deliveryman","Date","StoreName","Locality",
                     "SKUName","Issued","Return","Sales","NetSales","Status"]
        cols_avail = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_avail].head(500), use_container_width=True, height=400)
        st.caption(f"Showing first 500 of {len(df):,} rows")


# ══════════════════════════════════════════════════════════════
# TAB 5 — ROUTE OPTIMIZER (OSRM)
# ══════════════════════════════════════════════════════════════
with tab5:
    # st.markdown('<div class="section-header">🗺️ OSRM Route Optimizer — Nearest Neighbor</div>', unsafe_allow_html=True)

    has_coords = "Lat" in df.columns and "Lon" in df.columns
    if not has_coords:
        st.warning(
            "⚠️ Your Excel file does not contain **Lat** / **Lon** columns. "
            "Please upload a file with store coordinates to use the Route Optimizer."
        )
        st.info("Expected columns: `Lat` (or `Latitude`) and `Lon` (or `Longitude`) with decimal degree values.")
        st.stop()

    # Ensure numeric coords
    df["Lat"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Lon"] = pd.to_numeric(df["Lon"], errors="coerce")

    # ══════════════════════════════════════════════════════════
    # BATCH ROUTE SUMMARY — All Load Forms
    # ══════════════════════════════════════════════════════════
    st.markdown('<div class="section-header">📋 Date-wise Route Summary (All Load Forms)</div>', unsafe_allow_html=True)
    st.caption("Calculate OSRM road distance & drive time for every Load Form in one click.")

    wh_lat_batch = DEFAULT_WAREHOUSE["lat"]
    wh_lon_batch = DEFAULT_WAREHOUSE["lon"]
    warehouse_batch = {"name": "Warehouse", "lat": wh_lat_batch, "lon": wh_lon_batch}

    if st.button("📡 Calculate All Routes (OSRM)", type="primary", key="btn_batch"):
        all_lfs = df["LoadForm"].dropna().unique()
        batch_rows = []
        progress = st.progress(0, text="Calculating routes...")

        for idx, lf in enumerate(all_lfs):
            progress.progress((idx + 1) / len(all_lfs), text=f"Processing {lf} ({idx+1}/{len(all_lfs)})...")
            lf_sub = df[df["LoadForm"] == lf].copy()
            lf_sub = lf_sub.dropna(subset=["Lat", "Lon"])
            lf_sub = lf_sub[(lf_sub["Lat"] != 0) & (lf_sub["Lon"] != 0)]

            # Deduplicate stores
            shops = lf_sub.groupby(["StoreCode", "StoreName"]).agg(
                Lat=("Lat", "first"), Lon=("Lon", "first"), OrderValue=("NetSales", "sum"),
            ).reset_index().rename(columns={"StoreCode": "ShopCode", "StoreName": "ShopName"})
            shops_list = shops.to_dict("records")

            dm = lf_sub["Deliveryman"].iloc[0] if len(lf_sub) > 0 else ""
            ob = lf_sub["OrderBooker"].iloc[0] if len(lf_sub) > 0 and "OrderBooker" in lf_sub.columns else ""
            dt = lf_sub["Date"].iloc[0] if len(lf_sub) > 0 else None
            net = lf_sub["NetSales"].sum()
            drops = len(shops_list)

            if len(shops_list) >= 1:
                _, total_km, total_min, _, method = run_osrm_optimize(shops_list, warehouse_batch)
            else:
                total_km, total_min, method = 0, 0, "No valid coordinates"

            batch_rows.append({
                "Date": dt, "Load Form #": lf, "Deliveryman": dm,
                "Order Booker": ob, "Drops": drops,
                "Net Sales (Rs)": round(net, 0),
                "OSRM Km": round(total_km, 1),
                "Est. Min": round(total_min, 0),
                "Method": method.split(" (")[0],
            })

        progress.empty()
        batch_df = pd.DataFrame(batch_rows)
        batch_df["Date"] = pd.to_datetime(batch_df["Date"], errors="coerce")
        batch_df = batch_df.sort_values(["Date", "Load Form #"])
        batch_df["Date"] = batch_df["Date"].dt.strftime("%d %b %Y")
        st.session_state["batch_summary"] = batch_df

    if "batch_summary" in st.session_state:
        batch_df = st.session_state["batch_summary"]

        # KPI totals
        bk1, bk2, bk3, bk4 = st.columns(4)
        bk1.metric("📋 Load Forms", f"{len(batch_df)}")
        bk2.metric("🏪 Total Drops", f"{batch_df['Drops'].sum():,}")
        bk3.metric("🛣️ Total Km", f"{batch_df['OSRM Km'].sum():,.1f}")
        bk4.metric("⏱️ Total Drive", f"{batch_df['Est. Min'].sum():,.0f} min")

        # Styled table
        styled_batch = (
            batch_df.style
            .format({
                "Net Sales (Rs)": "{:,.0f}",
                "OSRM Km": "{:.1f}",
                "Est. Min": "{:.0f}",
            })
            .background_gradient(subset=["OSRM Km"], cmap="YlOrRd")
            .background_gradient(subset=["Net Sales (Rs)"], cmap="Blues")
        )
        st.dataframe(styled_batch, use_container_width=True, height=450)

        # Download
        buf_batch = BytesIO()
        batch_df.to_excel(buf_batch, index=False)
        st.download_button(
            "⬇️ Download Route Summary (.xlsx)", data=buf_batch.getvalue(),
            file_name="route_summary_all_loadforms.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("---")

    # ── Single LF Controls ────────────────────────────────────
    st.markdown('<div class="section-header">🗺️ Single Load Form — Route Optimizer</div>', unsafe_allow_html=True)

    col_ctrl1, col_ctrl2 = st.columns([2, 1])

    with col_ctrl1:
        # Build dropdown labels with DM name + date
        lf_info = df.groupby("LoadForm").agg(
            DM=("Deliveryman", "first"),
            Date=("Date", "first"),
        ).reset_index()
        lf_info["DMShort"] = lf_info["DM"].str.split(" DM ").str[0]
        lf_info["DateStr"] = lf_info["Date"].dt.strftime("%d %b %Y").fillna("")
        lf_label_map = {
            row["LoadForm"]: f"{row['LoadForm']}  —  {row['DMShort']} · {row['DateStr']}"
            for _, row in lf_info.iterrows()
        }
        lf_options = sorted(lf_label_map.keys())
        sel_lf = st.selectbox(
            "Select Load Form to optimize", lf_options,
            format_func=lambda x: lf_label_map.get(x, x),
            key="route_lf",
        )

    # with col_ctrl2:
        # st.markdown("**Warehouse Location**")
        # wh_lat = st.number_input("Lat", value=DEFAULT_WAREHOUSE["lat"], format="%.7f", key="wh_lat",disabled=True)
        # wh_lon = st.number_input("Lon", value=DEFAULT_WAREHOUSE["lon"], format="%.7f", key="wh_lon",disabled=True)

    warehouse = {"name": "Warehouse (Start/End)", "lat": DEFAULT_WAREHOUSE["lat"], "lon": DEFAULT_WAREHOUSE["lon"]}

    # Build shop list from selected LF
    lf_df = df[df["LoadForm"] == sel_lf].copy()
    lf_df = lf_df.dropna(subset=["Lat", "Lon"])
    lf_df = lf_df[(lf_df["Lat"] != 0) & (lf_df["Lon"] != 0)]

    # Deduplicate by store
    shop_agg = lf_df.groupby(["StoreCode", "StoreName"]).agg(
        Lat=("Lat", "first"), Lon=("Lon", "first"), OrderValue=("NetSales", "sum"),
    ).reset_index()
    shop_agg = shop_agg.rename(columns={"StoreCode": "ShopCode", "StoreName": "ShopName"})
    shops_list = shop_agg.to_dict("records")

    dm_name = lf_df["Deliveryman"].iloc[0] if len(lf_df) > 0 else ""
    ob_name = lf_df["OrderBooker"].iloc[0] if len(lf_df) > 0 and "OrderBooker" in lf_df.columns else "—"
    lf_date = lf_df["Date"].iloc[0].strftime("%d %b %Y") if len(lf_df) > 0 and pd.notna(lf_df["Date"].iloc[0]) else "—"
    lf_status = lf_df["Status"].iloc[0] if len(lf_df) > 0 and "Status" in lf_df.columns else "—"
    distributor = lf_df["Distributor"].iloc[0] if len(lf_df) > 0 and "Distributor" in lf_df.columns else "—"
    total_inv = lf_df["Invoice"].nunique() if "Invoice" in lf_df.columns else 0
    total_skus = lf_df["SKUCode"].nunique() if "SKUCode" in lf_df.columns else 0
    total_issued = lf_df["Issued"].sum() if "Issued" in lf_df.columns else 0
    total_return = lf_df["Return"].sum() if "Return" in lf_df.columns else 0

    # Load Form details card
    st.markdown(f"""
    <div style="background:#ffffff;color:#1a1a2e;border:1px solid #e8ecf0;
                border-radius:12px;padding:18px 24px;margin:12px 0 16px 0;
                box-shadow:0 1px 4px rgba(0,0,0,0.06)">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
        <div>
          <div style="font-size:18px;font-weight:700;color:#1a1a2e">📦 {sel_lf}</div>
          <div style="font-size:12px;color:#6b7280;margin-top:2px">{lf_date} · <span style="background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600">{lf_status}</span></div>
        </div>
        <div style="text-align:right">
          <div style="font-size:16px;font-weight:700;color:#1a1a2e">{fmt_rs(shop_agg["OrderValue"].sum())}</div>
          <div style="font-size:11px;color:#9ca3af">Net Sales</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:14px;
                  padding-top:12px;border-top:1px solid #f0f0f0">
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Deliveryman</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{dm_name}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Order Booker</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{ob_name}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Distributor</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{distributor}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Stores</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{len(shops_list)}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Invoices</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{total_inv}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">SKUs</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{total_skus}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Issued</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{total_issued:,.1f}</div></div>
        <div style="background:#f8f9fc;border-radius:8px;padding:8px 12px"><div style="font-size:10px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px">Returns</div><div style="font-size:13px;font-weight:600;color:#1a1a2e;margin-top:2px">{total_return:,.1f}</div></div>
      </div>
    </div>
    """, unsafe_allow_html=True)


    if len(shops_list) < 1:
        st.error("No stores with valid coordinates found for this Load Form.")
        st.stop()

    # ── Run Optimization ──────────────────────────────────────
    if st.button("🚀 Optimize Route", type="primary", key="btn_optimize"):
        with st.spinner("Fetching OSRM distance matrix & optimizing route..."):
            route_rows, total_km, total_min, route_idx, method = run_osrm_optimize(shops_list, warehouse)
            ordered_shops = [shops_list[i - 1] for i in route_idx[1:-1]]

        st.session_state["route_result"] = {
            "rows": route_rows, "total_km": total_km, "total_min": total_min,
            "route_idx": route_idx, "method": method,
            "ordered_shops": ordered_shops, "warehouse": warehouse,
            "load_form": sel_lf, "dm_name": dm_name,
        }

    # ── Display Results ───────────────────────────────────────
    if "route_result" in st.session_state:
        res = st.session_state["route_result"]

        # KPI row
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("🏪 Stops", f"{len(res['ordered_shops'])}")
        k2.metric("🛣️ Total Distance", f"{res['total_km']:.1f} km")
        k3.metric("⏱️ Est. Drive Time", f"{res['total_min']:.0f} min")
        k4.metric("📡 Method", res["method"].split(" (")[0])

        # Route table
        st.markdown('<div class="section-header">Optimized Route Sequence</div>', unsafe_allow_html=True)
        route_df = pd.DataFrame(res["rows"])
        st.dataframe(route_df, use_container_width=True, height=400)

        # Download route Excel
        buf_route = BytesIO()
        route_df.to_excel(buf_route, index=False)
        st.download_button(
            "⬇️ Download Route (.xlsx)", data=buf_route.getvalue(),
            file_name=f"route_{res['load_form']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # Interactive map
        st.markdown('<div class="section-header">Interactive Route Map</div>', unsafe_allow_html=True)
        with st.spinner("Fetching road geometry for map..."):
            full_stops = [res["warehouse"]] + res["ordered_shops"] + [res["warehouse"]]
            road_geom = get_osrm_route_geometry(full_stops)

        map_html = generate_route_map_html(
            ordered_shops=res["ordered_shops"], warehouse=res["warehouse"],
            road_geometry=road_geom, total_km=res["total_km"], total_min=res["total_min"],
            load_form=res["load_form"], deliveryman=res["dm_name"],
        )
        st.components.v1.html(map_html, height=650, scrolling=False)

        # Google Maps links
        st.markdown('<div class="section-header">Google Maps Navigation Links</div>', unsafe_allow_html=True)
        chunks = build_gmaps_chunks(res["ordered_shops"], res["warehouse"])
        for ch in chunks:
            st.markdown(
                f"**Link {ch['num']}** — {ch['stops']} ({ch['from']} → {ch['to']})  \n"
                f"[🔗 Open in Google Maps]({ch['url']})"
            )
