"""Filialbestand bei MediaMarkt und Saturn im Umkreis einer Postleitzahl.

Nutzt die GraphQL-Schnittstelle, die auch die Shop-Webseite verwendet (Persisted Queries).
Aufbau der Antworten ist nicht offiziell dokumentiert – deshalb werden sie generisch durchsucht
und bei Auffälligkeiten ausführlich protokolliert.
"""
from __future__ import annotations

import json
import math
import uuid
from urllib.parse import urlencode

from . import fetch
from .parse import matches_product

HASHES = {
    "SearchV4": "7aeed59ab03d5ecc470c3e70adb1d77fa989a72cf8ab14ba317abb55cdf0501c",
    "GetClosestStoresWithFoundLocation": "88ffaa9ef7d3133fb7ae856bd248dc01ffd6773e913fb938455d60d2ab2fadf4",
    "GetCofrPickupFeaturePickups": "81938af9c97c7bf5419b70d29819aba4cdee672fba4b020a5178dadd3b0cd979",
}
CHAINS = {"MediaMarkt": ("Media", "www.mediamarkt.de"), "Saturn": ("Saturn", "www.saturn.de")}
STATUS = {
    "AVAILABLE_WITHIN_THIRTY_MINUTES": "in_stock",
    "AVAILABLE_WITHIN_REASONABLE_TIMEFRAME": "soon",
    "NOT_AVAILABLE": "out_of_stock",
}


def _gql(chain: str, operation: str, variables: dict) -> dict:
    sales_line, host = CHAINS[chain]
    ext = {"persistedQuery": {"version": 1, "sha256Hash": HASHES[operation]},
           "pwa": {"captureChannel": "DESKTOP", "salesLine": sales_line, "country": "DE",
                   "language": "de", "globalLoyaltyProgram": True}}
    url = f"https://{host}/api/v1/graphql?" + urlencode({
        "operationName": operation, "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(ext, separators=(",", ":"))})
    headers = {
        "accept": "*/*", "content-type": "application/json", "x-operation": operation,
        "x-mms-salesline": sales_line, "x-mms-country": "DE", "x-mms-language": "de",
        "x-cacheable": "true", "x-flow-id": str(uuid.uuid4()),
        "apollographql-client-name": "pwa-client-pqm", "apollographql-client-version": "8.454.0",
        "referer": f"https://{host}/de/search.html",
    }
    try:
        data = json.loads(fetch.get(url, headers=headers))
    except json.JSONDecodeError as e:
        raise fetch.FetchError(f"{operation}: keine JSON-Antwort ({e})") from e
    if data.get("errors") and not data.get("data"):
        raise fetch.FetchError(f"{operation}: {str(data['errors'])[:200]}")
    return data.get("data") or {}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def distance_km(lat1, lng1, lat2, lng2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def nearby_stores(chain: str, cfg: dict) -> list[dict]:
    data = _gql(chain, "GetClosestStoresWithFoundLocation",
                {"lat": cfg["lat"], "lng": cfg["lng"], "limit": 25})
    stores = []
    for n in _walk(data):
        pos = n.get("position") or n.get("location") or {}
        sid = n.get("outlet_id") or n.get("outletId") or n.get("storeId")
        lat, lng = pos.get("lat") or pos.get("latitude"), pos.get("lng") or pos.get("longitude")
        if sid is None or lat is None or lng is None:
            continue
        d = distance_km(cfg["lat"], cfg["lng"], float(lat), float(lng))
        if d <= cfg["radius_km"]:
            addr = n.get("address") or {}
            stores.append({"id": str(sid), "name": n.get("name") or f"Markt {sid}",
                           "street": " ".join(filter(None, [addr.get("street"), addr.get("houseNumber")])),
                           "distance_km": round(d, 1)})
    uniq = {s["id"]: s for s in stores}
    return sorted(uniq.values(), key=lambda s: s["distance_km"])


def find_products(chain: str, cfg: dict, product_cfg: dict) -> list[dict]:
    """Produkt-IDs der Top-Trainer-Box im Sortiment der Kette (auch reine Filialware)."""
    found = {}
    for query in cfg.get("queries", []):
        data = _gql(chain, "SearchV4", {"query": query, "page": 1, "filters": [],
                                        "locale": "de-DE", "salesLine": CHAINS[chain][0]})
        for n in _walk(data):
            title = n.get("title") or n.get("name")
            pid = n.get("productId") or n.get("id")
            if not (isinstance(title, str) and pid) or not matches_product(title, set(), product_cfg):
                continue
            pid = str(pid).split(":")[2] if str(pid).count(":") >= 2 else str(pid)
            if pid.isdigit():
                url = n.get("url") or n.get("productUrl") or ""
                found[pid] = {"id": pid, "title": title,
                              "url": f"https://{CHAINS[chain][1]}{url}" if url.startswith("/") else url or None}
    return list(found.values())


def pickup_status(chain: str, product_ids: list[str], store_id: str) -> dict[str, str]:
    data = _gql(chain, "GetCofrPickupFeaturePickups",
                {"productIds": product_ids[:12], "storeId": store_id, "config": {}})
    result = {}
    for n in _walk(data):
        status = n.get("pickupStatus")
        nid = str(n.get("id") or n.get("productId") or "")
        if status:
            pid = nid.split(":")[2] if nid.count(":") >= 2 else nid
            result[pid or "?"] = status
    return result


def check(cfg: dict, product_cfg: dict, state: dict) -> tuple[list[dict], list[str]]:
    """-> (Einträge je Filiale und Produkt, Fehler)"""
    entries, errors = [], []
    for chain in cfg.get("chains", list(CHAINS)):
        try:
            stores = nearby_stores(chain, cfg)
            products = find_products(chain, cfg, product_cfg)
        except Exception as e:  # Filialabfrage darf den restlichen Lauf nie abbrechen
            errors.append(f"{chain}: {type(e).__name__}: {e}")
            continue
        print(f"Filialen {chain}: {len(stores)} im Umkreis, {len(products)} passende Produkte "
              f"{[p['title'][:50] for p in products]}")
        if not products:
            for s in stores:
                entries.append({"chain": chain, **s, "status": "not_listed", "product": None, "url": None})
            continue
        for s in stores:
            try:
                status = pickup_status(chain, [p["id"] for p in products], s["id"])
            except Exception as e:
                errors.append(f"{chain} {s['name']}: {e}")
                continue
            for p in products:
                raw = status.get(p["id"])
                entries.append({"chain": chain, **s, "product": p["title"], "url": p["url"],
                                "status": STATUS.get(raw, "unknown"), "raw": raw})
    return entries, errors
