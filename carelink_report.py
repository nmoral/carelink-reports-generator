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
    for _, fig in pages:
        plt.close(fig)

    with pd.ExcelWriter(out / "rapport_synthese.xlsx", engine="openpyxl") as xl:
        monthly_glucose_table(sg).to_excel(xl, sheet_name="Glycémie mensuelle", index=False)
        hourly_dose_table(bol).to_excel(xl, sheet_name="Doses par heure", index=False)

    print(f"\n-> PDF : {pdf_path}")
    print(f"-> Tables : {out / 'rapport_synthese.xlsx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
