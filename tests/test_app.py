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


def test_le_formulaire_survit_au_classement(client):
    """L'outil se travaille par iterations : regles -> classer -> regles -> classer.
    Le fichier de regles doit rester a l'ecran, rempli, avec un bouton pour reclasser."""
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert rep.status_code == 200
    assert 'name="regles"' in rep.text
    assert "[crawl:categories]" in rep.text        # les regles soumises sont reaffichees
    assert "https://exemple.fr/blog/a" in rep.text  # les URLs aussi
    assert "Reclasser" in rep.text


def test_une_erreur_ne_fait_pas_perdre_les_regles(client):
    """Le pire defaut possible pour cet outil : renvoyer une page d'erreur nue et
    laisser l'utilisateur retaper un fichier de regles en cours de mise au point."""
    beaucoup = "\n".join(f"https://exemple.fr/blog/{i}" for i in range(11))
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": beaucoup, "ignore_dom": "1"})
    assert rep.status_code == 413
    assert "[crawl:categories]" in rep.text
    assert "https://exemple.fr/blog/7" in rep.text
    assert 'name="regles"' in rep.text


def test_filtres_et_rapport_replie(client):
    """Filtrer par groupe ou univers sert a verifier un groupe a l'oeil. Le rapport,
    lui, passe en second plan : c'est un outil de travail, pas un rapport."""
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert 'id="f-groupe"' in rep.text and 'id="f-univers"' in rep.text
    assert 'id="table-urls"' in rep.text
    assert 'data-groupe="blog"' in rep.text
    # le rapport est dans un <details>, en fin de page
    assert "<details" in rep.text and "Rapport complet et diagnostic" in rep.text
    assert rep.text.index("table-urls") < rep.text.index("Rapport complet et diagnostic")


def test_par_defaut_tout_est_affiche():
    """Le defaut doit etre « tout » : un echantillon impose obligeait a expliquer une
    mecanique invisible (« 7 lignes sur 19 dans l'apercu »), alors que sur un corpus
    courant tout tient a l'ecran."""
    # 100 URLs : MAX_URLS_BYTES est volontairement bas dans ces tests (4 Kio).
    lignes = ["https://exemple.fr/blog/%d" % i for i in range(100)]
    lignes.append("https://exemple.fr/contact")        # groupe « autres », une seule URL
    resultat = engine.classer(REGLES, "\n".join(lignes), plafond=1000)
    assert resultat.panel_demande == "tout"
    assert resultat.panel_applique == "tout"
    assert len(resultat.apercu) == resultat.total_urls == 101


def test_le_panel_demande_reduit_laffichage_et_se_declare():
    """Choisir 10 % doit se voir dans les lignes ET dans ce que le resultat declare :
    c'est ce champ qui permet a la page de dire quel echantillon elle montre."""
    lignes = ["https://exemple.fr/blog/%d" % i for i in range(100)]
    resultat = engine.classer(REGLES, "\n".join(lignes), plafond=1000, panel="10")
    assert resultat.panel_demande == resultat.panel_applique == "10"
    assert len(resultat.apercu) == 10
    assert len(resultat.rows) == 100, "le classement reste complet derriere l'affichage"

    moitie = engine.classer(REGLES, "\n".join(lignes), plafond=1000, panel="50")
    assert len(moitie.apercu) == 50


def test_le_tirage_du_panel_est_reproductible():
    """Meme corpus, meme echantillon : sinon reclasser apres avoir corrige une regle
    changerait les URLs affichees, et on ne saurait plus ce qu'on compare."""
    lignes = ["https://exemple.fr/blog/%d" % i for i in range(100)]
    a = engine.classer(REGLES, "\n".join(lignes), plafond=1000, panel="10")
    b = engine.classer(REGLES, "\n".join(lignes), plafond=1000, panel="10")
    assert [r["url"] for r in a.apercu] == [r["url"] for r in b.apercu]


def test_un_panel_inconnu_retombe_sur_tout():
    """Le panel vient d'un champ de formulaire : une valeur fabriquee a la main ne doit
    pas faire tomber la requete, juste revenir au defaut."""
    resultat = engine.classer(REGLES, URLS, plafond=1000, panel="42")
    assert resultat.panel_demande == "tout"


