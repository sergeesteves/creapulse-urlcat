"""Abstraction moteur : la seule porte entre le service web et urlcat.

Le moteur EST `urlcat.py`, a la racine du depot — le meme fichier que la CLI, couvert par
les 54 verifications de tests/verifie.py. Rien de la grammaire, de la semantique des motifs
ou du classement n'est reecrit ici : ce module ne fait que passer du texte au moteur et
remettre en forme sa sortie. Un defaut de classement se corrige dans urlcat.py, jamais ici
(lecons promote-to-engine et modif-affichage-en-aval-du-coeur-valide).
"""
from __future__ import annotations

import csv
import io
import random
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace

import urlcat

from .config import settings

# Borne le nombre de classements simultanes. Le pic memoire du conteneur vaut
# MAX_CONCURRENT x plafond d'URLs x ~1,1 ko : c'est ce produit qu'il faut tenir,
# pas le nombre de requetes HTTP.
_places = threading.Semaphore(settings.max_concurrent)

COLONNES_CSV = ["url", "groupe", "univers", "nombre"]

# Au-dela, le tableau devient ingerable pour le navigateur. On bascule alors sur un
# echantillon par groupe, et on le DIT — plutot que de rogner en silence.
APERCU_MAX = 3000

PANELS = ("tout", "50", "10")


class EntreeTropGrosse(Exception):
    """Le texte envoye depasse un plafond d'octets."""


class TropDUrls(Exception):
    """Plus d'URLs uniques que le plafond autorise."""

    def __init__(self, trouvees: int, plafond: int) -> None:
        super().__init__(f"{trouvees} URLs pour un plafond de {plafond}")
        self.trouvees = trouvees
        self.plafond = plafond


class ServiceSature(Exception):
    """Toutes les places de calcul sont prises."""


class ReglesIllisibles(Exception):
    """Aucune regle de categorie n'a pu etre lue."""


@dataclass(frozen=True)
class Ligne:
    nom: str
    uniques: int
    occurrences: int


@dataclass(frozen=True)
class Resultat:
    rows: list[dict]
    rapport: str
    total_urls: int
    total_occurrences: int
    avertissements: list[str] = field(default_factory=list)
    groupes: list[Ligne] = field(default_factory=list)
    univers: list[Ligne] = field(default_factory=list)
    hotes_inconnus: list[str] = field(default_factory=list)
    groupes_sans_objet: list[str] = field(default_factory=list)
    apercu: list[dict] = field(default_factory=list)
    # Ce que l'utilisateur a demande, et ce qui a reellement ete applique. Les deux
    # different quand « tout » depassait APERCU_MAX : il faut alors pouvoir le dire.
    panel_demande: str = "tout"
    panel_applique: str = "tout"

    @property
    def regles_sans_objet(self) -> int:
        return len(self.groupes_sans_objet)


def _lignes(bloc: dict) -> list[Ligne]:
    sortie = [Ligne(nom, s.urls, s.hits) for nom, s in bloc.items()]
    sortie.sort(key=lambda l: (-l.uniques, l.nom))
    return sortie


def classer(
    regles_texte: str,
    urls_texte: str,
    *,
    plafond: int,
    url_column: str | None = None,
    ignore_dom: bool = True,
    diagnose: bool = True,
    panel: str = "tout",
) -> Resultat:
    """Classe les URLs. Leve une exception du module en cas de refus, jamais de SystemExit."""
    if len(regles_texte.encode("utf-8")) > settings.max_rules_bytes:
        raise EntreeTropGrosse("fichier de regles")
    if len(urls_texte.encode("utf-8")) > settings.max_urls_bytes:
        raise EntreeTropGrosse("liste d'URLs")

    regles = urlcat.parse_rules_text(regles_texte)
    if not regles.categories:
        raise ReglesIllisibles("aucune regle lue dans [crawl:categories]")

    # On compte les URLs AVANT de classer : refuser tot evite d'allouer pour rien.
    uniques = {u for u in urlcat.iter_urls_text(urls_texte, url_column)}
    if len(uniques) > plafond:
        raise TropDUrls(len(uniques), plafond)
    del uniques

    args = SimpleNamespace(
        urls=None,
        url_column=url_column,
        ignore_dom=ignore_dom,
        diagnose=diagnose,
        seed=1,
        out="(telechargement CSV)",
        html=None,
        panel=100,
        panel_per_group=0,
    )

    if not _places.acquire(blocking=False):
        raise ServiceSature("toutes les places de calcul sont prises")
    try:
        rows, stats, hotes, orphelines, total_hits = urlcat.analyse(
            regles, args, urls=urlcat.iter_urls_text(urls_texte, url_column)
        )
        rapport = urlcat.build_report(
            regles, stats, total_hits, len(rows), len(rows), "tout",
            hotes, orphelines, args,
        )
    finally:
        _places.release()

    demande = panel if panel in PANELS else "tout"
    apercu, applique = _apercu(rows, demande)

    # Une regle qui ne gagne jamais n'apparait PAS dans stats : il faut comparer aux regles
    # declarees. Sans ca, le compteur restait a zero et l'encadre ne s'affichait jamais.
    vus = set(stats["groupe"])
    sans_objet = [nom for nom in dict.fromkeys(r.name for r in regles.categories)
                  if nom not in vus]

    return Resultat(
        rows=rows,
        rapport=rapport,
        total_urls=len(rows),
        total_occurrences=total_hits,
        avertissements=list(regles.warnings),
        groupes=_lignes(stats["groupe"]),
        univers=_lignes(stats["univers"]),
        hotes_inconnus=sorted(hotes) if not ignore_dom else [],
        groupes_sans_objet=sans_objet,
        apercu=apercu,
        panel_demande=demande,
        panel_applique=applique,
    )


