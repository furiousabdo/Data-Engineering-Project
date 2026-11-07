"""Blocks & Streets: an interactive Dash site linking NYC housing distress (HPD) and street danger (NYPD crashes).

Reads the small pre-aggregated tables exported by the notebook (data/processed/) and rebuilds every chart from the
current filters when the user presses "Generate Report" (or submits a search query).
"""
from __future__ import annotations

import os
import re
import pathlib

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update
from scipy import stats

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data" / "processed"
DEMO = not (DATA / "zip_meta.csv").exists()
if DEMO:  # no real export yet: run on the schema-matched synthetic sample so the site still works end to end
    DATA = ROOT / "data" / "sample"

# ----------------------------------------------------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------------------------------------------------
META = pd.read_csv(DATA / "zip_meta.csv", dtype={"zip": str})
H = pd.read_csv(DATA / "housing_zip_month_category.csv.gz", dtype={"zip": str}, parse_dates=["month"])
C = pd.read_csv(DATA / "crash_zip_month_vehicle.csv.gz", dtype={"zip": str}, parse_dates=["month"])
HOURS = pd.read_csv(DATA / "crash_zip_year_vehicle_hour.csv.gz", dtype={"zip": str})
FACTORS = pd.read_csv(DATA / "crash_zip_year_vehicle_factor.csv.gz", dtype={"zip": str})
for t in (H, C):
    t["year"] = t["month"].dt.year
    t["borough"] = t["zip"].map(META.set_index("zip")["borough"])
for t in (HOURS, FACTORS):
    t["borough"] = t["zip"].map(META.set_index("zip")["borough"])

BORO_ORDER = ["MANHATTAN", "BRONX", "BROOKLYN", "QUEENS", "STATEN ISLAND"]
VEHICLE_ORDER = ["Bus", "Truck / Van", "Two-wheeler / Micromobility", "Taxi / For-hire", "Car / SUV", "Other / Unknown"]
CATEGORIES = H.groupby("major_category")["problems"].sum().sort_values(ascending=False).index.tolist()
YEARS = sorted(H["year"].unique().tolist())
MONTHS = sorted(H["month"].unique())

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRAY = "#9a9994"
BORO_COLOR = dict(zip(BORO_ORDER, PALETTE[:5]))
BORO_SYMBOL = dict(zip(BORO_ORDER, ["circle", "square", "diamond", "triangle-up", "x"]))
BURDEN_COLORS = {"Double burden": PALETTE[7], "Housing distress only": PALETTE[1], "Street danger only": PALETTE[0],
                 "Neither (below Q4)": GRAY}
QUARTILES = ["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"]

METRICS = {
    "sdi": "Street Danger Index (ped & cyclist casualties per 100 crashes)",
    "vru_casualties": "Pedestrian & cyclist casualties",
    "injured": "People injured",
    "killed": "People killed",
    "crashes": "Crashes",
}

# ----------------------------------------------------------------------------------------------------------------------
# Statistics helpers (same definitions as the notebook)
# ----------------------------------------------------------------------------------------------------------------------
def partial_spearman(x, y, z):
    d = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    if len(d) < 8:
        return np.nan, np.nan
    d = d.rank()
    rx = d["x"] - np.polyval(np.polyfit(d["z"], d["x"], 1), d["z"])
    ry = d["y"] - np.polyval(np.polyfit(d["z"], d["y"], 1), d["z"])
    r = np.corrcoef(rx, ry)[0, 1]
    t = r * np.sqrt((len(d) - 3) / max(1e-12, 1 - r ** 2))
    return r, 2 * stats.t.sf(abs(t), len(d) - 3)


def spearman(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 8:
        return np.nan, np.nan
    r, p = stats.spearmanr(d["x"], d["y"])
    return r, p


def strength(r):
    if pd.isna(r):
        return "not enough data"
    a = abs(r)
    return "negligible" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def quartile(s):
    s = s.dropna()
    if s.nunique() < 4:
        return pd.Series(pd.NA, index=s.index)
    return pd.qcut(s.rank(method="first"), 4, labels=QUARTILES)


def fmt_p(p):
    return "n/a" if pd.isna(p) else ("p < 0.001" if p < 0.001 else f"p = {p:.3f}")


# ----------------------------------------------------------------------------------------------------------------------
# Filtering and the ZIP-level view
# ----------------------------------------------------------------------------------------------------------------------
def _sel(df, boroughs, year=None, cat=None, veh=None):
    m = df["borough"].isin(boroughs)
    if year not in (None, "all"):
        m &= df["year"] == int(year)
    if cat not in (None, "all") and "major_category" in df:
        m &= df["major_category"] == cat
    if veh not in (None, "all") and "vehicle_group" in df:
        m &= df["vehicle_group"] == veh
    return df[m]


def zip_view(boroughs, year, cat, veh):
    h = _sel(H, boroughs, year, cat).groupby("zip")[["problems", "urgent", "resolved_n", "resolved_days_sum"]].sum()
    c = _sel(C, boroughs, year, veh=veh).groupby("zip")[["crashes", "injured", "killed", "vru_casualties", "night_crashes"]].sum()
    z = META[META["borough"].isin(boroughs)].set_index("zip").join(h).join(c).fillna(
        {k: 0 for k in ["problems", "urgent", "resolved_n", "resolved_days_sum", "crashes", "injured", "killed",
                        "vru_casualties", "night_crashes"]})
    z["hdi"] = z["urgent"] / z["buildings"]
    z["sdi"] = np.where(z["crashes"] >= 10, 100 * z["vru_casualties"] / z["crashes"].replace(0, np.nan), np.nan)
    z["repair_days"] = np.where(z["resolved_n"] >= 10, z["resolved_days_sum"] / z["resolved_n"].replace(0, np.nan), np.nan)
    z["log_buildings"] = np.log(z["buildings"])
    z["hdi_q"] = quartile(z["hdi"])
    z["sdi_q"] = quartile(z["sdi"])
    hi_h, hi_s = z["hdi"] >= z["hdi"].quantile(0.75), z["sdi"] >= z["sdi"].quantile(0.75)
    z["burden"] = np.select([hi_h & hi_s, hi_h, hi_s], list(BURDEN_COLORS)[:3], "Neither (below Q4)")
    return z.reset_index()


# ----------------------------------------------------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------------------------------------------------
def style(fig, x=None, y=None, height=380, legend=True):
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=55, r=20, t=20, b=50),
                      font=dict(family="Inter, system-ui, sans-serif", size=12, color="#0b0b0b"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#fcfcfb", showlegend=legend,
                      legend=dict(title=None, orientation="h", y=-0.22, x=0), hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(gridcolor="#ecebe8", linecolor="#c3c2b7", zeroline=False, title=x)
    fig.update_yaxes(gridcolor="#ecebe8", linecolor="#c3c2b7", zeroline=False, title=y)
    return fig


def empty(msg="Not enough data for this filter combination"):
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=14, color="#52514e"), x=0.5, y=0.5, xref="paper", yref="paper")
    fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
    return style(fig, legend=False, height=300)