def test_au_dela_du_plafond_dapercu_aucun_groupe_ne_disparait():
    """Le filet automatique, teste sur _apercu : au-dela d'APERCU_MAX, un tableau complet
    serait ingerable, et garder les N premieres lignes ferait disparaitre un petit groupe
    derriere un gros. On garde donc les plus vues de CHAQUE couple, et on le declare —
    « par-groupe » — pour que la page puisse le dire au lieu de rogner en silence."""
    rows = [{"url": "https://exemple.fr/blog/%d" % i, "groupe": "blog",
             "univers": "editorial", "nombre": 1}
            for i in range(engine.APERCU_MAX * 2)]
    rows.append({"url": "https://exemple.fr/contact", "groupe": "autres",
                 "univers": "autres", "nombre": 3})
    apercu, applique = engine._apercu(rows, "tout")
    assert applique == "par-groupe"
    assert len(apercu) <= engine.APERCU_MAX
    assert {r["groupe"] for r in apercu} == {"blog", "autres"}


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


# --------------------------------------------------------------------------- #
# Non-regressions
# --------------------------------------------------------------------------- #

def test_redirection_apres_jeton_porte_le_prefixe_meme_quand_traefik_le_retire(client):
    """Traefik retire le prefixe (is_stripprefix_enabled), donc le chemin qui arrive est
    DEJA nu. Reconstruire la cible depuis ce chemin renvoyait le membre vers la racine du
    site, dans l'iframe. Anonyme, il n'y a pas de jeton donc pas de redirection : seul un
    visiteur connecte voyait la panne."""
    rep = client.get("/?embed=1&wp_token=bidon", follow_redirects=False)
    assert rep.status_code == 302
    cible = rep.headers["location"]
    assert cible.startswith(f"{PREFIXE}/"), cible
    assert "embed=1" in cible
    assert "wp_token" not in cible


def test_head_est_accepte_sur_les_routes_de_lecture(client):
    """FastAPI n'ajoute pas HEAD aux routes GET (Starlette le fait, APIRoute non) : une sonde
    de monitoring ou un verificateur de liens recevait 405."""
    assert client.head("/health").status_code == 200
    assert client.head(f"{PREFIXE}/").status_code == 200


def test_health_dit_si_la_session_est_signable(client):
    """Sans SESSION_SECRET, un membre retombe SILENCIEUSEMENT sur le plafond gratuit."""
    assert client.get("/health").json()["session_configuree"] is True


def test_jeu_dexemple_servi_et_coherent(client):
    """Le bouton « Charger un exemple » remplit les deux champs depuis ces deux fichiers.
    S'ils ne sont pas servis, le bouton echoue en silence pour le visiteur."""
    r = client.get(f"{PREFIXE}/static/exemple-regles.txt")
    u = client.get(f"{PREFIXE}/static/exemple-urls.txt")
    assert r.status_code == 200 and u.status_code == 200
    assert "[crawl:categories]" in r.text and "[crawl:univers]" in r.text
    assert u.text.strip().count("\n") >= 10


def test_le_jeu_dexemple_declenche_bien_les_deux_diagnostics():
    """L'exemple n'est pas la pour classer, il est la pour DEMONTRER. S'il cesse de
    produire une regle eclipsee et une regle sans objet, il a perdu sa raison d'etre."""
    racine = Path(__file__).resolve().parents[1] / "app" / "static"
    regles = (racine / "exemple-regles.txt").read_text(encoding="utf-8")
    urls = (racine / "exemple-urls.txt").read_text(encoding="utf-8")

    resultat = engine.classer(regles, urls, plafond=1000)
    assert resultat.total_urls == 19
    assert resultat.total_occurrences == 25
    # une regle qui ne gagne jamais, nommee
    assert "regle-perimee" in resultat.groupes_sans_objet
    # et l'eclipse, visible dans le rapport
    assert "ECLIPS" in resultat.rapport.upper()
    assert "inutiles-tracking" in resultat.rapport
    # les deux axes divergent : c'est le point pedagogique
    groupes = {l.nom: l.uniques for l in resultat.groupes}
    univers = {l.nom: l.uniques for l in resultat.univers}
    assert univers["catalogue"] > max(groupes.values())


