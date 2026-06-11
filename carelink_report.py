#!/usr/bin/env python3
"""
Génération de rapports à partir d'un CSV CareLink fusionné (glycemie.csv).

Produit UN PDF multi-pages (rapport.pdf) regroupant :
  - une page de synthèse (table mensuelle + globale)
  - le profil glycémique journalier (AGP)
  - la comparaison mensuelle (profils médians superposés)
  - l'évolution des moyennes par mois + globale
  - la répartition hypo / cible / hyper en CAMEMBERTS (un par mois + global)
  - la dose de bolus moyenne par heure (par mois + global)
Plus rapport_synthese.xlsx avec les tables chiffrées.
Option --png pour aussi exporter chaque page en image.

Dépendances :
    pip install pandas matplotlib openpyxl

Usage :
    python carelink_report.py glycemie.csv --out rapports
    python carelink_report.py glycemie.csv --out rapports --png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

LOW, HIGH = 70, 180
MANUAL_SOURCES = {"CLOSED_LOOP_BG_CORRECTION_AND_FOOD_BOLUS", "BOLUS_WIZARD"}
COL_HYPO, COL_TIR, COL_HYPER = "#e53935", "#43a047", "#fb8c00"


def _header_row(path: Path) -> int:
    with open(path, encoding="utf-8-sig") as f:
        for i, line in enumerate(f):
            if line.lstrip("\ufeff").startswith("Index;"):
                return i
    raise ValueError("En-tête 'Index;...' introuvable.")


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ".", regex=False), errors="coerce")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", skiprows=_header_row(path), decimal=",",
                     encoding="utf-8-sig", dtype=str, on_bad_lines="skip")
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Time"], format="%Y/%m/%d %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["dt"]).copy()
    df["SG"] = _num(df.get("Sensor Glucose (mg/dL)"))
    df["bolus"] = _num(df.get("Bolus Volume Delivered (U)"))
    df["carb"] = _num(df.get("BWZ Carb Input (grams)"))
    df["source"] = df.get("Bolus Source", pd.Series(index=df.index, dtype=str))
    df["month"] = df["dt"].dt.to_period("M").astype(str)
    df["hour"] = df["dt"].dt.hour
    df["hh"] = df["dt"].dt.hour + df["dt"].dt.minute / 60
    return df


def glucose(df: pd.DataFrame) -> pd.DataFrame:
    sg = df.dropna(subset=["SG"]).copy()
    return sg[(sg["SG"] >= 40) & (sg["SG"] <= 400)]


def manual_boluses(df: pd.DataFrame) -> pd.DataFrame:
    bol = df.dropna(subset=["bolus"]).copy()
    return bol[bol["source"].isin(MANUAL_SOURCES)]


def _months(df: pd.DataFrame) -> list[str]:
    return sorted(df["month"].dropna().unique())


def _gmi(mean: float) -> float:
    return 3.31 + 0.02392 * mean


_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _month_label(m: str) -> str:
    """'2026-01' -> 'janvier 2026'. 'GLOBAL' reste 'GLOBAL'."""
    if m == "GLOBAL":
        return "GLOBAL"
    try:
        year, month = m.split("-")
        return f"{_MOIS_FR[int(month)]} {year}"
    except Exception:
        return m


def _agp(sg: pd.DataFrame) -> pd.DataFrame:
    bins = np.arange(0, 24.5, 0.5)
    cut = pd.cut(sg["hh"], bins, labels=bins[:-1], include_lowest=True).astype(float)
    g = sg.groupby(cut)["SG"]
    return pd.DataFrame({
        "x": bins[:-1], "p10": g.quantile(.10).values, "p25": g.quantile(.25).values,
        "med": g.median().values, "p75": g.quantile(.75).values, "p90": g.quantile(.90).values,
    })


def monthly_glucose_table(sg: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in _months(sg) + ["GLOBAL"]:
        x = (sg if m == "GLOBAL" else sg[sg["month"] == m])["SG"].values
        rows.append({
            "Mois": _month_label(m), "Lectures": len(x), "Moyenne": round(x.mean()),
            "GMI (%)": round(_gmi(x.mean()), 1),
            "Hypo <70 (%)": round((x < LOW).mean() * 100, 1),
            "Cible (%)": round(((x >= LOW) & (x <= HIGH)).mean() * 100, 1),
            "Hyper >180 (%)": round((x > HIGH).mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def hourly_dose_table(bol: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"Heure": [f"{h:02d}h" for h in range(24)]})
    for m in _months(bol) + ["GLOBAL"]:
        sub = bol if m == "GLOBAL" else bol[bol["month"] == m]
        n_days = max(sub["dt"].dt.date.nunique(), 1)
        per_hour = sub.groupby("hour")["bolus"].sum().reindex(range(24), fill_value=0)
        out[_month_label(m)] = (per_hour / n_days).round(2).values
    return out


def _table_page(df: pd.DataFrame, main_title: str, sub_title: str = "",
                fontsize: int = 10, row_scale: float = 1.8) -> plt.Figure:
    """Rend un DataFrame en page-table. Surligne une ligne et/ou colonne 'GLOBAL'."""
    fig, ax = plt.subplots(figsize=(11, 8.5)); ax.axis("off")
    fig.suptitle(main_title, fontsize=18, fontweight="bold", y=0.97)
    if sub_title:
        fig.text(0.5, 0.92, sub_title, ha="center", fontsize=12, color="#444")
    tbl = ax.table(cellText=df.values, colLabels=list(df.columns), loc="center", cellLoc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(fontsize); tbl.scale(1, row_scale)
    ncol = len(df.columns)
    for j in range(ncol):
        tbl[0, j].set_facecolor("#1565c0"); tbl[0, j].get_text().set_color("white")
    cols = list(df.columns)
    gcol = cols.index("GLOBAL") if "GLOBAL" in cols else None
    for i in range(1, len(df) + 1):
        if str(df.iloc[i - 1, 0]) == "GLOBAL":           # ligne GLOBAL
            for j in range(ncol):
                tbl[i, j].set_facecolor("#e3f2fd"); tbl[i, j].get_text().set_fontweight("bold")
        if gcol is not None:                              # colonne GLOBAL
            tbl[i, gcol].set_facecolor("#e3f2fd"); tbl[i, gcol].get_text().set_fontweight("bold")
    return fig


def fig_tables(sg: pd.DataFrame) -> plt.Figure:
    return _table_page(
        monthly_glucose_table(sg), "Rapport glycémie & insuline",
        f"Synthèse mensuelle — {sg['dt'].min().date()} au {sg['dt'].max().date()}",
        fontsize=10, row_scale=1.8)


def fig_dose_table(bol: pd.DataFrame) -> plt.Figure:
    return _table_page(
        hourly_dose_table(bol), "Doses de bolus moyennes par heure",
        "Bolus repas/correction (U) — moyenne par jour, par mois et global",
        fontsize=8, row_scale=1.15)


def fig_agp(sg: pd.DataFrame) -> plt.Figure:
    p = _agp(sg)
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.axhspan(LOW, HIGH, color="#c8e6c9", alpha=.5, label=f"Cible {LOW}-{HIGH}")
    ax.axhline(LOW, color=COL_HYPO, ls="--", lw=.8); ax.axhline(HIGH, color=COL_HYPER, ls="--", lw=.8)
    ax.fill_between(p.x, p.p10, p.p90, color="#90caf9", alpha=.45, label="10-90 %")
    ax.fill_between(p.x, p.p25, p.p75, color="#1e88e5", alpha=.45, label="25-75 %")
    ax.plot(p.x, p["med"], color="#0d47a1", lw=2.5, label="Médiane")
    ax.set_xlim(0, 24); ax.set_ylim(40, 330); ax.set_xticks(range(0, 25, 2))
    ax.set_xlabel("Heure"); ax.set_ylabel("Glycémie (mg/dL)")
    ax.set_title("Profil glycémique journalier (AGP) — toute la période", fontweight="bold")
    ax.legend(fontsize=8, ncol=4, loc="upper center"); ax.grid(alpha=.2)
    fig.tight_layout(); return fig


def fig_agp_monthly(sg: pd.DataFrame) -> plt.Figure:
    months = _months(sg)
    ncols = 2
    nrows = int(np.ceil(len(months) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 3.1 * nrows + 0.6),
                             sharex=True, sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, m in zip(axes, months):
        p = _agp(sg[sg["month"] == m])
        ax.axhspan(LOW, HIGH, color="#c8e6c9", alpha=.5)
        ax.axhline(LOW, color=COL_HYPO, ls="--", lw=.7)
        ax.axhline(HIGH, color=COL_HYPER, ls="--", lw=.7)
        ax.fill_between(p.x, p.p10, p.p90, color="#90caf9", alpha=.45)
        ax.fill_between(p.x, p.p25, p.p75, color="#1e88e5", alpha=.45)
        ax.plot(p.x, p["med"], color="#0d47a1", lw=2)
        ax.set_xlim(0, 24); ax.set_ylim(40, 330); ax.set_xticks(range(0, 25, 6))
        ax.set_title(_month_label(m), fontsize=10, fontweight="bold"); ax.grid(alpha=.2)
    for ax in axes[len(months):]:
        ax.axis("off")
    fig.suptitle("Profil glycémique journalier (AGP) — par mois", fontweight="bold", fontsize=14)
    fig.supxlabel("Heure"); fig.supylabel("Glycémie (mg/dL)")
    fig.tight_layout(rect=(0, 0, 1, 0.96)); return fig


def fig_monthly_comparison(sg: pd.DataFrame) -> plt.Figure:
    months = _months(sg)
    colors = plt.cm.viridis(np.linspace(0, .9, len(months)))
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.axhspan(LOW, HIGH, color="#c8e6c9", alpha=.4); ax.axhline(LOW, color=COL_HYPO, ls="--", lw=.8)
    for m, c in zip(months, colors):
        p = _agp(sg[sg["month"] == m]); ax.plot(p.x, p["med"], color=c, lw=2, label=_month_label(m))
    ax.set_xlim(0, 24); ax.set_ylim(40, 300); ax.set_xticks(range(0, 25, 2))
    ax.set_xlabel("Heure"); ax.set_ylabel("Glycémie médiane (mg/dL)")
    ax.set_title("Comparaison mensuelle — profil médian par mois", fontweight="bold")
    ax.legend(fontsize=8, title="Mois"); ax.grid(alpha=.2)
    fig.tight_layout(); return fig


def fig_mean_evolution(sg: pd.DataFrame) -> plt.Figure:
    t = monthly_glucose_table(sg)
    months = t[t.Mois != "GLOBAL"]; glob = t[t.Mois == "GLOBAL"]["Moyenne"].iloc[0]
    fig, ax = plt.subplots(figsize=(11, 6.5))
    bars = ax.bar(months.Mois, months.Moyenne, color="#1e88e5")
    ax.axhline(glob, color=COL_HYPO, ls="--", lw=2, label=f"Moyenne globale : {glob:.0f}")
    ax.axhspan(LOW, HIGH, color="#c8e6c9", alpha=.3)
    for b in bars:
        ax.annotate(f"{b.get_height():.0f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Glycémie moyenne (mg/dL)")
    ax.set_title("Évolution de la glycémie moyenne par mois", fontweight="bold")
    ax.tick_params(axis="x", labelrotation=20)
    ax.legend(); ax.grid(alpha=.2, axis="y"); fig.tight_layout(); return fig


def fig_tir_pies(sg: pd.DataFrame) -> plt.Figure:
    t = monthly_glucose_table(sg)
    labels = list(t.Mois)
    ncols = 4
    nrows = int(np.ceil(len(labels) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 3 * nrows + 1))
    axes = np.atleast_1d(axes).ravel()
    for ax, (_, row) in zip(axes, t.iterrows()):
        vals = [row["Hypo <70 (%)"], row["Cible (%)"], row["Hyper >180 (%)"]]
        is_global = row["Mois"] == "GLOBAL"
        ax.pie(vals, colors=[COL_HYPO, COL_TIR, COL_HYPER], startangle=90,
               autopct=lambda v: f"{v:.0f}%" if v >= 4 else "",
               textprops={"fontsize": 8, "color": "white", "fontweight": "bold"},
               wedgeprops={"edgecolor": "white", "linewidth": 1.5})
        ax.set_title(row["Mois"], fontweight="bold" if is_global else "normal",
                     fontsize=12 if is_global else 10,
                     color="#0d47a1" if is_global else "black")
    for ax in axes[len(labels):]:
        ax.axis("off")
    fig.legend(["Hypo <70", "Cible 70-180", "Hyper >180"], loc="lower center", ncol=3, fontsize=10)
    fig.suptitle("Répartition hypo / cible / hyper — par mois et global", fontweight="bold", fontsize=14)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95)); return fig


def fig_hourly_doses(bol: pd.DataFrame) -> plt.Figure:
    t = hourly_dose_table(bol)
    months = [c for c in t.columns if c not in ("Heure", "GLOBAL")]
    colors = plt.cm.viridis(np.linspace(0, .9, len(months)))
    fig, ax = plt.subplots(figsize=(11, 7)); x = range(24)
    for m, c in zip(months, colors):
        ax.plot(x, t[m], color=c, lw=1.5, alpha=.8, label=m)
    ax.plot(x, t["GLOBAL"], color=COL_HYPO, lw=3, label="GLOBAL")
    ax.set_xticks(range(0, 24, 2)); ax.set_xticklabels([f"{h}h" for h in range(0, 24, 2)])
    ax.set_xlabel("Heure"); ax.set_ylabel("Bolus moyen par jour (U)")
    ax.set_title("Dose de bolus moyenne par heure — par mois et global\n"
                 "(bolus repas/correction ; insuline auto SmartGuard exclue)", fontweight="bold")
    ax.legend(fontsize=8, title="Mois"); ax.grid(alpha=.2); fig.tight_layout(); return fig


PALETTE = ["#1565c0", "#00897b", "#6a1b9a", "#ef6c00", "#2e7d32", "#5d4037",
           "#0277bd", "#c2185b", "#558b2f", "#4527a0", "#00838f", "#9e9d24"]

HTML_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; margin: 0; background: #eef1f5; color: #1b2733; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 28px 20px 60px; }
header h1 { font-size: 26px; margin: 0 0 4px; color: #0d3b66; }
header .sub { color: #5b6b7b; margin: 0 0 20px; }
.card { background: #fff; border: 1px solid #e3e8ee; border-radius: 12px; padding: 18px 20px; margin: 18px 0; box-shadow: 0 1px 3px rgba(20,40,70,.05); }
.card h2 { font-size: 18px; margin: 0 0 6px; color: #0d3b66; }
.hint { color: #8a97a5; font-size: 12px; margin: 0 0 10px; }
.plot { width: 100%; height: 430px; }
.plot-sm { width: 100%; height: 250px; }
.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.grid.pies { grid-template-columns: repeat(4, 1fr); }
.chart-row { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
.chart-row .plot { flex: 1 1 560px; }
.legend { flex: 0 0 168px; display: flex; flex-direction: column; gap: 5px; padding-top: 8px; }
.leg-item { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 3px 6px; border-radius: 6px; cursor: pointer; transition: background .15s; }
.leg-item:hover { background: #eef4fb; }
.leg-item i { width: 16px; height: 4px; border-radius: 2px; display: inline-block; flex: none; }
table.data { border-collapse: collapse; width: 100%; font-size: 14px; }
table.data th { background: #1565c0; color: #fff; padding: 8px 10px; text-align: center; font-weight: 600; }
table.data td { padding: 7px 10px; text-align: center; border-bottom: 1px solid #eef1f5; }
table.data tbody tr:nth-child(even) { background: #f8fafc; }
table.data .glob { background: #e3f2fd; font-weight: 700; }
table.data tr.glob-row td { background: #e3f2fd; font-weight: 700; }
footer { color: #8a97a5; font-size: 12px; text-align: center; margin-top: 30px; }
@media (max-width: 760px) { .grid.pies { grid-template-columns: 1fr 1fr; } }
"""