def fig_rq1(z, metric):
    d = z[(z["problems"] > 0) & (z[metric] > 0)]
    if len(d) < 8:
        return empty(), "Not enough ZIPs with both housing problems and crashes."
    fig = px.scatter(d, x="problems", y=metric, color="borough", symbol="borough", log_x=True, log_y=metric != "sdi",
                     color_discrete_map=BORO_COLOR, symbol_map=BORO_SYMBOL, category_orders={"borough": BORO_ORDER},
                     hover_name="zip", hover_data={"borough": False, "problems": ":,", metric: ":,.1f", "buildings": ":,"})
    fig.update_traces(marker=dict(size=9, line=dict(width=1, color="white")))
    lx, ly = np.log10(d["problems"]), np.log10(d[metric]) if metric != "sdi" else d[metric]
    b, a = np.polyfit(lx, ly, 1)
    xs = np.linspace(lx.min(), lx.max(), 50)
    fig.add_scatter(x=10 ** xs, y=10 ** (a + b * xs) if metric != "sdi" else a + b * xs, mode="lines", name="trend",
                    line=dict(color=GRAY, width=2), hoverinfo="skip")
    r, p = spearman(d["problems"], d[metric])
    pr, pp = partial_spearman(d["problems"], d[metric], d["log_buildings"])
    note = (f"Spearman rho = {r:.2f} ({strength(r)}, {fmt_p(p)}). After controlling for building stock: rho = {pr:.2f} "
            f"({strength(pr)}, {fmt_p(pp)}), n = {len(d)} ZIPs.")
    return style(fig, "housing problems (log)", METRICS[metric] + ("" if metric == "sdi" else " (log)")), note


def fig_rq2(z, metric):
    d = z.dropna(subset=["latitude", "longitude", "hdi", "sdi"])
    if d.empty:
        return empty(), "No mappable ZIPs."
    size_col = metric if metric != "sdi" else "vru_casualties"
    d = d.assign(_size=d[size_col].clip(lower=0) + 1)
    fig = px.scatter_map(d, lat="latitude", lon="longitude", color="burden", size="_size", size_max=20, zoom=9.2,
                         center=dict(lat=d["latitude"].mean(), lon=d["longitude"].mean()), color_discrete_map=BURDEN_COLORS,
                         category_orders={"burden": list(BURDEN_COLORS)}, hover_name="zip", map_style=os.environ.get("MAP_STYLE", "carto-positron"),
                         hover_data={"borough": True, "hdi": ":.2f", "sdi": ":.1f", size_col: ":,", "_size": False,
                                     "latitude": False, "longitude": False})
    n2 = int((d["burden"] == "Double burden").sum())
    note = (f"{n2} double-burden ZIPs. If the two indices were unrelated we would expect about {len(d) / 16:.0f}. "
            f"Bubble size = {METRICS[size_col].lower()}.")
    fig = style(fig, height=430)
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend=dict(y=0.02, x=0.01, orientation="v", bgcolor="rgba(255,255,255,.85)"))
    return fig, note


def monthly(boroughs, year, cat, veh):
    h = _sel(H, boroughs, year, cat).groupby("month")[["problems", "urgent"]].sum()
    c = _sel(C, boroughs, year, veh=veh).groupby("month")[["crashes", "injured", "vru_casualties"]].sum()
    return h.join(c, how="outer").fillna(0)


