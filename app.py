from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

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

if excluded:
    df = df[~df["Estado"].isin(excluded)].copy()

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

st.divider()
kpi_row(fdf)
st.divider()

# Estado distribution / data quality
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
        fig_e = px.bar(estado_counts, x="Estado", y="count", color="bucket",
                       text="count")
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

st.divider()

# Monthly chart
monthly = monthly_breakdown(fdf)
st.subheader("📅 Appointments per month")
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
    st.subheader("Monthly cancellation rate")
    fig2 = px.line(monthly, x="mes", y="Cancellation rate %", markers=True)
    fig2.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig2, use_container_width=True)
with col_b:
    st.subheader("Rolling 3-month cancellation rate")
    r3 = rolling_3m(monthly)
    fig3 = px.line(r3, x="mes", y="Cancellation rate % (3M)", markers=True)
    fig3.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig3, use_container_width=True)

st.divider()
st.subheader("📆 Yearly breakdown")
yearly = yearly_breakdown(fdf)
st.dataframe(yearly, use_container_width=True, hide_index=True)
download_button(yearly, "⬇ Download yearly CSV", "yearly_breakdown.csv")

st.subheader("Monthly detail")
monthly_out = monthly.assign(mes=monthly["mes"].dt.strftime("%Y-%m"))
st.dataframe(monthly_out, use_container_width=True, hide_index=True)
download_button(monthly_out, "⬇ Download monthly CSV", "monthly_breakdown.csv")

st.subheader("Rolling 3-month detail")
r3_out = r3.assign(mes=r3["mes"].dt.strftime("%Y-%m"))
st.dataframe(r3_out, use_container_width=True, hide_index=True)
download_button(r3_out, "⬇ Download rolling 3-month CSV", "rolling_3m.csv")

st.divider()

# Treatment type
st.subheader("🧪 Top treatment types")

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

work = fdf.dropna(subset=["tratamiento"]).copy()
if group_mode.startswith("Merge"):
    work["tratamiento"] = work["tratamiento"].map(normalize_treatment)

treat = (
    work
    .groupby("tratamiento")
    .agg(
        total=("IdCita", "count"),
        attended=("status", lambda s: (s == "Attended").sum()),
        cancelled=("status", lambda s: (s == "Cancelled").sum()),
        noshow=("status", lambda s: (s == "No-show").sum()),
    )
    .sort_values("total", ascending=False)
    .reset_index()
)
treat["cancellation_rate_%"] = (
    treat["cancelled"]
    / (treat["attended"] + treat["cancelled"] + treat["noshow"]).replace(0, np.nan)
    * 100
).round(1)
top = treat.head(top_n)
fig_t = px.bar(top, x="total", y="tratamiento", orientation="h",
               hover_data=["attended", "cancelled", "noshow", "cancellation_rate_%"])
fig_t.update_layout(yaxis={"categoryorder": "total ascending"}, height=max(400, 22 * top_n))
st.plotly_chart(fig_t, use_container_width=True)
st.dataframe(treat, use_container_width=True, hide_index=True)
download_button(treat, "⬇ Download treatments CSV", "treatments.csv")

st.divider()

# New vs returning
if "paciente_tipo" in fdf.columns:
    st.subheader("👥 New vs returning patients")
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

st.divider()

# Booking lead time
if "lead_days" in fdf.columns and fdf["lead_days"].notna().any():
    st.subheader("⏱ Booking lead time")
    st.caption("Days between FechaRegistro (when booked) and Fecha requerido (when scheduled).")
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

        # Cancellation rate by lead-time bucket
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

st.divider()

# Retention
st.subheader("🔁 Patient retention")
st.caption(
    "Uses the full dataset (ignores the date range filter above). "
    "Patients whose first visit is in 2019 are dropped to avoid left-censoring."
)

