"""Neue Listungen in Shopify-Kartenläden finden.

Shopify-Shops haben eine einheitliche Suchschnittstelle (/search/suggest.json). Taucht dort die
Top-Trainer-Box auf, wird die Produktseite wie ein normaler Shop weiter überwacht.
"""
from __future__ import annotations

import json
from urllib.parse import quote, urljoin

from . import fetch
from .parse import is_asset, matches_product


def search(domain: str, query: str, product_cfg: dict) -> list[str]:
    url = (f"https://{domain}/search/suggest.json?q={quote(query)}"
           "&resources[type]=product&resources[limit]=10")
    try:
        data = json.loads(fetch.get(url, headers={"Accept": "application/json"}))
    except json.JSONDecodeError as e:
        raise fetch.FetchError(f"keine JSON-Antwort ({e})") from e
    products = ((data.get("resources") or {}).get("results") or {}).get("products") or []
    found = []
    for p in products:
        if matches_product(p.get("title"), set(), product_cfg) and p.get("url"):
            link = urljoin(f"https://{domain}/", p["url"].split("?")[0])
            if "/products/" in link and not is_asset(link):
                found.append(link)
    return found


def discover(cfg: dict, shops: list[dict], product_cfg: dict, state: dict) -> list[dict]:
    """Virtuelle Shop-Einträge für Shopify-Läden, in denen die Box (neu) gelistet ist."""
    known_hosts = {u.split("/")[2] for s in shops for u in s.get("urls", []) if "//" in u}
    found: dict[str, list[str]] = state.setdefault("shopify_found", {})
    for domain in cfg.get("domains", []):
        if domain in known_hosts or f"www.{domain}" in known_hosts:
            continue
        urls: list[str] = []
        for q in cfg.get("queries", []):
            try:
                urls += search(domain, q, product_cfg)
            except Exception as e:  # einzelne Shops dürfen den Lauf nicht abbrechen
                print(f"Shopify-Suche {domain}: {type(e).__name__}: {e}")
                break
        if urls:
            found[domain] = list(dict.fromkeys(found.get(domain, []) + urls))[-5:]
    print(f"Shopify-Suche: Box gelistet bei {sorted(found) or 'keinem weiteren Shop'}")
    return [{"name": domain.removeprefix("www."), "country": "DE", "ships_to_de": "yes",
             "urls": urls, "discovered": True} for domain, urls in sorted(found.items())]