def fig_rq3(m):
    if len(m) < 3 or m["problems"].sum() == 0 or m["injured"].sum() == 0:
        return empty(), "Not enough months."
    idx = m / m.mean() * 100
    fig = go.Figure()
    for i, (col, name) in enumerate([("problems", "Housing problems"), ("urgent", "Urgent housing problems"),
                                     ("injured", "People injured in crashes")]):
        fig.add_scatter(x=idx.index, y=idx[col], name=name, mode="lines+markers", line=dict(width=2, color=PALETTE[i]),
                        marker=dict(size=6), hovertemplate="%{x|%b %Y}: %{y:.0f}<extra>" + name + "</extra>")
    fig.add_hline(y=100, line_width=1, line_color=GRAY)
    r, p = spearman(m["problems"], m["injured"])
    note = (f"Housing peaks in {idx['problems'].idxmax():%b %Y}, injuries in {idx['injured'].idxmax():%b %Y}. "
            f"Month-to-month rho = {r:.2f} ({fmt_p(p)}). "
            + ("The seasons run opposite, so a shared calendar cannot explain why the burdens share places." if r < 0 else
               "The seasons move together, so seasonality must be removed before comparing places (see RQ4)."))
    return style(fig, None, "index (period mean = 100)"), note


def fig_rq4(boroughs, year, cat, veh):
    h = _sel(H, boroughs, year, cat).groupby(["zip", "month"])["problems"].sum()
    c = _sel(C, boroughs, year, veh=veh).groupby(["zip", "month"])["injured"].sum()
    p = pd.concat([h, c], axis=1).fillna(0).reset_index()
    if p["month"].nunique() < 6:
        return empty("Needs at least 6 months: choose 'All years'"), "Needs a longer time window."
    for col in ["problems", "injured"]:
        y = np.log1p(p[col])
        p[col + "_a"] = y - y.groupby(p["zip"]).transform("mean") - y.groupby(p["month"]).transform("mean") + y.mean()
    r = p.groupby("zip")[["problems_a", "injured_a"]].apply(lambda g: g["problems_a"].corr(g["injured_a"], method="spearman")).dropna()
    if len(r) < 8:
        return empty(), "Not enough ZIPs."
    fig = px.histogram(r, nbins=25, color_discrete_sequence=[PALETTE[0]])
    fig.update_traces(marker_line_width=1, marker_line_color="white", hovertemplate="rho %{x}: %{y} ZIPs<extra></extra>")
    fig.add_vline(x=0, line_width=1, line_color=GRAY)
    fig.add_vline(x=r.median(), line_width=2, line_color=PALETTE[7], annotation_text=f"median {r.median():.2f}")
    w = stats.wilcoxon(r).pvalue if len(r) > 10 else np.nan
    note = (f"Median within-ZIP rho = {r.median():.2f} (Wilcoxon {fmt_p(w)}); {(r > 0).mean():.0%} of ZIPs positive. "
            + ("No shared short-term shocks: the burdens share places, not months." if (pd.isna(w) or w >= 0.05) else
               "A shared short-term component exists beyond seasonality."))
    return style(fig, "rho between monthly anomalies (per ZIP)", "ZIPs", legend=False), note


def fig_rq5(boroughs, year, z):
    h = _sel(H, boroughs, year).groupby(["zip", "major_category"])["problems"].sum().unstack(fill_value=0)
    zi = z.set_index("zip")
    h = h.loc[h.index.intersection(zi.index)]
    rows = []
    for cat in [c for c in CATEGORIES if c in h.columns][:12]:
        r, p = partial_spearman(h[cat] / zi.loc[h.index, "buildings"], zi.loc[h.index, "sdi"], zi.loc[h.index, "log_buildings"])
        rows.append({"category": cat.title(), "rho": r, "p": p})
    d = pd.DataFrame(rows).dropna()
    if d.empty:
        return empty(), "Not enough data."
    d = d.sort_values("rho")
    d["sig"] = np.where(d["p"] < 0.05, "significant (p < 0.05)", "not significant")
    fig = px.bar(d, x="rho", y="category", orientation="h", color="sig",
                 color_discrete_map={"significant (p < 0.05)": PALETTE[0], "not significant": "#c3c2b7"},
                 hover_data={"p": ":.3f", "sig": False})
    fig.add_vline(x=0, line_width=1, line_color=GRAY)
    top = d.iloc[-1]
    return style(fig, "partial Spearman rho with Street Danger Index", None, height=420), \
        f"Strongest link: {top['category']} (rho = {top['rho']:.2f}, {fmt_p(top['p'])}). Vehicle filter applies to SDI."


def fig_rq6(boroughs, year, veh, z):
    f = _sel(FACTORS, boroughs, year, veh=veh)
    f = f[f["factor"] != "Unspecified"].merge(z[["zip", "hdi_q"]], on="zip").dropna(subset=["hdi_q"])
    if f.empty:
        return empty(), "Not enough data."
    s = f.groupby(["hdi_q", "factor"], observed=True)["crashes"].sum()
    share = (s / s.groupby(level=0, observed=True).transform("sum")).unstack(0)
    top = f.groupby("factor")["crashes"].sum().nlargest(12).index
    if not {"Q1 (lowest)", "Q4 (highest)"} <= set(share.columns):
        return empty(), "Not enough data."
    lift = (share.loc[top, "Q4 (highest)"] / share.loc[top, "Q1 (lowest)"] - 1).mul(100).dropna().sort_values()
    fig = go.Figure(go.Bar(x=lift.values, y=lift.index, orientation="h",
                           marker_color=np.where(lift.values >= 0, PALETTE[7], PALETTE[0]),
                           hovertemplate="%{y}: %{x:+.0f}%<extra></extra>"))
    fig.add_vline(x=0, line_width=1, line_color=GRAY)
    return style(fig, "% difference, top vs bottom HDI quartile", None, height=420, legend=False), \
        f"Most over-represented in high-distress ZIPs: {lift.index[-1]} ({lift.iloc[-1]:+.0f}%)."