def _agp_payload(sgsub: pd.DataFrame) -> dict:
    p = _agp(sgsub)

    def col(name):
        return [None if pd.isna(v) else round(float(v), 1) for v in p[name]]

    return {"x": [round(float(v), 2) for v in p["x"]],
            "p10": col("p10"), "p25": col("p25"), "med": col("med"),
            "p75": col("p75"), "p90": col("p90")}


# Script Plotly. DATA est injecté via .replace (pas de f-string : le JS contient
# beaucoup d'accolades).
JS_TEMPLATE = """
const DATA = /*DATA*/;
const BASE = {};
const CFG = {responsive:true, scrollZoom:true, displaylogo:false,
            modeBarButtonsToRemove:['select2d','lasso2d','autoScale2d']};
const CFG_STATIC = {responsive:true, displaylogo:false,
            modeBarButtonsToRemove:['select2d','lasso2d','zoom2d','pan2d','zoomIn2d','zoomOut2d','autoScale2d']};

function agpTraces(a) {
  const hidden = {mode:'lines', line:{width:0}, hoverinfo:'skip', showlegend:false};
  return [
    Object.assign({x:a.x, y:a.p90}, hidden),
    {x:a.x, y:a.p10, mode:'lines', line:{width:0}, fill:'tonexty',
     fillcolor:'rgba(144,202,249,0.45)', hoverinfo:'skip', showlegend:false},
    Object.assign({x:a.x, y:a.p75}, hidden),
    {x:a.x, y:a.p25, mode:'lines', line:{width:0}, fill:'tonexty',
     fillcolor:'rgba(30,136,229,0.50)', hoverinfo:'skip', showlegend:false},
    {x:a.x, y:a.med, mode:'lines', line:{color:'#0d47a1', width:2.5}, name:'Mediane',
     hovertemplate:'%{x}h \u2014 %{y} mg/dL<extra></extra>'}
  ];
}
function agpLayout(title, big) {
  return {
    title:{text:title, font:{size:13}, x:0.5},
    margin:{l:44, r:12, t:34, b:30},
    xaxis:{range:[0,24], dtick:6, title: big ? 'Heure' : '', ticksuffix:'h'},
    yaxis:{range:[40,330], title: big ? 'mg/dL' : ''},
    hovermode:'x', showlegend:false,
    shapes:[
      {type:'rect', xref:'x', yref:'y', x0:0, x1:24, y0:70, y1:180,
       fillcolor:'rgba(200,230,201,0.5)', line:{width:0}, layer:'below'},
      {type:'line', x0:0, x1:24, y0:70, y1:70, line:{color:'#e53935', width:1, dash:'dash'}},
      {type:'line', x0:0, x1:24, y0:180, y1:180, line:{color:'#fb8c00', width:1, dash:'dash'}}
    ]
  };
}
function highlight(divId, idx) {
  const base = BASE[divId];
  const w = base.map((b, k) => k === idx ? Math.max(b, 4) : 1.2);
  const o = base.map((b, k) => k === idx ? 1 : 0.18);
  Plotly.restyle(divId, {'line.width': w, 'opacity': o});
}
function resetLines(divId) {
  const base = BASE[divId];
  Plotly.restyle(divId, {'line.width': base, 'opacity': base.map(() => 1)});
}
function buildLegend(legId, divId, series, hasGlobal) {
  const leg = document.getElementById(legId);
  leg.innerHTML = '';
  const add = (label, color, idx) => {
    const el = document.createElement('span');
    el.className = 'leg-item';
    el.innerHTML = '<i style="background:' + color + '"></i>' + label;
    el.onmouseenter = () => highlight(divId, idx);
    el.onmouseleave = () => resetLines(divId);
    leg.appendChild(el);
  };
  series.forEach((s, i) => add(s.label, s.color, i));
  if (hasGlobal) add('GLOBAL', '#c62828', series.length);
}
function multiLine(divId, legId, xvals, series, globalY, layout, xunit, yunit) {
  const traces = [];
  const widths = [];
  series.forEach(s => {
    traces.push({x:xvals, y:s.y, mode:'lines', name:s.label, line:{color:s.color, width:2},
      showlegend:false, hovertemplate:s.label + ' \u2014 %{x}' + xunit + ' : %{y}' + yunit + '<extra></extra>'});
    widths.push(2);
  });
  if (globalY) {
    traces.push({x:xvals, y:globalY, mode:'lines', name:'GLOBAL', line:{color:'#c62828', width:3},
      showlegend:false, hovertemplate:'GLOBAL \u2014 %{x}' + xunit + ' : %{y}' + yunit + '<extra></extra>'});
    widths.push(3);
  }
  BASE[divId] = widths;
  Plotly.newPlot(divId, traces, layout, CFG);
  buildLegend(legId, divId, series, !!globalY);
}

Plotly.newPlot('agp_global', agpTraces(DATA.agp_global), agpLayout('', true), CFG);

DATA.agp_monthly.forEach((d, i) => {
  Plotly.newPlot('agp_m' + i, agpTraces(d.agp), agpLayout(d.label, false), CFG);
});

multiLine('comp', 'comp_leg', DATA.comparison.x, DATA.comparison.series, null, {
  margin:{l:44, r:12, t:10, b:34},
  xaxis:{range:[0,24], dtick:6, title:'Heure', ticksuffix:'h'},
  yaxis:{range:[40,300], title:'mg/dL'},
  hovermode:'closest', showlegend:false,
  shapes:[{type:'rect', x0:0, x1:24, y0:70, y1:180, fillcolor:'rgba(200,230,201,0.45)', line:{width:0}, layer:'below'}]
}, 'h', ' mg/dL');

(function() {
  const m = DATA.mean_evol;
  Plotly.newPlot('mean_evol', [{
    type:'bar', x:m.labels, y:m.means, marker:{color:'#1e88e5'},
    hovertemplate:'%{x} \u2014 %{y} mg/dL<extra></extra>'
  }], {
    margin:{l:44, r:12, t:14, b:70},
    yaxis:{title:'mg/dL'}, xaxis:{tickangle:-20},
    shapes:[
      {type:'rect', xref:'paper', x0:0, x1:1, y0:70, y1:180, fillcolor:'rgba(200,230,201,0.4)', line:{width:0}, layer:'below'},
      {type:'line', xref:'paper', x0:0, x1:1, y0:m.global, y1:m.global, line:{color:'#c62828', width:2, dash:'dash'}}
    ],
    annotations:[{xref:'paper', x:0.01, y:m.global, text:'Moyenne globale : ' + m.global, showarrow:false, yshift:10, font:{color:'#c62828', size:12}, xanchor:'left'}]
  }, CFG_STATIC);
})();

DATA.tir.forEach((t, i) => {
  Plotly.newPlot('pie' + i, [{
    type:'pie', values:[t.hypo, t.cible, t.hyper], labels:['Hypo', 'Cible', 'Hyper'],
    marker:{colors:['#e53935', '#43a047', '#fb8c00'], line:{color:'#fff', width:1.5}},
    textinfo:'percent', textfont:{size:11}, sort:false,
    hovertemplate:'%{label} : %{value}%<extra></extra>'
  }], {
    margin:{l:6, r:6, t:28, b:6}, showlegend:false,
    title:{text:t.label, font:{size:12, color: t.label === 'GLOBAL' ? '#0d47a1' : '#1b2733'}}
  }, CFG_STATIC);
});

if (DATA.doses) {
  multiLine('doses', 'doses_leg', DATA.doses.hours, DATA.doses.series, DATA.doses.global, {
    margin:{l:44, r:12, t:10, b:34},
    xaxis:{title:'Heure', dtick:2, ticksuffix:'h', range:[0,23]},
    yaxis:{title:'U', rangemode:'tozero'},
    hovermode:'closest', showlegend:false
  }, 'h', ' U');
}
"""