def _apercu(rows: list[dict], demande: str) -> tuple[list[dict], str]:
    """Choisit ce qui est AFFICHE. Renvoie les lignes et ce qui a reellement ete applique.

    « tout » est le defaut : sur un corpus courant, tout tient, et il n'y a alors aucune
    notion d'echantillon a expliquer. Les pourcentages sont un tirage uniforme reproductible
    (meme corpus, meme tirage). Le repli « par-groupe » ne sert que lorsque « tout »
    depasserait APERCU_MAX : il garde les URLs les plus vues de chaque couple groupe x
    univers, pour qu'aucun groupe ne disparaisse de l'ecran.
    """
    def ordonner(lignes: list[dict]) -> list[dict]:
        return sorted(lignes, key=lambda r: (r["groupe"], r["univers"], -r["nombre"], r["url"]))

    if demande in ("50", "10"):
        garder = max(1, round(len(rows) * int(demande) / 100.0))
        tirage = random.Random(1)
        echantillon = tirage.sample(rows, min(garder, len(rows)))
        if len(echantillon) <= APERCU_MAX:
            return ordonner(echantillon), demande
        rows = echantillon   # trop gros meme reduit : on retombe sur le filet ci-dessous

    if len(rows) <= APERCU_MAX:
        return ordonner(rows), "tout" if demande == "tout" else demande

    paniers: dict[tuple[str, str], list[dict]] = {}
    for ligne in rows:
        paniers.setdefault((ligne["groupe"], ligne["univers"]), []).append(ligne)
    par_couple = max(1, APERCU_MAX // max(1, len(paniers)))
    retenues: list[dict] = []
    for panier in paniers.values():
        panier.sort(key=lambda r: (-r["nombre"], r["url"]))
        retenues.extend(panier[:par_couple])
    return ordonner(retenues), "par-groupe"


def filtrer(rows: list[dict], groupe: str | None = None, univers: str | None = None) -> list[dict]:
    """Restreint a un groupe et/ou un univers. Porte sur la TOTALITE des lignes, pas sur
    l'apercu : telecharger « ce que je regarde » doit rendre toutes les URLs de ce groupe,
    pas seulement l'echantillon affiche."""
    sortie = rows
    if groupe:
        sortie = [r for r in sortie if r["groupe"] == groupe]
    if univers:
        sortie = [r for r in sortie if r["univers"] == univers]
    return sortie


def nom_fichier(groupe: str | None = None, univers: str | None = None) -> str:
    morceaux = [m for m in ("classement", groupe, univers) if m]
    propre = "-".join(
        "".join(c if (c.isalnum() or c in "-_") else "-" for c in m).strip("-")
        for m in morceaux
    )
    return (propre or "classement") + ".csv"


def csv_bytes(rows: list[dict]) -> bytes:
    """Le meme en-tete et le meme ordre de colonnes que la CLI."""
    tampon = io.StringIO(newline="")
    graveur = csv.DictWriter(tampon, fieldnames=COLONNES_CSV, extrasaction="ignore",
                             lineterminator="\r\n")
    graveur.writeheader()
    graveur.writerows(rows)
    # BOM : Excel ouvre sinon l'UTF-8 de travers sur les accents.
    return b"\xef\xbb\xbf" + tampon.getvalue().encode("utf-8")