def fig_rq7(z):
    d = z.dropna(subset=["sdi_q", "repair_days"])
    if d["sdi_q"].nunique() < 2:
        return empty(), "Not enough data."
    fig = px.box(d, x="sdi_q", y="repair_days", points="all", color_discrete_sequence=[PALETTE[0]],
                 category_orders={"sdi_q": QUARTILES}, hover_data=["zip", "borough"])
    groups = [g["repair_days"] for _, g in d.groupby("sdi_q", observed=True) if len(g) > 2]
    kp = stats.kruskal(*groups).pvalue if len(groups) >= 2 else np.nan
    r, p = spearman(d["sdi"], d["repair_days"])
    return style(fig, "Street Danger Index quartile", "mean days to close (capped 365)", legend=False), \
        f"Kruskal-Wallis across quartiles {fmt_p(kp)}; Spearman rho = {r:.2f} ({strength(r)})."


def fig_rq8(boroughs, year, veh, z):
    hr = _sel(HOURS, boroughs, year, veh=veh).merge(z[["zip", "hdi_q"]], on="zip").dropna(subset=["hdi_q"])
    s = hr.groupby(["hdi_q", "hour"], observed=True)["crashes"].sum()
    prof = (s / s.groupby(level=0, observed=True).transform("sum") * 100).unstack(0)
    if not {"Q1 (lowest)", "Q4 (highest)"} <= set(prof.columns):
        return empty(), "Not enough data."
    fig = go.Figure()
    for q, name, col in [("Q4 (highest)", "Most distressed ZIPs", PALETTE[7]), ("Q1 (lowest)", "Least distressed ZIPs", PALETTE[0])]:
        fig.add_scatter(x=prof.index, y=prof[q], name=name, mode="lines", line=dict(width=2, color=col),
                        hovertemplate="%{x}:00 %{y:.1f}%<extra>" + name + "</extra>")
    for x0, x1 in [(-0.5, 5.5), (19.5, 23.5)]:
        fig.add_vrect(x0=x0, x1=x1, fillcolor="#ecebe8", opacity=0.6, line_width=0, layer="below")
    night = [prof.loc[prof.index.isin([20, 21, 22, 23, 0, 1, 2, 3, 4, 5]), q].sum() for q in ["Q4 (highest)", "Q1 (lowest)"]]
    return style(fig, "hour of day (shaded = dark hours)", "% of the tier's crashes").update_xaxes(dtick=2), \
        f"Night share: {night[0]:.1f}% in the most distressed ZIPs vs {night[1]:.1f}% in the least."


def fig_rq9(boroughs, year, cat, veh):
    h = _sel(H, boroughs, year, cat).groupby(["borough", "month"])["urgent"].sum()
    c = _sel(C, boroughs, year, veh=veh).groupby(["borough", "month"])[["vru_casualties", "crashes"]].sum()
    b = META.groupby("borough")["buildings"].sum()
    d = pd.concat([h, c], axis=1).fillna(0).reset_index()
    if d.empty:
        return empty(), "Not enough data."
    d["hdi_m"] = d["urgent"] / d["borough"].map(b)
    d["sdi_m"] = 100 * d["vru_casualties"] / d["crashes"].replace(0, np.nan)
    for col in ["hdi_m", "sdi_m"]:
        sd = d[col].std()
        d[col + "_z"] = (d[col] - d[col].mean()) / (sd if sd else 1)
    d["stress"] = d[["hdi_m_z", "sdi_m_z"]].mean(axis=1)
    pv = d.pivot(index="borough", columns="month", values="stress").reindex([b for b in BORO_ORDER if b in boroughs])
    fig = go.Figure(go.Heatmap(z=pv.values, x=pv.columns, y=[s.title() for s in pv.index], zmid=0, xgap=2, ygap=2,
                               colorscale=[[0, "#2a78d6"], [0.5, "#f0efec"], [1, "#e34948"]], colorbar=dict(title="z"),
                               hovertemplate="%{y}, %{x|%b %Y}: %{z:.2f}<extra></extra>"))
    top = d.loc[d["stress"].idxmax()]
    return style(fig, None, None, height=320, legend=False), \
        f"Peak combined stress: {top['borough'].title()} in {top['month']:%b %Y} (z = {top['stress']:.2f})."


