# Clinic Appointments Dashboard

Interactive Streamlit dashboard for analyzing dental clinic appointment data from an Excel export.

The app is organized into two tabs: a **📊 Dashboard** and a **📐 Formulas** reference
that documents every computed value (rendered from `FORMULAS.md`).

## Features

- Upload an appointments `.xlsx` (Estado, Asunto, Cédula, Anio/Mes/Dia, HoraCita, etc.)
- KPIs: total / attended / cancelled / no-show appointments, cancellation & attendance rates, average monthly patients
- Monthly, rolling-3-month, and yearly breakdowns
- **Treatment performance** — popularity vs. financial impact: volume, list-price revenue, avg unit price (with min/max range), and a popularity-vs-value scatter
- **Yearly / quarterly treatment performance** — pivot matrix, first→last movers, and per-treatment trend lines (by revenue or volume)
- **Financial & revenue** — theoretical billed value (ortho separated, since ortho is installment-based), price-match coverage, lost revenue to cancellations/no-shows, and actual-vs-theoretical comparison when a revenue file is uploaded
- New vs returning patients
- Booking lead time and cancellation rate per lead-time bucket
- Patient retention: visits per patient, lifetime, days between visits, year-over-year, 6-month back-to-back, active-patient rate, cohort heatmap
- Day-of-week × hour heatmap (volume, cancellations, cancellation rate)
- Forecast — linear trend × multiplicative seasonal indices with a ±1.96σ band
- CSV export on every table
- Pandemic-months exclusion toggle (May–Aug 2020)
- Collapsible sections; full-screen Formulas tab

## Pricing & revenue

- **Price list** is bundled as `price_list.csv` (reference data, prices unchanged since 2019). Treatment text from `Asunto` is normalized and matched to it (~80% coverage by volume); unpriced appointments still count for volume but are excluded from value totals.
- **Monthly revenue** is uploaded in the sidebar (wide "Facturación año a año" layout: months in rows, years in columns) and is **never stored on disk** — re-upload an updated file anytime.
- Cancelled and no-show appointments contribute **$0** to revenue; their list-price value is reported separately as lost revenue.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then upload your appointments `.xlsx` from the sidebar.

## Expected columns

`Estado`, `IdCita`, `Fecha requerido`, `Cédula`, `Solicitante`, `Asunto`,
`Responsable del Servicio`, `Especialidad`, `HoraCita`, `Dia`, `Mes`, `Anio`,
`FechaRegistro`.

Title rows above the headers must be removed before upload.

## Data privacy

`.xlsx` / `.xls` / `.csv` files are gitignored so patient and revenue data are never
committed. The only exception is `price_list.csv` (reference price data, no patient or
financial records), which is bundled intentionally.
