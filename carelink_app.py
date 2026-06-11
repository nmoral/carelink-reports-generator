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

# En mode exe (--onefile), le dossier temporaire d'extraction (_MEIxxxx) change à
# chaque lancement et ne contient pas le navigateur. On force donc Playwright à
# installer ET retrouver Firefox dans un dossier persistant et inscriptible.
_BROWSERS_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "carelink-browsers"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_BROWSERS_DIR))

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
    print(f"Installation de Firefox (premier lancement, ~80 Mo) dans {_BROWSERS_DIR}…")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver = compute_driver_executable()
        cmd = list(driver) if isinstance(driver, (list, tuple)) else [driver]
        env = get_driver_env()
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        subprocess.run(cmd + ["install", "firefox"], env=env, check=True)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Échec de l'installation de Firefox : {e}\n"
            f"À tester manuellement :\n"
            f'  set PLAYWRIGHT_BROWSERS_PATH={_BROWSERS_DIR}\n'
            f"  playwright install firefox"
        ) from e


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

    html = rapports / "rapport.html"
    pdf = rapports / "rapport.pdf"
    print(f"\n✓ Terminé.\n  Données      : {glycemie}\n"
          f"  Rapport HTML : {html}\n  Rapport PDF  : {pdf}")
    try:
        if os.name == "nt" and html.exists():
            os.startfile(str(html))  # ouvre le rapport HTML dans le navigateur
    except Exception:
        pass

    pause_end()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # indispensable pour les exe --onefile
    try:
        rc = main()
    except SystemExit:
        raise
    except BaseException:               # capture tout plantage pour qu'il reste lisible
        import traceback
        print("\n" + "=" * 62)
        print("UNE ERREUR S'EST PRODUITE :")
        print("=" * 62)
        traceback.print_exc()
        try:
            input("\nAppuie sur Entrée pour quitter… (copie le message ci-dessus)")
        except EOFError:
            pass
        rc = 1
    raise SystemExit(rc)