if "Cédula" in df.columns:
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
        k2.metric("Returning patients", f"{returning_patients:,}",
                  help="Patients with 2+ visits")
        k3.metric("Returning rate", f"{ret_rate:.1f}%")
        k4.metric("Avg visits / patient", f"{appts_per_patient.mean():.2f}",
                  help=f"Median: {appts_per_patient.median():.0f}, "
                       f"Max: {appts_per_patient.max():.0f}")

        # ----- Visits-per-patient histogram -----
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

        # ----- Patient lifetime -----
        st.markdown("**Patient lifetime** — days between each patient's first and last visit "
                    "(0 = single-visit patient).")
        lifespan = (
            eligible.groupby("Cédula")["fecha"]
            .agg(["min", "max"])
        )
        lifespan["lifetime_days"] = (lifespan["max"] - lifespan["min"]).dt.days
        lt = lifespan["lifetime_days"]
        multi = lt[lt > 0]
        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Mean lifetime", f"{lt.mean():.0f} days",
                  help=f"≈ {lt.mean()/30:.1f} months")
        l2.metric("Median lifetime", f"{lt.median():.0f} days")
        l3.metric("Mean (multi-visit only)", f"{multi.mean():.0f} days" if not multi.empty else "—",
                  help="Excludes single-visit patients")
        l4.metric("Max lifetime", f"{lt.max():.0f} days")
        fig_lt = px.histogram(
            lt.clip(upper=2000),
            nbins=60,
            labels={"value": "Lifetime in days (capped at 2000)"},
        )
        fig_lt.update_layout(showlegend=False)
        st.plotly_chart(fig_lt, use_container_width=True)

        # ----- Days between consecutive visits -----
        st.markdown("**Days between consecutive visits**")
        sorted_visits = eligible.sort_values(["Cédula", "fecha"])
        sorted_visits["gap_days"] = (
            sorted_visits.groupby("Cédula")["fecha"].diff().dt.days
        )
        gaps = sorted_visits["gap_days"].dropna()
        if not gaps.empty:
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Mean gap", f"{gaps.mean():.0f} days")
            g2.metric("Median gap", f"{gaps.median():.0f} days")
            g3.metric("25th pct", f"{gaps.quantile(.25):.0f} days")
            g4.metric("75th pct", f"{gaps.quantile(.75):.0f} days")
            fig_gap = px.histogram(
                gaps.clip(upper=730),
                nbins=60,
                labels={"value": "Days since previous visit (capped at 730)"},
            )
            fig_gap.update_layout(showlegend=False)
            st.plotly_chart(fig_gap, use_container_width=True)

        # ----- Year-over-year retention -----
        st.markdown("**Year-over-year retention** — of patients seen in year X, "
                    "what % had any visit in year X+1.")
        eligible["año_visit"] = eligible["fecha"].dt.year
        patients_by_year = (
            eligible.groupby("año_visit")["Cédula"].apply(set).sort_index()
        )
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
                "YoY retention %": round(retained / len(this_yr) * 100, 1)
                if this_yr else 0,
            })
        if rows:
            yoy = pd.DataFrame(rows)
            st.dataframe(yoy, use_container_width=True, hide_index=True)
            download_button(yoy, "⬇ Download YoY CSV", "yoy_retention.csv")
            fig_yoy = px.line(yoy, x="Year", y="YoY retention %", markers=True)
            fig_yoy.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig_yoy, use_container_width=True)

        # ----- 6-month retention (SaaS-style formula, back-to-back) -----
        st.markdown(
            "**6-month retention (back-to-back)** — for each half-year period, "
            "`(E − N) / S × 100` where "
            "S = patients in the previous half-year, "
            "E = patients in the current half-year, "
            "N = patients in E who were NOT in S "
            "(both newly acquired and reactivated after a gap). "
            "This measures strict period-to-period continuity."
        )
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
            download_button(half_df, "⬇ Download 6-month retention CSV",
                            "half_year_retention.csv")

        # ----- Active patient rate (quarter-over-quarter) -----
        st.markdown("**Active-patient rate** — % of a quarter's patients who "
                    "were also active the previous quarter.")
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
                "Active retention %": round(overlap / len(cur) * 100, 1)
                if cur else 0,
            })
        if qrows:
            qdf = pd.DataFrame(qrows)
            fig_qr = px.line(qdf, x="Quarter", y="Active retention %", markers=True)
            fig_qr.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig_qr, use_container_width=True)
            with st.expander("Quarter-by-quarter detail"):
                st.dataframe(qdf, use_container_width=True, hide_index=True)

        # ----- Cohort retention heatmap -----
        st.markdown("**Cohort retention** — % of each first-visit cohort that "
                    "came back N months later.")
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
            cohort_pct,
            aspect="auto",
            color_continuous_scale="Blues",
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

st.divider()

# Heatmap
if "hora" in fdf.columns:
    st.subheader("🔥 Day-of-week × hour heatmap")
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

st.divider()

# Forecast
st.subheader("🔮 Forecast")
st.caption(
    "Naïve projection: each future month combines a seasonal baseline "
    "(same calendar month, prior years) with a 3-month moving-average trend. "
    "This is directional, not a calibrated model."
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
if len(series) >= 6:
    history = series.copy()
    trend = history.rolling(3, min_periods=1).mean()

    forecast_idx = pd.date_range(
        history.index.max() + pd.offsets.MonthBegin(1),
        periods=horizon, freq="MS",
    )
    forecasts = []
    for d in forecast_idx:
        same_month = history[history.index.month == d.month]
        seasonal = same_month.tail(3).mean() if not same_month.empty else history.tail(3).mean()
        recent = trend.iloc[-3:].mean()
        forecasts.append((seasonal + recent) / 2)
    fc_series = pd.Series(forecasts, index=forecast_idx, name="forecast")

    plot_df = pd.concat(
        [history.rename("actual").to_frame(),
         fc_series.to_frame()],
        axis=1,
    ).reset_index().rename(columns={"index": "month"})

    fig_fc = px.line(plot_df, x="month", y=["actual", "forecast"],
                     markers=True,
                     labels={"value": metric_fc.title(), "variable": ""})
    st.plotly_chart(fig_fc, use_container_width=True)

    fc_table = fc_series.round(0).astype(int).reset_index()
    fc_table.columns = ["Month", f"Forecast ({metric_fc})"]
    fc_table["Month"] = fc_table["Month"].dt.strftime("%Y-%m")
    st.dataframe(fc_table, use_container_width=True, hide_index=True)
    download_button(fc_table, "⬇ Download forecast CSV", "forecast.csv")
else:
    st.info("Need at least 6 months of history to forecast.")

st.divider()
with st.expander("🔍 Cleaned data sample"):
    st.dataframe(
        fdf[["fecha", "HoraCita", "Estado", "status", "tratamiento",
             "Cédula", "Solicitante", "Responsable del Servicio"]].head(200),
        use_container_width=True, hide_index=True,
    )
