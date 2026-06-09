#!/bin/sh
#
# run_carelink.sh — telecharge les exports CareLink puis les fusionne.
#
# Compatible sh / dash / bash / zsh. Peut donc se lancer indifferemment avec :
#   ./run_carelink.sh        (apres chmod +x)
#   bash run_carelink.sh
#   sh run_carelink.sh
#
# Il s'occupe de tout : venv Python, installation de Playwright + Chromium la
# premiere fois, saisie des identifiants (mot de passe masque, jamais stocke),
# choix de la periode, telechargement par fenetres de 15 jours, puis fusion.
#
# Les scripts download_carelink.py et merge_carelink.py doivent etre dans le
# meme dossier que ce script.

set -u

# --- Dossier du script (pour trouver les .py a cote) -------------------------
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR" || exit 1

DOWNLOAD_PY="$SCRIPT_DIR/download_carelink.py"
MERGE_PY="$SCRIPT_DIR/merge_carelink.py"
REPORT_PY="$SCRIPT_DIR/carelink_report.py"
OUT_DIR="$SCRIPT_DIR/exports"
FUSION="$SCRIPT_DIR/glycemie.csv"
REPORT_DIR="$SCRIPT_DIR/rapports"
VENV="$SCRIPT_DIR/venv"

# --- Verifs de base ----------------------------------------------------------
for f in "$DOWNLOAD_PY" "$MERGE_PY" "$REPORT_PY"; do
    if [ ! -f "$f" ]; then
        echo "x Fichier manquant : $f" >&2
        echo "  Place run_carelink.sh dans le meme dossier que les deux scripts Python." >&2
        exit 1
    fi
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "x python3 introuvable. Installe Python 3 d'abord." >&2
    exit 1
fi

# --- Environnement virtuel ---------------------------------------------------
if [ ! -d "$VENV" ]; then
    echo "-> Creation de l'environnement virtuel (premiere fois)..."
    python3 -m venv "$VENV" || exit 1
    . "$VENV/bin/activate"
    echo "-> Installation de Playwright..."
    pip install --quiet --upgrade pip
    pip install --quiet playwright
    playwright install firefox
else
    . "$VENV/bin/activate"
fi

# --- Identifiants ------------------------------------------------------------
# Plus besoin : la connexion se fait À LA MAIN dans la fenêtre Firefox.
echo "i La connexion CareLink se fera dans la fenetre Firefox (login + 2FA)."

# --- Dates (valeurs par defaut compatibles GNU et BSD/macOS) -----------------
today=$(date +%Y-%m-%d)
if default_start=$(date -d '60 days ago' +%Y-%m-%d 2>/dev/null); then
    :
else
    default_start=$(date -v-60d +%Y-%m-%d)
fi

printf "Date de debut [%s] : " "$default_start"
read -r START
[ -z "$START" ] && START="$default_start"

printf "Date de fin   [%s] : " "$today"
read -r END
[ -z "$END" ] && END="$today"

# --- Telechargement puis fusion ----------------------------------------------
echo
echo "-> Telechargement de $START a $END vers $OUT_DIR/"
if python "$DOWNLOAD_PY" --start "$START" --end "$END" --out "$OUT_DIR"; then
    echo
    echo "-> Fusion des fichiers telecharges..."
    python "$MERGE_PY" "$OUT_DIR" -o "$FUSION" --append
    echo
    echo "-> Generation du rapport PDF..."
    # dépendances d'analyse, installées seulement si absentes
    python -c "import pandas, matplotlib, openpyxl" 2>/dev/null || \
        pip install --quiet pandas matplotlib openpyxl
    if python "$REPORT_PY" "$FUSION" --out "$REPORT_DIR"; then
        echo "OK Termine. Donnees : $FUSION  |  Rapport : $REPORT_DIR/rapport.pdf"
    else
        echo "OK Donnees fusionnees : $FUSION (mais le rapport a echoue)." >&2
    fi
else
    echo "x Le telechargement a echoue -- rien a fusionner." >&2
    exit 1
fi