#!/usr/bin/env python3
"""
CareLink — orchestrateur tout-en-un : téléchargement → fusion → rapport.

Point d'entrée unique, conçu pour être empaqueté en exécutable Windows
(PyInstaller). Il réutilise les trois scripts du projet :
    download_carelink.py · merge_carelink.py · carelink_report.py

Au premier lancement, il installe le navigateur Firefox de Playwright
(téléchargement ~80 Mo, une seule fois). Ensuite il enchaîne tout seul.

Les fichiers produits (exports/, glycemie.csv, rapports/rapport.pdf,
carelink_profile/) sont créés à côté de l'exécutable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import download_carelink
import merge_carelink
import carelink_report


def ensure_firefox() -> None:
    """Installe le Firefox de Playwright s'il est absent (idempotent)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.firefox.executable_path
        if exe and Path(exe).exists():
            return
    except Exception:
        pass
    print("Installation de Firefox (premier lancement, ~80 Mo)…")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver = compute_driver_executable()
        cmd = list(driver) if isinstance(driver, (list, tuple)) else [driver]
        subprocess.run(cmd + ["install", "firefox"], env=get_driver_env(), check=True)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Installation auto échouée ({e}).")
        print("  Ouvre une invite de commande et lance : playwright install firefox")


def ask(prompt: str, default: str) -> str:
    try:
        v = input(f"{prompt} [{default}] : ").strip()
    except EOFError:
        v = ""
    return v or default


def pause_end() -> None:
    try:
        input("\nAppuie sur Entrée pour quitter…")
    except EOFError:
        pass


def main() -> int:
    base = Path.cwd()
    exports = base / "exports"
    glycemie = base / "glycemie.csv"
    rapports = base / "rapports"
    profile = base / "carelink_profile"

    print("=" * 62)
    print("  CareLink — téléchargement, fusion et rapport")
    print("=" * 62)
    today = date.today().isoformat()
    default_start = (date.today() - timedelta(days=60)).isoformat()
    start = ask("Date de début (AAAA-MM-JJ)", default_start)
    end = ask("Date de fin    (AAAA-MM-JJ)", today)

    ensure_firefox()

    # 1) Téléchargement (connexion manuelle dans Firefox + capture-rejoue)
    sys.argv = ["download", "--start", start, "--end", end,
                "--out", str(exports), "--profile", str(profile)]
    if download_carelink.main() != 0:
        print("\n✗ Téléchargement échoué — arrêt.")
        pause_end()
        return 1

    # 2) Fusion (accumulation dans glycemie.csv)
    sys.argv = ["merge", str(exports), "-o", str(glycemie), "--append"]
    merge_carelink.main()

    # 3) Rapport PDF
    sys.argv = ["report", str(glycemie), "--out", str(rapports)]
    carelink_report.main()

    pdf = rapports / "rapport.pdf"
    print(f"\n✓ Terminé.\n  Données : {glycemie}\n  Rapport : {pdf}")
    try:
        if os.name == "nt" and pdf.exists():
            os.startfile(str(pdf))  # ouvre le PDF sous Windows
    except Exception:
        pass

    pause_end()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
