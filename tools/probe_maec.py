"""Einmalige Diagnose: Mäc-Geiz-Prospekte auf kaufda/marktguru rund um 04275."""
import json, re, yaml
from urllib.parse import quote
from watcher import fetch, flyers, ocr

cfg = yaml.safe_load(open("config.yaml"))
fc, prod = cfg["flyers"], cfg["product"]
ids = set()
for q in ["Mäc-Geiz", "Mäc Geiz", "Maec Geiz", "MäcGeiz"]:
    try:
        data = flyers._json(f"{flyers.API}?query={quote(q)}&lat={fc['lat']}&lng={fc['lng']}&offset=0&limit=48")
        bs = ((data.get("searchResults") or {}).get("contents") or {}).get("brochures") or []
        print(f"Suche '{q}': {len(bs)} Prospekte")
        for b in bs:
            c = b.get("content") or {}
            print("   ", c.get("id"), json.dumps({k: c.get(k) for k in ("title", "publisherName", "publisher", "validFrom", "validUntil")}, ensure_ascii=False)[:300])
            if q == "Mäc-Geiz" and c.get("id"):
                ids.add(c["id"])
    except Exception as e:
        print(q, e)
for slug in ["Maec-Geiz", "Mäc-Geiz", "Maec-Geiz-Leipzig", "Mac-Geiz", "MaecGeiz"]:
    url = f"https://www.kaufda.de/Geschaefte/{slug}"
    try:
        got = flyers.publisher_brochures(url)
        print("Händlerseite", url, "->", got)
        ids |= got
    except Exception as e:
        print("Händlerseite", url, e)
try:
    h = flyers.marktguru_keys()
    d = json.loads(fetch.get("https://api.marktguru.de/api/v1/offers/search?as=web&q=Pokemon&zipCode=04275&limit=100&offset=0", headers=h))
    print("marktguru Händler mit Pokémon:", sorted({a.get("name") for o in d.get("results") or [] for a in o.get("advertisers") or []}))
except Exception as e:
    print("marktguru", e)
for bid in sorted(ids):
    try:
        offers = flyers.brochure_offers({**fc, "_extras": []}, bid, prod)
        data, pub = flyers._PAGES[bid]
        pages = data.get("contents") or []
        v = next(((o.get("content") or {}).get("publicationProfiles") or [{}])[0].get("validity")
                 for p in pages for o in p.get("offers") or [])
        print(f"MÄC-GEIZ {bid}: {pub}, {len(pages)} Seiten, gültig {v}, Pokémon: {[(o['title'], o['price']) for o in offers]}")
        if ocr.available():
            for h_ in ocr.scan(bid, pub, data, prod, max_pages=60) or []:
                print(f"   OCR Seite {h_['page']+1}: Preise {h_['prices']} | {h_['snippet'][:160]}")
    except Exception as e:
        print(bid, type(e).__name__, e)
