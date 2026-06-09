#!/usr/bin/env python3
"""
Fusion + dédoublonnage d'exports CSV CareLink (Medtronic MiniMed).

CareLink limite chaque export CSV à une courte fenêtre (~15 jours). Pour
reconstituer un historique plus long, on exporte plusieurs fenêtres
consécutives (qui peuvent se chevaucher) puis on les fusionne ici.

Le script :
  - détecte automatiquement la vraie ligne d'en-tête ("Index;Date;Time;...")
    quel que soit le nombre de lignes de métadonnées au-dessus ;
  - concatène toutes les lignes de données ;
  - dédoublonne sur le contenu (en ignorant la colonne "Index", qui est
    renumérotée à chaque export et n'identifie donc pas une ligne) ;
  - trie par date/heure ;
  - réécrit un CSV propre, ré-ingérable par le même pipeline (le préambule
    de métadonnées du premier fichier est conservé).

Dépendances : aucune (bibliothèque standard uniquement).

Exemples :
    python merge_carelink.py *.csv -o fusion.csv
    python merge_carelink.py ./exports -o fusion.csv          # un dossier
    python merge_carelink.py a.csv b.csv c.csv -o fusion.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import sys
from datetime import datetime
from pathlib import Path

HEADER_FIRST_COL = "Index"          # la ligne d'en-tête commence par "Index;Date;Time;..."
ENCODING = "utf-8-sig"              # CareLink exporte en UTF-8 avec BOM
DELIMITER = ";"
DATE_FMT = "%Y/%m/%d %H:%M:%S"


def find_header_row(path: Path) -> int:
    """Retourne l'index (0-based) de la ligne d'en-tête réelle."""
    with open(path, encoding=ENCODING, newline="") as f:
        for i, line in enumerate(f):
            if line.lstrip("\ufeff").startswith(HEADER_FIRST_COL + DELIMITER):
                return i
    raise ValueError(f"En-tête '{HEADER_FIRST_COL};...' introuvable dans {path}")


def read_export(path: Path) -> tuple[list[str], list[str], list[list[str]]]:
    """Lit un export CareLink.

    Renvoie (lignes_de_preambule, entete, lignes_de_donnees).
    """
    header_idx = find_header_row(path)
    with open(path, encoding=ENCODING, newline="") as f:
        lines = f.readlines()

    preamble = [ln.rstrip("\r\n") for ln in lines[:header_idx]]
    reader = csv.reader(lines[header_idx:], delimiter=DELIMITER)
    header = next(reader)
    width = len(header)
    rows = []
    for r in reader:
        if not any(cell.strip() for cell in r):
            continue
        # certaines lignes sont plus courtes (champs vides en fin) : on aligne sur l'en-tête
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width]
        rows.append(r)
    return preamble, header, rows


def parse_datetime(row: list[str], i_date: int, i_time: int) -> datetime | None:
    """Tente de parser Date + Time ; renvoie None si impossible."""
    try:
        return datetime.strptime(f"{row[i_date]} {row[i_time]}", DATE_FMT)
    except (ValueError, IndexError):
        return None


def merge(paths: list[Path], keep_index: bool = False) -> tuple[list[str], list[str], list[list[str]]]:
    """Fusionne et dédoublonne plusieurs exports.

    keep_index : si False (défaut), la colonne 'Index' est renumérotée
    séquentiellement dans la sortie.
    """
    ref_preamble: list[str] = []
    ref_header: list[str] | None = None
    seen: set[tuple[str, ...]] = set()
    merged: list[list[str]] = []

    for path in paths:
        preamble, header, rows = read_export(path)
        if ref_header is None:
            ref_header, ref_preamble = header, preamble
            idx_index = header.index(HEADER_FIRST_COL)
            dedup_cols = [c for c in range(len(header)) if c != idx_index]
        elif header != ref_header:
            print(
                f"  ! En-tête différent dans {path.name}, fichier ignoré "
                f"(schéma incompatible).",
                file=sys.stderr,
            )
            continue

        added = 0
        for row in rows:
            # clé de dédoublonnage = toutes les colonnes sauf 'Index'
            key = tuple(row[c] for c in dedup_cols)
            if key not in seen:
                seen.add(key)
                merged.append(row)
                added += 1
        print(f"  {path.name}: {len(rows)} lignes, +{added} nouvelles")

    assert ref_header is not None, "Aucun fichier valide lu."

    # tri chronologique (les lignes sans date valide vont à la fin)
    i_date = ref_header.index("Date")
    i_time = ref_header.index("Time")
    merged.sort(key=lambda r: parse_datetime(r, i_date, i_time) or datetime.max)

    if not keep_index:
        i_index = ref_header.index(HEADER_FIRST_COL)
        for n, row in enumerate(merged):
            row[i_index] = str(n)

    return ref_preamble, ref_header, merged


def collect_paths(inputs: list[str]) -> list[Path]:
    """Développe globs et dossiers en une liste de fichiers .csv dédupliquée."""
    found: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            found.extend(sorted(p.glob("*.csv")))
        elif any(ch in item for ch in "*?[]"):
            found.extend(sorted(Path(g) for g in glob.glob(item)))
        elif p.exists():
            found.append(p)
        else:
            print(f"  ! Introuvable : {item}", file=sys.stderr)
    # dédoublonne en conservant l'ordre
    uniq = list(dict.fromkeys(p.resolve() for p in found))
    return uniq


def write_output(path: Path, preamble: list[str], header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding=ENCODING, newline="") as f:
        for line in preamble:
            f.write(line + "\r\n")
        writer = csv.writer(f, delimiter=DELIMITER, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fusionne et dédoublonne des exports CSV CareLink.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("inputs", nargs="+", help="Fichiers .csv, globs, ou un dossier")
    ap.add_argument("-o", "--output", default="glycemie.csv", help="Fichier de sortie")
    ap.add_argument(
        "--append",
        action="store_true",
        help="Réinjecte le fichier de sortie existant comme entrée (accumulation)",
    )
    ap.add_argument(
        "--keep-index",
        action="store_true",
        help="Conserve la colonne Index d'origine au lieu de la renuméroter",
    )
    args = ap.parse_args()

    paths = collect_paths(args.inputs)
    # --append : on ajoute le fichier de sortie existant aux entrées. La lecture
    # se fait entièrement avant l'écriture, donc réécrire ce même fichier est sûr.
    if args.append:
        out_path = Path(args.output)
        if out_path.exists():
            paths.append(out_path.resolve())
            paths = list(dict.fromkeys(paths))  # dédoublonne les chemins
            print(f"(append) fichier existant réinjecté : {args.output}")
    if not paths:
        print("Aucun fichier CSV à traiter.", file=sys.stderr)
        return 1

    print(f"Fusion de {len(paths)} fichier(s) :")
    preamble, header, rows = merge(paths, keep_index=args.keep_index)
    write_output(Path(args.output), preamble, header, rows)
    print(f"\n→ {len(rows)} lignes uniques écrites dans {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())