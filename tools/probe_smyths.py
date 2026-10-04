"""Einmalige Diagnose: taucht Smyths Toys in Preisvergleichen auf?"""
import re
from curl_cffi import requests

URLS = ["https://geizhals.de/pok-mon-30-jahre-top-trainer-box-a3942792.html",
        "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=0196214144842",
        "https://www.tcgcheck.de/pokemon/set/de/30-jahre/30-jahre-top-trainer-box",
        "https://www.billiger.de/search?searchstring=0196214144842",
        "https://www.guenstiger.de/Suche.html?q=0196214144842",
        "https://www.preis.de/suche?q=0196214144842"]
for u in URLS:
    try:
        r = requests.get(u, impersonate="chrome", timeout=25, headers={"Accept-Language": "de-DE,de;q=0.9"})
    except Exception as e:
        print(u, type(e).__name__, e); continue
    t = r.text
    hits = [m.start() for m in re.finditer(r"smyth", t, re.I)]
    print(f"{u}: HTTP {r.status_code}, {len(t)} B, 'smyth' x{len(hits)}")
    for i in hits[:4]:
        print("   ", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, i-300): i+300]))[:300])
