# Clinic Appointments Dashboard

Interactive Streamlit dashboard for analyzing dental clinic appointment data from an Excel export.

## Features

- Upload an `.xlsx` file (Estado, Asunto, Cédula, Anio/Mes/Dia, HoraCita, etc.)
- KPIs: total / attended / cancelled / no-show appointments, cancellation & attendance rates, average monthly patients
- Monthly, rolling-3-month, and yearly breakdowns
- Treatment-type breakdown with optional "merge degrees" grouping
- New vs returning patients
- Booking lead time and cancellation rate per lead-time bucket
- Patient retention: visits per patient, lifetime, days between visits, year-over-year, active-patient rate, cohort heatmap
- Day-of-week × hour heatmap (volume, cancellations, cancellation rate)
- Naïve forecast (seasonal + 3-month trend)
- CSV export on every table
- Pandemic-months exclusion toggle (May–Aug 2020)

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

`.xlsx` / `.xls` / `.csv` files are gitignored. Do not commit patient data.
