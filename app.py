from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

st.set_page_config(page_title="Clinic Appointments Dashboard", layout="wide")

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

BUCKETS = ["Attended", "Cancelled", "No-show", "Scheduled", "Other"]

DEFAULT_STATUS_MAP = {
    "cobrado": "Attended",
    "sin cobrar": "Attended",
    "en consulta": "Attended",
    "en espera": "Attended",
    "cancelado": "Cancelled",
    "eliminado": "Cancelled",
    "no asistio": "No-show",
    "no asistió": "No-show",
    "confirmado": "Scheduled",
    "pendiente": "Scheduled",
    "sin confirmar": "Scheduled",
}


@st.cache_data(show_spinner=False)
def load_excel(file_bytes: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(file_bytes))


def extract_treatment(asunto: object) -> str | None:
    if not isinstance(asunto, str):
        return None
    text = asunto.split("Motivo de eliminacion")[0]
    text = text.split("(", 1)[0]
    text = re.sub(r"\s*-\s*$", "", text).strip(" -\t\n")
    if not text or re.fullmatch(r"[\d\s\-/]+", text):
        return None
    return text.upper()


def download_button(df: pd.DataFrame, label: str, filename: str) -> None:
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(label, csv, file_name=filename, mime="text/csv",
                       key=f"dl_{filename}")


def excel_serial_to_datetime(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")


# ---------- Pricing ----------
def normalize_name(s: object) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.upper()
    s = re.sub(r"\s+", " ", s).strip(" .-/")
    return s


def is_ortho_name(n: str) -> bool:
    return bool(re.search(r"ORTO|BRACKET|BRAKET|RETENEDOR", n))


try:
    APP_DIR = Path(__file__).parent
except NameError:
    APP_DIR = Path.cwd()


@st.cache_data(show_spinner=False)
def load_price_list() -> pd.DataFrame:
    pl = pd.read_csv(APP_DIR / "price_list.csv")
    pl["categoria"] = pl["categoria"].astype(str).str.strip().str.title()
    pl["procedimiento"] = pl["procedimiento"].astype(str).str.strip()
    pl["norm"] = pl["procedimiento"].apply(normalize_name)
    pl["is_ortho"] = (
        pl["categoria"].str.lower().str.contains("ortodoncia")
        | pl["norm"].apply(is_ortho_name)
    )
    pl = pl.drop_duplicates(subset="norm", keep="first")
    return pl


@st.cache_data(show_spinner=False)
def load_formulas() -> str:
    path = APP_DIR / "FORMULAS.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Formula reference (`FORMULAS.md`) not found."


def price_one(name: str, price_map: dict, ortho_map: dict) -> tuple[float, bool, bool]:
    """Return (price, matched, is_ortho) for a single treatment string."""
    n = normalize_name(name)
    n = re.sub(r"^TENTATIVA\s*/*\s*", "", n).strip(" /-")
    n = re.sub(r"//+", " ", n).strip()
    if not n:
        return np.nan, False, False
    if n in price_map:
        return price_map[n], True, ortho_map.get(n, is_ortho_name(n))
    if "+" in n:
        parts = [p.strip() for p in n.split("+") if p.strip()]
        if parts and all(p in price_map for p in parts):
            total = sum(price_map[p] for p in parts)
            ortho = any(ortho_map.get(p, False) for p in parts)
            return total, True, ortho
    return np.nan, False, is_ortho_name(n)


@st.cache_data(show_spinner=False)
def build_treatment_prices(treatments: tuple[str, ...]) -> pd.DataFrame:
    pl = load_price_list()
    price_map = dict(zip(pl["norm"], pl["precio"]))
    ortho_map = dict(zip(pl["norm"], pl["is_ortho"]))
    rows = []
    for t in treatments:
        price, matched, ortho = price_one(t, price_map, ortho_map)
        rows.append({"tratamiento": t, "precio": price,
                     "priced": matched, "is_ortho": ortho})
    return pd.DataFrame(rows)


def attach_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    uniq = tuple(sorted(df["tratamiento"].dropna().unique().tolist()))
    lookup = build_treatment_prices(uniq).set_index("tratamiento")
    df["precio"] = df["tratamiento"].map(lookup["precio"])
    df["priced"] = df["tratamiento"].map(lookup["priced"]).fillna(False)
    df["is_ortho"] = df["tratamiento"].map(lookup["is_ortho"]).fillna(False)
    return df


@st.cache_data(show_spinner=False)
def parse_revenue(file_bytes: bytes) -> pd.DataFrame:
    """Parse the wide 'Facturación año a año' sheet into long form."""
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None)
    # Locate the row holding the year headers (first row with 2+ numeric year-like cells)
    header_row = 0
    for i in range(min(5, len(raw))):
        vals = pd.to_numeric(raw.iloc[i, 1:], errors="coerce").dropna()
        if (vals.between(2000, 2100)).sum() >= 2:
            header_row = i
            break
    years = pd.to_numeric(raw.iloc[header_row, 1:], errors="coerce")
    records = []
    for _, row in raw.iloc[header_row + 1:].iterrows():
        month = str(row.iloc[0]).strip().lower()
        if month not in MONTHS_ES:
            continue
        for col_pos, yr in years.items():
            if pd.isna(yr):
                continue
            val = pd.to_numeric(row.iloc[col_pos], errors="coerce")
            if pd.isna(val):
                continue
            records.append({"year": int(yr), "month": MONTHS_ES[month],
                            "revenue": float(val)})
    rev = pd.DataFrame(records)
    if not rev.empty:
        rev["period"] = pd.to_datetime(
            dict(year=rev["year"], month=rev["month"], day=1)
        )
        rev = rev.sort_values("period").reset_index(drop=True)
    return rev