def fig_rq10(boroughs, cat, veh):
    if len(YEARS) < 2:
        return empty(), "Needs two years."
    y0, y1 = YEARS[-2], YEARS[-1]
    h = _sel(H, boroughs, None, cat).groupby(["zip", "year"])["problems"].sum().unstack()
    c = _sel(C, boroughs, None, veh=veh).groupby(["zip", "year"])["injured"].sum().unstack()
    d = pd.DataFrame({"problems_chg": (h[y1] / h[y0].replace(0, np.nan) - 1) * 100,
                      "injured_chg": (c[y1] / c[y0].replace(0, np.nan) - 1) * 100}).replace([np.inf, -np.inf], np.nan).dropna()
    d = d[d["problems_chg"].between(-90, 300) & d["injured_chg"].between(-90, 300)].join(META.set_index("zip")["borough"])
    if len(d) < 8:
        return empty(), "Not enough data."
    fig = px.scatter(d.reset_index(), x="problems_chg", y="injured_chg", color="borough", symbol="borough", hover_name="zip",
                     color_discrete_map=BORO_COLOR, symbol_map=BORO_SYMBOL, category_orders={"borough": BORO_ORDER})
    fig.update_traces(marker=dict(size=9, line=dict(width=1, color="white")))
    fig.add_hline(y=0, line_width=1, line_color=GRAY); fig.add_vline(x=0, line_width=1, line_color=GRAY)
    r, p = spearman(d["problems_chg"], d["injured_chg"])
    both = ((d["problems_chg"] > 0) & (d["injured_chg"] > 0)).sum()
    return style(fig, f"% change in housing problems, {y0} to {y1}", "% change in people injured"), \
        f"{both} ZIPs worsened on both. rho = {r:.2f} ({strength(r)}, {fmt_p(p)}). Uses both years regardless of the Year filter."


def fig_rq11(z):
    if len(z) < 8 or z["urgent"].sum() == 0 or z["vru_casualties"].sum() == 0:
        return empty(), "Not enough data."
    fig = go.Figure()
    for col, name, color in [("urgent", "Urgent housing problems", PALETTE[1]), ("vru_casualties", "Pedestrian & cyclist casualties", PALETTE[0])]:
        v = np.sort(z[col].to_numpy(float))
        cum = np.insert(np.cumsum(v) / v.sum(), 0, 0)
        x = np.linspace(0, 1, len(cum))
        gini = 1 - 2 * np.trapezoid(cum, x)
        fig.add_scatter(x=x * 100, y=cum * 100, mode="lines", name=f"{name} (Gini {gini:.2f})", line=dict(width=2, color=color))
    fig.add_scatter(x=[0, 100], y=[0, 100], mode="lines", name="Equal share", line=dict(width=1, color=GRAY))
    k = max(1, round(len(z) * 0.10))
    a, b = set(z.nlargest(k, "urgent")["zip"]), set(z.nlargest(k, "vru_casualties")["zip"])
    return style(fig, "% of ZIPs (least to most)", "% of total"), \
        f"Top-10% ZIP sets ({k} each) share {len(a & b)} ZIPs (random expectation about {k * k / len(z):.1f})."


def fig_rq12(boroughs, year, z):
    c = _sel(C, boroughs, year).merge(z[["zip", "hdi_q"]], on="zip").dropna(subset=["hdi_q"])
    c["tier"] = np.where(c["hdi_q"] == "Q4 (highest)", "Most distressed ZIPs (HDI Q4)", "All other ZIPs")
    d = c.groupby(["vehicle_group", "tier"], as_index=False)[["crashes", "vru_casualties"]].sum()
    d = d[(d["vehicle_group"] != "Other / Unknown") & (d["crashes"] >= 20)]
    if d.empty:
        return empty(), "Not enough data."
    d["rate"] = 100 * d["vru_casualties"] / d["crashes"]
    fig = px.bar(d, x="vehicle_group", y="rate", color="tier", barmode="group",
                 category_orders={"vehicle_group": VEHICLE_ORDER, "tier": ["Most distressed ZIPs (HDI Q4)", "All other ZIPs"]},
                 color_discrete_map={"Most distressed ZIPs (HDI Q4)": PALETTE[7], "All other ZIPs": PALETTE[0]},
                 hover_data={"crashes": ":,", "vru_casualties": ":,"})
    fig.update_layout(bargap=0.3, bargroupgap=0.08)
    top = d.sort_values("rate").iloc[-1]
    return style(fig, None, "ped & cyclist casualties per 100 crashes"), \
        f"Highest rate: {top['vehicle_group']}, {top['tier']} ({top['rate']:.1f} per 100 crashes). Ignores the vehicle filter."


# ----------------------------------------------------------------------------------------------------------------------
# Search parser: free text -> filter values
# ----------------------------------------------------------------------------------------------------------------------
BORO_WORDS = {"manhattan": "MANHATTAN", "mn": "MANHATTAN", "bronx": "BRONX", "bx": "BRONX", "brooklyn": "BROOKLYN", "bk": "BROOKLYN",
              "bklyn": "BROOKLYN", "queens": "QUEENS", "qn": "QUEENS", "staten": "STATEN ISLAND", "si": "STATEN ISLAND"}
CAT_WORDS = {"heat": "HEAT/HOT WATER", "heating": "HEAT/HOT WATER", "hot": "HEAT/HOT WATER", "boiler": "HEAT/HOT WATER",
             "plumbing": "PLUMBING", "pipes": "PLUMBING", "paint": "PAINT/PLASTER", "plaster": "PAINT/PLASTER", "lead": "PAINT/PLASTER",
             "mold": "UNSANITARY CONDITION", "mould": "UNSANITARY CONDITION", "pests": "UNSANITARY CONDITION",
             "roaches": "UNSANITARY CONDITION", "mice": "UNSANITARY CONDITION", "rats": "UNSANITARY CONDITION",
             "unsanitary": "UNSANITARY CONDITION", "door": "DOOR/WINDOW", "window": "DOOR/WINDOW", "windows": "DOOR/WINDOW",
             "electric": "ELECTRIC", "electrical": "ELECTRIC", "leak": "WATER LEAK", "leaks": "WATER LEAK", "elevator": "ELEVATOR",
             "appliance": "APPLIANCE", "stairs": "FLOORING/STAIRS", "floor": "FLOORING/STAIRS", "safety": "SAFETY"}
