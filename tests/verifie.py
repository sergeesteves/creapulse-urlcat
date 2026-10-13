#!/usr/bin/env python3
"""Verifications d'urlcat : semantique des motifs, agregation, panel, page HTML, contrastes.

Lancer depuis le dossier du projet :  python tests/verifie.py
Aucune dependance. Sortie : une ligne par verification, puis un bilan.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import urlcat  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    RESULTS.append((bool(ok), label))
    print(f"  {'OK  ' if ok else 'ECHEC'}  {label}")


# --------------------------------------------------------------------------- #

RULES = """[crawl:categories]

@names
100\tsite
200\tinutile

@
path /offers
dom exemple.fr
name tous-les-offers
parent-id\t100

@
path /offers/*
dom exemple.fr
name fiches-offers
parent-id 100

@
path\t*/users/sign_up*
+\t/users/sign_up*
dom = *
name\tinutiles-signup
parent-id\t200

@
dom exemple.fr
path\trx:^/fr/[\\w-]+
name fr-produits

@
dom exemple.fr
path *
name attrape-tout

[crawl:univers]
@
dom exemple.fr
name U.FR
path /fr
+ /fr/*
"""


def with_rules(body: str = RULES):
    tmp = Path(tempfile.mkdtemp()) / "regles.txt"
    tmp.write_text(body, encoding="utf-8")
    return tmp


def ns(**kwargs):
    import argparse

    base = dict(ignore_dom=False, diagnose=False, panel=100.0, panel_per_group=0, seed=1,
                url_column=None, explain=None, html=None, report=None)
    base.update(kwargs)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
print("\nGRAMMAIRE")
rules = urlcat.parse_rules(with_rules())
check(len(rules.categories) == 5, f"5 regles de categorie lues (lu : {len(rules.categories)})")
check(len(rules.univers) == 1, "1 regle d'univers lue")
check(rules.parents == {"100": "site", "200": "inutile"}, "@names : 100=site, 200=inutile")
signup = next(r for r in rules.categories if r.name == "inutiles-signup")
check(len(signup.patterns) == 2, "la ligne `+` ajoute un motif alternatif a la meme regle")
check(signup.any_dom is True, "`dom = *` (separateur =) vaut n'importe quel hote")
check(signup.parent == "inutile", "parent-id resolu via @names")
check(next(r for r in rules.categories if r.name == "fr-produits").parent is None,
      "parent-id absent -> pas de parent, pas d'erreur")

print("\nSEMANTIQUE DES MOTIFS")
cats = rules.categories
f = lambda u: urlcat.classify(cats, *urlcat.split_url(u), False)[0].name  # noqa: E731
check(f("https://exemple.fr/offers") == "tous-les-offers",
      "glob ancre : /offers ne matche QUE /offers")
check(f("https://exemple.fr/offers/abc") == "fiches-offers",
      "glob ancre : /offers/abc va bien a /offers/*")
check(f("https://exemple.fr/users/sign_up?from=1") == "inutiles-signup",
      "le motif porte sur chemin+query (`?` litteral, pas un joker)")
check(f("https://exemple.fr/fr/un-produit") == "fr-produits",
      "rx: en recherche non ancree avec ^ explicite")
check(f("https://exemple.fr/inconnu/profond") == "attrape-tout",
      "`path *` attrape le reste")
check(urlcat.classify(cats, *urlcat.split_url("https://autre.fr/offers"), False)[0] is None,
      "hote hors `dom` : aucune regle ne matche")
check(urlcat.classify(cats, *urlcat.split_url("https://autre.fr/offers"), True)[0].name
      == "tous-les-offers", "--ignore-dom passe outre la contrainte d'hote")

print("\nPREMIER MATCH GAGNANT")
check(f("https://exemple.fr/fr/offers") == "fr-produits",
      "l'ordre du fichier tranche : fr-produits (ligne 27) avant attrape-tout")
matches = urlcat.all_matches(cats, *urlcat.split_url("https://exemple.fr/fr/x"), False)
check(len(matches) == 2 and matches[0].name == "fr-produits",
      "all_matches expose aussi les regles eclipsees")

print("\nAGREGATION ET OCCURRENCES")
urls = with_rules().parent / "urls.txt"
urls.write_text("\n".join([
    "https://exemple.fr/offers/a"] * 3
    + ["https://exemple.fr/offers/b"] * 1
    + ["https://exemple.fr/fr/truc"] * 10), encoding="utf-8")
rules2 = urlcat.parse_rules(with_rules())
rows, stats, hosts, unmatched, total_hits = urlcat.analyse(rules2, ns(urls=str(urls)))
check(len(rows) == 3, f"3 URLs uniques pour 14 lignes (lu : {len(rows)})")
check(total_hits == 14, f"14 occurrences comptees (lu : {total_hits})")
by_url = {r["url"]: r for r in rows}
check(by_url["https://exemple.fr/offers/a"]["nombre"] == 3, "nombre = 3 pour l'URL repetee 3 fois")
check(stats["groupe"]["fiches-offers"].urls == 2, "2 URLs uniques dans fiches-offers")
check(stats["groupe"]["fiches-offers"].hits == 4, "4 occurrences dans fiches-offers")
check(stats["groupe"]["fr-produits"].hits == 10,
      "poids en occurrences different du poids en URLs uniques")
check(unmatched.urls == 0, "aucune non classee ici")

print("\nPANEL")
big = urls.parent / "big.txt"
big.write_text("\n".join(f"https://exemple.fr/offers/p{i}" for i in range(1000)), encoding="utf-8")
rows_big, _, _, _, _ = urlcat.analyse(urlcat.parse_rules(with_rules()), ns(urls=str(big)))
full, label = urlcat.pick_panel(rows_big, ns(urls=str(big)))
check(len(full) == 1000 and label == "complet", "defaut : 100%, rien n'est retire")
part, _ = urlcat.pick_panel(rows_big, ns(urls=str(big), panel=10))
check(len(part) == 100, f"--panel 10 garde 100 URLs sur 1000 (lu : {len(part)})")
a, _ = urlcat.pick_panel(rows_big, ns(urls=str(big), panel=10, seed=7))
b, _ = urlcat.pick_panel(rows_big, ns(urls=str(big), panel=10, seed=7))
c, _ = urlcat.pick_panel(rows_big, ns(urls=str(big), panel=10, seed=8))
check([r["url"] for r in a] == [r["url"] for r in b], "meme seed -> meme panel (reproductible)")
check([r["url"] for r in a] != [r["url"] for r in c], "seed different -> panel different")

mixed = urls.parent / "mixed.txt"
mixed.write_text("\n".join(
    [f"https://exemple.fr/offers/g{i}" for i in range(500)]
    + ["https://exemple.fr/fr/petit-groupe"]), encoding="utf-8")
rows_mixed, _, _, _, _ = urlcat.analyse(urlcat.parse_rules(with_rules()), ns(urls=str(mixed)))
strat, _ = urlcat.pick_panel(rows_mixed, ns(urls=str(mixed), panel_per_group=20))
groups = {r["groupe"] for r in strat}
check("fr-produits" in groups and "fiches-offers" in groups,
      "panel stratifie : le groupe a 1 URL reste represente")
check(sum(1 for r in strat if r["groupe"] == "fiches-offers") == 20,
      "panel stratifie : plafonne a 20 pour le gros groupe")

print("\nPAGE HTML")
out = urls.parent / "apercu.html"
hostile = urls.parent / "hostile.txt"
hostile.write_text("\n".join([
    "https://exemple.fr/offers/<script>alert(1)</script>",
    "https://exemple.fr/offers/\"guillemet",
    "https://exemple.fr/offers/accentue-e",
]), encoding="utf-8")
rules3 = urlcat.parse_rules(with_rules())
rows_h, stats_h, _, _, hits_h = urlcat.analyse(rules3, ns(urls=str(hostile)))
urlcat.write_html(out, rows_h, stats_h, len(rows_h), hits_h, "complet", "regles.txt", "hostile.txt")
page = out.read_text(encoding="utf-8")
check(page.startswith("<!doctype html>") and page.rstrip().endswith("</html>"),
      "document complet, doctype present")
check("<script>alert(1)</script>" not in page,
      "une URL contenant <script> n'est pas injectee telle quelle")
check(page.count("<script>") == 1, "un seul bloc script : rien n'a casse le balisage")
payload = re.search(r"const DATA = (\[.*?\]);\n", page, re.S)
check(payload is not None, "le bloc DATA est present")
if payload:
    parsed = json.loads(payload.group(1))
    check(len(parsed) == 3, "les 3 URLs sont dans DATA")
    check(all(len(r) == 5 for r in parsed), "chaque ligne a 5 champs")
check("__DATA__" not in page and "__PAPER__" not in page, "tous les jetons sont remplaces")
for token in ("#f7f7f7", "#111111", "#595959", "#027dba", "#e2177f"):
    check(token in page, f"jeton de charte present : {token}")
check("select" in page and 'id="g"' in page and 'id="u"' in page,
      "les deux listes deroulantes (groupe, univers) sont la")

print("\nCONTRASTES (WCAG, calcules)")


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


paper, white = "#f7f7f7", "#ffffff"
for fg, bg, name in (
    ("#111111", paper, "encre sur papier"),
    ("#111111", white, "encre sur surface"),
    ("#595959", paper, "encre atténuée sur papier"),
    ("#595959", white, "encre atténuée sur surface"),
    ("#ffffff", "#027dba", "blanc sur badge secondaire"),
    ("#ffffff", "#e2177f", "blanc sur accent"),
):
    value = ratio(fg, bg)
    check(value >= 4.5, f"{name} : {value:.2f}:1 (seuil 4,5)")

value = ratio("#027dba", paper)
check(value < 4.5, f"garde-fou : #027dba en TEXTE sur papier echoue bien ({value:.2f}:1), "
                   "d'ou son usage en fond seulement")
check("color: var(--secondary)" not in page,
      "aucun texte ne porte la couleur secondaire dans la page generee")

print("\nBOUT EN BOUT (CLI)")
proc = subprocess.run(
    [sys.executable, str(ROOT / "urlcat.py"), "--rules", str(with_rules()),
     "--urls", str(urls), "--out", str(urls.parent / "cli.csv"), "--diagnose"],
    capture_output=True, text=True)
check(proc.returncode == 0, "la CLI sort en 0")
check("URLs uniques" in proc.stdout and "GROUPES" in proc.stdout, "le rapport est imprime")
csv_text = (urls.parent / "cli.csv").read_text(encoding="utf-8-sig")
check(csv_text.splitlines()[0].startswith("url;groupe;univers;nombre"),
      "entete CSV dans l'ordre demande : url / groupe / univers / nombre")

bad = subprocess.run(
    [sys.executable, str(ROOT / "urlcat.py"), "--rules", str(with_rules()),
     "--urls", str(urls), "--panel", "0"],
    capture_output=True, text=True)
check(bad.returncode != 0, "--panel 0 est refuse")

# --------------------------------------------------------------------------- #
failed = [label for ok, label in RESULTS if not ok]
print(f"\n{'-' * 70}")
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} verifications passent")
for label in failed:
    print(f"  ECHEC : {label}")
sys.exit(1 if failed else 0)