# --------------------------------------------------------------------------- #
# Export filtre et composition de l'apercu
# --------------------------------------------------------------------------- #

def test_lexport_filtre_depasse_lapercu():
    """Le piege : l'apercu est un echantillon. « Telecharger ces URLs » doit rendre toutes
    les URLs du groupe, pas seulement celles affichees. Verifie au niveau du moteur, ou la
    difference est observable (le plafond anonyme des tests est volontairement a 10)."""
    lignes = ["https://exemple.fr/blog/%d" % i for i in range(60)]
    lignes.append("https://exemple.fr/contact")
    resultat = engine.classer(REGLES, "\n".join(lignes), plafond=1000, panel="10")

    dans_apercu = [r for r in resultat.apercu if r["groupe"] == "blog"]
    a_exporter = engine.filtrer(resultat.rows, groupe="blog")
    assert len(dans_apercu) < 60, "l'affichage est bien reduit par le panel"
    assert len(a_exporter) == 60, "l'export porte sur la totalite du groupe, pas sur l'apercu"
    assert all(r["groupe"] == "blog" for r in a_exporter)


def test_le_csv_respecte_le_filtre(client):
    rep = client.post(f"{PREFIXE}/classer", data={
        "regles": REGLES, "urls": URLS, "ignore_dom": "1",
        "sortie": "csv", "filtre_groupe": "blog",
    })
    assert rep.status_code == 200
    texte = rep.content.decode("utf-8-sig")
    corps = [l for l in texte.strip().splitlines()[1:] if l]
    assert corps, "le filtre ne doit pas vider l'export"
    assert all(",blog," in l for l in corps)
    assert "contact" not in texte          # presente dans le jeu, mais hors du filtre


def test_le_nom_du_fichier_porte_le_filtre(client):
    rep = client.post(f"{PREFIXE}/classer", data={
        "regles": REGLES, "urls": URLS, "ignore_dom": "1",
        "sortie": "csv", "filtre_groupe": "blog",
    })
    assert 'filename="classement-blog.csv"' in rep.headers["content-disposition"]

    rep = client.post(f"{PREFIXE}/classer", data={
        "regles": REGLES, "urls": URLS, "ignore_dom": "1", "sortie": "csv",
    })
    assert 'filename="classement.csv"' in rep.headers["content-disposition"]


def test_export_sans_filtre_rend_tout(client):
    rep = client.post(f"{PREFIXE}/classer", data={
        "regles": REGLES, "urls": URLS, "ignore_dom": "1", "sortie": "csv",
    })
    lignes = rep.content.decode("utf-8-sig").strip().splitlines()
    assert len(lignes) == 1 + 3   # en-tete + les 3 URLs uniques du jeu de test


def test_le_filet_par_groupe_garde_les_urls_les_plus_vues():
    """Quand le filet par groupe s'enclenche, il garde les plus VUES de chaque couple, pas
    un tirage au hasard : trier par nombre un tirage aleatoire donnerait « les plus
    frequentes parmi celles tirees au hasard », ce qui n'est pas la meme chose."""
    rows = [{"url": "https://exemple.fr/blog/rare-%d" % i, "groupe": "blog",
             "univers": "editorial", "nombre": 1}
            for i in range(engine.APERCU_MAX * 2)]
    rows.append({"url": "https://exemple.fr/blog/vedette", "groupe": "blog",
                 "univers": "editorial", "nombre": 25})
    rows.append({"url": "https://exemple.fr/blog/second", "groupe": "blog",
                 "univers": "editorial", "nombre": 12})

    apercu, applique = engine._apercu(rows, "tout")
    assert applique == "par-groupe"
    urls = [r["url"] for r in apercu]
    assert "https://exemple.fr/blog/vedette" in urls
    assert "https://exemple.fr/blog/second" in urls
    nombres = [r["nombre"] for r in apercu]
    assert nombres == sorted(nombres, reverse=True)