VEH_WORDS = {"truck": "Truck / Van", "trucks": "Truck / Van", "van": "Truck / Van", "vans": "Truck / Van", "bus": "Bus", "buses": "Bus",
             "bike": "Two-wheeler / Micromobility", "bikes": "Two-wheeler / Micromobility", "ebike": "Two-wheeler / Micromobility",
             "e-bike": "Two-wheeler / Micromobility", "scooter": "Two-wheeler / Micromobility", "moped": "Two-wheeler / Micromobility",
             "motorcycle": "Two-wheeler / Micromobility", "taxi": "Taxi / For-hire", "taxis": "Taxi / For-hire",
             "uber": "Taxi / For-hire", "cab": "Taxi / For-hire", "car": "Car / SUV", "cars": "Car / SUV", "suv": "Car / SUV",
             "sedan": "Car / SUV"}
METRIC_WORDS = {"pedestrian": "vru_casualties", "pedestrians": "vru_casualties", "cyclist": "vru_casualties",
                "cyclists": "vru_casualties", "injuries": "injured", "injured": "injured", "deaths": "killed", "killed": "killed",
                "fatal": "killed", "crashes": "crashes", "collisions": "crashes", "danger": "sdi"}


def parse_query(text: str):
    """Return (filters dict, recognized list, ignored list)."""
    t = (text or "").lower()
    t = t.replace("staten island", "staten").replace("hot water", "hot").replace("the bronx", "bronx")
    out, seen, ignored = {}, [], []
    for tok in re.findall(r"[a-z0-9\-/]+", t):
        if tok in BORO_WORDS:
            out.setdefault("borough", [])
            if BORO_WORDS[tok] not in out["borough"]:
                out["borough"].append(BORO_WORDS[tok])
            seen.append(f"Borough: {BORO_WORDS[tok].title()}")
        elif tok.isdigit() and int(tok) in YEARS:
            out["year"] = tok; seen.append(f"Year: {tok}")
        elif tok in CAT_WORDS and CAT_WORDS[tok] in CATEGORIES:
            out["cat"] = CAT_WORDS[tok]; seen.append(f"Housing: {CAT_WORDS[tok].title()}")
        elif tok in VEH_WORDS:
            out["veh"] = VEH_WORDS[tok]; seen.append(f"Vehicle: {VEH_WORDS[tok]}")
        elif tok in METRIC_WORDS:
            out["metric"] = METRIC_WORDS[tok]; seen.append(f"Metric: {METRICS[METRIC_WORDS[tok]]}")
        else:
            hit = next((c for c in CATEGORIES if len(tok) > 3 and tok in c.lower()), None)
            if hit:
                out["cat"] = hit; seen.append(f"Housing: {hit.title()}")
            elif tok not in {"in", "and", "the", "of", "for", "with", "nyc", "new", "york", "all", "show", "me", "buildings",
                             "building", "complaints", "problems"}:
                ignored.append(tok)
    return out, list(dict.fromkeys(seen)), ignored


# ----------------------------------------------------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------------------------------------------------
app = Dash(__name__, title="Blocks & Streets | NYC", suppress_callback_exceptions=True)
server = app.server  # for gunicorn / Render

RQS = [
    ("rq1", "RQ1", "Member 1", "Do ZIPs with more housing problems also record more crash harm, beyond sheer size?"),
    ("rq2", "RQ2", "Member 1", "Where are the double-burden ZIPs (top quartile on both indices)?"),
    ("rq3", "RQ3", "Member 2", "Do housing problems and injuries share a seasonal rhythm?"),
    ("rq4", "RQ4", "Member 2", "After removing seasonality, do a ZIP's bad housing months match its bad street months?"),
    ("rq5", "RQ5", "Member 3", "Which housing problem types track street danger most closely?"),
    ("rq6", "RQ6", "Member 3", "Which crash causes are over-represented in high-distress ZIPs?"),
    ("rq7", "RQ7", "Member 4", "Do ZIPs with more dangerous streets wait longer for housing repairs?"),
    ("rq8", "RQ8", "Member 4", "Do crashes in high-distress ZIPs happen at different hours?"),
    ("rq9", "RQ9", "Member 5", "When and where does combined neighborhood stress peak?"),
    ("rq10", "RQ10", "Member 5", "Did ZIPs where housing problems grew also see injuries grow?"),
    ("rq11", "RQ11", "Member 6", "Are both burdens concentrated in the same few ZIPs?"),
    ("rq12", "RQ12", "Member 6", "Which vehicles hurt pedestrians and cyclists most, and is it worse in high-distress ZIPs?"),
]
WIDE = {"rq9", "rq12"}


def card(key, tag, member, question):
    return html.Section(className="card wide" if key in WIDE else "card", children=[
        html.Div(className="card-head", children=[html.Span(tag, className="tag"), html.Span(member, className="member")]),
        html.H3(question),
        dcc.Loading(dcc.Graph(id=f"g-{key}", config={"displaylogo": False, "scrollZoom": key == "rq2"}), type="dot", color=PALETTE[0]),
        html.P(id=f"n-{key}", className="note"),
    ])


