## 📐 Formulas & methodology

Every computed value in the dashboard, with its exact formula. Unless noted, metrics
run on the **date-filtered** data (`fdf`); **retention** and **forecast** use the full
dataset (`df`). "Decided" = `Attended + Cancelled + No-show` (Scheduled/Other are
excluded from rate denominators).

### Status bucketing
- **status** = `status_map[Estado]`, unmapped → `Other`. Sidebar maps each Estado into one of: Attended, Cancelled, No-show, Scheduled, Other.

### Headline KPIs
- **Total appointments** = `count(rows)`
- **Attended / Cancelled / No-show** = `count(status == bucket)`
- **Cancellation rate** = `Cancelled / Decided × 100`
- **Attendance rate** = `Attended / Decided × 100`
- **No-show rate** = `No-show / Decided × 100`
- **Unique patients** = `nunique(Cédula)` (blanks stripped)
- **Avg appointments / month** = `Total ÷ nunique(month)`
- **Avg unique patients / month** = `mean over months of nunique(Cédula)`

### Monthly / yearly / rolling
- **Bucket counts** = `groupby(period, status).size()`
- **Total** = `sum(all 5 buckets)`
- **Cancellation / No-show rate %** = `bucket ÷ Decided × 100`
- **Rolling 3-month rate** = `Cancelled(3M) ÷ Decided(3M) × 100`, where `X(3M) = rolling(3).sum()` (volume-weighted, not an average of rates)

### Pricing engine
- **treatment extraction** = take `Asunto` before `(`, drop staff note prefixes split on `!!!` (keep the procedure after the note), strip leading junk
- **normalize_name** = strip accents → uppercase → remove periods → collapse spaces → trim `-/`
- **price match** = exact normalized match → confirmed alias table (e.g. `…CORONA EXT` → `…CORONA PAC EXTERNO`) → strip `TENTATIVA` prefix → split on `+` and sum parts. Exact-only (no fuzzy), so a price is never guessed. ~82% of attended appointments priced
- **att_value** (per row) = `precio if (status == Attended and priced) else 0`. Cancelled/no-show and unpriced rows contribute $0; ~47% of priced attended volume is $0 ortho controls (paid via installments)

### Treatments (popularity vs. value)
- **total / attended / cancelled / noshow** = per-treatment counts
- **avg_unit_price** = `mean(precio)` over priced rows (row-weighted blend across merged degree variants; `price_min` / `price_max` show the spread)
- **theoretical_value** = `sum(att_value)` = attended × list price (**cancelled & no-show contribute $0**)
- **%_of_revenue** = `theoretical_value ÷ Σ theoretical_value × 100`
- **cancellation_rate_%** = `cancelled ÷ (attended + cancelled + noshow) × 100`
- **scatter quadrant lines** = `median(volume)`, `median(theoretical_value)`

### Financial
- **Price-match coverage** = `mean(priced among Attended) × 100`
- **Theoretical billed (ortho / non-ortho)** = `sum(att_value)` split by `is_ortho`
- **Lost to cancel/no-show** = `sum(precio where status ∈ {Cancelled, No-show} and priced)`
- **Actual revenue** = `sum(revenue cells)` from the uploaded file (cash basis, source of truth)
- **Gap** = `Actual − Theoretical(all)` (shown instead of a ratio — actual ÷ theoretical is **not** a valid collection rate here: ortho is paid in installments collected at near-$0 control visits, ~20% of visits are unpriced, and procedure mix shifts over time)

### Yearly / quarterly treatment performance
- **metric** = `sum(att_value)` (Revenue) or `count(rows)` (Volume)
- **matrix** = `pivot_table(treatment × period, metric)` for the top-N treatments by the chosen metric
- **movers Δ / Δ%** = `last_period − first_period`, and `Δ ÷ first_period × 100`

### New vs returning
- **paciente_tipo** = `New if fecha == min(fecha per Cédula) else Returning` (first-ever appearance = New; patients whose true first visit predates 2019 look "New")

### Booking lead time
- **lead_days** = `fecha − fecha_registro` (negatives filtered out)
- **same-day %** = `(lead == 0).mean() × 100`; **<7 days %** = `(lead < 7).mean() × 100`
- **rate by bucket** = `pd.cut(lead_days)` then `cancelled ÷ Decided × 100` per bucket

### Retention (full data, first-visit ≥ 2020)
- **eligible** = visits where `status ∈ chosen buckets`, Cédula non-blank, `first_visit.year ≥ 2020` (drops 2019 to avoid left-censoring)
- **Returning rate** = `patients_with_≥2_visits ÷ total_patients × 100`
- **Avg visits / patient** = `mean(visits per patient)` (median in tooltip)
- **Patient lifetime** = `max(fecha) − min(fecha)` per patient (single-visit = 0)
- **Days between visits** = `groupby(Cédula).fecha.diff()`
- **YoY retention** = `|S_X ∩ S_{X+1}| ÷ |S_X| × 100` (return anywhere in the next year)
- **6-month retention (back-to-back)** = `(E − N) ÷ S × 100`, with `N = E − S` ⟹ `|S ∩ E| ÷ |S|` (strict consecutive half-year continuity)
- **Active-patient rate (quarterly)** = `|cur ∩ prev| ÷ |cur| × 100`
- **Cohort retention** = `unique_patients(cohort, offset) ÷ cohort_size × 100` (offset 0 = 100% by construction)

### Day × hour heatmap
- **counts** = `pivot_table(day × hour, count)`
- **cell cancellation rate** = `cancelled ÷ total × 100` per cell (thin cells are noisy)

### Forecast (full data, ≥12 months)
- **trend** = linear fit `b0 + b1·t` via `np.polyfit` on the month index
- **seasonal index** (per calendar month) = `mean(actual ÷ trend)`, normalized to mean 1; applied only with ≥24 months, else trend-only
- **forecast** = `trend(future) × seasonal_index(month)`, floored at 0
- **band** = `forecast ± 1.96 × residual_std` (rough 95% interval, not a rigorous prediction interval)