def test_le_selecteur_de_panel_est_servi_et_poste_avec_le_formulaire(client):
    """Le panel se decide cote serveur (seul lui a toutes les lignes) : la liste doit donc
    etre associee au formulaire de classement par form=, sinon changer le panel ne poste
    rien et l'affichage ne bouge pas."""
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert 'id="f-panel"' in rep.text
    assert 'name="panel" form="form-classer"' in rep.text
    # Et le message dit ce qu'il montre, en clair, plutot que « N ligne(s) dans l'apercu ».
    assert "URLs uniques</strong> sont" in rep.text
    assert "dans l’aperçu" not in rep.text

    reduit = client.post(f"{PREFIXE}/classer",
                         data={"regles": REGLES, "urls": URLS, "sortie": "apercu",
                               "ignore_dom": "1", "panel": "50"})
    assert "URLs affichées" in reduit.text
    assert 'value="50" selected' in reduit.text


def test_la_liste_durls_se_replie_apres_lanalyse(client):
    """Une fois les URLs chargees, la zone ne sert plus a rien pendant qu'on itere sur les
    regles : elle doit se replier pour rendre la hauteur au fichier de regles."""
    vierge = client.get(f"{PREFIXE}/")
    assert '<details class="bloc-urls" open>' in vierge.text

    apres = client.post(f"{PREFIXE}/classer",
                        data={"regles": REGLES, "urls": URLS, "sortie": "apercu",
                              "ignore_dom": "1"})
    assert '<details class="bloc-urls" >' in apres.text
    assert 'id="compte-urls"' in apres.text


def test_boutons_et_entetes_triables_presents(client):
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert 'id="f-export"' in rep.text                 # export la ou se porte le regard
    assert 'form="form-classer"' in rep.text           # sans dupliquer regles et URLs
    assert 'name="filtre_groupe"' in rep.text
    assert 'data-tri="nombre"' in rep.text             # colonne Nb triable
    assert 'class="triable"' in rep.text


def test_les_deux_volets_existent_apres_classement(client):
    """Regles a gauche, resultats a droite : la mise en page est faite en CSS, mais elle
    repose sur ces deux conteneurs. Les perdre casserait la disposition sans rien casser
    d'autre — donc sans que rien ne le signale."""
    rep = client.post(f"{PREFIXE}/classer",
                      data={"regles": REGLES, "urls": URLS, "sortie": "apercu", "ignore_dom": "1"})
    assert 'class="atelier"' in rep.text
    assert 'class="volet-regles"' in rep.text
    assert 'class="volet-resultats"' in rep.text
    # le formulaire est dans le volet de gauche, le tableau dans celui de droite
    assert rep.text.index('volet-regles') < rep.text.index('name="regles"')
    assert rep.text.index('volet-resultats') < rep.text.index('id="table-urls"')
    assert rep.text.index('name="regles"') < rep.text.index('volet-resultats')


def test_pas_de_volet_vide_avant_le_premier_classement(client):
    """Sans resultat, pas de grille a deux colonnes : une colonne vide a droite serait
    une invitation a chercher ce qui manque."""
    rep = client.get(f"{PREFIXE}/")
    assert 'class="atelier"' not in rep.text
    assert 'class="volet-resultats"' not in rep.text
    assert 'class="volet-regles"' in rep.text


def test_le_mode_embarque_survit_au_classement(client):
    """Dans l'iframe, le formulaire poste vers /classer : sans embed=1 dans l'action, la
    page reaffichee sort du mode embarque et le titre h1 reapparait au milieu de la page
    d'accueil du site. Le defaut ne se voit QUE dans l'iframe, apres la premiere iteration."""
    rep = client.get(f"{PREFIXE}/?embed=1")
    assert 'action="/outils/urlcat/classer?embed=1#resultats"' in rep.text
    assert "<h1>" not in rep.text

    rep = client.get(f"{PREFIXE}/")
    assert 'action="/outils/urlcat/classer#resultats"' in rep.text
    assert "<h1>" in rep.text
