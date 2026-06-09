#!/usr/bin/env python3
"""
Téléchargement des exports CSV CareLink par capture-et-rejoue de la requête.

La génération d'un CSV CareLink se fait en TROIS appels :
    1. POST /patient/reports/generateReport      → {"uuid": "..."}
    2. GET  /patient/reports/reportStatus?uuid=…  → {"status": "READY"}  (on attend)
    3. GET  /patient/reports/reportCsv?uuid=…&dMInFileName=false → le CSV

PRINCIPE
--------
  1. ouvre Firefox (Playwright) ; TU te connectes À LA MAIN (login, 2FA, pays) ;
  2. tu génères UN rapport CSV à la main, n'importe quelle plage de dates ;
  3. le script intercepte le POST generateReport (URL, en-têtes dont le Bearer,
     corps JSON avec patientId et réglages) et s'en sert de MODÈLE ;
  4. pour chaque fenêtre de 15 jours il rejoue les trois appels en ne changeant
     que les dates, via le contexte du navigateur (cookies de session réutilisés
     automatiquement), et enregistre chaque CSV.

Un PROFIL PERSISTANT conserve ta session entre deux lancements.

⚠️  Le token Bearer expire vite (~50 min) : tout s'enchaîne juste après ta
connexion. En cas de 401, relance et régénère un rapport (nouveau token capté).
Le dossier de profil contient des jetons de session — garde-le privé.

Installation :
    pip install playwright
    playwright install firefox

Usage :
    python download_carelink.py --start 2026-04-01 --end 2026-06-07 --out ./exports
    python merge_carelink.py ./exports -o fusion.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_URL = "https://carelink.minimed.eu"
LOGIN_URL = f"{BASE_URL}/"
REPORT_ENDPOINT_FRAGMENT = "/reports/generateReport"

MAX_WINDOW_DAYS = 15
STATUS_TIMEOUT_S = 90      # délai max d'attente de la génération du rapport
STATUS_POLL_S = 2          # intervalle entre deux vérifications de statut

DROP_HEADERS = {
    "host", "content-length", "connection", "te", "cookie",
    "accept-encoding", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "priority",
}


def date_windows(start: date, end: date, max_days: int = MAX_WINDOW_DAYS):
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


def pause(message: str) -> None:
    try:
        input(message)
    except EOFError:
        pass


def wait_until_ready(req, status_url: str, headers: dict, uuid: str) -> bool:
    """Interroge reportStatus jusqu'à READY (ou expiration du délai)."""
    deadline = time.monotonic() + STATUS_TIMEOUT_S
    while time.monotonic() < deadline:
        r = req.get(status_url, headers=headers, params={"uuid": uuid})
        if r.status == 401:
            raise PermissionError("401 : token expiré.")
        try:
            status = r.json().get("status", "")
        except Exception:  # noqa: BLE001
            status = ""
        if status == "READY":
            return True
        if status in ("ERROR", "FAILED"):
            print(f"    statut renvoyé : {status}")
            return False
        time.sleep(STATUS_POLL_S)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Télécharge les CSV CareLink en rejouant l'appel generateReport."
    )
    ap.add_argument("--start", required=True, help="Date de début (AAAA-MM-JJ)")
    ap.add_argument("--end", required=True, help="Date de fin (AAAA-MM-JJ)")
    ap.add_argument("--out", default="./exports", help="Dossier de sortie")
    ap.add_argument("--profile", default="./carelink_profile",
                    help="Profil Firefox persistant (garde la session)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright manquant : pip install playwright && playwright install firefox")

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if start > end:
        sys.exit("La date de début doit précéder la date de fin.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    Path(args.profile).mkdir(parents=True, exist_ok=True)

    windows = list(date_windows(start, end))
    print(f"Période {start} → {end} : {len(windows)} fenêtre(s) de ≤{MAX_WINDOW_DAYS} j.")

    template: dict = {}

    def on_request(request) -> None:
        if request.method == "POST" and REPORT_ENDPOINT_FRAGMENT in request.url:
            body = request.post_data
            if body:
                template["url"] = request.url
                template["headers"] = dict(request.headers)
                template["body"] = body
                print("  ✓ Requête generateReport capturée.", flush=True)

    saved: list[str] = []

    with sync_playwright() as p:
        context = p.firefox.launch_persistent_context(
            user_data_dir=args.profile, headless=False,
            accept_downloads=True, locale="fr-FR",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.on("request", on_request)

        try:
            page.goto(LOGIN_URL)
        except Exception as e:  # noqa: BLE001
            print(f"  (navigation initiale : {e})", file=sys.stderr)

        print("\n" + "=" * 70)
        print("ÉTAPE 1 — Connecte-toi À LA MAIN (login, 2FA, pays), va sur Rapports")
        print("et GÉNÈRE UN RAPPORT CSV (n'importe quelle plage). Cette requête")
        print("sert de modèle.")
        print("=" * 70)
        pause("Quand le rapport CSV a été généré, reviens ici et appuie sur Entrée… ")

        # IMPORTANT : input() ne « pompe » pas la boucle Playwright, donc les
        # évènements 'request' déclenchés pendant l'attente ne sont livrés qu'ici.
        # On laisse Playwright les traiter, puis on revérifie (avec un 2e essai).
        page.wait_for_timeout(1500)
        if not template:
            pause("Requête pas encore captée — régénère un rapport CSV puis Entrée… ")
            page.wait_for_timeout(1500)

        if not template:
            context.close()
            print("\n✗ Aucune requête generateReport captée. Génère bien un export "
                  "CSV dans le navigateur avant d'appuyer sur Entrée.", file=sys.stderr)
            return 1

        headers = {k: v for k, v in template["headers"].items()
                   if k.lower() not in DROP_HEADERS}
        # URLs dérivées du même chemin /patient/reports/…
        base = template["url"].rsplit("/", 1)[0]          # …/patient/reports
        status_url = f"{base}/reportStatus"
        csv_url = f"{base}/reportCsv"
        req = context.request

        print("\nÉTAPE 2 — Génération + téléchargement par fenêtre :")
        for w_start, w_end in windows:
            try:
                body = json.loads(template["body"])
                body["startDate"] = w_start.isoformat()
                body["endDate"] = w_end.isoformat()
                body["reportFileFormat"] = "CSV"
                body["aggregatedCsvEnabled"] = True
                body["clientTime"] = datetime.now().astimezone().isoformat(timespec="seconds")

                # 1) lancer la génération
                gen = req.post(template["url"], headers=headers,
                               data=json.dumps(body, ensure_ascii=False))
                if gen.status == 401:
                    print("  ✗ 401 : token expiré. Relance et régénère un rapport.",
                          file=sys.stderr)
                    break
                uuid = gen.json().get("uuid")
                if not uuid:
                    print(f"  ! {w_start}→{w_end} : pas d'uuid (réponse {gen.status}).")
                    continue

                # 2) attendre READY
                if not wait_until_ready(req, status_url, headers, uuid):
                    print(f"  ! {w_start}→{w_end} : rapport non prêt dans le délai.")
                    continue

                # 3) récupérer le CSV
                csv = req.get(csv_url, headers=headers,
                              params={"uuid": uuid, "dMInFileName": "false"})
                if not csv.ok:
                    print(f"  ! {w_start}→{w_end} : reportCsv statut {csv.status}.")
                    continue
                target = out_dir / f"carelink_{w_start:%Y%m%d}_{w_end:%Y%m%d}.csv"
                target.write_bytes(csv.body())
                saved.append(target.name)
                print(f"  ✓ {w_start} → {w_end} : {target.name} ({len(csv.body())} o)")

            except PermissionError as e:
                print(f"  ✗ {e} Relance et régénère un rapport.", file=sys.stderr)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  ! {w_start}→{w_end} : {e}", file=sys.stderr)

        context.close()

    print(f"\n{len(saved)} CSV enregistré(s) dans {out_dir}/")
    if saved:
        print(f"Fusionne-les avec :  python merge_carelink.py {out_dir}/ -o fusion.csv")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())