"""Einmalige Diagnose: Sprache/Varianten der ETB bei Pokemillon und PokeEliteTCG."""
import json, re
from urllib.parse import quote_plus
from watcher import fetch

for host, q in [("www.pokemillon.com", "30th"), ("pokeelitetcg.com", "30th Celebration Elite Trainer Box")]:
    d = json.loads(fetch.get(f"https://{host}/search/suggest.json?q={quote_plus(q)}&resources[type]=product&resources[limit]=10",
                             headers={"Accept": "application/json"}))
    for p in d["resources"]["results"]["products"]:
        if not re.search(r"etb|elite|élite", p["title"], re.I):
            continue
        url = f"https://{host}{p['url'].split('?')[0]}"
        js = json.loads(fetch.get(url + ".js", headers={"Accept": "application/json"}))
        body = re.sub(r"<[^>]+>", " ", js.get("description") or "")
        print("==", url, "|", js["title"], "| available", js["available"], "| tags", js.get("tags"))
        for v in js["variants"]:
            print("   variant:", v.get("title"), v.get("available"), v.get("price"), v.get("barcode"))
        print("   lang:", re.findall(r"(?i)(ingl[eé]s|english|espa[ñn]ol|spanish|alem[aá]n|german|idioma[^.]{0,60})", body)[:6])
        print("   desc:", re.sub(r"\s+", " ", body)[:300])