def clean(df: pd.DataFrame, status_map: dict[str, str]) -> pd.DataFrame:
    df = df.copy()

    if {"Anio", "Mes", "Dia"}.issubset(df.columns):
        df["fecha"] = pd.to_datetime(
            dict(year=df["Anio"], month=df["Mes"], day=df["Dia"]), errors="coerce"
        )
    else:
        df["fecha"] = excel_serial_to_datetime(df.get("Fecha requerido"))

    if "Fecha requerido" in df.columns:
        fallback = excel_serial_to_datetime(df["Fecha requerido"])
        df["fecha"] = df["fecha"].fillna(fallback)

    df["tratamiento"] = df["Asunto"].apply(extract_treatment)
    df["status"] = df["Estado"].map(status_map).fillna("Other")

    if "HoraCita" in df.columns:
        df["hora"] = pd.to_datetime(df["HoraCita"].astype(str), errors="coerce").dt.hour

    if "FechaRegistro" in df.columns:
        df["fecha_registro"] = excel_serial_to_datetime(df["FechaRegistro"])
        df["lead_days"] = (df["fecha"] - df["fecha_registro"]).dt.days

    df = df.dropna(subset=["fecha"]).copy()
    df["año"] = df["fecha"].dt.year
    df["mes"] = df["fecha"].dt.to_period("M").dt.to_timestamp()
    df["dia_semana"] = df["fecha"].dt.day_name()

    if "Cédula" in df.columns:
        df = df.sort_values("fecha")
        first_seen = df.groupby("Cédula")["fecha"].transform("min")
        df["paciente_tipo"] = (df["fecha"] == first_seen).map(
            {True: "New", False: "Returning"}
        )

    return df


def kpi_row(df: pd.DataFrame) -> None:
    total = len(df)
    attended = int((df["status"] == "Attended").sum())
    cancelled = int((df["status"] == "Cancelled").sum())
    noshow = int((df["status"] == "No-show").sum())
    decided = attended + cancelled + noshow
    cancel_rate = (cancelled / decided * 100) if decided else 0
    noshow_rate = (noshow / decided * 100) if decided else 0
    attend_rate = (attended / decided * 100) if decided else 0

    if "Cédula" in df.columns:
        months = df["mes"].nunique()
        unique_patients = df["Cédula"].dropna().astype(str).str.strip().replace("", np.nan).dropna().nunique()
        monthly_unique = (
            df.dropna(subset=["Cédula"])
              .groupby("mes")["Cédula"].nunique()
        )
        avg_monthly_patients = monthly_unique.mean() if not monthly_unique.empty else 0
        avg_monthly_appts = total / months if months else 0
    else:
        unique_patients = 0
        avg_monthly_patients = 0
        avg_monthly_appts = 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total appointments", f"{total:,}")
    c2.metric("Attended", f"{attended:,}")
    c3.metric("Cancelled", f"{cancelled:,}")
    c4.metric("No-show", f"{noshow:,}")
    c5.metric("Cancellation rate", f"{cancel_rate:.1f}%")
    c6.metric("Attendance rate", f"{attend_rate:.1f}%",
              help=f"No-show rate: {noshow_rate:.1f}%")

    d1, d2, d3 = st.columns(3)
    d1.metric("Unique patients", f"{unique_patients:,}")
    d2.metric("Avg appointments / month", f"{avg_monthly_appts:,.0f}")
    d3.metric("Avg unique patients / month", f"{avg_monthly_patients:,.0f}")


def _ensure_buckets(g: pd.DataFrame) -> pd.DataFrame:
    for col in BUCKETS:
        if col not in g.columns:
            g[col] = 0
    return g


def monthly_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["mes", "status"]).size().unstack(fill_value=0).reset_index()
    g = _ensure_buckets(g)
    g["Total"] = g[BUCKETS].sum(axis=1)
    decided = g["Attended"] + g["Cancelled"] + g["No-show"]
    g["Cancellation rate %"] = (g["Cancelled"] / decided.replace(0, np.nan) * 100).round(1)
    g["No-show rate %"] = (g["No-show"] / decided.replace(0, np.nan) * 100).round(1)
    return g


def yearly_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["año", "status"]).size().unstack(fill_value=0)
    g = _ensure_buckets(g)
    g["Total"] = g[BUCKETS].sum(axis=1)
    decided = g["Attended"] + g["Cancelled"] + g["No-show"]
    g["Cancellation rate %"] = (g["Cancelled"] / decided.replace(0, np.nan) * 100).round(1)
    g["No-show rate %"] = (g["No-show"] / decided.replace(0, np.nan) * 100).round(1)
    return g.reset_index()


def seasonal_trend_forecast(series: pd.Series, horizon: int):
    """Forecast via linear trend × multiplicative monthly seasonal indices.

    Returns (forecast, lower, upper) Series indexed by future month-starts, plus a
    flag for whether seasonality was applied. Seasonal indices need ~2 full years to
    be stable, so they are only used when >= 24 months of history are available;
    otherwise it degrades to a pure linear trend. The band is ±1.96 × in-sample
    residual std (a rough 95% interval, not a rigorous prediction interval).
    """
    y = series.values.astype(float)
    n = len(y)
    t = np.arange(n)
    b1, b0 = np.polyfit(t, y, 1)
    trend = b0 + b1 * t

    months = series.index.month.values
    safe_trend = np.where(trend == 0, np.nan, trend)
    ratios = y / safe_trend
    seasonal = {}
    for m in range(1, 13):
        vals = ratios[months == m]
        seasonal[m] = np.nanmean(vals) if len(vals) and not np.all(np.isnan(vals)) else 1.0
    mean_s = np.nanmean(list(seasonal.values()))
    if mean_s and not np.isnan(mean_s):
        seasonal = {m: (v / mean_s if not np.isnan(v) else 1.0) for m, v in seasonal.items()}

    use_seasonal = n >= 24
    fitted = trend * np.array([seasonal[m] if use_seasonal else 1.0 for m in months])
    resid_std = float(np.nanstd(y - fitted))

    future_idx = pd.date_range(series.index.max() + pd.offsets.MonthBegin(1),
                               periods=horizon, freq="MS")
    ft = np.arange(n, n + horizon)
    base = b0 + b1 * ft
    fc = np.array([base[k] * (seasonal[future_idx[k].month] if use_seasonal else 1.0)
                   for k in range(horizon)])
    fc = np.clip(fc, 0, None)
    lower = np.clip(fc - 1.96 * resid_std, 0, None)
    upper = fc + 1.96 * resid_std
    return (pd.Series(fc, index=future_idx, name="forecast"),
            pd.Series(lower, index=future_idx, name="lower"),
            pd.Series(upper, index=future_idx, name="upper"),
            use_seasonal)


def rolling_3m(monthly: pd.DataFrame) -> pd.DataFrame:
    out = monthly[["mes", "Attended", "Cancelled", "No-show", "Total"]].copy().sort_values("mes")
    for c in ("Attended", "Cancelled", "No-show", "Total"):
        out[f"{c} (3M)"] = out[c].rolling(3, min_periods=1).sum()
    decided = out["Attended (3M)"] + out["Cancelled (3M)"] + out["No-show (3M)"]
    out["Cancellation rate % (3M)"] = (
        out["Cancelled (3M)"] / decided.replace(0, np.nan) * 100
    ).round(1)
    out["No-show rate % (3M)"] = (
        out["No-show (3M)"] / decided.replace(0, np.nan) * 100
    ).round(1)
    return out


