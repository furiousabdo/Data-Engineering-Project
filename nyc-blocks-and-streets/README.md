# Blocks & Streets

**Where NYC housing distress meets street danger.**
GIU · Data Engineering and Visualization · Winter 2026 · Project Milestone 1

> Do the ZIP codes where buildings are failing also have the streets that hurt people walking and cycling?

| | |
|---|---|
| **Housing & Development** | [Housing Maintenance Code Complaints and Problems](https://data.cityofnewyork.us/d/ygpa-z7cr) (HPD, `ygpa-z7cr`, catalog H6) |
| **Transportation** | [Motor Vehicle Collisions, Crashes](https://data.cityofnewyork.us/d/h9gi-nx95) (NYPD, `h9gi-nx95`) |
| **Integration** | Both aggregated to **ZIP code × month** (Jan 2024 to Dec 2025), then outer-joined |
| **Website** | Dash (Python/Plotly), deployed on Render |
| **Live site** | `https://<your-service>.onrender.com` *(fill in after deploying)* |

---

## Repository layout

```
.
├── app.py                       # Dash website (filters, search, Generate Report, story, 10 charts)
├── assets/style.css             # site styling (Dash loads it automatically)
├── data/
│   ├── processed/               # REAL aggregated tables exported by the notebook (commit these)
│   └── sample/                  # synthetic demo tables (used only when processed/ is empty)
├── notebook/
│   └── blocks_and_streets.ipynb # full pipeline: EDA, cleaning, integration, 10 RQs, export
├── requirements.txt             # website dependencies
├── render.yaml / Procfile       # deployment config (Render / Heroku)
└── README.md
```

---

## Setup

### 1. Run the notebook (produces the real data)
1. Open `notebook/blocks_and_streets.ipynb` in **Google Colab** (File → Upload notebook) or Jupyter.
2. *Optional:* set a free Socrata app token to avoid rate limits: `os.environ["SOCRATA_APP_TOKEN"] = "..."`.
3. **Runtime → Run all.** The notebook downloads 24 monthly slices of each dataset through the SoQL API (about 15 to 30 minutes the first
   time; cached as parquet afterwards), cleans, integrates, answers the 10 research questions and exports 5 small tables.
4. In Colab the last cell downloads `processed.zip`. Unzip it into `data/processed/` in this repo and commit.

### 2. Run the website locally
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                                           # http://localhost:8050
```
If `data/processed/` is empty the site runs on `data/sample/` and shows a **Demo mode** banner.

### 3. Deploy (Render, free tier)
1. Push the repo to GitHub (with `data/processed/` committed).
2. On [render.com](https://render.com): **New → Blueprint**, pick the repo. `render.yaml` configures everything
   (build `pip install -r requirements.txt`, start `gunicorn app:server`).
   *Manual alternative:* **New → Web Service**, runtime Python, same build and start commands.
3. Wait for the build, open the URL, press **Generate Report**, try a search, and paste the URL into this README and the submission email.

Free Render instances sleep after inactivity; the first request after a pause takes about 30 to 60 seconds.

---

## Website features (rubric checklist)

| Requirement | Where |
|---|---|
| Multiple dropdown filters | Borough (multi), Year, Housing problem type, Vehicle involved, Street metric |
| Search mode | Free text such as `Brooklyn 2025 heat trucks` or `Bronx pedestrian mold`; recognised terms appear as chips and set the dropdowns |
| Central **Generate Report** button | Rebuilds all 10 charts, KPI tiles, findings and the ZIP table from the current filters (a search also triggers it) |
| Variety of chart types | scatter, map, line, histogram, bars, diverging bars, box, heatmap |
| Cross-domain visualizations | RQ1 scatter (housing vs crashes), RQ2 map (both indices), RQ5 to RQ10 |
| Story section | Question, data, discovery (computed live from the filtered data), limits, impact |
| Interactivity | hover tooltips everywhere, zoom and pan, legend toggles, sortable table |

---

## Method in one minute

* **Pre-integration cleaning.** Duplicates, mixed-case boroughs, malformed ZIPs (`11201.0`), coordinates at (0,0), free-text vehicle types
  (hundreds of spellings → 6 groups), factor typos, inconsistent injury totals, impossible repair durations. Missing crash ZIPs (about a
  third of crashes) are **imputed by nearest-neighbour from coordinates** (≤200 m) instead of dropped.
* **Integration.** Both sides aggregated to ZIP × month (many-to-one), outer-joined with a one-to-one key check.
* **Post-integration cleaning.** Join-created NaNs → 0 for counts, NaN for undefined rates; duplicate borough columns reconciled; types fixed;
  full ZIP × month grid; low-volume non-residential ZIPs removed; anomalous ZIP-months flagged.
* **Two size-robust indices with different denominators** (so no shared denominator manufactures a correlation):
  * **HDI**, Housing Distress Index = urgent problems per complaining building
  * **SDI**, Street Danger Index = pedestrian and cyclist casualties per 100 crashes
* **Statistics.** Spearman and partial Spearman (controlling for building stock), Wilcoxon, Kruskal-Wallis, chi-square.

---

## Research questions

| # | Member | Question | Chart |
|---|---|---|---|
| RQ1 | 1 | Do ZIPs with more housing problems also record more crash harm, beyond sheer size? | log-log scatter + partial rho |
| RQ2 | 1 | Where are the double-burden ZIPs (top quartile on HDI and SDI)? | map |
| RQ3 | 2 | Do housing problems and injuries share a seasonal rhythm? | indexed line (one axis) |
| RQ4 | 2 | After removing seasonality, do a ZIP's bad housing months match its bad street months? | histogram |
| RQ5 | 3 | Which housing problem types track street danger most closely? | ranked bars |
| RQ6 | 3 | Which crash causes are over-represented in high-distress ZIPs? | diverging bars |
| RQ7 | 4 | Do ZIPs with more dangerous streets wait longer for housing repairs? | box plot |
| RQ8 | 4 | Do crashes in high-distress ZIPs happen at different hours? | hour profile lines |
| RQ9 | 5 | When and where does combined neighborhood stress peak? | heatmap |
| RQ10 | 5 | Did ZIPs where housing problems grew (2024 → 2025) also see injuries grow? | change scatter |

---

## Team contributions

| Member | Research questions | Visualizations | Data cleaning | Integration | Website |
|---|---|---|---|---|---|
| Member 1 (Mohammed Walid, 13003322) | RQ1, RQ2 | log-log scatter, burden map | shared geo helpers (borough, ZIP, coordinates) | ZIP × month key design | map + cross-domain scatter |
| Member 2 (Abdelrahman Farajallah, 13003482) | RQ3, RQ4 | indexed line, anomaly histogram | date parsing, study window | full grid reindex (post-join) | time-series panels |
| Member 3 (Omar Turki, 19010638) | RQ5, RQ6 | ranked bars, diverging bars | housing category standardization | housing aggregation | category filter + search parser |
| Member 4 (Mostafa Eltatawi, 13007048) | RQ7, RQ8 | box plot, hour profile | repair-time outliers (IQR + cap) | post-join NaN policy | KPI tiles |
| Member 5 (Ahmed Raslan, 13005475) | RQ9, RQ10 | heatmap, change scatter | crash victim counts consistency | crash aggregation | Generate Report callback |

---

## Limitations
Correlation is not causation: income, housing age, arterial roads and reporting habits plausibly drive both burdens. Complaints measure
reporting, not conditions. Crashes are police-reported only, and crashes with no location are excluded. "Complaining buildings" is a proxy
for residential stock. ZIP codes are postal routes, not neighborhoods, and ZIP-level results say nothing about any single household.

## Prompts used
* *"Go through the project details and tell me the project requirements, choose the datasets Housing Maintenance Code Complaints and Problems (https://data.cityofnewyork.us/d/ygpa-z7cr) Motor Vehicle Collisions, Crashes(https://data.cityofnewyork.us/d/h9gi-nx95), make sure to go through the details
  line by line, and for this project you are a professional data engineer and visualizer."* (Claude; dataset selection, pipeline and site
  scaffolding, README draft. All code was reviewed and run by the team.)
* *(add every further prompt the team used)*
