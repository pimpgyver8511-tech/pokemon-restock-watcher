"""Einmalige Diagnose: Smyths-Toys-Prospekte auf kaufda (Händlerseite)."""
import json, re, yaml
from watcher import fetch, flyers

cfg = yaml.safe_load(open("config.yaml"))["flyers"]
prod = yaml.safe_load(open("config.yaml"))["product"]
for url in ["https://www.kaufda.de/Geschaefte/Smyths-Toys", "https://www.kaufda.de/Leipzig/Geschaefte/Smyths-Toys",
            "https://www.kaufda.de/Leipzig/Smyths-Toys/p-r1234"]:
    try:
        t = fetch.get(url)
    except Exception as e:
        print(url, e); continue
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', t, re.S)
    print(url, len(t), "NEXT" if m else "")
    if not m:
        continue
    d = json.loads(m.group(1))
    found = {}
    def walk(n, path=""):
        if isinstance(n, dict):
            if isinstance(n.get("id"), str) and re.fullmatch(r"[0-9a-f-]{36}", n["id"]) and ("title" in n or "publisher" in n or "validUntil" in n or "validFrom" in n):
                found[n["id"]] = {k: n.get(k) for k in ("title", "type", "validFrom", "validUntil", "pageCount")} | {"pub": (n.get("publisher") or {}).get("name") if isinstance(n.get("publisher"), dict) else n.get("publisherName"), "path": path[-60:]}
            for k, v in n.items(): walk(v, path + "/" + k)
        elif isinstance(n, list):
            for v in n: walk(v, path)
    walk(d)
    for k, v in list(found.items())[:15]:
        print("  ", k, json.dumps(v, ensure_ascii=False)[:230])
    stores = re.findall(r"Leipzig[^\"<]{0,80}", t)
    print("   Leipzig-Erwähnungen:", stores[:5])
    for bid in [k for k, v in found.items() if "smyth" in json.dumps(v).lower()][:4]:
        try:
            offers = flyers.brochure_offers(cfg, bid, prod)
            data, pub = flyers._PAGES[bid]
            print("   Seiten:", len(data.get("contents") or []), "Pokémon-Angebote:", [(o["title"], o["price"]) for o in offers][:8])
        except Exception as e:
            print("   ", bid, type(e).__name__, e)