# ---------- UI ----------
st.title("🦷 Clinic Appointments Dashboard")

with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload appointments .xlsx", type=["xlsx"])
    st.caption("Expected columns: Estado, Asunto, Cédula, Anio/Mes/Dia, HoraCita, etc.")
    revenue_file = st.file_uploader(
        "Upload monthly revenue .xlsx (optional)", type=["xlsx"], key="revenue_upload"
    )
    st.caption("Wide 'Facturación año a año' layout: months in rows, years in columns.")

if not uploaded:
    st.info("⬅️ Upload the Excel file to begin. Cell data stays on your machine.")
    st.stop()

with st.spinner("Loading…"):
    raw = load_excel(uploaded.getvalue())

available_estados = sorted(raw["Estado"].dropna().astype(str).unique().tolist())

with st.sidebar:
    st.header("Status mapping")
    st.caption("Map each Estado to a bucket. 'Other' is excluded from rate calculations.")
    status_map: dict[str, str] = {}
    for estado in available_estados:
        default = DEFAULT_STATUS_MAP.get(estado.strip().lower(), "Other")
        idx = BUCKETS.index(default)
        status_map[estado] = st.selectbox(f"{estado} →", BUCKETS, idx, key=f"map_{estado}")

    st.header("Exclude from calculations")
    excluded = st.multiselect(
        "Estado values to drop entirely",
        options=available_estados,
        default=[],
        help="Rows with these Estado values are removed before any metric is computed.",
    )

with st.spinner("Cleaning…"):
    df = clean(raw, status_map)
    df = attach_prices(df)

if excluded:
    df = df[~df["Estado"].isin(excluded)].copy()

revenue_df = pd.DataFrame()
if revenue_file is not None:
    try:
        revenue_df = parse_revenue(revenue_file.getvalue())
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"Could not parse revenue file: {exc}")

exclude_pandemic = st.toggle(
    "🦠 Exclude May–Aug 2020 (pandemic months)",
    value=False,
    help="Drops these four months from every chart, KPI, retention metric, and forecast.",
)
if exclude_pandemic:
    pandemic_mask = (df["fecha"].dt.year == 2020) & df["fecha"].dt.month.isin([5, 6, 7, 8])
    dropped = int(pandemic_mask.sum())
    df = df[~pandemic_mask].copy()
    st.caption(f"Excluded {dropped:,} rows from May–Aug 2020.")

if df.empty:
    st.warning("No rows left after exclusions.")
    st.stop()

st.success(
    f"Loaded **{len(df):,}** rows "
    f"({df['fecha'].min().date()} → {df['fecha'].max().date()})"
    + (f" — excluded: {', '.join(excluded)}" if excluded else "")
)

