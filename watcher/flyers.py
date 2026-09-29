"""Prospekt-Angebote von kaufda.de (wie in der Bier-App) und marktguru.de rund um eine Postleitzahl.

1. /api/search liefert die IDs aller Prospekte, die zu einem Suchbegriff passen.
2. content-viewer-be.kaufda.de liefert pro Prospekt alle Angebote mit Produktname,
   Preis, Händler und Gültigkeit.
3. Zusätzlich die gerenderte Themenseite (…/Angebote/Pokemon, __NEXT_DATA__).
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from . import fetch, ocr
from .parse import matches_product

API = "https://www.kaufda.de/api/search"
PAGES = "https://content-viewer-be.kaufda.de/v1/brochures/{id}/pages"
JSON_HEADERS = {"Accept": "application/json"}
_PAGES: dict[str, tuple[dict, str | None]] = {}  # Prospekt-ID -> (Seitendaten, Händler) für die OCR


def _json(url: str):
    try:
        return json.loads(fetch.get(url, headers=JSON_HEADERS))
    except json.JSONDecodeError as e:
        raise fetch.FetchError(f"keine JSON-Antwort ({e})") from e


def _offer_url(cfg: dict, brochure_id, page, offer_id) -> str | None:
    if not brochure_id or page is None or not offer_id:
        return None
    params = urlencode({"lat": cfg["lat"], "lng": cfg["lng"], "zip": cfg["postal_code"],
                        "page": page, "productId": offer_id})
    return f"https://www.kaufda.de/contentViewer/static/{brochure_id}?{params}"


def search_brochures(cfg: dict, query: str, max_items: int = 240) -> set[str]:
    ids: set[str] = set()
    limit = 24
    for offset in range(0, max_items, limit):
        data = _json(f"{API}?query={quote(query)}&lat={cfg['lat']}&lng={cfg['lng']}"
                     f"&offset={offset}&limit={limit}")
        brochures = ((data.get("searchResults") or {}).get("contents") or {}).get("brochures") or []
        ids |= {b["content"]["id"] for b in brochures if (b.get("content") or {}).get("id")}
        if len(brochures) < limit:
            break
    return ids


def _classify(text: str, product_cfg: dict, cfg: dict) -> str | None:
    """exact = die 30-Jahre-Top-Trainer-Box, pokemon = sonstige Pokémon-Karten (nur Info)."""
    if matches_product(text, set(), product_cfg):
        return "exact"
    t = text.lower()
    related = cfg.get("related_any") or []
    if ("pokemon" in t or "pokémon" in t) and (not related or any(w in t for w in related)):
        return "pokemon"
    return None


def brochure_offers(cfg: dict, brochure_id: str, product_cfg: dict) -> list[dict]:
    data = _json(PAGES.format(id=brochure_id) + f"?partner=kaufda_web&brochureKey=&lat={cfg['lat']}&lng={cfg['lng']}")
    found = []
    total, publisher = 0, None
    for page in data.get("contents") or []:
        for offer in page.get("offers") or []:
            c = offer.get("content") or {}
            total += 1
            publisher = publisher or (c.get("publisher") or {}).get("name")
            product = (c.get("products") or [{}])[0]
            desc = " ".join(d.get("paragraph") or "" for d in product.get("description") or [])
            title = " ".join(filter(None, [product.get("brandName"), product.get("name")]))
            cats = " ".join(x.get("name") or "" for x in product.get("categoryPaths") or [])
            kind = _classify(f"{title} {desc} {cats}", product_cfg, cfg)
            if not kind:
                continue
            deal = next((d for d in c.get("deals") or [] if d.get("type") == "SALES_PRICE"), {})
            validity = ((c.get("publicationProfiles") or [{}])[0].get("validity") or {})
            parent = c.get("parentContent") or {}
            found.append({
                "id": c.get("id"), "kind": kind, "store": (c.get("publisher") or {}).get("name"),
                "title": title or product.get("name"), "description": desc[:160],
                "price": deal.get("min") or deal.get("max"),
                "valid_from": (validity.get("startDate") or "")[:10] or None,
                "valid_until": (validity.get("endDate") or "")[:10] or None,
                "url": _offer_url(cfg, parent.get("id"), (parent.get("page") or {}).get("number"), c.get("id")),
            })
    _PAGES[brochure_id] = (data, publisher)
    print(f"  Prospekt {brochure_id[:8]} ({publisher or '?'}): {total} Angebote, {len(found)} mit Pokémon")
    return found


def page_offers(cfg: dict, url: str, product_cfg: dict) -> list[dict]:
    html = fetch.get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    items = (((((data.get("props") or {}).get("pageProps") or {}).get("pageInformation") or {})
              .get("offers") or {}).get("main") or {}).get("items") or []
    found = []
    for it in items:
        title = " ".join(filter(None, [it.get("brand"), it.get("title")]))
        kind = _classify(f"{title} {it.get('description') or ''}", product_cfg, cfg)
        if not kind:
            continue
        parent = it.get("parentContent") or {}
        found.append({
            "id": it.get("id"), "kind": kind, "store": it.get("publisherName"), "title": title,
            "description": (it.get("description") or "")[:160],
            "price": (it.get("prices") or {}).get("mainPrice"),
            "valid_from": (it.get("validFrom") or "")[:10] or None,
            "valid_until": (it.get("validUntil") or "")[:10] or None,
            "url": _offer_url(cfg, parent.get("id"), (parent.get("page") or {}).get("number"), it.get("id")),
        })
    return found


def ocr_offers(cfg: dict, product_cfg: dict, state: dict, current_ids: set[str]) -> list[dict]:
    """Seitenbilder ausgewählter Händler per OCR lesen (je Prospekt nur einmal)."""
    ocfg = cfg.get("ocr") or {}
    if state.get("ocr_version") != 5:  # verbesserte Erkennung → alte Ergebnisse neu lesen
        state["ocr_cache"], state["ocr_version"] = {}, 5
    cache: dict = state.setdefault("ocr_cache", {})
    for bid in list(cache):  # abgelaufene Prospekte vergessen
        if bid not in current_ids:
            del cache[bid]
    if not ocfg.get("enabled"):
        return []
    if not ocr.available():
        print("OCR: tesseract nicht installiert – übersprungen")
        return []
    wanted = [r.lower() for r in ocfg.get("retailers", [])]
    budget = ocfg.get("max_brochures_per_run", 6)
    pinned = set(cfg.get("brochures", []))
    for bid in sorted(_PAGES, key=lambda b: b not in pinned):  # fest eingetragene Prospekte zuerst
        data, publisher = _PAGES[bid]
        if bid in cache or budget <= 0:
            continue
        if bid not in pinned and wanted and not any(w in (publisher or "").lower() for w in wanted):
            continue
        budget -= 1
        hits = ocr.scan(bid, publisher, data, product_cfg, ocfg.get("max_pages", 60))
        if hits is not None:
            cache[bid] = {"publisher": publisher, "hits": hits}
    offers = []
    for bid, entry in cache.items():
        for h in entry["hits"]:
            params = urlencode({"lat": cfg["lat"], "lng": cfg["lng"], "zip": cfg["postal_code"], "page": h["page"]})
            offers.append({
                "id": f"ocr-{bid}-{h['page']}", "kind": "exact" if h["exact"] else "pokemon",
                "store": entry["publisher"], "title": f"Pokémon auf Seite {h['page'] + 1} (Texterkennung)",
                "description": h["snippet"][:160], "price": None, "ocr_prices": h["prices"],
                "valid_from": None, "valid_until": None, "source": "ocr",
                "url": f"https://www.kaufda.de/contentViewer/static/{bid}?{params}",
            })
    return offers


def check(cfg: dict, product_cfg: dict, state: dict | None = None) -> tuple[list[dict], list[str]]:
    offers: dict[str, dict] = {}
    errors: list[str] = []
    brochure_ids: set[str] = set()
    for q in cfg.get("queries", []) + cfg.get("retailers", []):
        try:
            brochure_ids |= search_brochures(cfg, q, max_items=48)
        except Exception as e:  # Prospekte dürfen den restlichen Lauf nie abbrechen
            errors.append(f"Suche '{q}': {type(e).__name__}: {e}")
    brochure_ids |= set(cfg.get("brochures", []))
    print(f"Prospekte: {len(brochure_ids)} Prospekte werden durchsucht")
    for bid in sorted(brochure_ids)[: cfg.get("max_brochures", 40)]:
        try:
            for o in brochure_offers(cfg, bid, product_cfg):
                offers[o["id"] or f"{bid}-{o['title']}"] = o
        except Exception as e:
            errors.append(f"Prospekt {bid}: {type(e).__name__}: {e}")
    for url in cfg.get("pages", []):
        try:
            for o in page_offers(cfg, url, product_cfg):
                offers.setdefault(o["id"] or o["title"], o)
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {e}")
    try:
        for o in ocr_offers(cfg, product_cfg, state if state is not None else {}, brochure_ids):
            offers.setdefault(o["id"], o)
    except Exception as e:
        errors.append(f"OCR: {type(e).__name__}: {e}")
    if cfg.get("marktguru", True):
        try:
            mg = marktguru_offers(cfg, product_cfg)
            print(f"Prospekte marktguru: {len(mg)} Pokémon-Angebote")
            for o in mg:
                offers.setdefault(o["id"], o)
        except Exception as e:
            errors.append(f"marktguru: {type(e).__name__}: {e}")
    result = sorted(offers.values(), key=lambda o: (o["kind"] != "exact", o.get("price") or 1e9))
    return result, errors


# --------------------------------------------------------------------------- marktguru.de

MARKTGURU = "https://api.marktguru.de/api/v1/offers/search"


def marktguru_keys() -> dict:
    """Öffentliche API-Schlüssel, die marktguru.de selbst in die Startseite einbettet."""
    html = fetch.get("https://www.marktguru.de/")
    for raw in re.findall(r'<script\s+type="application/json">(.*?)</script>', html, re.S):
        try:
            conf = json.loads(raw).get("config") or {}
        except (json.JSONDecodeError, AttributeError):
            continue
        if conf.get("apiKey") and conf.get("clientKey"):
            return {"x-apikey": conf["apiKey"], "x-clientkey": conf["clientKey"], "Accept": "application/json"}
    raise fetch.FetchError("marktguru: API-Schlüssel nicht gefunden")


def marktguru_offers(cfg: dict, product_cfg: dict) -> list[dict]:
    headers = marktguru_keys()
    found = []
    for q in cfg.get("queries", []):
        url = f"{MARKTGURU}?as=web&q={quote(q)}&zipCode={cfg['postal_code']}&limit=100&offset=0"
        try:
            data = json.loads(fetch.get(url, headers=headers))
        except json.JSONDecodeError as e:
            raise fetch.FetchError(f"marktguru: keine JSON-Antwort ({e})") from e
        for o in data.get("results") or []:
            product = o.get("product") or {}
            title = " ".join(filter(None, [(o.get("brand") or {}).get("name"), product.get("name")]))
            kind = _classify(f"{title} {o.get('description') or ''}", product_cfg, cfg)
            if not kind:
                continue
            validity = (o.get("validityDates") or [{}])[0]
            found.append({
                "id": f"mg-{o.get('id')}", "kind": kind,
                "store": ", ".join(a.get("name", "") for a in o.get("advertisers") or []) or None,
                "title": title, "description": (o.get("description") or "")[:160], "price": o.get("price"),
                "valid_from": (validity.get("from") or "")[:10] or None,
                "valid_until": (validity.get("to") or "")[:10] or None,
                "url": f"https://www.marktguru.de/search/{quote(q)}",
                "source": "marktguru",
            })
    return found
