"""Session courte de l'app, signee, posee apres verification du jeton WordPress.

Le jeton WP vit 10 minutes et ne sert qu'au chargement (usage unique). La session, elle,
tient le temps de la visite pour que le plafond membre s'applique aux classements suivants.
Meme schema base64url que le jeton, secret SEPARE (SESSION_SECRET).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .config import settings

COOKIE = "urlcat_session"


def _sign(payload_b64: str) -> str:
    mac = hmac.new(settings.session_secret.encode("utf-8"), payload_b64.encode("ascii"),
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def encode(user_id: int, levels: tuple[int, ...]) -> str:
    payload = {"sub": int(user_id), "levels": list(levels), "exp": int(time.time()) + settings.session_ttl_s}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{b64}.{_sign(b64)}"


def decode(valeur: str | None) -> dict | None:
    """Renvoie le payload, ou None. Jamais d'exception."""
    if not valeur or not settings.session_secret:
        return None
    try:
        b64, sig = valeur.strip().split(".", 1)
        if "." in sig or not hmac.compare_digest(_sign(b64), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8"))
        if not isinstance(payload, dict) or time.time() >= int(payload["exp"]):
            return None
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