def _html_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    gcol = cols.index("GLOBAL") if "GLOBAL" in cols else None
    out = ["<table class='data'><thead><tr>"]
    for j, c in enumerate(cols):
        cls = " class='glob'" if gcol is not None and j == gcol else ""
        out.append(f"<th{cls}>{c}</th>")
    out.append("</tr></thead><tbody>")
    for _, row in df.iterrows():
        rcls = " class='glob-row'" if str(row.iloc[0]) == "GLOBAL" else ""
        out.append(f"<tr{rcls}>")
        for j, c in enumerate(cols):
            cls = " class='glob'" if gcol is not None and j == gcol else ""
            out.append(f"<td{cls}>{row[c]}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def write_html(out: Path, subtitle: str, sg: pd.DataFrame, bol: pd.DataFrame) -> Path:
    """Construit un rapport HTML interactif (graphiques Plotly + tableaux HTML)."""
    import json

    months = _months(sg)
    palette = [PALETTE[i % len(PALETTE)] for i in range(len(months))]

    agp_global = _agp_payload(sg)
    agp_monthly = [{"label": _month_label(m), "agp": _agp_payload(sg[sg["month"] == m])}
                   for m in months]
    comparison = {"x": agp_global["x"],
                  "series": [{"label": _month_label(m), "color": palette[i],
                              "y": _agp_payload(sg[sg["month"] == m])["med"]}
                             for i, m in enumerate(months)]}
    mt = monthly_glucose_table(sg)
    mean_evol = {"labels": [r for r in mt["Mois"] if r != "GLOBAL"],
                 "means": [int(v) for r, v in zip(mt["Mois"], mt["Moyenne"]) if r != "GLOBAL"],
                 "global": int(mt.loc[mt["Mois"] == "GLOBAL", "Moyenne"].iloc[0])}
    tir = [{"label": r["Mois"], "hypo": float(r["Hypo <70 (%)"]),
            "cible": float(r["Cible (%)"]), "hyper": float(r["Hyper >180 (%)"])}
           for _, r in mt.iterrows()]
    doses = None
    if not bol.empty:
        ht = hourly_dose_table(bol)
        mcols = [c for c in ht.columns if c not in ("Heure", "GLOBAL")]
        doses = {"hours": list(range(24)),
                 "series": [{"label": c, "color": palette[i % len(palette)],
                             "y": [float(v) for v in ht[c]]} for i, c in enumerate(mcols)],
                 "global": [float(v) for v in ht["GLOBAL"]]}

    data = {"agp_global": agp_global, "agp_monthly": agp_monthly,
            "comparison": comparison, "mean_evol": mean_evol, "tir": tir, "doses": doses}
    js = JS_TEMPLATE.replace("/*DATA*/", json.dumps(data, ensure_ascii=False))

    agp_cells = "".join(f"<div id='agp_m{i}' class='plot-sm'></div>" for i in range(len(agp_monthly)))
    pie_cells = "".join(f"<div id='pie{i}' class='plot-sm'></div>" for i in range(len(tir)))

    sections = [
        ("Synthèse mensuelle", _html_table(mt), None),
        ("Profil glycémique journalier (AGP) — global",
         "<div id='agp_global' class='plot'></div>",
         "Glissez pour zoomer sur une zone · molette pour zoomer · double-clic pour réinitialiser"),
        ("Profil glycémique journalier (AGP) — par mois",
         f"<div class='grid'>{agp_cells}</div>",
         "Chaque graphique est zoomable indépendamment"),
        ("Comparaison mensuelle",
         "<div class='chart-row'><div id='comp' class='plot'></div><div id='comp_leg' class='legend'></div></div>",
         "Survolez un mois dans la légende pour mettre sa courbe en avant · zoom à la molette"),
        ("Évolution de la glycémie moyenne",
         "<div id='mean_evol' class='plot'></div>", None),
        ("Répartition hypo / cible / hyper",
         f"<div class='grid pies'>{pie_cells}</div>",
         "Rouge = hypo (moins de 70) · vert = cible (70-180) · orange = hyper (plus de 180)"),
    ]
    if doses is not None:
        sections.append(("Dose de bolus moyenne par heure",
                         "<div class='chart-row'><div id='doses' class='plot'></div><div id='doses_leg' class='legend'></div></div>",
                         "Survolez un mois dans la légende · bolus repas/correction (insuline auto SmartGuard exclue)"))
        sections.append(("Doses de bolus moyennes par heure (détail)",
                         _html_table(hourly_dose_table(bol)), None))

    body = []
    for title, content, hint in sections:
        h = f"<p class='hint'>{hint}</p>" if hint else ""
        body.append(f"<section class='card'><h2>{title}</h2>{h}{content}</section>")

    html = (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Rapport glycémie &amp; insuline</title>"
        "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js' charset='utf-8'></script>"
        f"<style>{HTML_CSS}</style></head><body><div class='wrap'>"
        f"<header><h1>Rapport glycémie &amp; insuline</h1><p class='sub'>{subtitle}</p></header>"
        + "".join(body)
        + "<footer>Généré automatiquement à partir des données CareLink.</footer></div>"
        f"<script>{js}</script></body></html>"
    )
    path = out / "rapport.html"
    path.write_text(html, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Rapports glycémie + insuline (PDF) depuis un CSV CareLink.")
    ap.add_argument("csv", help="Fichier CSV fusionné (ex. glycemie.csv)")
    ap.add_argument("--out", default="rapports", help="Dossier de sortie")
    ap.add_argument("--png", action="store_true", help="Exporte aussi chaque page en PNG")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    df = load(Path(args.csv)); sg = glucose(df); bol = manual_boluses(df)
    if sg.empty:
        print("Aucune donnée de glycémie exploitable."); return 1
    print(f"Période : {df['dt'].min().date()} → {df['dt'].max().date()} "
          f"| {len(sg)} lectures glycémie | {len(bol)} bolus repas")

    pages = [("synthese", fig_tables(sg)),
             ("profil_glycemique_journalier", fig_agp(sg)),
             ("profil_agp_mensuel", fig_agp_monthly(sg)),
             ("comparaison_mensuelle", fig_monthly_comparison(sg)),
             ("evolution_moyennes", fig_mean_evolution(sg)),
             ("parts_tir_camemberts", fig_tir_pies(sg))]
    if not bol.empty:
        pages.append(("doses_par_heure", fig_hourly_doses(bol)))
        pages.append(("doses_par_heure_table", fig_dose_table(bol)))

    pdf_path = out / "rapport.pdf"
    with PdfPages(pdf_path) as pdf:
        for _, fig in pages:
            pdf.savefig(fig)
    if args.png:
        for name, fig in pages:
            fig.savefig(out / f"{name}.png", dpi=140, bbox_inches="tight")

    subtitle = (f"{df['dt'].min().date()} au {df['dt'].max().date()} — "
                f"{len(sg)} lectures de glycémie")
    html_path = write_html(out, subtitle, sg, bol)

    for _, fig in pages:
        plt.close(fig)

    with pd.ExcelWriter(out / "rapport_synthese.xlsx", engine="openpyxl") as xl:
        monthly_glucose_table(sg).to_excel(xl, sheet_name="Glycémie mensuelle", index=False)
        hourly_dose_table(bol).to_excel(xl, sheet_name="Doses par heure", index=False)

    print(f"\n-> HTML  : {html_path}")
    print(f"-> PDF   : {pdf_path}")
    print(f"-> Tables: {out / 'rapport_synthese.xlsx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