# Date filter
min_d, max_d = df["fecha"].min().date(), df["fecha"].max().date()
date_range = st.date_input(
    "Date range",
    value=(min_d, max_d),
    min_value=min_d,
    max_value=max_d,
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d, end_d = min_d, max_d
mask = (df["fecha"].dt.date >= start_d) & (df["fecha"].dt.date <= end_d)
fdf = df.loc[mask].copy()

tab_dash, tab_formulas = st.tabs(["📊 Dashboard", "📐 Formulas"])

with tab_dash:
    st.divider()
    kpi_row(fdf)

    # ----- Estado distribution / data quality -----
    with st.expander("📊 Estado distribution & data quality", expanded=False):
        estado_counts = (
            fdf["Estado"].value_counts(dropna=False).rename_axis("Estado")
            .reset_index(name="count")
        )
        estado_counts["%"] = (estado_counts["count"] / estado_counts["count"].sum() * 100).round(2)
        estado_counts["bucket"] = estado_counts["Estado"].map(status_map).fillna("Other")
        c1, c2 = st.columns([2, 3])
        with c1:
            st.dataframe(estado_counts, use_container_width=True, hide_index=True)
            download_button(estado_counts, "⬇ Download Estado counts", "estado_distribution.csv")
        with c2:
            fig_e = px.bar(estado_counts, x="Estado", y="count", color="bucket", text="count")
            st.plotly_chart(fig_e, use_container_width=True)

        dq = pd.DataFrame({
            "Check": [
                "Rows after cleaning",
                "Rows with missing Cédula",
                "Rows with unparseable date",
                "Rows in 'Other' bucket",
                "Unique Estado values",
            ],
            "Value": [
                f"{len(fdf):,}",
                f"{fdf['Cédula'].isna().sum():,}" if "Cédula" in fdf.columns else "n/a",
                f"{int(raw.shape[0] - df.shape[0]):,}",
                f"{int((fdf['status'] == 'Other').sum()):,}",
                f"{fdf['Estado'].nunique()}",
            ],
        })
        st.dataframe(dq, use_container_width=True, hide_index=True)

    # ----- Appointments per month -----
    with st.expander("📅 Appointments per month", expanded=True):
        monthly = monthly_breakdown(fdf)
        fig = px.bar(
            monthly,
            x="mes",
            y=BUCKETS,
            labels={"value": "Appointments", "mes": "Month", "variable": "Status"},
            barmode="stack",
        )
        st.plotly_chart(fig, use_container_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Monthly cancellation rate**")
            fig2 = px.line(monthly, x="mes", y="Cancellation rate %", markers=True)
            fig2.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig2, use_container_width=True)
        with col_b:
            st.markdown("**Rolling 3-month cancellation rate**")
            r3 = rolling_3m(monthly)
            fig3 = px.line(r3, x="mes", y="Cancellation rate % (3M)", markers=True)
            fig3.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig3, use_container_width=True)

    # ----- Yearly & monthly breakdown tables -----
    with st.expander("📆 Yearly & monthly breakdown", expanded=False):
        yearly = yearly_breakdown(fdf)
        st.markdown("**Yearly**")
        st.dataframe(yearly, use_container_width=True, hide_index=True)
        download_button(yearly, "⬇ Download yearly CSV", "yearly_breakdown.csv")

        st.markdown("**Monthly detail**")
        monthly_out = monthly.assign(mes=monthly["mes"].dt.strftime("%Y-%m"))
        st.dataframe(monthly_out, use_container_width=True, hide_index=True)
        download_button(monthly_out, "⬇ Download monthly CSV", "monthly_breakdown.csv")

        st.markdown("**Rolling 3-month detail**")
        r3_out = r3.assign(mes=r3["mes"].dt.strftime("%Y-%m"))
        st.dataframe(r3_out, use_container_width=True, hide_index=True)
        download_button(r3_out, "⬇ Download rolling 3-month CSV", "rolling_3m.csv")

    # ----- Treatment type -----
    with st.expander("🧪 Top treatment types", expanded=False):
        c_top, c_group = st.columns([1, 2])
        top_n = c_top.slider("How many to show", 5, 40, 15)
        group_mode = c_group.radio(
            "Grouping",
            ["Raw (as written)", "Merge degrees (strip trailing 1/2/3 or I/II/III)"],
            index=1,
            horizontal=True,
            help="Folds e.g. 'TX ORTO CER 1' and 'TX ORTO CER 2' into 'TX ORTO CER'.",
        )

        DEGREE_RE = re.compile(r"\s+(?:[0-9]+|[IVX]+)\s*$", re.IGNORECASE)

        def normalize_treatment(name: str) -> str:
            prev = None
            cur = name.strip()
            while cur != prev:
                prev = cur
                cur = DEGREE_RE.sub("", cur).strip(" -")
            return cur or name

        sort_by = c_top.radio("Rank by", ["Volume", "Revenue"], horizontal=True,
                              help="Volume = appointment count. Revenue = attended × list price.")

        work = fdf.dropna(subset=["tratamiento"]).copy()
        if group_mode.startswith("Merge"):
            work["tratamiento"] = work["tratamiento"].map(normalize_treatment)
        work["att_value"] = np.where(
            (work["status"] == "Attended") & work["priced"], work["precio"], 0.0
        )

        treat = (
            work
            .groupby("tratamiento")
            .agg(
                total=("IdCita", "count"),
                attended=("status", lambda s: (s == "Attended").sum()),
                cancelled=("status", lambda s: (s == "Cancelled").sum()),
                noshow=("status", lambda s: (s == "No-show").sum()),
                avg_unit_price=("precio", "mean"),
                price_min=("precio", "min"),
                price_max=("precio", "max"),
                theoretical_value=("att_value", "sum"),
                is_ortho=("is_ortho", "max"),
                priced_rows=("priced", "sum"),
            )
            .reset_index()
        )
        # Row-weighted average list price across (possibly merged) variants;
        # single-price treatments are unaffected since min == max == mean.
        treat["avg_unit_price"] = treat["avg_unit_price"].round(0)
        treat["cancellation_rate_%"] = (
            treat["cancelled"]
            / (treat["attended"] + treat["cancelled"] + treat["noshow"]).replace(0, np.nan)
            * 100
        ).round(1)
        total_value = treat["theoretical_value"].sum()
        treat["%_of_revenue"] = (treat["theoretical_value"] / total_value * 100).round(1) \
            if total_value else 0
        treat["theoretical_value"] = treat["theoretical_value"].round(0)
        treat = treat.sort_values(
            "theoretical_value" if sort_by == "Revenue" else "total", ascending=False
        ).reset_index(drop=True)

        top = treat.head(top_n)
        x_col = "theoretical_value" if sort_by == "Revenue" else "total"
        x_label = "Attended revenue (list price)" if sort_by == "Revenue" else "Appointments"
        fig_t = px.bar(top, x=x_col, y="tratamiento", orientation="h", color="is_ortho",
                       labels={x_col: x_label, "is_ortho": "Ortho"},
                       hover_data=["total", "attended", "avg_unit_price",
                                   "price_min", "price_max",
                                   "theoretical_value", "cancellation_rate_%"])
        fig_t.update_layout(yaxis={"categoryorder": "total ascending"}, height=max(400, 24 * top_n))
        st.plotly_chart(fig_t, use_container_width=True)

        st.markdown("**Popularity vs. financial impact** — bubble size = unit price. "
                    "Top-left = high volume, low value; bottom-right = low volume, high value.")
        scatter_df = treat[treat["theoretical_value"] > 0].copy()
        fig_sc = px.scatter(
            scatter_df, x="total", y="theoretical_value",
            size="avg_unit_price", color="is_ortho", hover_name="tratamiento",
            labels={"total": "Appointments (popularity)",
                    "theoretical_value": "Attended revenue (impact)", "is_ortho": "Ortho"},
            log_y=True,
        )
        med_v = scatter_df["total"].median()
        med_val = scatter_df["theoretical_value"].median()
        fig_sc.add_vline(x=med_v, line_dash="dot", line_color="gray")
        fig_sc.add_hline(y=med_val, line_dash="dot", line_color="gray")
        st.plotly_chart(fig_sc, use_container_width=True)

        st.dataframe(treat, use_container_width=True, hide_index=True)
        download_button(treat, "⬇ Download treatments CSV", "treatments.csv")

    # ----- Financial & revenue -----
    with st.expander("💰 Financial & revenue", expanded=False):
        fin = fdf.copy()
        fin["att_value"] = np.where(
            (fin["status"] == "Attended") & fin["priced"], fin["precio"], 0.0
        )
        att_rows = fin[fin["status"] == "Attended"]
        coverage = att_rows["priced"].mean() * 100 if len(att_rows) else 0

        theo_total = fin["att_value"].sum()
        theo_ortho = fin.loc[fin["is_ortho"], "att_value"].sum()
        theo_non = theo_total - theo_ortho
        lost = fin.loc[fin["status"].isin(["Cancelled", "No-show"]) & fin["priced"], "precio"].sum()

        st.caption(
            "Theoretical billed value = attended appointments × list price (list prices "
            "unchanged since 2019). Ortho is installment-based and shown separately. "
            f"Only {coverage:.0f}% of attended appointments could be priced — "
            "the rest are excluded from value totals."
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Theoretical billed (non-ortho)", f"${theo_non:,.0f}")
        m2.metric("Theoretical billed (ortho)", f"${theo_ortho:,.0f}",
                  help="Installment-based; list price booked at visit date.")
        m3.metric("Price-match coverage", f"{coverage:.0f}%",
                  help="Share of attended appointments mapped to a list price.")
        m4.metric("Lost to cancel/no-show", f"${lost:,.0f}",
                  help="List-price value of cancelled & no-show appointments.")

        # Monthly theoretical value, ortho vs non-ortho
        theo_m = (
            fin.assign(grp=np.where(fin["is_ortho"], "Ortho", "Non-ortho"))
            .groupby(["mes", "grp"])["att_value"].sum().unstack(fill_value=0).reset_index()
        )
        for col in ("Ortho", "Non-ortho"):
            if col not in theo_m.columns:
                theo_m[col] = 0.0
        fig_theo = px.bar(theo_m, x="mes", y=["Non-ortho", "Ortho"], barmode="stack",
                          labels={"value": "Theoretical billed ($)", "mes": "Month",
                                  "variable": ""})
        st.plotly_chart(fig_theo, use_container_width=True)

        # Unpriced treatments (coverage transparency)
        unpriced = (
            fin.loc[~fin["priced"] & fin["tratamiento"].notna(), "tratamiento"]
            .value_counts().head(30).rename_axis("tratamiento").reset_index(name="appointments")
        )
        if not unpriced.empty:
            with st.expander("Top unpriced treatments (excluded from value)"):
                st.dataframe(unpriced, use_container_width=True, hide_index=True)
                download_button(unpriced, "⬇ Download unpriced list", "unpriced_treatments.csv")

        # Actual revenue vs theoretical (two series, no realization %)
        if not revenue_df.empty:
            st.markdown("**Actual revenue vs. theoretical billed value**")
            st.warning(
                "These two series are **not** a collection rate. They diverge because "
                "ortho is paid in installments collected at near-$0 control visits, ~20% "
                "of appointments are unpriced, and procedure mix shifts over time. "
                "Use *actual revenue* as the income source of truth; treat *theoretical "
                "value* as a list-price composition signal, not something to divide into actual."
            )
            fin_year = fin.copy()
            fin_year["año"] = fin_year["fecha"].dt.year
            theo_year = fin_year.groupby("año")["att_value"].sum()
            theo_year_non = fin_year[~fin_year["is_ortho"]].groupby("año")["att_value"].sum()
            act_year = revenue_df.groupby("year")["revenue"].sum()

            yr_tbl = pd.DataFrame({
                "Actual revenue": act_year,
                "Theoretical (all)": theo_year,
                "Theoretical (excl ortho)": theo_year_non,
            }).dropna(how="all")
            yr_tbl["Gap (actual − theo all)"] = yr_tbl["Actual revenue"] - yr_tbl["Theoretical (all)"]
            yr_tbl.index.name = "Year"
            yr_tbl = yr_tbl.round(0).reset_index()

            fig_rev = px.line(
                yr_tbl, x="Year",
                y=["Actual revenue", "Theoretical (all)", "Theoretical (excl ortho)"],
                markers=True, labels={"value": "$", "variable": ""},
            )
            st.plotly_chart(fig_rev, use_container_width=True)
            st.dataframe(yr_tbl, use_container_width=True, hide_index=True)
            download_button(yr_tbl, "⬇ Download yearly actual vs theoretical",
                            "revenue_vs_theoretical.csv")

            # Actual revenue trend on its own (the reliable series)
            st.markdown("**Actual revenue trend**")
            rev_plot = revenue_df.copy()
            fig_act = px.line(rev_plot, x="period", y="revenue", markers=True,
                              labels={"revenue": "Actual revenue ($)", "period": ""})
            st.plotly_chart(fig_act, use_container_width=True)
        else:
            st.info("Upload the monthly revenue file in the sidebar to compare actual "
                    "revenue against theoretical billed value.")

    # ----- Yearly / Quarterly treatment performance -----
    with st.expander("📅 Yearly / Quarterly treatment performance", expanded=False):
        pc1, pc2, pc3 = st.columns(3)
        perf_metric = pc1.radio("Metric", ["Revenue", "Volume"], horizontal=True,
                                key="perf_metric")
        perf_gran = pc2.radio("Period", ["Yearly", "Quarterly"], horizontal=True,
                              key="perf_gran")
        perf_n = pc3.slider("Top treatments", 5, 30, 12, key="perf_n")

        pf = fdf.dropna(subset=["tratamiento"]).copy()
        pf["period"] = (
            pf["fecha"].dt.year.astype(str) if perf_gran == "Yearly"
            else pf["fecha"].dt.to_period("Q").astype(str)
        )
        pf["att_value"] = np.where(
            (pf["status"] == "Attended") & pf["priced"], pf["precio"], 0.0
        )
        value_col = "att_value" if perf_metric == "Revenue" else "IdCita"
        aggfunc = "sum" if perf_metric == "Revenue" else "count"

        ranking = (
            pf.groupby("tratamiento")[value_col].agg(aggfunc).sort_values(ascending=False)
        )
        top_treats = ranking.head(perf_n).index.tolist()
        sub = pf[pf["tratamiento"].isin(top_treats)]
        pivot = sub.pivot_table(index="tratamiento", columns="period",
                                values=value_col, aggfunc=aggfunc, fill_value=0)
        pivot = pivot.loc[ranking.head(perf_n).index]  # keep ranked order

        unit = "$" if perf_metric == "Revenue" else "appts"
        fig_perf = px.imshow(
            pivot, aspect="auto", color_continuous_scale="Greens",
            labels=dict(x="Period", y="Treatment", color=f"{perf_metric} ({unit})"),
            text_auto=".0f",
        )
        fig_perf.update_layout(height=max(400, 26 * perf_n))
        st.plotly_chart(fig_perf, use_container_width=True)

        # First-vs-last period movers
        if pivot.shape[1] >= 2:
            movers = pivot.iloc[:, [0, -1]].copy()
            movers.columns = [f"{pivot.columns[0]}", f"{pivot.columns[-1]}"]
            movers["Δ"] = movers.iloc[:, 1] - movers.iloc[:, 0]
            movers["Δ %"] = (
                movers["Δ"] / movers.iloc[:, 0].replace(0, np.nan) * 100
            ).round(1)
            st.markdown(f"**Change: {pivot.columns[0]} → {pivot.columns[-1]}**")
            st.dataframe(movers.round(0).reset_index(), use_container_width=True, hide_index=True)

        st.markdown("**Trend for selected treatments**")
        chosen = st.multiselect("Treatments", top_treats, default=top_treats[:3],
                                key="perf_chosen")
        if chosen:
            trend = (
                pf[pf["tratamiento"].isin(chosen)]
                .groupby(["period", "tratamiento"])[value_col].agg(aggfunc).reset_index()
            )
            fig_tr = px.line(trend, x="period", y=value_col, color="tratamiento", markers=True,
                             labels={value_col: f"{perf_metric} ({unit})", "period": ""})
            st.plotly_chart(fig_tr, use_container_width=True)

        download_button(pivot.reset_index(), "⬇ Download performance matrix",
                        "treatment_performance.csv")

    # ----- New vs returning -----
    if "paciente_tipo" in fdf.columns:
        with st.expander("👥 New vs returning patients", expanded=False):
            nr = fdf.groupby(["mes", "paciente_tipo"]).size().reset_index(name="count")
            fig_nr = px.bar(nr, x="mes", y="count", color="paciente_tipo", barmode="group")
            st.plotly_chart(fig_nr, use_container_width=True)

            summary = (
                fdf.groupby("paciente_tipo")
                .agg(
                    appointments=("IdCita", "count"),
                    unique_patients=("Cédula", "nunique"),
                    attended=("status", lambda s: (s == "Attended").sum()),
                    cancelled=("status", lambda s: (s == "Cancelled").sum()),
                    noshow=("status", lambda s: (s == "No-show").sum()),
                )
                .reset_index()
            )
            summary["cancellation_rate_%"] = (
                summary["cancelled"]
                / (summary["attended"] + summary["cancelled"] + summary["noshow"]).replace(0, np.nan)
                * 100
            ).round(1)
            st.dataframe(summary, use_container_width=True, hide_index=True)
            download_button(summary, "⬇ Download new/returning CSV", "new_vs_returning.csv")

    # ----- Booking lead time -----
    if "lead_days" in fdf.columns and fdf["lead_days"].notna().any():
        with st.expander("⏱ Booking lead time", expanded=False):
            st.caption("Days between FechaRegistro (booked) and Fecha requerido (scheduled).")
            lt = fdf["lead_days"].dropna()
            lt = lt[lt >= 0]
            if not lt.empty:
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Mean lead", f"{lt.mean():.1f} days")
                b2.metric("Median lead", f"{lt.median():.0f} days")
                b3.metric("Same-day bookings", f"{(lt == 0).mean()*100:.1f}%")
                b4.metric("Booked < 7 days out", f"{(lt < 7).mean()*100:.1f}%")

                fig_lead = px.histogram(
                    lt.clip(upper=180), nbins=60,
                    labels={"value": "Lead time in days (capped at 180)"},
                )
                fig_lead.update_layout(showlegend=False)
                st.plotly_chart(fig_lead, use_container_width=True)

                bins = [-1, 0, 1, 3, 7, 14, 30, 90, 365, 100000]
                labels = ["Same day", "1 day", "2-3 days", "4-7 days", "8-14 days",
                          "15-30 days", "1-3 months", "3-12 months", "12+ months"]
                tmp = fdf.dropna(subset=["lead_days"]).copy()
                tmp = tmp[tmp["lead_days"] >= 0]
                tmp["bucket"] = pd.cut(tmp["lead_days"], bins=bins, labels=labels)
                lead_breakdown = (
                    tmp.groupby("bucket")
                    .agg(
                        total=("IdCita", "count"),
                        attended=("status", lambda s: (s == "Attended").sum()),
                        cancelled=("status", lambda s: (s == "Cancelled").sum()),
                        noshow=("status", lambda s: (s == "No-show").sum()),
                    )
                    .reset_index()
                )
                denom = lead_breakdown["attended"] + lead_breakdown["cancelled"] + lead_breakdown["noshow"]
                lead_breakdown["cancellation_rate_%"] = (
                    lead_breakdown["cancelled"] / denom.replace(0, np.nan) * 100
                ).round(1)
                lead_breakdown["noshow_rate_%"] = (
                    lead_breakdown["noshow"] / denom.replace(0, np.nan) * 100
                ).round(1)
                st.markdown("**Cancellation & no-show rate by lead-time bucket**")
                fig_lb = px.bar(
                    lead_breakdown, x="bucket",
                    y=["cancellation_rate_%", "noshow_rate_%"],
                    barmode="group",
                    labels={"value": "Rate %", "variable": "Metric"},
                )
                st.plotly_chart(fig_lb, use_container_width=True)
                st.dataframe(lead_breakdown, use_container_width=True, hide_index=True)
                download_button(lead_breakdown, "⬇ Download lead-time CSV", "lead_time.csv")

    # ----- Retention -----
    if "Cédula" in df.columns:
        with st.expander("🔁 Patient retention", expanded=False):
            st.caption("Full dataset (ignores date range). First-visit-2019 patients dropped.")
            visit_buckets = st.multiselect(
                "Which statuses count as a 'visit' for retention?",
                BUCKETS,
                default=["Attended"],
                key="retention_buckets",
            )
            visits = (
                df[df["status"].isin(visit_buckets) & df["Cédula"].notna()]
                .loc[:, ["Cédula", "fecha"]]
                .copy()
            )
            visits["Cédula"] = visits["Cédula"].astype(str).str.strip()
            visits = visits[visits["Cédula"] != ""]

            first_visit = visits.groupby("Cédula")["fecha"].min().rename("first_visit")
            visits = visits.join(first_visit, on="Cédula")
            eligible = visits[visits["first_visit"].dt.year >= 2020].copy()

            if eligible.empty:
                st.info("Not enough data from 2020+ to compute retention.")
            else:
                total_patients = eligible["Cédula"].nunique()
                returning_patients = (
                    eligible.groupby("Cédula").size().loc[lambda s: s > 1].count()
                )
                ret_rate = returning_patients / total_patients * 100 if total_patients else 0
                appts_per_patient = eligible.groupby("Cédula").size()

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Unique patients (2020+)", f"{total_patients:,}")
                k2.metric("Returning patients", f"{returning_patients:,}", help="Patients with 2+ visits")
                k3.metric("Returning rate", f"{ret_rate:.1f}%")
                k4.metric("Avg visits / patient", f"{appts_per_patient.mean():.2f}",
                          help=f"Median: {appts_per_patient.median():.0f}, Max: {appts_per_patient.max():.0f}")

                st.markdown("**How many times patients return**")
                vp = appts_per_patient.value_counts().sort_index().reset_index()
                vp.columns = ["visits_per_patient", "patients"]
                vp["visits_per_patient"] = vp["visits_per_patient"].clip(upper=20)
                vp = vp.groupby("visits_per_patient", as_index=False)["patients"].sum()
                fig_vp = px.bar(
                    vp, x="visits_per_patient", y="patients",
                    labels={"visits_per_patient": "Visits per patient (20+ grouped)",
                            "patients": "Number of patients"},
                )
                st.plotly_chart(fig_vp, use_container_width=True)

                st.markdown("**Patient lifetime** — days between first and last visit.")
                lifespan = eligible.groupby("Cédula")["fecha"].agg(["min", "max"])
                lifespan["lifetime_days"] = (lifespan["max"] - lifespan["min"]).dt.days
                lt = lifespan["lifetime_days"]
                multi = lt[lt > 0]
                l1, l2, l3, l4 = st.columns(4)
                l1.metric("Mean lifetime", f"{lt.mean():.0f} days", help=f"≈ {lt.mean()/30:.1f} months")
                l2.metric("Median lifetime", f"{lt.median():.0f} days")
                l3.metric("Mean (multi-visit)", f"{multi.mean():.0f} days" if not multi.empty else "—",
                          help="Excludes single-visit patients")
                l4.metric("Max lifetime", f"{lt.max():.0f} days")
                fig_lt = px.histogram(
                    lt.clip(upper=2000), nbins=60,
                    labels={"value": "Lifetime in days (capped at 2000)"},
                )
                fig_lt.update_layout(showlegend=False)
                st.plotly_chart(fig_lt, use_container_width=True)

                st.markdown("**Days between consecutive visits**")
                sorted_visits = eligible.sort_values(["Cédula", "fecha"])
                sorted_visits["gap_days"] = sorted_visits.groupby("Cédula")["fecha"].diff().dt.days
                gaps = sorted_visits["gap_days"].dropna()
                if not gaps.empty:
                    g1, g2, g3, g4 = st.columns(4)
                    g1.metric("Mean gap", f"{gaps.mean():.0f} days")
                    g2.metric("Median gap", f"{gaps.median():.0f} days")
                    g3.metric("25th pct", f"{gaps.quantile(.25):.0f} days")
                    g4.metric("75th pct", f"{gaps.quantile(.75):.0f} days")
                    fig_gap = px.histogram(
                        gaps.clip(upper=730), nbins=60,
                        labels={"value": "Days since previous visit (capped at 730)"},
                    )
                    fig_gap.update_layout(showlegend=False)
                    st.plotly_chart(fig_gap, use_container_width=True)

                st.markdown("**Year-over-year retention** — of patients seen in year X, % seen in X+1.")
                eligible["año_visit"] = eligible["fecha"].dt.year
                patients_by_year = eligible.groupby("año_visit")["Cédula"].apply(set).sort_index()
                rows = []
                years = patients_by_year.index.tolist()
                for i, yr in enumerate(years[:-1]):
                    this_yr = patients_by_year.loc[yr]
                    next_yr = patients_by_year.loc[years[i + 1]]
                    retained = len(this_yr & next_yr)
                    rows.append({
                        "Year": yr,
                        "Patients seen": len(this_yr),
                        f"Returned in {years[i+1]}": retained,
                        "YoY retention %": round(retained / len(this_yr) * 100, 1) if this_yr else 0,
                    })
                if rows:
                    yoy = pd.DataFrame(rows)
                    st.dataframe(yoy, use_container_width=True, hide_index=True)
                    download_button(yoy, "⬇ Download YoY CSV", "yoy_retention.csv")
                    fig_yoy = px.line(yoy, x="Year", y="YoY retention %", markers=True)
                    fig_yoy.update_yaxes(ticksuffix="%")
                    st.plotly_chart(fig_yoy, use_container_width=True)

                st.markdown("**6-month retention (back-to-back)** — consecutive half-year continuity.")
                eligible["half"] = (
                    eligible["fecha"].dt.year.astype(str)
                    + "-H"
                    + ((eligible["fecha"].dt.month - 1) // 6 + 1).astype(str)
                )
                patients_by_half = eligible.groupby("half")["Cédula"].apply(set).sort_index()
                halves = patients_by_half.index.tolist()
                hrows = []
                for i in range(1, len(halves)):
                    prev_h, cur_h = halves[i - 1], halves[i]
                    S = patients_by_half.loc[prev_h]
                    E = patients_by_half.loc[cur_h]
                    N = E - S
                    retained = len(E - N)
                    retention = (retained / len(S) * 100) if S else 0
                    hrows.append({
                        "Period": cur_h,
                        "S (prev half)": len(S),
                        "E (this half)": len(E),
                        "N (not in S)": len(N),
                        "E − N (retained)": retained,
                        "Retention %": round(retention, 1),
                    })
                if hrows:
                    half_df = pd.DataFrame(hrows)
                    fig_half = px.line(half_df, x="Period", y="Retention %", markers=True)
                    fig_half.update_yaxes(ticksuffix="%")
                    st.plotly_chart(fig_half, use_container_width=True)
                    st.dataframe(half_df, use_container_width=True, hide_index=True)
                    download_button(half_df, "⬇ Download 6-month retention CSV", "half_year_retention.csv")

                st.markdown("**Active-patient rate** — % of a quarter's patients also active the prior quarter.")
                eligible["quarter"] = eligible["fecha"].dt.to_period("Q")
                patients_by_q = eligible.groupby("quarter")["Cédula"].apply(set).sort_index()
                qrows = []
                qs = patients_by_q.index.tolist()
                for i in range(1, len(qs)):
                    cur = patients_by_q.loc[qs[i]]
                    prev = patients_by_q.loc[qs[i - 1]]
                    overlap = len(cur & prev)
                    qrows.append({
                        "Quarter": str(qs[i]),
                        "Active patients": len(cur),
                        "Active in prev quarter": overlap,
                        "Active retention %": round(overlap / len(cur) * 100, 1) if cur else 0,
                    })
                if qrows:
                    qdf = pd.DataFrame(qrows)
                    fig_qr = px.line(qdf, x="Quarter", y="Active retention %", markers=True)
                    fig_qr.update_yaxes(ticksuffix="%")
                    st.plotly_chart(fig_qr, use_container_width=True)
                    with st.expander("Quarter-by-quarter detail"):
                        st.dataframe(qdf, use_container_width=True, hide_index=True)

                st.markdown("**Cohort retention** — % of each first-visit cohort returning N periods later.")
                cohort_unit = st.radio(
                    "Cohort granularity", ["Yearly", "Monthly"], horizontal=True, index=0,
                    key="cohort_unit",
                )
                if cohort_unit == "Yearly":
                    eligible["cohort"] = eligible["first_visit"].dt.year
                    eligible["period_offset"] = (
                        eligible["fecha"].dt.year - eligible["first_visit"].dt.year
                    )
                    period_label = "Years since first visit"
                else:
                    eligible["cohort"] = eligible["first_visit"].dt.to_period("M").astype(str)
                    eligible["period_offset"] = (
                        (eligible["fecha"].dt.year - eligible["first_visit"].dt.year) * 12
                        + (eligible["fecha"].dt.month - eligible["first_visit"].dt.month)
                    )
                    period_label = "Months since first visit"

                cohort_sizes = eligible.groupby("cohort")["Cédula"].nunique()
                cohort_pivot = (
                    eligible.groupby(["cohort", "period_offset"])["Cédula"]
                    .nunique()
                    .unstack(fill_value=0)
                )
                cohort_pct = cohort_pivot.div(cohort_sizes, axis=0) * 100
                cohort_pct = cohort_pct.round(1).sort_index()

                fig_cohort = px.imshow(
                    cohort_pct, aspect="auto", color_continuous_scale="Blues",
                    labels=dict(x=period_label, y="Cohort", color="% returning"),
                    text_auto=".0f",
                )
                st.plotly_chart(fig_cohort, use_container_width=True)
                download_button(cohort_pct.reset_index(),
                                "⬇ Download cohort retention CSV", "cohort_retention.csv")

                with st.expander("Cohort sizes & raw counts"):
                    st.write("Cohort sizes (unique patients):")
                    st.dataframe(cohort_sizes.rename("patients").reset_index(),
                                 use_container_width=True, hide_index=True)
                    st.write("Returning patients per cohort × offset:")
                    st.dataframe(cohort_pivot.reset_index(),
                                 use_container_width=True, hide_index=True)

    # ----- Heatmap -----
    if "hora" in fdf.columns:
        with st.expander("🔥 Day-of-week × hour heatmap", expanded=False):
            metric = st.radio(
                "Metric", ["All appointments", "Cancellations only", "Cancellation rate %"],
                horizontal=True,
            )
            if metric == "All appointments":
                pivot = fdf.pivot_table(index="dia_semana", columns="hora",
                                        values="IdCita", aggfunc="count", fill_value=0)
            elif metric == "Cancellations only":
                pivot = (
                    fdf[fdf["status"] == "Cancelled"]
                    .pivot_table(index="dia_semana", columns="hora",
                                 values="IdCita", aggfunc="count", fill_value=0)
                )
            else:
                total = fdf.pivot_table(index="dia_semana", columns="hora",
                                        values="IdCita", aggfunc="count", fill_value=0)
                cancel = (
                    fdf[fdf["status"] == "Cancelled"]
                    .pivot_table(index="dia_semana", columns="hora",
                                 values="IdCita", aggfunc="count", fill_value=0)
                )
                cancel = cancel.reindex(index=total.index, columns=total.columns, fill_value=0)
                pivot = (cancel / total.replace(0, np.nan) * 100).round(1)

            pivot = pivot.reindex([d for d in DAY_ORDER if d in pivot.index])
            fig_h = px.imshow(pivot, aspect="auto", color_continuous_scale="Reds",
                              labels=dict(x="Hour", y="Day", color=metric))
            st.plotly_chart(fig_h, use_container_width=True)

    # ----- Forecast -----
    with st.expander("🔮 Forecast", expanded=False):
        st.caption(
            "Decomposition forecast: linear trend × multiplicative monthly seasonal "
            "indices, with a ±1.96σ residual band. Seasonality is applied only with "
            "≥24 months of history; otherwise it falls back to a trend line."
        )
        monthly_total = (
            df.groupby(df["fecha"].dt.to_period("M"))
              .agg(
                  total=("IdCita", "count"),
                  attended=("status", lambda s: (s == "Attended").sum()),
                  cancelled=("status", lambda s: (s == "Cancelled").sum()),
              )
        )
        monthly_total.index = monthly_total.index.to_timestamp()

        horizon = st.slider("Months to forecast", 1, 12, 3, key="forecast_horizon")
        metric_fc = st.selectbox(
            "Metric to forecast", ["total", "attended", "cancelled"],
            format_func=lambda x: {"total": "Total appointments",
                                    "attended": "Attended",
                                    "cancelled": "Cancelled"}[x],
        )

        series = monthly_total[metric_fc].dropna()
        if len(series) >= 12:
            fc_series, lower, upper, used_seasonal = seasonal_trend_forecast(series, horizon)
            if not used_seasonal:
                st.info("Fewer than 24 months — using trend only (no seasonal adjustment).")

            fig_fc = go.Figure()
            fig_fc.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines+markers",
                                        name="Actual"))
            # confidence band
            fig_fc.add_trace(go.Scatter(x=list(upper.index) + list(lower.index[::-1]),
                                        y=list(upper.values) + list(lower.values[::-1]),
                                        fill="toself", fillcolor="rgba(99,110,250,0.15)",
                                        line=dict(width=0), hoverinfo="skip",
                                        name="±95% band"))
            fig_fc.add_trace(go.Scatter(x=fc_series.index, y=fc_series.values,
                                        mode="lines+markers", name="Forecast",
                                        line=dict(dash="dash")))
            fig_fc.update_layout(yaxis_title=metric_fc.title(), xaxis_title="")
            st.plotly_chart(fig_fc, use_container_width=True)

            fc_table = pd.DataFrame({
                "Month": fc_series.index.strftime("%Y-%m"),
                f"Forecast ({metric_fc})": fc_series.round(0).astype(int).values,
                "Low": lower.round(0).astype(int).values,
                "High": upper.round(0).astype(int).values,
            })
            st.dataframe(fc_table, use_container_width=True, hide_index=True)
            download_button(fc_table, "⬇ Download forecast CSV", "forecast.csv")
        else:
            st.info("Need at least 12 months of history to forecast.")

    # ----- Cleaned data sample -----
    with st.expander("🔍 Cleaned data sample", expanded=False):
        st.dataframe(
            fdf[["fecha", "HoraCita", "Estado", "status", "tratamiento",
                 "Cédula", "Solicitante", "Responsable del Servicio"]].head(200),
            use_container_width=True, hide_index=True,
        )

with tab_formulas:
    st.markdown(load_formulas())