def dropdown(id_, label, options, value, multi=False):
    return html.Div(className="control", children=[
        html.Label(label, htmlFor=id_),
        dcc.Dropdown(id=id_, options=options, value=value, multi=multi, clearable=False),
    ])


app.layout = html.Div(className="page", children=[
    html.Div("Demo mode: showing synthetic sample data. Run the notebook and commit data/processed/ to show real NYC data.",
             className="demo-banner") if DEMO else None,
    html.Header(className="hero", children=[
        html.P("NYC Open Data · Housing & Development × Transportation · 2024 to 2025", className="eyebrow"),
        html.H1("Blocks & Streets"),
        html.P("Do the neighborhoods where buildings are failing also have the streets that hurt people walking and cycling?",
               className="lede"),
    ]),

    html.Section(className="story", id="story", children=[
        html.Div(className="story-col", children=[
            html.H2("The question"),
            html.P("HPD tracks broken boilers, leaks and mold. NYPD and DOT track crashes. They are studied apart, yet a family "
                   "in a building with no heat that also crosses a dangerous arterial carries both burdens at once. We test "
                   "whether these burdens land on the same ZIP codes."),
            html.H2("The data"),
            html.P(["HPD ", html.A("Housing Maintenance Code Complaints and Problems", href="https://data.cityofnewyork.us/d/ygpa-z7cr",
                                   target="_blank"),
                    " and NYPD ", html.A("Motor Vehicle Collisions, Crashes", href="https://data.cityofnewyork.us/d/h9gi-nx95",
                                          target="_blank"),
                    ", cleaned separately and joined at ZIP × month. Two indices with different denominators avoid the trap of "
                    "'bigger places have more of everything':"]),
            html.Ul([html.Li([html.B("Housing Distress Index (HDI): "), "urgent problems per complaining building."]),
                     html.Li([html.B("Street Danger Index (SDI): "), "pedestrian and cyclist casualties per 100 crashes."])]),
        ]),
        html.Div(className="story-col", children=[
            html.H2("The discovery"),
            html.Ul(id="findings", className="findings"),
            html.H2("The limits"),
            html.P("Correlation, not causation: income, housing age, arterial roads and reporting habits plausibly drive both. "
                   "Complaints measure reporting, not conditions. Crashes are police-reported only, and crashes without any "
                   "location are excluded. ZIP codes are postal routes, and a ZIP-level link says nothing about any single household."),
            html.H2("The impact"),
            html.P("HPD and DOT can co-target double-burden ZIPs: proactive inspections alongside Vision Zero redesigns. Tenant "
                   "groups and community boards can cite both burdens in one funding request."),
        ]),
    ]),

    html.Div(className="controls-wrap", id="controls", children=[
        html.Div(className="search-row", children=[
            dcc.Input(id="search", type="text", debounce=False, n_submit=0, className="search",
                      placeholder="Search, e.g. “Brooklyn 2025 heat trucks” or “Bronx pedestrian mold”"),
            html.Button("Apply search", id="search-btn", className="btn secondary", n_clicks=0),
        ]),
        html.Div(id="search-feedback", className="search-feedback"),
        html.Div(className="controls", children=[
            dropdown("f-borough", "Borough", [{"label": b.title(), "value": b} for b in BORO_ORDER], BORO_ORDER, multi=True),
            dropdown("f-year", "Year", [{"label": "All years", "value": "all"}] + [{"label": str(y), "value": str(y)} for y in YEARS], "all"),
            dropdown("f-cat", "Housing problem type", [{"label": "All problem types", "value": "all"}] +
                     [{"label": c.title(), "value": c} for c in CATEGORIES], "all"),
            dropdown("f-veh", "Vehicle involved", [{"label": "All vehicles", "value": "all"}] +
                     [{"label": v, "value": v} for v in VEHICLE_ORDER], "all"),
            dropdown("f-metric", "Street metric (RQ1, RQ2)", [{"label": v, "value": k} for k, v in METRICS.items()], "vru_casualties"),
        ]),
        html.Div(className="report-row", children=[
            html.Button("Generate Report", id="report-btn", className="btn primary", n_clicks=0),
            html.Span(id="report-status", className="status"),
        ]),
    ]),

    html.Section(className="kpis", id="kpis"),
    html.Main(className="grid", children=[card(*rq) for rq in RQS]),

    html.Section(className="card wide table-card", children=[
        html.H3("Top 15 ZIPs by Housing Distress Index (current filters)"),
        dash_table.DataTable(id="zip-table", page_size=15, sort_action="native", style_as_list_view=True,
                             style_header={"fontWeight": 600, "backgroundColor": "#f4f3f0", "border": "none"},
                             style_cell={"fontFamily": "Inter, system-ui, sans-serif", "fontSize": 13, "padding": "8px 10px",
                                         "border": "none", "borderBottom": "1px solid #ecebe8", "textAlign": "left"}),
    ]),
    html.Footer(className="footer", children=[
        html.P("GIU · Data Engineering and Visualization · Winter 2026 · Milestone 1. Source: NYC Open Data (HPD, NYPD). "
               "See the README for team contributions."),
    ]),
])


