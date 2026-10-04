"""Einmalige Diagnose: Smyths-Toys-Prospekte auf kaufda (Filiale Leipzig)."""
import json, re, yaml
from watcher import fetch, flyers

cfg = yaml.safe_load(open("config.yaml"))["flyers"]
prod = yaml.safe_load(open("config.yaml"))["product"]
for url in ["https://www.kaufda.de/Leipzig/Smyths-Toys/p-r4683", "https://www.kaufda.de/Geschaefte/Smyths-Toys"]:
    t = fetch.get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', t, re.S)
    print("==", url, len(t))
    pp = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
    print("   pageProps keys:", list(pp)[:20])
    s = json.dumps(pp, ensure_ascii=False)
    for i in [x.start() for x in re.finditer(r'"(?:brochures?|contentViewer|brochureId)"', s)][:3]:
        print("   ...", s[i:i+500])
    ids = list(dict.fromkeys(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', t)))
    print("   IDs:", len(ids))
    for bid in ids[:12]:
        try:
            offers = flyers.brochure_offers(cfg, bid, prod)
            data, pub = flyers._PAGES[bid]
            print(f"   {bid}: {pub}, {len(data.get('contents') or [])} Seiten, Pokémon: {[(o['title'], o['price']) for o in offers][:6]}")
        except Exception as e:
            print(f"   {bid}: {type(e).__name__} {str(e)[:80]}")
