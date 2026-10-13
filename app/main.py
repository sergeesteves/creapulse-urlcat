"""Service web d'urlcat : un formulaire, un classement, un CSV.

Servi sous un SOUS-CHEMIN (`/outils/urlcat`) et non un sous-domaine, pour que l'iframe de la
landing soit en meme origine : cookies et telechargement marchent sans reglage cross-origin
(lecon outil-embarque-sous-chemin-same-origin). Le prefixe est retire par un middleware, donc
les routes sont declarees nues et l'app fonctionne que Traefik le retire ou non.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import engine, session, wp_token
from .config import settings
from .wp_events import notify_tool_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("urlcat.web")

ICI = Path(__file__).parent
gabarits = Jinja2Templates(directory=str(ICI / "templates"))

app = FastAPI(title="urlcat", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(ICI / "static")), name="static")

# Marge au-dessus de la somme des plafonds : bornes du formulaire + en-tetes multipart.
PLAFOND_CORPS = settings.max_rules_bytes + settings.max_urls_bytes + 65_536


@app.middleware("http")
async def prefixe_et_garde_fous(request: Request, call_next):
    """Retire le prefixe public, refuse un corps trop gros, pose les en-tetes de securite."""
    chemin = request.scope["path"]
    prefixe = settings.root_path
    if prefixe and (chemin == prefixe or chemin.startswith(prefixe + "/")):
        # Traefik n'a pas retire le prefixe : le chemin recu EST le chemin public.
        request.scope["chemin_public"] = chemin
        request.scope["path"] = chemin[len(prefixe):] or "/"
    else:
        # Traefik l'a retire (cas de Coolify, is_stripprefix_enabled) : le chemin public
        # se reconstruit. Sans ca, la redirection d'apres-jeton renvoyait le membre vers la
        # racine du site DANS l'iframe, et seul un visiteur connecte le voyait.
        request.scope["chemin_public"] = (prefixe + chemin) if prefixe else chemin

    longueur = request.headers.get("content-length")
    if longueur and longueur.isdigit() and int(longueur) > PLAFOND_CORPS:
        return _erreur(request, 413, "Envoi trop volumineux",
                       f"Le corps de la requete depasse {PLAFOND_CORPS // 1_048_576} Mio.")

    reponse = await call_next(request)
    # Le jeton WP ne doit fuir ni par Referer, ni dans un cache partage.
    reponse.headers["Referrer-Policy"] = "no-referrer"
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers.setdefault("Cache-Control", "no-store")
    return reponse


def _url_publique(request: Request, *, sans: str | None = None) -> str:
    chemin = request.scope.get("chemin_public") or request.url.path
    params = dict(request.query_params)
    if sans:
        params.pop(sans, None)
    return chemin + ("?" + urlencode(params) if params else "")


def _membre(request: Request) -> dict | None:
    return session.decode(request.cookies.get(session.COOKIE))


def _contexte(request: Request, **extra) -> dict:
    membre = _membre(request)
    base = {
        "request": request,
        "prefixe": settings.root_path,
        "embed": request.query_params.get("embed") == "1",
        "membre": membre,
        "plafond": settings.plafond(bool(membre)),
        "plafond_anon": settings.max_urls_anon,
        "plafond_membre": settings.max_urls_membre,
    }
    base.update(extra)
    return base


def _rendu(request: Request, status_code: int = 200, **extra) -> Response:
    """Une seule page : formulaire en haut (toujours), resultats dessous s'il y en a.

    L'outil se travaille par iterations — on corrige les regles, on reclasse, on
    recommence — donc le fichier de regles ne doit jamais disparaitre, erreur comprise.
    """
    return gabarits.TemplateResponse(
        request, "index.html", _contexte(request, **extra), status_code=status_code)


def _erreur(request: Request, code: int, titre: str, detail: str) -> Response:
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"erreur": titre, "detail": detail}, status_code=code)
    return gabarits.TemplateResponse(
        request, "erreur.html", _contexte(request, titre=titre, detail=detail), status_code=code)


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "outil": settings.tool_slug,
        "plafond_anon": settings.max_urls_anon,
        "plafond_membre": settings.max_urls_membre,
        "places": settings.max_concurrent,
        "jeton_configure": bool(settings.wp_token_secret),
        "session_configuree": bool(settings.session_secret),
    })


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def accueil(request: Request):
    """Formulaire. Consomme le jeton WP au chargement, puis le retire de l'URL."""
    brut = request.query_params.get("wp_token")
    if brut:
        identite = wp_token.verify(brut, settings.wp_token_secret)
        cible = _url_publique(request, sans="wp_token")
        reponse = RedirectResponse(cible, status_code=302)
        if identite:
            # Declaration d'usage : jamais bloquante, jamais relancee.
            await notify_tool_event(brut)
            reponse.set_cookie(
                session.COOKIE, session.encode(identite.user_id, identite.levels),
                max_age=settings.session_ttl_s, httponly=True, secure=True,
                samesite="lax", path=settings.root_path or "/",
            )
            log.info("jeton accepte user=%s niveaux=%s", identite.user_id, list(identite.levels))
        else:
            log.info("jeton refuse ou expire — poursuite en anonyme")
        return reponse

    return _rendu(request)


@app.post("/classer")
def classer(
    request: Request,
    regles: str = Form(""),
    urls: str = Form(""),
    url_column: str = Form(""),
    ignore_dom: str = Form(""),
    sortie: str = Form("apercu"),
):
    """Route synchrone volontairement : FastAPI l'execute dans un thread, et le classement
    est CPU-bound. La concurrence reelle est bornee par le semaphore du moteur."""
    regles, urls = regles.strip(), urls.strip()
    saisie = {"regles": regles, "urls": urls, "url_column": url_column, "ignore_dom": ignore_dom}

    def refus(code: int, titre: str, detail: str) -> Response:
        """Rend la page avec les champs intacts : perdre un fichier de regles en cours
        de mise au point serait le pire defaut possible pour cet outil."""
        return _rendu(request, status_code=code, erreur_titre=titre, erreur_detail=detail, **saisie)

    if not regles or not urls:
        return refus(400, "Il manque quelque chose",
                     "Il faut un fichier de regles ET une liste d'URLs.")

    plafond = settings.plafond(bool(_membre(request)))
    try:
        resultat = engine.classer(
            regles, urls,
            plafond=plafond,
            url_column=url_column.strip() or None,
            ignore_dom=bool(ignore_dom),
        )
    except engine.TropDUrls as exc:
        return refus(413, "Trop d'URLs pour la version en ligne",
                     f"{exc.trouvees} URLs uniques pour un plafond de {exc.plafond}. "
                     "Le script en ligne de commande, lui, n'a aucune limite.")
    except engine.EntreeTropGrosse as exc:
        return refus(413, "Envoi trop volumineux", f"Le {exc} depasse la taille autorisee.")
    except engine.ReglesIllisibles:
        return refus(400, "Aucune regle lue",
                     "Verifiez la section [crawl:categories] du fichier de regles.")
    except engine.ServiceSature:
        return refus(503, "Le service est occupe",
                     "Deux classements tournent deja. Reessayez dans quelques secondes.")
    except ValueError as exc:
        return refus(400, "Colonne introuvable", str(exc))

    log.info("classement urls=%s occurrences=%s groupes=%s",
             resultat.total_urls, resultat.total_occurrences, len(resultat.groupes))

    if sortie == "csv":
        return Response(
            engine.csv_bytes(resultat.rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="classement.csv"'},
        )

    return _rendu(request, r=resultat, **saisie)
