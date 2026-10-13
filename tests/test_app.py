"""Verifications du service web. Complementaire de tests/verifie.py, qui couvre le moteur.

Lancement, depuis la racine du depot :
    python -m pytest tests/test_app.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

# L'environnement se fixe AVANT d'importer app.config : les reglages sont lus a l'import.
SECRET = "secret-de-test-0123456789abcdef0123456789abcdef0123456789abcdefx"
os.environ.update({
    "ROOT_PATH": "/outils/urlcat",
    "WP_TOKEN_SECRET": SECRET,
    "SESSION_SECRET": "session-de-test-0123456789abcdef0123456789abcdef0123456789abcd",
    "WP_TOOL_EVENT_URL": "",          # pas d'appel reseau pendant les tests
    "MAX_URLS_ANON": "10",            # plafond minuscule, pour le tester vite
    "MAX_URLS_MEMBRE": "100",
    "MAX_RULES_BYTES": "2048",
    "MAX_URLS_BYTES": "4096",
    "MAX_CONCURRENT": "2",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import engine, wp_token  # noqa: E402
from app.main import app  # noqa: E402

PREFIXE = "/outils/urlcat"

REGLES = """[crawl:categories]

@names
100\tsite
200\tinutile

@
path /blog/*
name blog
parent-id\t100

@
path *utm_*
name inutiles-tracking
parent-id\t200

[crawl:univers]

@
path /blog/*
name editorial
"""

URLS = """https://exemple.fr/blog/a
https://exemple.fr/blog/a
https://exemple.fr/blog/b?utm_source=x
https://exemple.fr/contact
"""


@pytest.fixture
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Jeton WordPress — vecteur de reference, octet pour octet
# --------------------------------------------------------------------------- #

PAYLOAD_REF = {
    "sub": 42, "email": "serge@exemple.fr", "name": "Serge Esteves",
    "levels": [1], "aud": "creapulse-tools", "iat": 1760000000, "exp": 1760000600,
}
JETON_REF = (
    "eyJzdWIiOjQyLCJlbWFpbCI6InNlcmdlQGV4ZW1wbGUuZnIiLCJuYW1lIjoiU2VyZ2UgRXN0ZXZlcyIsImxldmVsc"
    "yI6WzFdLCJhdWQiOiJjcmVhcHVsc2UtdG9vbHMiLCJpYXQiOjE3NjAwMDAwMDAsImV4cCI6MTc2MDAwMDYwMH0"
    ".tGDpGelY5jummCGzTTeFSZig62Gu7dELDjRSeh6gW_Q"
)


def test_vecteur_de_reference_du_jeton():
    """Fige l'algorithme. Toute divergence d'ordre de cles, d'echappement ou de padding
    entre l'emetteur PHP et ce verificateur fait echouer CE test, et lui seul."""
    assert wp_token.make_token(PAYLOAD_REF, SECRET) == JETON_REF


def test_jeton_valide():
    identite = wp_token.verify(JETON_REF, SECRET, now=1760000100)
    assert identite is not None
    assert identite.user_id == 42
    assert identite.email == "serge@exemple.fr"
    assert identite.levels == (1,)


@pytest.mark.parametrize("cas, jeton, maintenant", [
    ("expire", JETON_REF, 1760000601),
    ("signature fausse", JETON_REF[:-1] + ("A" if JETON_REF[-1] != "A" else "B"), 1760000100),
    ("vide", "", 1760000100),
    ("sans point", "abcdef", 1760000100),
    ("deux points", JETON_REF + ".xyz", 1760000100),
])
def test_jeton_refuse(cas, jeton, maintenant):
    assert wp_token.verify(jeton, SECRET, now=maintenant) is None, cas


def test_jeton_refuse_mauvaise_audience():
    mauvais = dict(PAYLOAD_REF, aud="autre-chose")
    assert wp_token.verify(wp_token.make_token(mauvais, SECRET), SECRET, now=1760000100) is None


def test_jeton_sans_secret_configure():
    assert wp_token.verify(JETON_REF, "", now=1760000100) is None


# --------------------------------------------------------------------------- #
# Moteur, vu depuis l'abstraction
# --------------------------------------------------------------------------- #

def test_classement_et_agregation():
    r = engine.classer(REGLES, URLS, plafond=10)
    assert r.total_urls == 3           # /blog/a vue deux fois ne compte qu'une URL
    assert r.total_occurrences == 4    # mais quatre occurrences
    par_url = {ligne["url"]: ligne for ligne in r.rows}
    assert par_url["https://exemple.fr/blog/a"]["nombre"] == 2
    assert par_url["https://exemple.fr/blog/a"]["groupe"] == "blog"
    assert par_url["https://exemple.fr/contact"]["groupe"] == "autres"


def test_premier_motif_gagnant_eclipse_la_regle_suivante():
    """/blog/b?utm_source=x correspond aux DEUX regles ; celle du haut gagne.
    C'est le comportement attendu, et c'est ce que le diagnostic doit nommer."""
    r = engine.classer(REGLES, URLS, plafond=10)
    par_url = {ligne["url"]: ligne for ligne in r.rows}
    assert par_url["https://exemple.fr/blog/b?utm_source=x"]["groupe"] == "blog"
    noms = {l.nom: l.uniques for l in r.groupes}
    assert noms.get("inutiles-tracking", 0) == 0
    assert r.regles_sans_objet >= 1


def test_plafond_durls():
    beaucoup = "\n".join(f"https://exemple.fr/blog/{i}" for i in range(11))
    with pytest.raises(engine.TropDUrls) as exc:
        engine.classer(REGLES, beaucoup, plafond=10)
    assert exc.value.trouvees == 11
    assert exc.value.plafond == 10


def test_entree_trop_grosse():
    with pytest.raises(engine.EntreeTropGrosse):
        engine.classer(REGLES, "https://exemple.fr/a\n" * 5000, plafond=100_000)


def test_regles_illisibles():
    with pytest.raises(engine.ReglesIllisibles):
        engine.classer("ceci n'est pas un fichier de regles", URLS, plafond=10)


def test_colonne_csv_absente_leve_valueerror():
    with pytest.raises(ValueError):
        engine.classer(REGLES, "a,b\n1,2\n", plafond=10, url_column="absente")


def test_csv_entete_et_bom():
    r = engine.classer(REGLES, URLS, plafond=10)
    brut = engine.csv_bytes(r.rows)
    assert brut.startswith(b"\xef\xbb\xbf")           # Excel lit les accents de travers sans BOM
    texte = brut.decode("utf-8-sig")
    assert texte.splitlines()[0] == "url,groupe,univers,nombre"
    assert len(texte.strip().splitlines()) == 1 + r.total_urls


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

def test_health_sans_prefixe(client):
    """Le HEALTHCHECK Docker frappe 127.0.0.1:8000/health, sans passer par Traefik."""
    rep = client.get("/health")
    assert rep.status_code == 200
    corps = rep.json()
    assert corps["ok"] is True
    assert corps["jeton_configure"] is True


def test_health_avec_prefixe(client):
    assert client.get(f"{PREFIXE}/health").status_code == 200


def test_formulaire_sert_le_css_derriere_le_prefixe(client):
    """Piege vecu sur le premier deploiement du generateur de voix : CSS en 404."""
    rep = client.get(f"{PREFIXE}/")
    assert rep.status_code == 200
    assert f'href="{PREFIXE}/static/urlcat.css"' in rep.text
    assert client.get(f"{PREFIXE}/static/urlcat.css").status_code == 200


def test_entetes_de_securite(client):
    rep = client.get(f"{PREFIXE}/")
    assert rep.headers["referrer-policy"] == "no-referrer"
    assert rep.headers["x-content-type-options"] == "nosniff"


def test_classement_html(client):
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert rep.status_code == 200
    assert "URLs uniques" in rep.text
    assert "blog" in rep.text
    assert "Rapport et diagnostic" in rep.text


def test_classement_csv(client):
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "csv", "ignore_dom": "1"})
    assert rep.status_code == 200
    assert rep.headers["content-type"].startswith("text/csv")
    assert "attachment" in rep.headers["content-disposition"]
    assert rep.content.decode("utf-8-sig").splitlines()[0] == "url,groupe,univers,nombre"


def test_champ_manquant(client):
    rep = client.post(f"{PREFIXE}/classer", data={"regles": REGLES, "urls": ""})
    assert rep.status_code == 400


def test_plafond_refuse_avec_un_message_utile(client):
    beaucoup = "\n".join(f"https://exemple.fr/blog/{i}" for i in range(11))
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": beaucoup, "ignore_dom": "1"})
    assert rep.status_code == 413
    assert "ligne de commande" in rep.text      # on renvoie vers le script, sans limite


def test_jeton_pose_une_session_et_disparait_de_lurl(client):
    """Usage unique au chargement : 302 vers la meme URL sans le parametre."""
    import app.wp_token as wt
    jeton = wt.make_token(
        dict(PAYLOAD_REF, iat=int(__import__("time").time()),
             exp=int(__import__("time").time()) + 600), SECRET)
    rep = client.get(f"{PREFIXE}/?embed=1&wp_token={jeton}", follow_redirects=False)
    assert rep.status_code == 302
    cible = rep.headers["location"]
    assert "wp_token" not in cible
    assert cible.startswith(f"{PREFIXE}/")
    assert "embed=1" in cible
    assert "urlcat_session" in rep.headers.get("set-cookie", "")


def test_jeton_invalide_poursuit_en_anonyme(client):
    rep = client.get(f"{PREFIXE}/?wp_token=nimportequoi", follow_redirects=False)
    assert rep.status_code == 302
    assert "wp_token" not in rep.headers["location"]
    assert "urlcat_session" not in rep.headers.get("set-cookie", "")


def test_plafond_membre_plus_haut_que_lanonyme():
    from app.config import settings
    assert settings.plafond(membre=False) == 10
    assert settings.plafond(membre=True) == 100
