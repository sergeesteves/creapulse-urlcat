"""Configuration par variables d'environnement. Aucun secret en dur."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(nom: str, defaut: int) -> int:
    brut = os.getenv(nom, "").strip()
    try:
        valeur = int(brut)
    except ValueError:
        return defaut
    return valeur if valeur > 0 else defaut


@dataclass(frozen=True)
class Settings:
    root_path: str = os.getenv("ROOT_PATH", "/outils/urlcat").rstrip("/")
    wp_token_secret: str = os.getenv("WP_TOKEN_SECRET", "")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    wp_tool_event_url: str = os.getenv("WP_TOOL_EVENT_URL", "").strip()
    tool_slug: str = os.getenv("TOOL_SLUG", "urlcat").strip()
    wp_tool_event_timeout_s: float = 4.0

    max_urls_anon: int = _int("MAX_URLS_ANON", 50_000)
    max_urls_membre: int = _int("MAX_URLS_MEMBRE", 150_000)
    max_rules_bytes: int = _int("MAX_RULES_BYTES", 1_048_576)
    max_urls_bytes: int = _int("MAX_URLS_BYTES", 8_388_608)
    max_concurrent: int = _int("MAX_CONCURRENT", 2)

    session_ttl_s: int = 7200

    def plafond(self, membre: bool) -> int:
        return self.max_urls_membre if membre else self.max_urls_anon


settings = Settings()
