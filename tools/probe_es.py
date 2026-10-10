"""Einmalige Diagnose: spanische/Palma-Shops – erreichbar? Shopify? 30th Celebration ETB (EN) gelistet?"""
import json
from urllib.parse import quote_plus
from watcher import fetch
from watcher.parse import product_links
import yaml

cfg = yaml.safe_load(open("config.yaml"))
ttb = cfg["product"]
SITES = ["gamers-palma.com", "www.pokemillon.com", "todohits.com", "ludicon.es", "tristantshop.com", "www.tristantshop.com",
         "kantocards.com", "pokeelitetcg.com", "www.overkicks.com", "dracotienda.com", "www.dracotienda.com", "www.jugamosotra.com",
         "www.goblintrader.es", "www.cartasmagicas.es", "www.mtgtienda.com"]
for host in SITES:
    out = []
    try:
        d = json.loads(fetch.get(f"https://{host}/search/suggest.json?q={quote_plus('30th Celebration Elite Trainer Box')}&resources[type]=product&resources[limit]=10",
                                 headers={"Accept": "application/json"}, timeout=15))
        prods = d["resources"]["results"]["products"]
        out.append("SHOPIFY " + str([(p["title"][:70], p.get("available"), p.get("price")) for p in prods][:6]))
    except Exception as e:
        out.append(f"kein Shopify ({str(e)[:60]})")
        for pat in ["/?s={q}&post_type=product", "/search?q={q}", "/busqueda?controller=search&s={q}", "/index.php?controller=search&s={q}"]:
            url = f"https://{host}" + pat.format(q=quote_plus("30th Celebration Elite Trainer Box"))
            try:
                html = fetch.get(url, timeout=15)
                links = product_links(html, url, ttb, limit=5)
                out.append(f"{pat}: {len(html)} B, Links {links}")
                if links:
                    break
            except Exception as e2:
                out.append(f"{pat}: {str(e2)[:60]}")
    print(host, "|", " || ".join(out))
