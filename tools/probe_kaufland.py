"""Einmalige Diagnose: Kaufland-Prospekte (inkl. Sonderprospekte ab 12.10.) auf kaufda + Pokémon-Suche."""
import json, re, yaml
from watcher import fetch, flyers, ocr

cfg = yaml.safe_load(open("config.yaml"))
fc, prod = cfg["flyers"], cfg["product"]
extras = [{"eans": [], "exclude_any": [], **p, "main": False} for p in cfg.get("more_products", [])]
fcx = {**fc, "_extras": extras}
for url in ["https://www.kaufda.de/Geschaefte/Kaufland", "https://www.kaufda.de/Leipzig/Kaufland"]:
    try:
        html = fetch.get(url)
    except Exception as e:
        print(url, e); continue
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    info = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("pageInformation") or {} if m else {}
    br = info.get("brochures") or {}
    print("==", url, {k: len(v) for k, v in br.items() if isinstance(v, list)})
    seen = set()
    for key in ("publisher", "viewer", "topRanked"):
        for b in br.get(key) or []:
            if not isinstance(b, dict) or b.get("contentId") in seen:
                continue
            seen.add(b.get("contentId"))
            print(f"  [{key}] {b.get('contentId')} | {b.get('title')} | {b.get('publisherName') or (b.get('publisher') or {}).get('name')} | "
                  f"{(b.get('validFrom') or '')[:10]} – {(b.get('validUntil') or '')[:10]} | pub {(b.get('publishedFrom') or '')[:10]}")
ids = set()
for q in ["Kaufland", "Pokemon", "Pokémon", "Spielwaren", "Sammelkarten"]:
    try:
        ids |= flyers.search_brochures(fc, q, max_items=96)
    except Exception as e:
        print("search", q, e)
ids |= flyers.publisher_brochures("https://www.kaufda.de/Geschaefte/Kaufland")
print("Prospekt-IDs gesamt:", len(ids))
for bid in sorted(ids):
    try:
        offers = flyers.brochure_offers(fcx, bid, prod)
    except Exception as e:
        print(" ", bid, e); continue
    data, pub = flyers._PAGES[bid]
    if "kaufland" not in (pub or "").lower():
        continue
    pages = data.get("contents") or []
    # Gültigkeit aus erstem Angebot
    v = None
    for p in pages:
        for o in p.get("offers") or []:
            v = ((o.get("content") or {}).get("publicationProfiles") or [{}])[0].get("validity"); break
        if v: break
    print(f"KAUFLAND {bid}: {len(pages)} Seiten, gültig {v}, Pokémon: {[(o['kind'], o['product'], o['title'], o['price']) for o in offers]}")
    if ocr.available():
        hits = ocr.scan(bid, pub, data, prod, max_pages=80)
        for h in hits or []:
            print(f"   OCR Seite {h['page']+1}: exact={h['exact']} Preise={h['prices']} | {h['snippet'][:150]}")
