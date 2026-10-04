"""Einmalige Diagnose: Smyths-Toys-Prospekte rund um Leipzig (kaufda, marktguru)."""
import json, re, yaml
from urllib.parse import quote
from watcher import fetch, flyers

cfg = yaml.safe_load(open("config.yaml"))["flyers"]
for q in ["Smyths Toys", "Smyths", "Spielzeug"]:
    data = flyers._json(f"{flyers.API}?query={quote(q)}&lat={cfg['lat']}&lng={cfg['lng']}&offset=0&limit=48")
    bs = ((data.get("searchResults") or {}).get("contents") or {}).get("brochures") or []
    pubs = sorted({json.dumps({k: (b.get("content") or {}).get(k) for k in ("id", "title")} | {"pub": ((b.get("content") or {}).get("publisher") or {}).get("name")}, ensure_ascii=False) for b in bs})
    print(f"kaufda '{q}': {len(bs)} Prospekte")
    for p in pubs:
        if "smyth" in p.lower() or q == "Smyths Toys":
            print("   ", p)
for url in ["https://www.kaufda.de/Leipzig/Smyths-Toys", "https://www.kaufda.de/Geschaefte/Smyths-Toys",
            "https://www.marktguru.de/r/smyths-toys", "https://www.smythstoys.com/de/de-de/prospekt"]:
    try:
        t = fetch.get(url)
        ids = set(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', t))
        print(url, "OK", len(t), "B, Leipzig" if "leipzig" in t.lower() else "", "IDs:", list(ids)[:8])
        for m in list(re.finditer(r"smyths[^<]{0,120}", t, re.I))[:5]:
            print("   ", m.group(0)[:120])
    except Exception as e:
        print(url, type(e).__name__, e)
try:
    h = flyers.marktguru_keys()
    d = json.loads(fetch.get(f"https://api.marktguru.de/api/v1/offers/search?as=web&q=Smyths&zipCode=04275&limit=50&offset=0", headers=h))
    print("marktguru Smyths:", d.get("totalResults"), [ (o.get("advertisers") or [{}])[0].get("name") for o in (d.get("results") or [])[:10]])
except Exception as e:
    print("marktguru", type(e).__name__, e)
