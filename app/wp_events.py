"""Declaration d'usage a WordPress (route creapulse-tools `tool-event`) -> Nimble.

Appel serveur -> serveur, fire-and-forget : jamais bloquant pour le membre, jamais de retry,
jeton jamais logge. Le jeton va dans le CORPS, jamais en query string : c'est la seule
authentification de la route.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("urlcat.wp-events")


async def notify_tool_event(wp_token: str) -> None:
    """A appeler avec un jeton DEJA verifie localement, dans la meme requete (il vit 10 min)."""
    if not settings.wp_tool_event_url or not settings.tool_slug:
        return
    try:
        async with httpx.AsyncClient(timeout=settings.wp_tool_event_timeout_s) as client:
            resp = await client.post(
                settings.wp_tool_event_url,
                json={"wp_token": wp_token, "tool": settings.tool_slug},
                headers={"Content-Type": "application/json", "User-Agent": "creapulse-urlcat/1.0"},
            )
        recorded = None
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                recorded = resp.json().get("recorded")
            except ValueError:
                recorded = None
        log.info("tool-event tool=%s http=%s recorded=%s", settings.tool_slug, resp.status_code, recorded)
    except httpx.HTTPError as exc:
        log.warning("tool-event tool=%s echec reseau (%s) — ignore", settings.tool_slug, exc.__class__.__name__)
    except Exception as exc:  # pragma: no cover — filet : ne jamais remonter
        log.warning("tool-event tool=%s erreur inattendue (%s) — ignore", settings.tool_slug, exc.__class__.__name__)
