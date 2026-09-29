"""HTTP-Abruf mit Browser-Fingerprint (curl_cffi), Fallback auf requests."""
from __future__ import annotations

import random
import time

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
}

try:
    from curl_cffi import requests as _http  # imitiert den TLS-Fingerprint eines echten Chrome

    def _get(url: str, timeout: int):
        return _http.get(url, headers=HEADERS, impersonate="chrome", timeout=timeout, allow_redirects=True)
except ImportError:  # pragma: no cover - lokale Umgebung ohne curl_cffi
    import requests as _http

    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

    def _get(url: str, timeout: int):
        return _http.get(url, headers={**HEADERS, "User-Agent": _UA}, timeout=timeout)


class FetchError(Exception):
    pass


_BLOCK_MARKERS = ("captcha", "access denied", "are you a robot", "cf-challenge",
                  "request unsuccessful", "px-captcha", "_incapsula_", "bot protection",
                  "pardon our interruption", "just a moment...", "attention required")


def get(url: str, timeout: int = 25) -> str:
    """Seite laden; wirft FetchError bei HTTP-Fehler oder erkennbarer Bot-Sperre."""
    time.sleep(random.uniform(0.5, 1.5))  # höflich bleiben
    try:
        r = _get(url, timeout)
    except Exception as e:  # Netzwerkfehler jeglicher Art
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code == 404:
        raise FetchError("HTTP 404 (Seite existiert nicht mehr?)")
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    text = r.text
    head = text[:5000].lower()
    if len(text) < 20000 and any(m in head for m in _BLOCK_MARKERS):
        raise FetchError("Bot-Schutz/Captcha")
    # Kleine Seite ganz ohne Links = JavaScript-Prüfseite statt Shop (z. B. Cards Paradise).
    if len(text) < 40000 and "<a " not in text.lower() and not text.lstrip().startswith(("{", "[", "<?xml", "<rss")):
        raise FetchError("Bot-Schutz (JavaScript-Prüfseite)")
    return text


def get_json(url: str, timeout: int = 25):
    import json
    try:
        return json.loads(get(url, timeout))
    except json.JSONDecodeError as e:
        raise FetchError(f"Kein JSON: {e}") from e