# ----------------------------------------------------------------------------------------------------------------------
# Callbacks
# ----------------------------------------------------------------------------------------------------------------------
@app.callback(
    Output("f-borough", "value"), Output("f-year", "value"), Output("f-cat", "value"), Output("f-veh", "value"),
    Output("f-metric", "value"), Output("search-feedback", "children"), Output("report-btn", "n_clicks"),
    Input("search-btn", "n_clicks"), Input("search", "n_submit"),
    State("search", "value"), State("report-btn", "n_clicks"),
    prevent_initial_call=True,
)
def apply_search(_btn, _enter, text, clicks):
    filters, seen, ignored = parse_query(text)
    if not seen:
        return (no_update,) * 5 + ([html.Span("No filters recognised. Try a borough, a year, a problem type (heat, mold) or "
                                              "a vehicle (truck, bike).", className="warn")], no_update)
    chips = [html.Span(s, className="chip") for s in seen]
    if ignored:
        chips.append(html.Span(f"ignored: {', '.join(ignored)}", className="chip muted"))
    # Unmentioned dimensions reset to "all" so each search is a fresh query; the report regenerates automatically.
    return (filters.get("borough", BORO_ORDER), filters.get("year", "all"), filters.get("cat", "all"), filters.get("veh", "all"),
            filters.get("metric", "vru_casualties"), chips, (clicks or 0) + 1)


def kpi(label, value, sub=""):
    return html.Div(className="kpi", children=[html.Span(label, className="kpi-label"), html.Span(value, className="kpi-value"),
                                               html.Span(sub, className="kpi-sub")])


OUTPUTS = [Output(f"g-{k}", "figure") for k, *_ in RQS] + [Output(f"n-{k}", "children") for k, *_ in RQS] + [
    Output("kpis", "children"), Output("findings", "children"), Output("zip-table", "data"), Output("zip-table", "columns"),
    Output("report-status", "children")]


@app.callback(*OUTPUTS, Input("report-btn", "n_clicks"),
              State("f-borough", "value"), State("f-year", "value"), State("f-cat", "value"), State("f-veh", "value"),
              State("f-metric", "value"))
def generate_report(_clicks, boroughs, year, cat, veh, metric):
    boroughs = boroughs or BORO_ORDER
    z = zip_view(boroughs, year, cat, veh)
    m = monthly(boroughs, year, cat, veh)
    results = [
        fig_rq1(z, metric), fig_rq2(z, metric), fig_rq3(m), fig_rq4(boroughs, year, cat, veh), fig_rq5(boroughs, year, z),
        fig_rq6(boroughs, year, veh, z), fig_rq7(z), fig_rq8(boroughs, year, veh, z), fig_rq9(boroughs, year, cat, veh),
        fig_rq10(boroughs, cat, veh), fig_rq11(z), fig_rq12(boroughs, year, z),
    ]
    figs, notes = [r[0] for r in results], [r[1] for r in results]

    probs, urg, crashes, inj, vru = (z[c].sum() for c in ["problems", "urgent", "crashes", "injured", "vru_casualties"])
    pr, pp = partial_spearman(z["hdi"], z["sdi"], z["log_buildings"])
    kpis = [
        kpi("Housing problems", f"{probs:,.0f}", f"{urg / probs:.0%} urgent" if probs else ""),
        kpi("Crashes", f"{crashes:,.0f}", f"{inj:,.0f} people injured"),
        kpi("Ped & cyclist casualties", f"{vru:,.0f}", f"{100 * vru / crashes:.1f} per 100 crashes" if crashes else ""),
        kpi("HDI × SDI link", "n/a" if pd.isna(pr) else f"ρ = {pr:+.2f}", f"{strength(pr)}, {fmt_p(pp)}, size-controlled"),
        kpi("ZIPs in view", f"{len(z):,}", f"{(z['burden'] == 'Double burden').sum()} double-burden"),
    ]
    r1, _ = partial_spearman(z["problems"], z["vru_casualties"], z["log_buildings"])
    findings = [
        html.Li([html.B("Co-location: "), f"after controlling for building stock, ZIPs with more urgent housing problems per building "
                 f"show a {strength(pr)} link to street danger for people walking and cycling (ρ = {pr:+.2f}, {fmt_p(pp)})."]),
        html.Li([html.B("Double burden: "), notes[1]]),
        html.Li([html.B("Timing: "), notes[3]]),
        html.Li([html.B("Concentration: "), notes[10]]),
        html.Li([html.B("Response gap: "), notes[6]]),
    ]
    table = z.sort_values("hdi", ascending=False).head(15).assign(
        borough=lambda d: d["borough"].str.title(), hdi=lambda d: d["hdi"].round(2), sdi=lambda d: d["sdi"].round(1),
        repair_days=lambda d: d["repair_days"].round(0), burden=lambda d: d["burden"])
    cols = [("zip", "ZIP"), ("borough", "Borough"), ("problems", "Problems"), ("hdi", "HDI"), ("crashes", "Crashes"),
            ("sdi", "SDI"), ("repair_days", "Repair days"), ("burden", "Burden class")]
    table_data = table[[c for c, _ in cols]].to_dict("records")
    label_year = "all years" if year == "all" else year
    status = (f"Report for {len(boroughs)} borough(s), {label_year}, "
              f"{'all problem types' if cat == 'all' else cat.title()}, {'all vehicles' if veh == 'all' else veh}.")
    return (*figs, *notes, kpis, findings, table_data, [{"name": n, "id": c} for c, n in cols], status)


#if __name__ == "__main__":
 #   import os
  #  app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))

# Vercel serves the WSGI callable named `app`, so expose the Flask server under that name (after all callbacks are registered)
app = server
